"""CLI interface for attestable-builds."""

import sys
from pathlib import Path

import typer

from .cargo import hash_cargo_lock, parse_cargo_lock
from .git import get_git_info
from .golden import calculate_runner_measurement, save_golden_measurement
from .passport import generate_passport, hash_binary
from .tee_runner import run_tee_build
from .toolchain import get_toolchain_info
from .verify import verify_all

app = typer.Typer(help="Build-time verification and attestation for TEE deployments")


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
        if not git_info.is_clean:
            print(f"  ✗ Working tree has uncommitted changes", file=sys.stderr)
            print(f"\n  Uncommitted files:", file=sys.stderr)
            for file in git_info.dirty_files:
                print(f"    - {file}", file=sys.stderr)
            print(f"\n  Error: Builds require a clean git working tree.", file=sys.stderr)
            print(f"  Commit or stash your changes before building.", file=sys.stderr)
            raise typer.Exit(1)

        print(f"  ✓ Commit: {git_info.commit_hash}")
        print(f"  ✓ Tree hash: {git_info.tree_hash}")
        print(f"  ✓ Git binary: {git_info.git_path}")
        print(f"    Hash: {git_info.git_binary_hash[:16]}...")
        print(f"  ✓ Working tree: clean")
        if git_info.repository_url:
            print(f"  ✓ Repository: {git_info.repository_url}")
    else:
        print(f"  ⊘ Not a git repository (skipped)")

    return git_info


def print_verification_results(results, show_all: bool = False):
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
            if r.dependency.checksum:
                print(f"    Cargo.lock checksum: {r.dependency.checksum}")
            if r.crate_path:
                print(f"    Crate path: {r.crate_path}")

    if show_all and verified:
        print("\nVERIFIED:")
        for r in verified:
            print(f"  • {r.dependency.name} {r.dependency.version}")
            print(f"    Status: {r.message}")
            if r.dependency.checksum:
                print(f"    Cargo.lock checksum: {r.dependency.checksum}")
            if r.crate_path:
                print(f"    Crate path: {r.crate_path}")
                # Calculate and show the actual checksum
                import hashlib
                actual_hash = hashlib.sha256(r.crate_path.read_bytes()).hexdigest()
                print(f"    Computed checksum:   {actual_hash}")
                print(f"    Match: ✓" if actual_hash == r.dependency.checksum else f"    Match: ✗")


@app.command()
def verify(
    project_dir: Path = typer.Argument(
        ".",
        help="Path to Cargo project directory",
        exists=True,
        file_okay=False,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all results"),
):
    """Verify all Phase 1 inputs for a Rust project.

    This command verifies:
    - Git source (commit hash)
    - Cargo.lock hash
    - All dependencies (.crate files in cargo cache)
    - Rust toolchain (rustc and cargo binaries)
    """
    try:
        cargo_lock = project_dir / "Cargo.lock"
        if not cargo_lock.exists():
            print(f"Error: Cargo.lock not found in {project_dir}", file=sys.stderr)
            raise typer.Exit(1)

        # 1. Verify Git source (optional)
        print("=" * 60)
        print("Phase 1: Input Verification")
        print("=" * 60)
        print("\n[1/4] Verifying git source...")
        git_info = verify_git_source_strict(project_dir)

        # 2. Hash Cargo.lock
        print("\n[2/4] Hashing Cargo.lock...")
        cargo_lock_hash = hash_cargo_lock(cargo_lock)
        print(f"  ✓ SHA256: {cargo_lock_hash}")

        # 3. Verify dependencies
        print("\n[3/4] Verifying dependencies...")
        dependencies = parse_cargo_lock(cargo_lock)
        print(f"  Found {len(dependencies)} external dependencies")
        results = verify_all(dependencies)
        print_verification_results(results, verbose)

        if any(not r.verified for r in results):
            print("\n✗ Some dependencies failed verification", file=sys.stderr)
            raise typer.Exit(1)

        # 4. Verify toolchain
        print("\n[4/4] Verifying Rust toolchain...")
        try:
            toolchain = get_toolchain_info()
            print(f"  ✓ rustc: {toolchain.rustc_version}")
            print(f"    Hash: {toolchain.rustc_hash[:16]}...")
            print(f"  ✓ cargo: {toolchain.cargo_version}")
            print(f"    Hash: {toolchain.cargo_hash[:16]}...")
        except Exception as e:
            print(f"  ✗ Toolchain verification failed: {e}", file=sys.stderr)
            raise typer.Exit(1)

        print("\n" + "=" * 60)
        print("✓ All Phase 1 inputs verified successfully")
        print("=" * 60)

    except typer.Exit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


@app.command()
def passport(
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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all results"),
):
    """Generate a Phase 1 passport document with verified inputs.

    This command performs full input verification and generates a passport
    document containing all verified inputs.
    """
    try:
        cargo_lock = project_dir / "Cargo.lock"
        if not cargo_lock.exists():
            print(f"Error: Cargo.lock not found in {project_dir}", file=sys.stderr)
            raise typer.Exit(1)

        print("=" * 60)
        print("Generating Phase 1 Passport")
        print("=" * 60)

        # Verify all inputs
        print("\n[1/4] Verifying git source...")
        git_info = verify_git_source_strict(project_dir)

        print("\n[2/4] Hashing Cargo.lock...")
        cargo_lock_hash = hash_cargo_lock(cargo_lock)
        print(f"  ✓ SHA256: {cargo_lock_hash}")

        print("\n[3/4] Verifying dependencies...")
        dependencies = parse_cargo_lock(cargo_lock)
        results = verify_all(dependencies)
        print_verification_results(results, verbose)

        if any(not r.verified for r in results):
            print("\n✗ Cannot generate passport: dependency verification failed", file=sys.stderr)
            raise typer.Exit(1)

        print("\n[4/4] Verifying toolchain...")
        toolchain = get_toolchain_info()
        print(f"  ✓ rustc: {toolchain.rustc_version}")
        print(f"  ✓ cargo: {toolchain.cargo_version}")

        # Generate passport
        print(f"\nGenerating passport: {output}")
        passport_data = generate_passport(
            git_source=git_info,
            cargo_lock_path=cargo_lock,
            cargo_lock_hash=cargo_lock_hash,
            toolchain=toolchain,
            verification_results=results,
            output_path=output,
        )

        print("\n" + "=" * 60)
        print(f"✓ Passport generated successfully: {output}")
        if git_info:
            print(f"  - Source commit: {git_info.commit_hash[:8]}...")
        print(f"  - {len(dependencies)} dependencies verified")
        print(f"  - Toolchain: {toolchain.rustc_version.split()[1]}")
        print("=" * 60)

    except typer.Exit:
        raise
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
    passport_output: Path = typer.Option(
        "passport.json",
        "--passport",
        "-p",
        help="Output path for passport JSON",
    ),
    attestation_output: Path = typer.Option(
        "attestation.json",
        "--attestation",
        "-a",
        help="Output path for attestation JSON",
    ),
    release: bool = typer.Option(
        True,
        "--release/--debug",
        help="Build in release mode (default) or debug mode",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Execute Phase 2 attestable build inside TEE environment (simulated).

    This command runs the complete attestable build flow:
    1. Generate launch measurement (hash of build runner code)
    2. Verify all Phase 1 inputs (git, cargo.lock, deps, toolchain)
    3. Execute cargo build
    4. Measure output artifacts
    5. Generate passport with verified inputs and outputs
    6. Create attestation report binding everything together
    """
    try:
        print("=" * 60)
        print("Phase 2: TEE Attestable Build")
        print("=" * 60)

        print("\nExecuting attestable build in TEE environment...")
        print(f"  Project: {project_dir}")
        print(f"  Mode: {'release' if release else 'debug'}")

        # Run the complete TEE build
        result = run_tee_build(
            project_dir=project_dir,
            passport_output=passport_output,
            attestation_output=attestation_output,
            release=release,
        )

        if not result.success:
            print(f"\n✗ Build failed: {result.error_message}", file=sys.stderr)
            raise typer.Exit(1)

        # Display results
        print("\n" + "=" * 60)
        print("Build Results")
        print("=" * 60)

        print("\n[TEE Launch Measurement]")
        print(f"  Runner hash: {result.launch_measurement.runner_hash}")
        print(f"  Modules measured: {len(result.launch_measurement.modules)}")
        if verbose:
            for module, hash_val in result.launch_measurement.modules.items():
                print(f"    - {module}: {hash_val[:16]}...")

        if result.git_source:
            print("\n[Source Code]")
            print(f"  ✓ Commit: {result.git_source.commit_hash}")
            print(f"  ✓ Tree hash: {result.git_source.tree_hash}")
            print(f"  ✓ Working tree: clean")
            if result.git_source.repository_url:
                print(f"  ✓ Repository: {result.git_source.repository_url}")

        print("\n[Dependencies]")
        verified = [r for r in result.verification_results if r.verified]
        print(f"  ✓ {len(verified)}/{len(result.verification_results)} dependencies verified")

        print("\n[Toolchain]")
        print(f"  ✓ rustc: {result.toolchain.rustc_version}")
        print(f"  ✓ cargo: {result.toolchain.cargo_version}")

        print("\n[Build Output]")
        print(f"  ✓ Build successful")
        print(f"  ✓ Artifacts: {len(result.output_artifacts)}")
        for artifact_path, artifact_hash in result.output_artifacts:
            print(f"    - {artifact_path.name}")
            print(f"      SHA256: {artifact_hash}")

        print("\n[Attestation Report]")
        print(f"  ✓ Report ID: {result.attestation.report_id}")
        print(f"  ✓ Launch measurement: {result.attestation.launch_measurement[:16]}...")
        print(f"  ✓ Passport hash: {result.attestation.passport_hash[:16]}...")
        print(f"  ✓ Nonce: {result.attestation.nonce[:16]}...")
        print(f"  ✓ Signature: {result.attestation.signature[:40]}...")

        print("\n" + "=" * 60)
        print(f"✓ Attestable build completed successfully")
        print("=" * 60)
        print(f"\nOutputs:")
        print(f"  - Passport: {passport_output}")
        print(f"  - Attestation: {attestation_output}")
        for artifact_path, _ in result.output_artifacts:
            print(f"  - Binary: {artifact_path}")

    except typer.Exit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


@app.command()
def measure(
    output: Path = typer.Option(
        "golden-measurements.json",
        "--output",
        "-o",
        help="Output path for golden measurement manifest",
    ),
):
    """Generate golden measurement manifest for build runner code.

    This creates a cryptographic hash of the build runner code itself,
    which serves as the "launch measurement" in the TEE attestation.

    Verifiers check this golden measurement against the TEE's launch
    measurement to ensure the correct trusted code is running.
    """
    try:
        print("=" * 60)
        print("Generating Golden Measurement")
        print("=" * 60)

        print("\nCalculating hash of build runner modules...")
        measurement = calculate_runner_measurement()

        print(f"\n  Modules measured:")
        for module, hash_val in measurement.modules.items():
            print(f"    - {module}")
            print(f"      {hash_val}")

        print(f"\n  Combined runner hash:")
        print(f"    {measurement.runner_hash}")

        # Save the manifest
        save_golden_measurement(measurement, output)

        print("\n" + "=" * 60)
        print(f"✓ Golden measurement saved to: {output}")
        print("=" * 60)
        print("\nThis hash should be published for verifiers to check against")
        print("TEE attestation reports to ensure trusted code is running.")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
