"""Verification logic for build inputs and outputs."""

from pathlib import Path

from kettle.attestation import verify_attestation
from kettle.cargo import hash_cargo_lock, parse_cargo_lock, verify_all
from kettle.git import get_git_info
from kettle.logger import log, log_error, log_section, log_success, log_warning
from kettle.output import display_dependency_results, display_verification_checks
from kettle.passport import verify_build_passport
from kettle.toolchain import get_toolchain_info


def verify_git_source_strict(project_dir: Path) -> tuple:
    """Verify git source with strict mode (fail on dirty working tree).

    Returns:
        Tuple of (git_info, should_exit) where should_exit indicates if we should exit

    Raises:
        typer.Exit: If working tree has uncommitted changes
    """
    import typer

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


def verify_inputs(
    project_dir: Path, verbose: bool = False
) -> tuple[dict | None, str, list[dict], dict]:
    """Verify all build inputs (git, Cargo.lock, dependencies, toolchain).

    Returns:
        Tuple of (git_info, cargo_lock_hash, verification_results, toolchain)

    Raises:
        typer.Exit: If verification fails
    """
    import typer

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
    display_dependency_results(results, verbose=verbose)

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


def run_verify_passport_workflow(
    passport: Path,
    manifest: Path,
    project_dir: Path,
    binary: Path,
    strict: bool,
) -> None:
    """Complete passport verification workflow with display.

    Args:
        passport: Path to passport JSON file
        manifest: Path to verification manifest JSON file (optional)
        project_dir: Path to project directory (optional)
        binary: Path to binary artifact to verify (optional)
        strict: Fail if any optional checks cannot be performed

    Raises:
        typer.Exit: If verification fails
    """
    import typer

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
        all_passed = display_verification_checks(
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


def run_verify_attestation_workflow(
    attestation: Path,
    passport: Path,
) -> None:
    """Complete attestation verification workflow with display.

    Args:
        attestation: Path to attestation file (evidence.b64)
        passport: Path to passport JSON file

    Raises:
        typer.Exit: If verification fails
    """
    import typer

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
        all_passed = display_verification_checks(
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


def run_combined_verify_workflow(
    build_dir: Path,
    project_dir: Path,
    binary: Path,
    strict: bool,
) -> None:
    """Complete combined attestation + passport verification workflow.

    Args:
        build_dir: Path to build directory containing passport.json and evidence.b64
        project_dir: Path to project directory containing verification manifest.json (optional)
        binary: Path to binary artifact to verify (optional)
        strict: Fail if any optional checks cannot be performed

    Raises:
        typer.Exit: If verification fails
    """
    import typer

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
            attestation_passed = display_verification_checks(
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
        passport_passed = display_verification_checks(
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
