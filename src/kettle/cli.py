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
from .build import run_cargo_build, execute_build, generate_attestation, run_build_workflow
from .cargo import hash_cargo_lock, parse_cargo_lock, verify_all
from .client import (
    run_tee_build_workflow,
    run_tee_workload_workflow,
    run_tee_get_results_workflow,
)
from .git import get_git_info
from .logger import log, log_error, log_section, log_success, log_warning
from .output import display_checks, display_dependency_results, display_verification_checks
from .provenance import generate_provenance, verify_build_provenance, generate_passport, verify_build_passport  # Now generates SLSA provenance
from .results import CheckResult
from .toolchain import get_toolchain_info
from .verification import (
    verify_git_source_strict,
    verify_inputs,
    run_verify_passport_workflow,
    run_verify_attestation_workflow,
    run_combined_verify_workflow,
)
from .merkle import gen_inclusion_proof
from .workload import (
    WorkloadExecutor,
    generate_workload_provenance,
)


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
    5. Generates SLSA v1.2 provenance and manifest

    Build artifacts are stored:
    - manifest.json → project directory
    - provenance.json → ./build/
    - evidence.b64 → ./build/ (if --attestation is used)
    - binaries → ./build/ (Nix builds)

    Use --output to specify a different project directory.
    """
    # Default output to project directory if not specified
    if output is None:
        output = project_dir

    run_build_workflow(project_dir, output, release, verbose, attestation)


@app.command()
def verify_passport(
    passport: Path = typer.Argument(
        ...,
        help="Path to SLSA provenance JSON file",
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
    """Verify a SLSA provenance document against known values.

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
    run_verify_passport_workflow(passport, manifest, project_dir, binary, strict)


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
    """Verify an attestation report against a SLSA provenance document.

    This command verifies:
    1. Cryptographic signature (via attest-amd verify)
    2. Provenance binding (hash in attestation matches provenance)
    3. Nonce freshness (timestamp-based replay protection)

    Requires attest-amd to be installed for cryptographic verification.

    Example:
        attestable-builds verify-attestation evidence.b64 custom_data.hex \\
            --passport provenance.json \\
            --max-age 3600
    """
    run_verify_attestation_workflow(attestation, passport)

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
    """Verify SLSA provenance document and attestation.

    This command verifies both:
    1. Attestation report (evidence.b64) - cryptographic TEE verification
    2. Provenance content (provenance.json) - build parameters verification

    Expected directory structure:
    - build_dir/build/provenance.json
    - build_dir/build/evidence.b64
    - build_dir/manifest.json (optional verification manifest)

    Example:
        attestable-builds verify ./my-project --project-dir ./my-project
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
    """Build project remotely via API and download results.

    This command:
    1. Creates source archive from project directory (includes .git, excludes target/)
    2. Uploads to remote build API
    3. Saves passport, attestation, and artifacts to kettle-{build_id}/ directory

    Example:
        attestable-builds tee-build . --api http://builder.example.com
    """
    run_tee_build_workflow(project_dir, api_url)


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
    the provenance's merkle root AND immediately verifies those proofs.

    Supports partial hash matching for convenience (e.g., "abc123" or "serde:1.0").

    Examples:
        # Prove inclusion of cargo.lock hash
        attestable-builds prove-inclusion provenance.json abc123...

        # Prove multiple hashes
        attestable-builds prove-inclusion provenance.json hash1 hash2 hash3

        # Prove inclusion of a dependency by partial match
        attestable-builds prove-inclusion provenance.json serde:1.0

        # Save proofs to file
        attestable-builds prove-inclusion provenance.json abc123 --output proofs.json
    """
    gen_inclusion_proof(passport, hashes, output)




@app.command(name="tee-run-workload")
def tee_run_workload(
    workload_dir: Path = typer.Argument(
        ...,
        help="Path to workload directory containing workload.yaml, tools/, scripts/",
        exists=True,
        file_okay=False,
    ),
    build_id: str = typer.Argument(
        ...,
        help="Build ID from remote build",
    ),
    expected_input_root: str = typer.Argument(
        ...,
        help="Expected input merkle root from build passport",
    ),
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api",
        help="Attestable builds API URL (TEE service)",
    ),
):
    """Upload and execute workload on remote build via TEE API.

    Example:
        attestable-builds tee-run-workload ./security-audit abc123 sha256:xyz... \\
            --api https://tee.example.com
    """
    run_tee_workload_workflow(workload_dir, build_id, expected_input_root, api_url)


@app.command(name="tee-get-results")
def tee_get_results(
    build_id: str = typer.Argument(
        ...,
        help="Build ID",
    ),
    workload_id: str = typer.Argument(
        ...,
        help="Workload ID from execution",
    ),
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api",
        help="Attestable builds API URL (TEE service)",
    ),
    output_dir: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: workload-results-{workload_id})",
    ),
):
    """Download full workload execution results from TEE.

    Example:
        attestable-builds tee-get-results abc123 def456 --api https://tee.example.com
    """
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
    """Execute workload in sandboxed environment and generate provenance.

    This command:
    1. Loads workload definition from workload directory
    2. Uses build at /tmp/kettle-{build_id}
    3. Executes workload steps in sandbox
    4. Generates workload provenance with execution results

    Example:
        attestable-builds run-workload ./my-workload abc123
    """
    try:
        log_section("Running Workload")

        # Determine build location from build ID
        build_location = Path(f"/tmp/kettle/{build_id}")
        if not build_location.exists():
            log_error(f"Build location not found: {build_location}")
            log(f"Expected build at /tmp/kettle/{build_id}")
            raise typer.Exit(1)

        # Check for workload.yaml
        workload_yaml = workload_location / "workload.yaml"
        if not workload_yaml.exists():
            log_error(f"workload.yaml not found in {workload_location}")
            raise typer.Exit(1)

        log(f"\nWorkload Location: {workload_location}", style="bold")
        log(f"Build ID: {build_id}", style="dim")
        log(f"Build Location: {build_location}", style="dim")

        # Initialize executor
        log("\n[1/3] Initializing workload executor...")
        executor = WorkloadExecutor(workload_yaml, build_location)
        log_success(f"Workload: {executor.workload.name}")
        log(f"  Timeout: {executor.workload.environment.timeout_seconds}s", style="dim")
        log(f"  Network: {'enabled' if executor.workload.environment.network_access else 'blocked'}", style="dim")

        # Execute workload
        log("\n[2/3] Executing workload steps...")
        result = executor.execute()

        # Display step results
        for i, step in enumerate(result.steps, 1):
            status_icon = "✓" if step.status == "SUCCESS" else "✗"
            log(f"\n  [{i}/{len(result.steps)}] {step.name}: {status_icon} {step.status}")
            log(f"      Exit code: {step.exit_code}", style="dim")
            log(f"      Duration: {step.duration_seconds:.2f}s", style="dim")
            if step.status != "SUCCESS" and step.stderr:
                log(f"      Error: {step.stderr[:200]}", style="dim")

        # Display summary
        log(f"\nExecution Status: {result.status}", style="bold")
        log(f"Total Time: {result.execution_time_seconds:.2f}s", style="dim")

        if result.summary.get("content"):
            log(f"\nResult Summary: {result.summary['content']}", style="bold")

        # Generate workload provenance
        log("\n[3/3] Generating workload provenance...")
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

        # Generate workload ID from provenance
        import hashlib
        workload_id = hashlib.sha256(json.dumps(provenance_data, sort_keys=True).encode()).hexdigest()[:8]

        # Create output directory in current working directory
        output_dir = Path.cwd() / f"kettle-workload-{workload_id}"
        output_dir.mkdir(exist_ok=True)

        # Save workload provenance
        provenance_file = output_dir / "provenance.json"
        provenance_file.write_text(json.dumps(provenance_data, indent=2))

        # Save summary
        summary_file = output_dir / "summary.json"
        summary_file.write_text(json.dumps(result.summary, indent=2))

        # Save full results as individual files
        for path, data in result.full_results.items():
            result_file = output_dir / Path(path).name
            if data["type"] == "json":
                result_file.write_text(json.dumps(data["content"], indent=2))
            elif data["type"] == "text":
                result_file.write_text(data["content"])

        log("\n")
        log_success("Workload execution complete")
        log_success(f"Results saved: {output_dir}/")
        log(f"  Workload ID: {workload_id}", style="dim")
        log(f"  - provenance.json", style="dim")
        log(f"  - summary.json", style="dim")
        for path in result.full_results.keys():
            log(f"  - {Path(path).name}", style="dim")

    except Exception as e:
        log_error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)




def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
