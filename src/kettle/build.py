"""Execute cargo build and collect output artifacts."""

import subprocess
from pathlib import Path
from subprocess import CalledProcessError

from kettle.subprocess_utils import run_command
from kettle.utils import hash_file, hash_provenance_to_32bytes
from kettle.logger import log, log_error, log_section, log_success
from kettle.provenance import generate_provenance
from kettle.verification import verify_inputs


def detect_build_system(project_dir: Path) -> str:
    """Detect build system from project files.

    Args:
        project_dir: Path to project directory

    Returns:
        "nix" if flake.nix exists, "cargo" if Cargo.toml exists

    Raises:
        ValueError: If no supported build system detected
    """
    if (project_dir / "flake.nix").exists():
        return "nix"
    elif (project_dir / "Cargo.toml").exists():
        return "cargo"
    else:
        raise ValueError(
            "No supported build system detected. "
            "Expected flake.nix (Nix) or Cargo.toml (Cargo)."
        )


def run_cargo_build(project_dir: Path, release: bool = True) -> dict:
    """Execute cargo build and return artifacts with measurements.

    Returns:
        dict with:
            - success: bool
            - artifacts: list of dicts with 'path' and 'hash' keys
            - stdout: str
            - stderr: str
    """
    cmd = ["cargo", "build", "--locked"]
    if release:
        cmd.append("--release")

    try:
        result = run_command(cmd, cwd=project_dir)

        # Find built artifacts
        target_dir = project_dir / "target"
        build_type = "release" if release else "debug"
        bin_dir = target_dir / build_type

        artifacts = []
        if bin_dir.exists():
            # Find all executables (no extension on Unix, .exe on Windows)
            for item in bin_dir.iterdir():
                if item.is_file() and (not item.suffix or item.suffix == ".exe"):
                    # Check if executable
                    if item.stat().st_mode & 0o111:
                        artifacts.append({
                            "path": str(item),
                            "hash": hash_file(item),
                            "name": item.name,
                        })

        return {
            "success": True,
            "artifacts": artifacts,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except CalledProcessError as e:
        return {
            "success": False,
            "artifacts": [],
            "stdout": e.stdout,
            "stderr": e.stderr,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "artifacts": [],
            "stdout": "",
            "stderr": "cargo command not found",
        }


def execute_build(project_dir: Path, release: bool = True) -> dict:
    """Execute cargo build and measure output artifacts with logging.

    Args:
        project_dir: Path to Cargo project directory
        release: Whether to build in release mode

    Returns:
        Build result dictionary with success status and artifacts

    Raises:
        typer.Exit: If build fails
    """
    import typer

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


def generate_attestation(provenance_data: dict) -> tuple[Path, Path]:
    """Generate attestation using attest-amd command.

    Args:
        provenance_data: SLSA provenance dictionary to hash for attestation

    Returns:
        Tuple of (attestation_path, custom_data_path)
        - attestation_path: Path to evidence.b64 (base64 compressed bincode)
        - custom_data_path: Path to custom_data.hex (for verification)

    Raises:
        typer.Exit: If attestation generation fails
    """
    import typer

    log_section("Generating Attestation")

    # Hash provenance to 32-byte custom data
    custom_data = hash_provenance_to_32bytes(provenance_data)
    log_success("Custom data generated (64 hex chars)")
    log(f"  - Provenance hash: {custom_data}", style="dim")

    # Call attest-amd command
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


def run_build_workflow(
    project_dir: Path,
    output: Path,
    release: bool,
    verbose: bool,
    attestation: bool,
) -> None:
    """Complete build workflow: auto-detect build system and execute.

    This orchestrates the entire build command workflow:
    1. Detects build system (flake.nix → Nix, Cargo.toml → Cargo)
    2. Verifies all inputs (git, lock file, deps, toolchain)
    3. Executes build
    4. Measures output artifacts
    5. Generates passport with inputs and outputs
    6. Optionally generates attestation

    Args:
        project_dir: Path to project directory
        output: Output path for passport JSON
        release: Whether to build in release mode (Cargo only)
        verbose: Show all results
        attestation: Generate attestation report using attest-amd

    Raises:
        typer.Exit: If any step fails
    """
    import typer
    from . import nix

    try:
        # Detect build system
        try:
            build_system = detect_build_system(project_dir)
        except ValueError as e:
            log_error(str(e))
            raise typer.Exit(1)

        log(f"Detected build system: {build_system}", style="bold cyan")

        # Dispatch to appropriate workflow
        if build_system == "nix":
            # Nix workflow
            git_info, flake_lock_hash, results, toolchain = nix.verify_nix_inputs(
                project_dir, verbose
            )

            log_section("Building and Generating Provenance")
            provenance_data = nix.generate_nix_provenance(
                project_dir=project_dir,
                output_path=output,
                git_source=git_info,
                verbose=verbose,
            )

            log_success(f"Provenance generated: {output}")
            if git_info:
                log(f"  - Source commit: {git_info['commit_hash'][:8]}...", style="dim")
            log(f"  - {len(results)} flake inputs verified", style="dim")
            log(f"  - Toolchain: {toolchain['nix_version']}", style="dim")
            log(f"  - {len(provenance_data['subject'])} artifact(s) measured", style="dim")

        else:  # cargo
            # Existing Cargo workflow
            git_info, cargo_lock_hash, results, toolchain = verify_inputs(
                project_dir, verbose
            )

            build_result = execute_build(project_dir, release)

            log_section("Generating Provenance")

            output_artifacts = [(artifact['path'], artifact['hash']) for artifact in build_result['artifacts']]

            provenance_data = generate_provenance(
                git_source=git_info,
                cargo_lock_hash=cargo_lock_hash,
                toolchain=toolchain,
                verification_results=results,
                output_artifacts=output_artifacts,
                output_path=output,
            )

            log_success(f"Provenance generated: {output}")
            log_success(f"Manifest generated: manifest.json")

            if git_info:
                log(f"  - Source commit: {git_info['commit_hash'][:8]}...", style="dim")
            log(f"  - {len(results)} dependencies verified", style="dim")
            log(f"  - Toolchain: {toolchain['rustc_version'].split()[1]}", style="dim")
            log(f"  - {len(output_artifacts)} artifact(s) measured", style="dim")

        # Generate attestation if requested (same for both)
        if attestation:
            attestation_path, custom_data_path = generate_attestation(provenance_data)
            log("\n")
            log_success("Build complete with attestation")
            log(f"  - Passport: {output}", style="dim")
            log(f"  - Attestation: {attestation_path}", style="dim")

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)
