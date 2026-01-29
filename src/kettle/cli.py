"""CLI interface for attestable-builds."""

import json
import typer

from pathlib import Path

from .build import run_build_workflow
from .client import (
    run_tee_build_workflow,
    run_tee_workload_workflow,
    run_tee_get_results_workflow,
)
from .logger import log, log_error, log_section, log_success
from .provenance.verification import (
    run_verify_passport_workflow,
    run_verify_attestation_workflow,
    run_combined_verify_workflow,
)
from .merkle import prove_inclusion_from_provenance
from .workload import WorkloadExecutor, generate_workload_provenance


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
        help="Generate attestation report using attest-amd command",
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

    run_build_workflow(project_dir, output, release, verbose, attestation)


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
    1. Cryptographic signature (via attest-amd verify)
    2. Provenance binding (hash in attestation matches provenance)

    Requires attest-amd to be installed.
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


@app.command()
def tee_build(
    project_dir: Path = typer.Argument(
        ".",
        help="Path to project directory",
        exists=True,
        file_okay=False,
    ),
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api",
        help="Attestable builds API URL",
    ),
):
    """Build project remotely via TEE API and download results."""
    run_tee_build_workflow(project_dir, api_url)


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


@app.command(name="tee-run-workload")
def tee_run_workload(
    workload_dir: Path = typer.Argument(
        ...,
        help="Path to workload directory containing workload.yaml",
        exists=True,
        file_okay=False,
    ),
    build_id: str = typer.Argument(..., help="Build ID from remote build"),
    expected_input_root: str = typer.Argument(..., help="Expected input merkle root"),
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api",
        help="Attestable builds API URL",
    ),
):
    """Upload and execute workload on remote build via TEE API."""
    run_tee_workload_workflow(workload_dir, build_id, expected_input_root, api_url)


@app.command(name="tee-get-results")
def tee_get_results(
    build_id: str = typer.Argument(..., help="Build ID"),
    workload_id: str = typer.Argument(..., help="Workload ID from execution"),
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api",
        help="Attestable builds API URL",
    ),
    output_dir: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: workload-results-{workload_id})",
    ),
):
    """Download full workload execution results from TEE."""
    run_tee_get_results_workflow(build_id, workload_id, api_url, output_dir)


@app.command(name="run-workload")
def run_workload(
    workload_location: Path = typer.Argument(
        ...,
        help="Path to workload directory containing workload.yaml",
        exists=True,
        file_okay=False,
    ),
    build_id: str = typer.Argument(
        ...,
        help="Build ID (build location will be /tmp/kettle-{build_id})",
    ),
):
    """Execute workload in sandboxed environment and generate provenance."""
    try:
        log_section("Running Workload")

        build_location = Path(f"/tmp/kettle/{build_id}")
        if not build_location.exists():
            log_error(f"Build location not found: {build_location}")
            raise typer.Exit(1)

        workload_yaml = workload_location / "workload.yaml"
        if not workload_yaml.exists():
            log_error(f"workload.yaml not found in {workload_location}")
            raise typer.Exit(1)

        log(f"\nWorkload: {workload_location}", style="bold")
        log(f"Build: {build_location}", style="dim")

        # Initialize and execute
        log("\n[1/3] Initializing...")
        executor = WorkloadExecutor(workload_yaml, build_location)
        log_success(f"Workload: {executor.workload.name}")

        log("\n[2/3] Executing...")
        result = executor.execute()

        for i, step in enumerate(result.steps, 1):
            icon = "✓" if step.status == "SUCCESS" else "✗"
            log(f"  [{i}] {step.name}: {icon} {step.status}")

        log(f"\nStatus: {result.status}", style="bold")

        # Generate provenance
        log("\n[3/3] Generating provenance...")
        provenance_path = build_location / "provenance.json"
        tools_dir = workload_location / "tools"
        scripts_dir = workload_location / "scripts"

        provenance_data = generate_workload_provenance(
            build_provenance_path=provenance_path,
            workload_path=workload_yaml,
            workload_result=result,
            tools_dir=tools_dir if tools_dir.exists() else None,
            scripts_dir=scripts_dir if scripts_dir.exists() else None,
        )

        import hashlib
        workload_id = hashlib.sha256(json.dumps(provenance_data, sort_keys=True).encode()).hexdigest()[:8]

        output_dir = Path.cwd() / f"kettle-workload-{workload_id}"
        output_dir.mkdir(exist_ok=True)

        (output_dir / "provenance.json").write_text(json.dumps(provenance_data, indent=2))
        (output_dir / "summary.json").write_text(json.dumps(result.summary, indent=2))

        for path, data in result.full_results.items():
            result_file = output_dir / Path(path).name
            if data["type"] == "json":
                result_file.write_text(json.dumps(data["content"], indent=2))
            elif data["type"] == "text":
                result_file.write_text(data["content"])

        log("\n")
        log_success(f"Results saved: {output_dir}/")

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
