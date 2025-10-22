"""CLI interface for attestable-builds."""

import sys
from pathlib import Path

import typer

from .build import run_cargo_build
from .cargo import hash_cargo_lock, parse_cargo_lock, verify_all
from .git import get_git_info
from .passport import generate_passport, verify_passport
from .toolchain import get_toolchain_info

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
):
    """Build project with full input verification and output measurement.

    This command:
    1. Verifies all Phase 1 inputs (git, Cargo.lock, deps, toolchain)
    2. Executes cargo build
    3. Measures output artifacts
    4. Generates passport with inputs and outputs
    """
    try:
        cargo_lock = project_dir / "Cargo.lock"
        if not cargo_lock.exists():
            print(f"Error: Cargo.lock not found in {project_dir}", file=sys.stderr)
            raise typer.Exit(1)

        # Phase 1: Input Verification
        print("=" * 60)
        print("Phase 1: Input Verification")
        print("=" * 60)
        print("\n[1/4] Verifying git source...")
        git_info = verify_git_source_strict(project_dir)

        print("\n[2/4] Hashing Cargo.lock...")
        cargo_lock_hash = hash_cargo_lock(cargo_lock)
        print(f"  ✓ SHA256: {cargo_lock_hash}")

        print("\n[3/4] Verifying dependencies...")
        dependencies = parse_cargo_lock(cargo_lock)
        print(f"  Found {len(dependencies)} external dependencies")
        results = verify_all(dependencies)
        print_verification_results(results, verbose)

        if any(not r["verified"] for r in results):
            print("\n✗ Some dependencies failed verification", file=sys.stderr)
            raise typer.Exit(1)

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
        print("✓ All Phase 1 inputs verified successfully")
        print("=" * 60)

        # Phase 2: Build Execution
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
        print(f"  - {len(dependencies)} dependencies verified")
        print(f"  - Toolchain: {toolchain['rustc_version'].split()[1]}")
        print(f"  - {len(output_artifacts)} artifact(s) measured")
        print("=" * 60)

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
        passed_checks = []
        failed_checks = []
        skipped_checks = []

        for check_name, check_result in results["checks"].items():
            if check_result["passed"]:
                passed_checks.append((check_name, check_result["message"]))
            else:
                # Check if it's a skip (not a failure)
                if "skipped" in check_result["message"].lower() or "no " in check_result["message"].lower():
                    skipped_checks.append((check_name, check_result["message"]))
                else:
                    failed_checks.append((check_name, check_result["message"]))

        # Show passed checks
        if passed_checks:
            print("PASSED:")
            for name, message in passed_checks:
                print(f"  ✓ {name.replace('_', ' ').title()}")
                print(f"    {message}")

        # Show failed checks
        if failed_checks:
            print("\nFAILED:")
            for name, message in failed_checks:
                print(f"  ✗ {name.replace('_', ' ').title()}")
                print(f"    {message}")

        # Show skipped checks
        if skipped_checks:
            print("\nSKIPPED:")
            for name, message in skipped_checks:
                print(f"  ⊘ {name.replace('_', ' ').title()}")
                print(f"    {message}")

        print(f"\n{'=' * 60}")
        if results["valid"]:
            print("✓ Passport verification PASSED")
        else:
            print("✗ Passport verification FAILED")
        print("=" * 60)

        if not results["valid"]:
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)






def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
