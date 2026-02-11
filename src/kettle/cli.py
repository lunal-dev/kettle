"""CLI interface for attestable-builds."""

import json
import typer

from pathlib import Path

from .build import run_build_workflow
from .client import (
    run_tee_build_workflow,
)
from .logger import log, log_error, log_section, log_success
from .provenance.verification import (
    run_verify_passport_workflow,
    run_verify_attestation_workflow,
    run_combined_verify_workflow,
)
from .merkle import prove_inclusion_from_provenance


app = typer.Typer(help="Build-time verification and attestation for TEE deployments")


@app.command()
def build(
    project_dir: Path = typer.Argument(
        ".",
        help="Path to project directory",
        exists=True,
        file_okay=False,
    ),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory for build artifacts (default: project directory)",
    ),
    release: bool = typer.Option(
        True,
        "--release/--debug",
        help="Build in release mode (default) or debug mode (Cargo only)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all results"),
    attestation: bool = typer.Option(
        False,
        "--attestation",
        "-a",
        help="Generate attestation report using attestation service command",
    ),
    shallow: bool = typer.Option(
        False,
        "--shallow",
        help="Use shallow verification (flake inputs only, skip derivation graph evaluation for Nix)",
    ),
):
    """Build project with full input verification and output measurement.

    Auto-detects build system (Nix or Cargo) and executes appropriate workflow.

    This command:
    1. Detects build system (flake.nix → Nix, Cargo.toml → Cargo)
    2. Verifies all inputs (git, lock file, deps, toolchain)
    3. Executes build
    4. Measures output artifacts
    5. Generates SLSA v1.2 provenance

    """
    if output is None:
        output = project_dir

    run_build_workflow(project_dir, output, release, verbose, attestation, shallow)


@app.command(name="verify-provenance")
def verify_provenance(
    provenance_path: Path = typer.Argument(
        ...,
        help="Path to SLSA provenance JSON file",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    project_dir: Path = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Path to project directory (for verification against current state)",
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
    """Verify a SLSA provenance document.

    Verifies:
    - Provenance format and structure
    - Git commit/tree hash (if --project-dir)
    - Lockfile hash (if --project-dir)
    - Input merkle root (if --project-dir)
    - Binary artifact hash (if --binary)
    """
    run_verify_passport_workflow(provenance_path, project_dir, binary, strict)


# Keep old command name for backwards compatibility
@app.command(name="verify-passport", hidden=True)
def verify_passport(
    provenance_path: Path = typer.Argument(..., exists=True),
    project_dir: Path = typer.Option(None, "--project-dir", "-p", exists=True, file_okay=False),
    binary: Path = typer.Option(None, "--binary", "-b", exists=True, dir_okay=False),
    strict: bool = typer.Option(False, "--strict"),
):
    """Alias for verify-provenance (deprecated)."""
    run_verify_passport_workflow(provenance_path, project_dir, binary, strict)


@app.command(name="verify-attestation")
def verify_attestation_cmd(
    attestation: Path = typer.Argument(
        ...,
        help="Path to attestation file (evidence.b64)",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    provenance: Path = typer.Option(
        ...,
        "--provenance",
        "-p",
        help="Path to provenance JSON file",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
):
    """Verify an attestation report against a SLSA provenance document.

    Verifies:
    1. Cryptographic signature (via attestation service verify)
    2. Provenance binding (hash in attestation matches provenance)

    Requires attestation service to be installed.
    """
    run_verify_attestation_workflow(attestation, provenance)


@app.command()
def verify(
    build_dir: Path = typer.Argument(
        ...,
        help="Path to build directory containing provenance.json and evidence.b64",
        exists=True,
        file_okay=False,
    ),
    project_dir: Path = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Path to project directory for verification",
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
    """Verify SLSA provenance and attestation.

    Verifies both:
    1. Attestation report (evidence.b64) - cryptographic TEE verification
    2. Provenance content (provenance.json) - build parameters verification

    Expected structure:
    - build_dir/provenance.json
    - build_dir/evidence.b64 (optional)
    """
    run_combined_verify_workflow(build_dir, project_dir, binary, strict)



@app.command(name="prove-inclusion")
def prove_inclusion_cmd(
    provenance_path: Path = typer.Argument(
        ...,
        help="Path to provenance JSON file",
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
    """Generate and verify Merkle inclusion proofs.

    Generates proofs that specified hashes are included in the
    provenance's merkle root AND immediately verifies those proofs.

    Supports partial hash matching (e.g., "abc123" or "serde:1.0").
    """
    prove_inclusion_from_provenance(provenance_path, hashes, output)



def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
