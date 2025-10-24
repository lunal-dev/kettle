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
from .passport import generate_passport, verify_passport
from .toolchain import get_toolchain_info
from .utils import hash_passport_to_32bytes


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
    passed_checks = []
    failed_checks = []
    skipped_checks = []

    for check_name, check_result in checks.items():
        message = check_result["message"]

        if check_result["verified"]:
            # Check if it's a warning/skip
            if any(word in message.lower() for word in ["mock", "not implemented", "skipped", "no "]):
                skipped_checks.append((check_name, message))
            else:
                passed_checks.append((check_name, message))
        else:
            # Check if it's a skip (not a hard failure)
            if any(word in message.lower() for word in ["skipped", "no "]):
                skipped_checks.append((check_name, message))
            else:
                failed_checks.append((check_name, message))

    # Display results
    print(f"\n{'=' * 60}")
    print(title)
    print(f"{'=' * 60}\n")

    if passed_checks:
        print("PASSED:")
        for name, message in passed_checks:
            print(f"  ✓ {name.replace('_', ' ').title()}")
            print(f"    {message}")

    if failed_checks:
        print("\nFAILED:")
        for name, message in failed_checks:
            print(f"  ✗ {name.replace('_', ' ').title()}")
            print(f"    {message}")

    if skipped_checks:
        label = "\nWARNINGS (POC Limitations):" if any("mock" in m.lower() or "not implemented" in m.lower()
                                                        for _, m in skipped_checks) else "\nSKIPPED:"
        print(label)
        for name, message in skipped_checks:
            print(f"  ⊘ {name.replace('_', ' ').title()}")
            print(f"    {message}")

    print(f"\n{'=' * 60}")
    all_passed = len(failed_checks) == 0
    print(f"✓ {success_message}" if all_passed else f"✗ {failure_message}")
    print("=" * 60)

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
            print(f"  ✗ Working tree has uncommitted changes", file=sys.stderr)
            print(f"\n  Uncommitted files:", file=sys.stderr)
            for file in git_info["dirty_files"]:
                print(f"    - {file}", file=sys.stderr)
            print(f"\n  Error: Builds require a clean git working tree.", file=sys.stderr)
            print(f"  Commit or stash your changes before building.", file=sys.stderr)
            raise typer.Exit(1)

        print(f"  ✓ Commit: {git_info['commit_hash']}")
        print(f"  ✓ Tree hash: {git_info['tree_hash']}")
        print(f"  ✓ Git binary: {git_info['git_path']}")
        print(f"    Hash: {git_info['git_binary_hash'][:16]}...")
        print(f"  ✓ Working tree: clean")
        if git_info.get("repository_url"):
            print(f"  ✓ Repository: {git_info['repository_url']}")
    else:
        print(f"  ⊘ Not a git repository (skipped)")

    return git_info


def print_verification_results(results, show_all: bool = False):
    """Print verification results to console."""
    verified = [r for r in results if r["verified"]]
    failed = [r for r in results if not r["verified"]]

    print(f"\n{'='*60}")
    print(f"Verification Results: {len(verified)}/{len(results)} passed")
    print(f"{'='*60}\n")

    if failed:
        print("FAILED:")
        for r in failed:
            dep = r["dependency"]
            print(f"  • {dep['name']} {dep['version']}")
            print(f"    {r['message']}")
            if dep.get("checksum"):
                print(f"    Cargo.lock checksum: {dep['checksum']}")
            if r.get("crate_path"):
                print(f"    Crate path: {r['crate_path']}")

    if show_all and verified:
        print("\nVERIFIED:")
        for r in verified:
            dep = r["dependency"]
            print(f"  • {dep['name']} {dep['version']}")
            print(f"    Status: {r['message']}")
            if dep.get("checksum"):
                print(f"    Cargo.lock checksum: {dep['checksum']}")
            if r.get("crate_path"):
                print(f"    Crate path: {r['crate_path']}")
                # Calculate and show the actual checksum
                import hashlib
                actual_hash = hashlib.sha256(r["crate_path"].read_bytes()).hexdigest()
                print(f"    Computed checksum:   {actual_hash}")
                print(f"    Match: ✓" if actual_hash == dep["checksum"] else f"    Match: ✗")


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
        print(f"Error: Cargo.lock not found in {project_dir}", file=sys.stderr)
        raise typer.Exit(1)

    print("=" * 60)
    print("Verifying Build Inputs")
    print("=" * 60)

    # Git source verification
    print("\n[1/4] Verifying git source...")
    git_info = verify_git_source_strict(project_dir)

    # Cargo.lock hash
    print("\n[2/4] Hashing Cargo.lock...")
    cargo_lock_hash = hash_cargo_lock(cargo_lock)
    print(f"  ✓ SHA256: {cargo_lock_hash}")

    # Dependencies verification
    print("\n[3/4] Verifying dependencies...")
    dependencies = parse_cargo_lock(cargo_lock)
    print(f"  Found {len(dependencies)} external dependencies")
    results = verify_all(dependencies)
    print_verification_results(results, verbose)

    if any(not r["verified"] for r in results):
        print("\n✗ Some dependencies failed verification", file=sys.stderr)
        raise typer.Exit(1)

    # Toolchain verification
    print("\n[4/4] Verifying Rust toolchain...")
    try:
        toolchain = get_toolchain_info()
        print(f"  ✓ rustc: {toolchain['rustc_version']}")
        print(f"    Hash: {toolchain['rustc_hash'][:16]}...")
        print(f"  ✓ cargo: {toolchain['cargo_version']}")
        print(f"    Hash: {toolchain['cargo_hash'][:16]}...")
    except Exception as e:
        print(f"  ✗ Toolchain verification failed: {e}", file=sys.stderr)
        raise typer.Exit(1)

    print("\n" + "=" * 60)
    print("✓ All inputs verified successfully")
    print("=" * 60)

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
    print("\n" + "=" * 60)
    print("Building Project")
    print("=" * 60)
    print(f"\n  Mode: {'release' if release else 'debug'}")
    print(f"  Command: cargo build --locked {'--release' if release else ''}")

    build_result = run_cargo_build(project_dir, release=release)

    if not build_result["success"]:
        print(f"\n✗ Build failed", file=sys.stderr)
        if build_result["stderr"]:
            print(f"\n{build_result['stderr']}", file=sys.stderr)
        raise typer.Exit(1)

    print(f"\n  ✓ Build successful")
    print(f"  ✓ Artifacts: {len(build_result['artifacts'])}")

    for artifact in build_result['artifacts']:
        print(f"    - {artifact['name']}")
        print(f"      SHA256: {artifact['hash']}")

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
    print(f"\n" + "=" * 60)
    print(f"Generating Attestation")
    print(f"=" * 60)

    # Hash passport to 32-byte custom data
    custom_data = hash_passport_to_32bytes(passport_data)
    print(f"\n  ✓ Custom data generated (128 hex chars)")
    print(f"    - Passport hash: {custom_data[:64]}")
    print(f"    - Nonce: {custom_data[64:80]}...")

    # Save custom data for later verification
    # custom_data_path = Path("custom_data.hex")
    # custom_data_path.write_text(custom_data)

    # Call attest-amd command
    # TODO this doesn't error out when it fails.
    try:
        print(f"\n  Running: sudo attest-amd attest --custom-data {custom_data[:16]}...")
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

        print(f"\n  ✓ Attestation generated successfully")
        print(f"  ✓ Attestation saved: {attestation_path} (base64 compressed bincode)")
        print(f"  ✓ Custom data saved: {custom_data_path}")

        return attestation_path, custom_data_path

    except subprocess.CalledProcessError as e:
        print(f"\n  ✗ Attestation generation failed with exit code {e.returncode}", file=sys.stderr)
        if e.stderr:
            print(f"\n{e.stderr}", file=sys.stderr)
        raise typer.Exit(1)
    except FileNotFoundError:
        print(f"\n  ✗ attest-amd command not found", file=sys.stderr)
        print(f"  Install attest-amd or run without --attestation flag", file=sys.stderr)
        raise typer.Exit(1)
    finally:
        print("=" * 60)


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
        print(f"\n" + "=" * 60)
        print(f"Generating Passport")
        print(f"=" * 60)

        output_artifacts = [(artifact['path'], artifact['hash']) for artifact in build_result['artifacts']]

        passport_data = generate_passport(
            git_source=git_info,
            cargo_lock_hash=cargo_lock_hash,
            toolchain=toolchain,
            verification_results=results,
            output_artifacts=output_artifacts,
            output_path=output,
        )

        print(f"\n✓ Passport generated: {output}")
        if git_info:
            print(f"  - Source commit: {git_info['commit_hash'][:8]}...")
        print(f"  - {len(results)} dependencies verified")
        print(f"  - Toolchain: {toolchain['rustc_version'].split()[1]}")
        print(f"  - {len(output_artifacts)} artifact(s) measured")

        # Generate attestation if requested
        if attestation:
            attestation_path, custom_data_path = generate_attestation(passport_data)
            print(f"\n✓ Build complete with attestation")
            print(f"  - Passport: {output}")
            print(f"  - Attestation: {attestation_path}")
        else:
            print("=" * 60)

    except typer.Exit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


@app.command()
def verify_pass(
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
        print("=" * 60)
        print("Passport Verification")
        print("=" * 60)

        # Gather verification inputs
        git_commit = None
        cargo_lock_hash = None

        if manifest:
            print(f"\n[1/2] Loading verification manifest: {manifest.name}")
        elif project_dir:
            print(f"\n[1/2] Gathering verification data from project...")
            # Get git commit
            git_info = get_git_info(project_dir)
            if git_info:
                git_commit = git_info["commit_hash"]
                print(f"  ✓ Git commit: {git_commit[:8]}...")
            else:
                print(f"  ⊘ Not a git repository")

            # Get Cargo.lock hash
            cargo_lock = project_dir / "Cargo.lock"
            if cargo_lock.exists():
                cargo_lock_hash = hash_cargo_lock(cargo_lock)
                print(f"  ✓ Cargo.lock hash: {cargo_lock_hash[:16]}...")
            else:
                print(f"  ⊘ Cargo.lock not found")

        print(f"\n[2/2] Verifying passport: {passport.name}")

        # Run verification
        results = verify_passport(
            passport_path=passport,
            manifest_path=manifest,
            git_commit=git_commit,
            cargo_lock_hash=cargo_lock_hash,
            binary_path=binary,
            strict=strict,
        )

        # Print results
        print(f"\n{'=' * 60}")
        print(f"Verification Results")
        print(f"{'=' * 60}\n")

        # Show passport metadata
        if results["passport"]:
            passport_data = results["passport"]
            print(f"Passport Version: {passport_data.get('version', 'unknown')}")
            if passport_data.get("build_process", {}).get("timestamp"):
                print(f"Build Timestamp: {passport_data['build_process']['timestamp']}")
            print(f"\n{'=' * 60}\n")

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
        print(f"Error: {e}", file=sys.stderr)
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
        print("=" * 60)
        print("Attestation Verification")
        print("=" * 60)
        print(f"\nAttestation: {attestation}")
        print(f"Passport: {passport}")

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
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


@app.command()
def verify(
passport: Path = typer.Argument(
        ...,
        help="Path to passport JSON file",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    attestation: Path = typer.Option(
        None,
        "--attestation",
        "-a",
        help="Path to attestation f    ile (evidence.b64) for cryptographic verification",
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
    """Verify a passport document and optionally its attestation.

    This command performs two-phase verification:
    1. Attestation verification (if --attestation provided):
       - Cryptographic signature verification via attest-amd
       - Passport binding (attestation custom data matches passport hash)
       - Nonce freshness (timestamp-based replay protection)

    2. Passport content verification:
       - Passport format and structure
       - Git commit hash (from manifest or project directory)
       - Git tree hash (from manifest)
       - Cargo.lock hash (from manifest or project directory)
       - Input merkle root (from manifest)
       - Toolchain binary hashes - rustc and cargo (from manifest)
       - Binary artifact hashes (from manifest or --binary)

    Verification inputs can be provided via:
    - Verification manifest file (--manifest): A JSON file containing expected values
    - Project directory (--project-dir): Gathers git commit and Cargo.lock from project
    - Individual values (--binary): Direct specification of values to check

    Examples:
        # Verify passport only
        attestable-builds verify passport.json --project-dir .

        # Verify attestation + passport
        attestable-builds verify passport.json --attestation evidence.b64 --project-dir .

        # Verify with manifest
        attestable-builds verify passport.json --attestation evidence.b64 --manifest verify.json
    """
    try:
        attestation_passed = True
        passport_passed = True

        # Phase 1: Attestation Verification (if provided)
        if attestation:
            print("=" * 60)
            print("Phase 1: Attestation Verification")
            print("=" * 60)
            print(f"\nAttestation: {attestation}")
            print(f"Passport: {passport}")

            # Verify attestation
            attestation_results = verify_attestation(
                attestation_path=attestation,
                passport_path=passport,
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
                print(f"\n✗ Stopping verification due to attestation failure (strict mode)", file=sys.stderr)
                raise typer.Exit(1)

        # Phase 2: Passport Content Verification
        print("=" * 60)
        phase_title = "Phase 2: Passport Content Verification" if attestation else "Passport Verification"
        print(phase_title)
        print("=" * 60)

        # Gather verification inputs
        git_commit = None
        cargo_lock_hash = None

        if manifest:
            print(f"\nLoading verification manifest: {manifest.name}")
        elif project_dir:
            print(f"\nGathering verification data from project...")
            # Get git commit
            git_info = get_git_info(project_dir)
            if git_info:
                git_commit = git_info["commit_hash"]
                print(f"  ✓ Git commit: {git_commit[:8]}...")
            else:
                print(f"  ⊘ Not a git repository")

            # Get Cargo.lock hash
            cargo_lock = project_dir / "Cargo.lock"
            if cargo_lock.exists():
                cargo_lock_hash = hash_cargo_lock(cargo_lock)
                print(f"  ✓ Cargo.lock hash: {cargo_lock_hash[:16]}...")
            else:
                print(f"  ⊘ Cargo.lock not found")

        print(f"\nVerifying passport: {passport.name}")

        # Run passport verification
        passport_results = verify_passport(
            passport_path=passport,
            manifest_path=manifest,
            git_commit=git_commit,
            cargo_lock_hash=cargo_lock_hash,
            binary_path=binary,
            strict=strict,
        )

        # Show passport metadata
        if passport_results["passport"]:
            passport_data = passport_results["passport"]
            print(f"\nPassport Version: {passport_data.get('version', 'unknown')}")
            if passport_data.get("build_process", {}).get("timestamp"):
                print(f"Build Timestamp: {passport_data['build_process']['timestamp']}")

        # Display passport results
        passport_passed = _display_verification_checks(
            checks=passport_results["checks"],
            title="Passport Content Verification Results",
            success_message="Passport content verification PASSED",
            failure_message="Passport content verification FAILED",
        )

        # Final overall result
        overall_passed = attestation_passed and passport_passed

        if attestation:
            print("\n" + "=" * 60)
            if overall_passed:
                print("✓ OVERALL VERIFICATION PASSED")
                print("  Both attestation and passport verification succeeded")
            else:
                print("✗ OVERALL VERIFICATION FAILED")
                if not attestation_passed:
                    print("  Attestation verification failed")
                if not passport_passed:
                    print("  Passport content verification failed")
            print("=" * 60)

        if not overall_passed:
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
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
        print("=" * 60)
        print("Tee Build")
        print("=" * 60)

        # Create source archive
        print(f"\n[1/4] Creating source archive from {project_dir}...")
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            archive_path = Path(tmp.name)

        # Create zip manually to include .git but exclude target/
        print(f"  Creating zip archive (including .git, excluding target/)...")

        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in project_dir.rglob('*'):
                if file_path.is_file():
                    relative_path = file_path.relative_to(project_dir)
                    # Exclude target directory and common build artifacts
                    if not any(part in ['target', '__pycache__', '.pytest_cache', 'node_modules']
                              for part in relative_path.parts):
                        zipf.write(file_path, relative_path)

        # Show git info if available
        git_info = get_git_info(project_dir)
        if git_info:
            print(f"  ✓ Including git metadata from commit {git_info['commit_hash'][:8]}...")

        print(f"  ✓ Archive created: {archive_path.stat().st_size / 1024:.1f} KB")

        # Upload and build
        print(f"\n[2/4] Uploading to {api_url}/build...")
        with open(archive_path, "rb") as f:
            response = requests.post(
                f"{api_url}/build",
                files={"source": ("source.zip", f, "application/zip")},
                timeout=300,  # 5 minute timeout
            )

        if response.status_code != 200:
            print(f"  ✗ API error: {response.status_code}", file=sys.stderr)
            print(response.text, file=sys.stderr)
            raise typer.Exit(1)

        result = response.json()
        build_id = result["build_id"]

        if result["status"] != "success":
            print(f"  ✗ Build failed: {result.get('error', 'Unknown error')}", file=sys.stderr)
            raise typer.Exit(1)

        print(f"  ✓ Build succeeded")
        print(f"  ✓ Build ID: {build_id}")

        # Create output directory
        output_dir = Path(f"kettle-{build_id}")
        output_dir.mkdir(exist_ok=True)

        # Save passport and attestation
        print(f"\n[3/4] Saving passport and attestation to {output_dir}/...")

        # Save passport
        if result.get("passport"):
            passport_path = output_dir / "passport.json"
            passport_path.write_text(json.dumps(result["passport"], indent=2))
            print(f"  ✓ Passport: {passport_path}")

        # Save attestation
        if result.get("attestation"):
            attestation_path = output_dir / "evidence.b64"
            attestation_path.write_text(result["attestation"])
            print(f"  ✓ Attestation: {attestation_path}")
        else:
            print(f"  ⊘ Attestation not available")

        # Download artifacts
        if result.get("artifacts"):
            print(f"\n[4/4] Downloading {len(result['artifacts'])} artifact(s) to {output_dir}/...")
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
                        print(f"  ✓ {artifact_name}: {artifact_path} ({size_kb:.1f} KB)")
                    else:
                        print(f"  ⊘ Failed to download {artifact_name} (HTTP {artifact_response.status_code})")

                except Exception as e:
                    print(f"  ⊘ Failed to download {artifact_name}: {e}")
        else:
            print(f"\n[4/4] No artifacts to download")

        print("\n" + "=" * 60)
        print("✓ Remote build complete")
        print("=" * 60)

        # Summary
        print(f"\nBuild artifacts in: {output_dir}/")
        if result.get("passport"):
            print(f"  - passport.json")
        if result.get("attestation"):
            print(f"  - evidence.b64")
        if result.get("artifacts"):
            for artifact_name in result["artifacts"]:
                print(f"  - {artifact_name}")

        # Cleanup
        archive_path.unlink()

    except requests.exceptions.RequestException as e:
        print(f"\n✗ API request failed: {e}", file=sys.stderr)
        raise typer.Exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        raise typer.Exit(1)

def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
