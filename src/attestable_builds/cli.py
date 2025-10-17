"""CLI interface for attestable-builds."""

import asyncio
import sys
from pathlib import Path

import typer

from . import __version__
from .build import run_cargo_build
from .cargo import parse_cargo_lock
from .evidence import generate_build_evidence
from .verify import hash_cargo_lock, verify_all

app = typer.Typer(help="Build-time verification and attestation for TEE deployments")


def _parse_and_verify(path: Path):
    """Parse Cargo.lock and verify dependencies. Returns results."""
    print(f"Parsing {path}...")
    dependencies = parse_cargo_lock(path)
    print(f"Found {len(dependencies)} dependencies")
    print("Verifying...")
    return asyncio.run(verify_all(dependencies))


def print_results(results, show_all: bool = False):
    """Print verification results to console."""
    verified = [r for r in results if r.verified]
    failed = [r for r in results if not r.verified]

    print(f"\n{'='*60}")
    print(f"Verification Results: {len(verified)}/{len(results)} passed")
    print(f"{'='*60}\n")

    if failed:
        print("FAILED:")
        for r in failed:
            print(f"  • {r.dependency.name} {r.dependency.version}")
            print(f"    {r.message}")

    if show_all and verified:
        print("\nVERIFIED:")
        for r in verified:
            print(f"  • {r.dependency.name} {r.dependency.version}: {r.message}")


@app.command()
def verify(
    path: Path = typer.Argument(
        "Cargo.lock",
        help="Path to Cargo.lock file",
        exists=True,
        dir_okay=False,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all results"),
):
    """Verify dependencies in Cargo.lock against registries."""
    try:
        results = _parse_and_verify(path)
        print_results(results, verbose)

        if any(not r.verified for r in results):
            raise typer.Exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


@app.command()
def evidence(
    path: Path = typer.Argument(
        "Cargo.lock",
        help="Path to Cargo.lock file",
        exists=True,
        dir_okay=False,
    ),
    output: Path = typer.Option(
        "build-evidence.json",
        "--output",
        "-o",
        help="Output path for build evidence JSON",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all results"),
):
    """Verify dependencies and generate build evidence."""
    try:
        results = _parse_and_verify(path)
        print_results(results, verbose)

        print(f"\nGenerating build evidence: {output}")
        cargo_lock_hash = hash_cargo_lock(path)
        evidence_data = generate_build_evidence(path, cargo_lock_hash, results, output_path=output)

        summary = evidence_data["verification_summary"]
        print(f"Build evidence generated: {summary['verified']}/{summary['total']} verified")

        if summary["failed"] > 0:
            raise typer.Exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
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
        "build-evidence.json",
        "--output",
        "-o",
        help="Output path for build evidence JSON",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all results"),
):
    """Verify inputs, build project, and generate build evidence."""
    try:
        cargo_lock = project_dir / "Cargo.lock"
        if not cargo_lock.exists():
            print(f"Error: Cargo.lock not found in {project_dir}", file=sys.stderr)
            raise typer.Exit(1)

        # Phase 1: Verify inputs
        results = _parse_and_verify(cargo_lock)
        print_results(results, verbose)

        if any(not r.verified for r in results):
            print("\nWARNING: Input verification failed, aborting build", file=sys.stderr)
            raise typer.Exit(1)

        # Phase 2: Execute build
        print(f"\nBuilding project in {project_dir}...")
        build_result = run_cargo_build(project_dir)

        if not build_result.success:
            print("Build failed:", file=sys.stderr)
            print(build_result.stderr, file=sys.stderr)
            raise typer.Exit(1)

        print(f"Build succeeded, {len(build_result.artifacts)} artifact(s) produced")

        # Phase 3: Generate build evidence
        print(f"\nGenerating build evidence: {output}")
        cargo_lock_hash = hash_cargo_lock(cargo_lock)
        evidence_data = generate_build_evidence(
            cargo_lock,
            cargo_lock_hash,
            results,
            output_artifacts=build_result.artifacts,
            output_path=output,
        )

        summary = evidence_data["verification_summary"]
        print(f"Build evidence generated: {summary['verified']}/{summary['total']} inputs verified")
        if "outputs" in evidence_data:
            print(f"{len(evidence_data['outputs'])} output(s) included")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


@app.command()
def version():
    """Show version information."""
    print(f"attestable-builds {__version__}")


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
