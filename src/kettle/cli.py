"""CLI interface for attestable-builds."""

import hashlib
import json
import secrets
import subprocess
import sys
import requests
import tempfile
import zipfile
import typer

from pathlib import Path

from .attestation import verify_attestation
from .build import run_cargo_build
from .cargo import hash_cargo_lock, parse_cargo_lock, verify_all
from .git import get_git_info
from .logger import log, log_error, log_section, log_success, log_warning
from .output import display_checks, display_dependency_results
from .passport import generate_passport, verify_build_passport
from .results import CheckResult
from .toolchain import get_toolchain_info
from .utils import hash_passport_to_32bytes
from .merkle import generate_inclusion_proofs, verify_inclusion_proof_from_data


app = typer.Typer(help="Build-time verification and attestation for TEE deployments")


def _display_verification_checks(
    checks: dict,
    title: str,
    success_message: str,
    failure_message: str,
) -> bool:
    """Display verification check results in consistent format.

    Args:
        checks: Dict of check results with 'verified' and 'message' keys
        title: Title to display above results
        success_message: Message to show if all checks pass
        failure_message: Message to show if any checks fail

    Returns:
        True if all checks passed, False otherwise
    """
    # Convert dict checks to CheckResult format
    check_results = {}
    for check_name, check_data in checks.items():
        # Determine if this is a warning/skip
        message = check_data["message"]
        is_skip = any(word in message.lower() for word in ["mock", "not implemented", "skipped", "no "])

        check_results[check_name.replace('_', ' ').title()] = CheckResult(
            verified=check_data["verified"],
            message=message,
            details={"critical": not is_skip}
        )

    # Use the new display function
    display_checks(check_results, title)

    # Check if all critical checks passed
    all_passed = all(
        result.verified or not result.details.get("critical", True)
        for result in check_results.values()
    )

    # Show final message
    if all_passed:
        log_success(success_message)
    else:
        log_error(failure_message)

    return all_passed



def verify_git_source_strict(project_dir: Path) -> tuple:
    """Verify git source with strict mode (fail on dirty working tree).

    Returns:
        Tuple of (git_info, should_exit) where should_exit indicates if we should exit

    Raises:
        typer.Exit: If working tree has uncommitted changes
    """
    git_info = get_git_info(project_dir)
    if git_info:
        # Check for uncommitted changes (strict mode)
        if not git_info["is_clean"]:
            log_error("Working tree has uncommitted changes")
            log("\nUncommitted files:")
            for file in git_info["dirty_files"]:
                log(f"  - {file}")
            log("\nError: Builds require a clean git working tree.")
            log("Commit or stash your changes before building.")
            raise typer.Exit(1)

        log_success(f"Commit: {git_info['commit_hash']}")
        log_success(f"Tree hash: {git_info['tree_hash']}")
        log_success(f"Git binary: {git_info['git_path']}")
        log(f"  Hash: {git_info['git_binary_hash'][:16]}...", style="dim")
        log_success("Working tree: clean")
        if git_info.get("repository_url"):
            log_success(f"Repository: {git_info['repository_url']}")
    else:
        log_warning("Not a git repository (skipped)")

    return git_info


def print_verification_results(results, show_all: bool = False):
    """Print verification results to console."""
    # If verbose, add detailed info to results
    if show_all:
        for r in results:
            if r.get("crate_path") and r.get("dependency", {}).get("checksum"):
                actual_hash = hashlib.sha256(r["crate_path"].read_bytes()).hexdigest()
                match = actual_hash == r["dependency"]["checksum"]
                r["message"] += f" | Match: {'✓' if match else '✗'}"

    # Use the new display function
    display_dependency_results(results)


def verify_inputs(
    project_dir: Path, verbose: bool = False
) -> tuple[dict | None, str, list[dict], dict]:
    """Verify all build inputs (git, Cargo.lock, dependencies, toolchain).

    Returns:
        Tuple of (git_info, cargo_lock_hash, verification_results, toolchain)

    Raises:
        typer.Exit: If verification fails
    """
    cargo_lock = project_dir / "Cargo.lock"
    if not cargo_lock.exists():
        log_error(f"Cargo.lock not found in {project_dir}")
        raise typer.Exit(1)

    log_section("Verifying Build Inputs")

    # Git source verification
    log("\n[1/4] Verifying git source...")
    git_info = verify_git_source_strict(project_dir)

    # Cargo.lock hash
    log("\n[2/4] Hashing Cargo.lock...")
    cargo_lock_hash = hash_cargo_lock(cargo_lock)
    log_success(f"SHA256: {cargo_lock_hash}")

    # Dependencies verification
    log("\n[3/4] Verifying dependencies...")
    dependencies = parse_cargo_lock(cargo_lock)
    log(f"Found {len(dependencies)} external dependencies", style="dim")
    results = verify_all(dependencies)
    print_verification_results(results, verbose)

    if any(not r["verified"] for r in results):
        log_error("Some dependencies failed verification")
        raise typer.Exit(1)

    # Toolchain verification
    log("\n[4/4] Verifying Rust toolchain...")
    try:
        toolchain = get_toolchain_info()
        log_success(f"rustc: {toolchain['rustc_version']}")
        log(f"  Hash: {toolchain['rustc_hash'][:16]}...", style="dim")
        log_success(f"cargo: {toolchain['cargo_version']}")
        log(f"  Hash: {toolchain['cargo_hash'][:16]}...", style="dim")
    except Exception as e:
        log_error(f"Toolchain verification failed: {e}")
        raise typer.Exit(1)

    log("\n")
    log_success("All inputs verified successfully")

    return git_info, cargo_lock_hash, results, toolchain


def execute_build(project_dir: Path, release: bool = True) -> dict:
    """Execute cargo build and measure output artifacts.

    Args:
        project_dir: Path to Cargo project directory
        release: Whether to build in release mode

    Returns:
        Build result dictionary with success status and artifacts

    Raises:
        typer.Exit: If build fails
    """
    log_section("Building Project")
    log(f"\nMode: {'release' if release else 'debug'}", style="dim")
    log(f"Command: cargo build --locked {'--release' if release else ''}", style="dim")

    build_result = run_cargo_build(project_dir, release=release)

    if not build_result["success"]:
        log_error("Build failed")
        if build_result["stderr"]:
            log(f"\n{build_result['stderr']}")
        raise typer.Exit(1)

    log_success("Build successful")
    log_success(f"Artifacts: {len(build_result['artifacts'])}")

    for artifact in build_result['artifacts']:
        log(f"  - {artifact['name']}", style="bold")
        log(f"    SHA256: {artifact['hash']}", style="dim")

    return build_result


def generate_attestation(passport_data: dict) -> tuple[Path, Path]:
    """Generate attestation using attest-amd command.

    Args:
        passport_data: Passport dictionary to hash for attestation

    Returns:
        Tuple of (attestation_path, custom_data_path)
        - attestation_path: Path to evidence.b64 (base64 compressed bincode)
        - custom_data_path: Path to custom_data.hex (for verification)

    Raises:
        typer.Exit: If attestation generation fails
    """
    log_section("Generating Attestation")

    # Hash passport to 32-byte custom data
    custom_data = hash_passport_to_32bytes(passport_data)
    log_success("Custom data generated (128 hex chars)")
    log(f"  - Passport hash: {custom_data[:64]}", style="dim")
    log(f"  - Nonce: {custom_data[64:80]}...", style="dim")

    # Save custom data for later verification
    # custom_data_path = Path("custom_data.hex")
    # custom_data_path.write_text(custom_data)

    # Call attest-amd command
    # TODO this doesn't error out when it fails.
    try:
        log(f"\nRunning: sudo attest-amd attest --custom-data {custom_data[:16]}...", style="dim")
        result = subprocess.run(
            ["sudo", "attest-amd", "attest", "--custom-data", custom_data],
            capture_output=True,
            text=True,
            check=True,
        )

        # attest-amd saves to evidence.b64 automatically
        attestation_path = Path("evidence.b64")
        if not attestation_path.exists():
            # Fallback: save stdout if file wasn't created
            attestation_path.write_text(result.stdout.strip())

        log_success("Attestation generated successfully")
        log_success(f"Attestation saved: {attestation_path} (base64 compressed bincode)")
        # TODO: Uncomment when custom_data_path saving is enabled (lines 290-291)
        # log_success(f"Custom data saved: {custom_data_path}")

        # TODO: Return custom_data_path when saving is enabled
        return attestation_path, None

    except subprocess.CalledProcessError as e:
        log_error(f"Attestation generation failed with exit code {e.returncode}")
        if e.stderr:
            log(f"\n{e.stderr}")
        raise typer.Exit(1)
    except FileNotFoundError:
        log_error("attest-amd command not found")
        log("Install attest-amd or run without --attestation flag")
        raise typer.Exit(1)


@app.command()
def build(
    project_dir: Path = typer.Argument(
        ".",
        help="Path to Cargo project directory",
        exists=True,
        file_okay=False,
    ),
    output: Path = typer.Option(
        "passport.json",
        "--output",
        "-o",
        help="Output path for passport JSON",
    ),
    release: bool = typer.Option(
        True,
        "--release/--debug",
        help="Build in release mode (default) or debug mode",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all results"),
    attestation: bool = typer.Option(
        False,
        "--attestation",
        "-a",
        help="Generate attestation report using attest-amd command",
    ),
):
    """Build project with full input verification and output measurement.

    This command:
    1. Verifies all inputs (git, Cargo.lock, deps, toolchain)
    2. Executes cargo build
    3. Measures output artifacts
    4. Generates passport with inputs and outputs
    """
    try:
        # Verify inputs
        git_info, cargo_lock_hash, results, toolchain = verify_inputs(
            project_dir, verbose
        )

        # Execute build
        build_result = execute_build(project_dir, release)

        # Generate passport with outputs
        log_section("Generating Passport")

        output_artifacts = [(artifact['path'], artifact['hash']) for artifact in build_result['artifacts']]

        passport_data = generate_passport(
            git_source=git_info,
            cargo_lock_hash=cargo_lock_hash,
            toolchain=toolchain,
            verification_results=results,
            output_artifacts=output_artifacts,
            output_path=output,
        )

        log_success(f"Passport generated: {output}")
        log_success(f"Manifest generated: manifest.json")

        if git_info:
            log(f"  - Source commit: {git_info['commit_hash'][:8]}...", style="dim")
        log(f"  - {len(results)} dependencies verified", style="dim")
        log(f"  - Toolchain: {toolchain['rustc_version'].split()[1]}", style="dim")
        log(f"  - {len(output_artifacts)} artifact(s) measured", style="dim")

        # Generate attestation if requested
        if attestation:
            attestation_path, custom_data_path = generate_attestation(passport_data)
            log("\n")
            log_success("Build complete with attestation")
            log(f"  - Passport: {output}", style="dim")
            log(f"  - Attestation: {attestation_path}", style="dim")

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)


@app.command()
def verify_passport(
    passport: Path = typer.Argument(
        ...,
        help="Path to passport JSON file",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    manifest: Path = typer.Option(
        None,
        "--manifest",
        "-m",
        help="Path to verification manifest JSON file",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    project_dir: Path = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Path to project directory (for git commit and Cargo.lock verification)",
        exists=True,
        file_okay=False,
    ),
    binary: Path = typer.Option(
        None,
        "--binary",
        "-b",
        help="Path to binary artifact to verify",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Fail if any optional checks cannot be performed",
    ),
):
    """Verify a passport document against known values.

    Verification can be done via:
    1. Verification manifest file (--manifest): A JSON file containing expected values
    2. Project directory (--project-dir): Gathers git commit and Cargo.lock from project
    3. Individual values (--binary): Direct specification of values to check

    This command verifies:
    - Passport format and structure
    - Git commit hash (from manifest or project directory)
    - Git tree hash (from manifest)
    - Cargo.lock hash (from manifest or project directory)
    - Input merkle root (from manifest)
    - Toolchain binary hashes - rustc and cargo (from manifest)
    - Binary artifact hashes (from manifest or --binary)
    """
    try:
        log_section("Passport Verification")

        # Gather verification inputs
        git_commit = None
        cargo_lock_hash = None

        if manifest:
            log(f"\n[1/2] Loading verification manifest: {manifest.name}")
        elif project_dir:
            log(f"\n[1/2] Gathering verification data from project...")
            # Get git commit
            git_info = get_git_info(project_dir)
            if git_info:
                git_commit = git_info["commit_hash"]
                log_success(f"Git commit: {git_commit[:8]}...")
            else:
                log_warning("Not a git repository")

            # Get Cargo.lock hash
            cargo_lock = project_dir / "Cargo.lock"
            if cargo_lock.exists():
                cargo_lock_hash = hash_cargo_lock(cargo_lock)
                log_success(f"Cargo.lock hash: {cargo_lock_hash[:16]}...")
            else:
                log_warning("Cargo.lock not found")

        log(f"\n[2/2] Verifying passport: {passport.name}")

        # Run verification
        results = verify_build_passport(
            passport_path=passport,
            manifest_path=manifest,
            git_commit=git_commit,
            cargo_lock_hash=cargo_lock_hash,
            binary_path=binary,
            strict=strict,
        )

        # Show passport metadata
        if results["passport"]:
            passport_data = results["passport"]
            log(f"\nPassport Version: {passport_data.get('version', 'unknown')}", style="dim")
            if passport_data.get("build_process", {}).get("timestamp"):
                log(f"Build Timestamp: {passport_data['build_process']['timestamp']}", style="dim")
            log("")

        # Display check results
        all_passed = _display_verification_checks(
            checks=results["checks"],
            title="Verification Results",
            success_message="Passport verification PASSED",
            failure_message="Passport verification FAILED",
        )

        if not all_passed:
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)


@app.command(name="verify-attestation")
def verify_attestation_cmd(
    attestation: Path = typer.Argument(
        ...,
        help="Path to attestation file (evidence.b64)",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    passport: Path = typer.Option(
        ...,
        "--passport",
        "-p",
        help="Path to passport JSON file",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),

):
    """Verify an attestation report against a passport document.

    This command verifies:
    1. Cryptographic signature (via attest-amd verify)
    2. Passport binding (hash in attestation matches passport)
    3. Nonce freshness (timestamp-based replay protection)

    Requires attest-amd to be installed for cryptographic verification.

    Example:
        attestable-builds verify-attestation evidence.b64 custom_data.hex \\
            --passport passport.json \\
            --max-age 3600
    """
    try:
        log_section("Attestation Verification")
        log(f"\nAttestation: {attestation}", style="dim")
        log(f"Passport: {passport}", style="dim")

        # Verify attestation
        results = verify_attestation(
            attestation_path=attestation,
            passport_path=passport,
        )

        # Display results
        all_passed = _display_verification_checks(
            checks=results["checks"],
            title="Verification Results",
            success_message="Attestation verification PASSED",
            failure_message="Attestation verification FAILED",
        )

        if not all_passed:
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)

@app.command()
def verify(
    build_dir: Path = typer.Argument(
        ...,
        help="Path to build directory containing passport.json and evidence.b64",
        exists=True,
        file_okay=False,
    ),
    project_dir: Path = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Path to project directory containing verification manifest.json",
        exists=True,
        file_okay=False,
    ),
    binary: Path = typer.Option(
        None,
        "--binary",
        "-b",
        help="Path to binary artifact to verify",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Fail if any optional checks cannot be performed",
    ),
):
    """
    Verify a passport document and it's attestation
    """
    try:
        # Find passport and attestation in build directory
        passport_path = build_dir / "passport.json"
        attestation_path = build_dir / "evidence.b64"

        if not passport_path.exists():
            log_error(f"passport.json not found in {build_dir}")
            raise typer.Exit(1)

        log(f"Build directory: {build_dir}", style="dim")
        log(f"Passport: {passport_path.name}", style="dim")

        # Check if attestation exists
        has_attestation = attestation_path.exists()
        if has_attestation:
            log(f"Attestation: {attestation_path.name}", style="dim")
        else:
            log("Attestation: not found (passport-only verification)", style="dim")

        attestation_passed = True
        passport_passed = True

        # Phase 1: Attestation Verification (if available)
        if has_attestation:
            log_section("Phase 1: Attestation Verification")

            # Verify attestation
            attestation_results = verify_attestation(
                attestation_path=attestation_path,
                passport_path=passport_path,
            )

            # Display attestation results
            attestation_passed = _display_verification_checks(
                checks=attestation_results["checks"],
                title="Attestation Verification Results",
                success_message="Attestation verification PASSED",
                failure_message="Attestation verification FAILED",
            )

            # Stop here if attestation fails and strict mode
            if not attestation_passed and strict:
                log_error("Stopping verification due to attestation failure (strict mode)")
                raise typer.Exit(1)

        # Phase 2: Passport Content Verification
        phase_title = "Phase 2: Passport Content Verification" if has_attestation else "Passport Verification"
        log_section(phase_title)

        # Gather verification inputs
        git_commit = None
        cargo_lock_hash = None
        manifest_path = None

        if project_dir:
            # Check for manifest in project directory
            potential_manifest = project_dir / "manifest.json"
            if potential_manifest.exists():
                manifest_path = potential_manifest
                log(f"Loading verification manifest: {manifest_path.name}")
            else:
                log(f"Gathering verification data from project directory...")
                # Get git commit
                git_info = get_git_info(project_dir)
                if git_info:
                    git_commit = git_info["commit_hash"]
                    log_success(f"Git commit: {git_commit[:8]}...")
                else:
                    log_warning("Not a git repository")

                # Get Cargo.lock hash
                cargo_lock = project_dir / "Cargo.lock"
                if cargo_lock.exists():
                    cargo_lock_hash = hash_cargo_lock(cargo_lock)
                    log_success(f"Cargo.lock hash: {cargo_lock_hash[:16]}...")
                else:
                    log_warning("Cargo.lock not found")

        log(f"\nVerifying passport: {passport_path.name}")

        # Run passport verification
        passport_results = verify_build_passport(
            passport_path=passport_path,
            manifest_path=manifest_path,
            git_commit=git_commit,
            cargo_lock_hash=cargo_lock_hash,
            binary_path=binary,
            strict=strict,
        )

        # Show passport metadata
        if passport_results["passport"]:
            passport_data = passport_results["passport"]
            log(f"\nPassport Version: {passport_data.get('version', 'unknown')}", style="dim")
            if passport_data.get("build_process", {}).get("timestamp"):
                log(f"Build Timestamp: {passport_data['build_process']['timestamp']}", style="dim")

        # Display passport results
        passport_passed = _display_verification_checks(
            checks=passport_results["checks"],
            title="Passport Content Verification Results",
            success_message="Passport content verification PASSED",
            failure_message="Passport content verification FAILED",
        )

        # Final overall result
        overall_passed = attestation_passed and passport_passed

        if has_attestation:
            log("\n")
            log_section("Overall Results")
            if overall_passed:
                log_success("OVERALL VERIFICATION PASSED")
                log("  Both attestation and passport verification succeeded", style="dim")
            else:
                log_error("OVERALL VERIFICATION FAILED")
                if not attestation_passed:
                    log("  Attestation verification failed", style="dim")
                if not passport_passed:
                    log("  Passport content verification failed", style="dim")

        if not overall_passed:
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)


@app.command()
def tee_build(
    project_dir: Path = typer.Argument(
        ".",
        help="Path to Cargo project directory",
        exists=True,
        file_okay=False,
    ),
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api",
        help="Attestable builds API URL",
    ),
):
    """Build project remotely via API and download results.

    This command:
    1. Creates source archive from project directory (includes .git, excludes target/)
    2. Uploads to remote build API
    3. Saves passport, attestation, and artifacts to kettle-{build_id}/ directory

    Example:
        attestable-builds tee-build . --api http://builder.example.com
    """


    try:
        log_section("TEE Build")

        # Create source archive
        log(f"\n[1/4] Creating source archive from {project_dir}...")
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            archive_path = Path(tmp.name)

        # Create zip manually to include .git but exclude target/
        log("  Creating zip archive (including .git, excluding target/)...", style="dim")

        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in project_dir.rglob('*'):
                if file_path.is_file():
                    relative_path = file_path.relative_to(project_dir)
                    # Exclude target directory and common build artifacts
                    if not any(part in ['target', '__pycache__', '.pytest_cache', 'node_modules', 'passport.json', 'manifest.json']
                              for part in relative_path.parts):
                        zipf.write(file_path, relative_path)

        # Show git info if available
        git_info = get_git_info(project_dir)
        if git_info:
            log_success(f"Including git metadata from commit {git_info['commit_hash'][:8]}...")

        log_success(f"Archive created: {archive_path.stat().st_size / 1024:.1f} KB")

        # Upload and build
        log(f"\n[2/4] Uploading to {api_url}/build...")
        with open(archive_path, "rb") as f:
            response = requests.post(
                f"{api_url}/build",
                files={"source": ("source.zip", f, "application/zip")},
                timeout=300,  # 5 minute timeout
            )

        if response.status_code != 200:
            log_error(f"API error: {response.status_code}")
            log(response.text)
            raise typer.Exit(1)

        result = response.json()
        build_id = result["build_id"]

        if result["status"] != "success":
            log_error(f"Build failed: {result.get('error', 'Unknown error')}")
            raise typer.Exit(1)

        log_success("Build succeeded")
        log_success(f"Build ID: {build_id}")

        # Create output directory
        output_dir = Path(f"kettle-{build_id}")
        output_dir.mkdir(exist_ok=True)

        # Save passport and attestation
        log(f"\n[3/4] Saving passport and attestation to {output_dir}/...")

        # Save passport
        if result.get("passport"):
            passport_path = output_dir / "passport.json"
            passport_path.write_text(json.dumps(result["passport"], indent=2))
            log_success(f"Passport: {passport_path}")

        # Save attestation
        if result.get("attestation"):
            attestation_path = output_dir / "evidence.b64"
            attestation_path.write_text(result["attestation"])
            log_success(f"Attestation: {attestation_path}")
        else:
            log_warning("Attestation not available")

        # Download artifacts
        if result.get("artifacts"):
            log(f"\n[4/4] Downloading {len(result['artifacts'])} artifact(s) to {output_dir}/...")
            for artifact_name in result["artifacts"]:
                try:
                    artifact_response = requests.get(
                        f"{api_url}/builds/{build_id}/artifacts/{artifact_name}",
                        timeout=60
                    )

                    if artifact_response.status_code == 200:
                        artifact_path = output_dir / artifact_name
                        artifact_path.write_bytes(artifact_response.content)
                        artifact_path.chmod(0o755)  # Make executable
                        size_kb = len(artifact_response.content) / 1024
                        log_success(f"{artifact_name}: {artifact_path} ({size_kb:.1f} KB)")
                    else:
                        log_warning(f"Failed to download {artifact_name} (HTTP {artifact_response.status_code})")

                except Exception as e:
                    log_warning(f"Failed to download {artifact_name}: {e}")
        else:
            log("\n[4/4] No artifacts to download")

        log("\n")
        log_success("Remote build complete")

        # Summary
        log(f"\nBuild artifacts in: {output_dir}/", style="bold")
        if result.get("passport"):
            log("  - passport.json", style="dim")
        if result.get("attestation"):
            log("  - evidence.b64", style="dim")
        if result.get("artifacts"):
            for artifact_name in result["artifacts"]:
                log(f"  - {artifact_name}", style="dim")

        # Cleanup
        archive_path.unlink()

    except requests.exceptions.RequestException as e:
        log_error(f"API request failed: {e}")
        raise typer.Exit(1)
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)


@app.command(name="prove-inclusion")
def prove_inclusion(
    passport: Path = typer.Argument(
        ...,
        help="Path to passport JSON file",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    hashes: list[str] = typer.Argument(
        ...,
        help="Hash values to prove inclusion for (supports partial matching)",
    ),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Save proofs to JSON file (default: print to stdout)",
    ),
):
    """Generate and verify Merkle inclusion proofs for hashes in a passport.

    This command both generates proofs that specified hashes are included in
    the passport's merkle root AND immediately verifies those proofs.

    Supports partial hash matching for convenience (e.g., "abc123" or "serde:1.0").

    Examples:
        # Prove inclusion of cargo.lock hash
        attestable-builds prove-inclusion passport.json abc123...

        # Prove multiple hashes
        attestable-builds prove-inclusion passport.json hash1 hash2 hash3

        # Prove inclusion of a dependency by partial match
        attestable-builds prove-inclusion passport.json serde:1.0

        # Save proofs to file
        attestable-builds prove-inclusion passport.json abc123 --output proofs.json
    """
    try:
        log_section("Merkle Inclusion Proof")

        # Load passport
        log(f"\nLoading passport: {passport}")
        passport_data = json.loads(passport.read_text())

        log(f"Generating proofs for {len(hashes)} hash(es)...\n")

        # Generate proofs
        result = generate_inclusion_proofs(passport_data, hashes)

        # Display generation results
        log(f"Merkle Root: {result['merkle_root']}", style="bold")
        log(f"Tree Size: {result['tree_size']} leaves\n")

        if result['proofs']:
            log_success(f"Generated {len(result['proofs'])} proof(s):")
            for proof in result['proofs']:
                log(f"\n  Target: {proof['target_hash']}", style="bold")
                log(f"  Label: {proof['label']}", style="dim")
                log(f"  Leaf Index: {proof['leaf_index']}", style="dim")
                log(f"  Leaf Hash: {proof['leaf_value'][:32]}...", style="dim")
                log(f"  Proof Size: {len(json.dumps(proof['proof']))} bytes", style="dim")

        if result['not_found']:
            log("\n")
            log_warning(f"{len(result['not_found'])} hash(es) not found:")
            for missing in result['not_found']:
                log(f"  - {missing}", style="dim")

        # Verify the proofs immediately
        if result['proofs']:
            log("\n")
            log_section("Verifying Proofs")

            merkle_root = bytes.fromhex(result['merkle_root'])
            all_valid = True

            for i, proof in enumerate(result['proofs'], 1):
                log(f"\n[{i}/{len(result['proofs'])}] {proof['label']}", style="bold")
                log(f"  Target: {proof['target_hash']}", style="dim")

                is_valid = verify_inclusion_proof_from_data(proof, merkle_root)

                if is_valid:
                    log_success("  Proof VALID")
                else:
                    log_error("  Proof INVALID")
                    all_valid = False

            # Final verification result
            log("\n")
            if all_valid:
                log_success(f"All {len(result['proofs'])} proof(s) verified successfully")
            else:
                log_error("Some proofs failed verification")
                if output:
                    output.write_text(json.dumps(result, indent=2))
                    log_warning(f"Proofs saved to: {output} (contains invalid proofs)")
                raise typer.Exit(1)

        # Save if requested
        if output and result['proofs']:
            output.write_text(json.dumps(result, indent=2))
            log("\n")
            log_success(f"Proofs saved to: {output}")
        elif output:
            log_warning("No proofs generated, file not saved")

        # Exit with error if some hashes weren't found
        if result['not_found'] and not result['proofs']:
            raise typer.Exit(1)

    except json.JSONDecodeError as e:
        log_error(f"Invalid JSON in passport: {e}")
        raise typer.Exit(1)
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)


@app.command()
def train(
    config: Path = typer.Argument(Path("./config.json"), help="Path to model config JSON (default: ./config.json)"),
    dataset: Path = typer.Option(Path("./data"), "--dataset", "-d", help="Path to dataset directory (default: ./data)"),
    output: Path = typer.Option(Path("./output"), "--output", "-o", help="Output directory (default: ./output)"),
    quick: bool = typer.Option(False, "--quick", help="Quick test mode (1 epoch)"),
    rebuild_binary: bool = typer.Option(False, "--rebuild-binary", help="Force rebuild of training binary"),
):
    """
    Train a model with attestable training.

    Training uses default settings: 10 epochs (or 1 with --quick), batch_size=256, lr=0.01, seed=42.
    If dataset is missing and download.py exists next to config, auto-downloads dataset.

    Examples:
      kettle train                                         # Use ./config.json, ./data, ./output
      kettle train --quick                                 # Quick test with defaults
      kettle train examples/training/mnist                 # Use directory as base
      kettle train custom.json --dataset /path             # Explicit config
    """
    try:
        from .training import train as train_impl

        # Handle quick mode
        if quick:
            log("[yellow]Quick mode: Training for 1 epoch only")

        # Smart config argument handling
        if config.is_dir():
            # Directory mode: use as base for config, data, output
            base_dir = config
            actual_config = base_dir / "config.json"
            actual_dataset = base_dir / "data" if dataset == Path("./data") else dataset
            actual_output = base_dir / "output" if output == Path("./output") else output
        else:
            # File mode: treat as explicit config path
            actual_config = config
            actual_dataset = dataset
            actual_output = output

        # Validate config path
        if not actual_config.exists():
            log_error(f"Model configuration not found: {actual_config}")
            raise typer.Exit(1)

        # Run training (auto-downloads dataset if missing)
        passport_path = train_impl(
            config=actual_config,
            dataset_path=actual_dataset,
            output_dir=actual_output,
            quick=quick,
            rebuild_binary=rebuild_binary,
        )

        log("\n")
        log_success(f"Training passport: {passport_path}")

    except Exception as e:
        log_error(f"Training failed: {e}")
        raise typer.Exit(1)


@app.command(name="train-verify")
def train_verify(
    passport: Path = typer.Argument(..., help="Path to training passport JSON"),
):
    """Verify a training passport and its chain of trust."""
    try:
        from .training import verify_training_passport

        if not passport.exists():
            log_error(f"Training passport not found: {passport}")
            raise typer.Exit(1)

        success = verify_training_passport(passport)

        if not success:
            raise typer.Exit(1)

    except Exception as e:
        log_error(f"Verification failed: {e}")
        raise typer.Exit(1)




@app.command(name="verify-determinism")
def verify_determinism_cmd(
    checkpoint1: Path = typer.Argument(..., help="Path to first checkpoint file"),
    checkpoint2: Path = typer.Argument(..., help="Path to second checkpoint file"),
):
    """Verify deterministic training by comparing checkpoint hashes.

    This command compares two checkpoint files to verify that training is deterministic.
    If the hashes match, it proves that the same inputs produce identical outputs.

    Example:
        kettle verify-determinism run1/final.safetensors run2/final.safetensors
    """
    try:
        from .training_inputs import hash_file

        log_section("Verifying Determinism")

        # Verify files exist
        if not checkpoint1.exists():
            log_error(f"Checkpoint 1 not found: {checkpoint1}")
            raise typer.Exit(1)
        if not checkpoint2.exists():
            log_error(f"Checkpoint 2 not found: {checkpoint2}")
            raise typer.Exit(1)

        log(f"\nCheckpoint 1: {checkpoint1}")
        hash1 = hash_file(checkpoint1)
        log(f"  Hash: {hash1}")

        log(f"\nCheckpoint 2: {checkpoint2}")
        hash2 = hash_file(checkpoint2)
        log(f"  Hash: {hash2}")

        log("")
        if hash1 == hash2:
            log_success("✓ Checkpoints are identical - training is deterministic!")
            log("  Same inputs produce the same outputs", style="dim")
        else:
            log_error("✗ Checkpoints differ - training is NOT deterministic!")
            log("  Different hashes indicate non-determinism", style="dim")
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
