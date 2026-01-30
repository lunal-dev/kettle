"""Build orchestration for attestable builds."""

import json
import shutil
import subprocess
from pathlib import Path

from kettle import core, provenance
from kettle.utils import hash_provenance_to_32bytes
from kettle.logger import log, log_error, log_section, log_success
from kettle.provenance.verification import verify_inputs


def generate_attestation(provenance_data: dict, output_dir: Path) -> Path:
    """Generate attestation using attest-amd command.

    Args:
        provenance_data: SLSA provenance dictionary to hash for attestation
        output_dir: Directory to write evidence.b64 file

    Returns:
        Path to evidence.b64 file

    Raises:
        typer.Exit: If attestation generation fails
    """
    import typer

    log_section("Generating Attestation")

    custom_data = hash_provenance_to_32bytes(provenance_data)
    log_success("Custom data generated")
    log(f"  Provenance hash: {custom_data}", style="dim")

    attestation_path = output_dir / "evidence.b64"

    try:
        log(f"\nRunning: sudo attest-amd attest --custom-data {custom_data[:16]}...", style="dim")
        result = subprocess.run(
            ["sudo", "attest-amd", "attest", "--custom-data", custom_data],
            capture_output=True,
            text=True,
            check=True,
        )

        # attest-amd saves to evidence.b64 in current directory
        default_attestation = Path("evidence.b64")
        if default_attestation.exists() and default_attestation.resolve() != attestation_path.resolve():
            default_attestation.rename(attestation_path)
        elif not attestation_path.exists():
            attestation_path.write_text(result.stdout.strip())

        log_success(f"Attestation saved: {attestation_path}")
        return attestation_path

    except subprocess.CalledProcessError as e:
        log_error(f"Attestation failed (exit {e.returncode})")
        if e.stderr:
            log(f"\n{e.stderr}")
        raise typer.Exit(1)
    except FileNotFoundError:
        log_error("attest-amd command not found")
        raise typer.Exit(1)


def run_build_workflow(
    project_dir: Path,
    output_dir: Path,
    release: bool = True,
    verbose: bool = False,
    attestation: bool = False,
    shallow: bool = False,
) -> None:
    """Complete build workflow.

    1. Detect toolchain
    2. Verify inputs (git, lockfile, deps, toolchain)
    3. Execute build
    4. Generate provenance
    5. Optionally generate attestation

    Args:
        project_dir: Path to project directory
        output_dir: Output directory for provenance.json
        release: Build in release mode (Cargo only)
        verbose: Show verbose output
        attestation: Generate attestation using attest-amd
        shallow: Use shallow verification (skip derivation graph evaluation)

    Raises:
        typer.Exit: If any step fails
    """
    import typer

    try:
        # Detect toolchain
        toolchain = core.detect(project_dir)
        if not toolchain:
            log_error("No supported build system detected (expected flake.nix or Cargo.toml)")
            raise typer.Exit(1)

        log(f"Detected: {toolchain.name}", style="bold cyan")

        # Setup output directories
        output_dir = output_dir.resolve()
        build_dir = output_dir / "kettle-build"

        # Clean up previous build artifacts to keep git working tree clean
        if build_dir.exists():
            shutil.rmtree(build_dir)

        build_dir.mkdir(parents=True, exist_ok=True)
        provenance_path = build_dir / "provenance.json"

        # Verify inputs
        git_info, lock, _, toolchain_info = verify_inputs(
            toolchain, project_dir, verbose=verbose, shallow=shallow
        )

        # Execute build
        log_section("Building Project")
        build_result = toolchain.build(project_dir, release=release)

        if not build_result["ok"]:
            log_error("Build failed")
            if build_result.get("stderr"):
                log(f"\n{build_result['stderr']}")
            raise typer.Exit(1)

        log_success("Build successful")
        for artifact in build_result["artifacts"]:
            log(f"  {artifact['name']}: {artifact['hash'][:16]}...", style="dim")

        # Generate provenance
        log_section("Generating Provenance")
        provenance_data = provenance.generate(
            toolchain=toolchain,
            git=git_info,
            lock=lock,
            info=toolchain_info,
            artifacts=build_result["artifacts"],
            output_path=provenance_path,
        )

        log_success(f"Provenance: {provenance_path}")

        # Generate manifest
        manifest_data = provenance.generate_verification_manifest(provenance_data)
        manifest_path = build_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data, indent=2))
        log_success(f"Manifest: {manifest_path}")
        if git_info:
            log(f"  Source: {git_info['commit_hash'][:8]}...", style="dim")
        # Show appropriate dependency count based on evaluation mode
        if lock.get("fetches"):
            log(f"  Dependencies: {len(lock['fetches'])} fetches (deep)", style="dim")
        else:
            log(f"  Dependencies: {len(lock['deps'])}", style="dim")
        log(f"  Artifacts: {len(build_result['artifacts'])}", style="dim")

        # Attestation (optional)
        if attestation:
            attestation_path = generate_attestation(provenance_data, build_dir)
            log("\n")
            log_success("Build complete with attestation")
            log(f"  Provenance: {provenance_path}", style="dim")
            log(f"  Attestation: {attestation_path}", style="dim")

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)
