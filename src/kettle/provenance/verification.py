"""Verification logic for build inputs and outputs."""

from pathlib import Path

from kettle.core import Toolchain
from kettle.attestation import verify_attestation
from kettle.git import get_git_info
from kettle.logger import log, log_error, log_section, log_success, log_warning
from kettle.output import display_dependency_results, display_verification_checks
from .generate import verify as verify_provenance


def verify_git_source(project_dir: Path, strict: bool = True) -> dict | None:
    """Verify git source.

    Args:
        project_dir: Path to project directory
        strict: Fail on dirty working tree

    Returns:
        Git info dict or None

    Raises:
        typer.Exit: If strict and working tree is dirty
    """
    import typer

    git_info = get_git_info(project_dir)
    if not git_info:
        if strict:
            log_warning("Not a git repository")
        return None

    if strict and not git_info["is_clean"]:
        log_error("Working tree has uncommitted changes")
        log("\nUncommitted files:")
        for file in git_info["dirty_files"]:
            log(f"  - {file}")
        log("\nError: Builds require a clean git working tree.")
        log("Commit or stash your changes before building.")
        raise typer.Exit(1)

    log_success(f"Commit: {git_info['commit_hash']}")
    log_success(f"Tree hash: {git_info['tree_hash']}")
    if git_info.get("git_binary_hash"):
        log(f"  Git binary hash: {git_info['git_binary_hash'][:16]}...", style="dim")
    if git_info.get("repository_url"):
        log_success(f"Repository: {git_info['repository_url']}")

    return git_info


def verify_inputs(
    toolchain: Toolchain,
    project_dir: Path,
    verbose: bool = False,
    strict: bool = True,
) -> tuple[dict | None, dict, list[dict], dict]:
    """Verify all build inputs (git, lockfile, dependencies, toolchain).

    Args:
        toolchain: Toolchain instance
        project_dir: Path to project directory
        verbose: Show verbose output
        strict: Fail on dirty git tree

    Returns:
        Tuple of (git_info, lock, dep_results, toolchain_info)

    Raises:
        typer.Exit: If verification fails
    """
    import typer

    log_section("Verifying Build Inputs")

    # [1/4] Git source
    log("\n[1/4] Verifying git source...")
    git_info = verify_git_source(project_dir, strict=strict)

    # [2/4] Lockfile
    log("\n[2/4] Hashing lockfile...")
    try:
        lock = toolchain.parse_lockfile(project_dir)
        log_success(f"SHA256: {lock['hash']}")
        log(f"  {len(lock['deps'])} dependencies", style="dim")
    except Exception as e:
        log_error(f"Failed to parse lockfile: {e}")
        raise typer.Exit(1)

    # [3/4] Dependencies
    log("\n[3/4] Verifying dependencies...")
    results = toolchain.verify_deps(lock["deps"])
    display_dependency_results(results, verbose=verbose)

    failed = [r for r in results if not r.get("verified")]
    if failed:
        log_error(f"{len(failed)} dependencies failed verification")
        raise typer.Exit(1)

    # [4/4] Toolchain
    log(f"\n[4/4] Verifying {toolchain.name} toolchain...")
    try:
        info = toolchain.get_info()
        for key, val in info.items():
            if isinstance(val, dict) and "version" in val:
                log_success(f"{key}: {val['version']}")
                if val.get("hash"):
                    log(f"  Hash: {val['hash'][:16]}...", style="dim")
            elif isinstance(val, str):
                log_success(f"{key}: {val}")
    except Exception as e:
        log_error(f"Toolchain verification failed: {e}")
        raise typer.Exit(1)

    log("\n")
    log_success("All inputs verified successfully")

    return git_info, lock, results, info


def run_verify_passport_workflow(
    passport: Path,
    project_dir: Path | None = None,
    binary: Path | None = None,
    strict: bool = False,
) -> None:
    """Provenance verification workflow.

    Args:
        passport: Path to provenance JSON file
        project_dir: Optional project directory to verify against
        binary: Optional binary to verify hash
        strict: Fail if optional checks cannot be performed

    Raises:
        typer.Exit: If verification fails
    """
    import typer

    try:
        log_section("Provenance Verification")
        log(f"Provenance: {passport}", style="dim")

        results = verify_provenance(
            provenance_path=passport,
            project_dir=project_dir,
            binary_path=binary,
            strict=strict,
        )

        # Show provenance metadata
        if results["provenance"]:
            metadata = results["provenance"].get("predicate", {}).get("runDetails", {}).get("metadata", {})
            if metadata.get("invocationId"):
                log(f"\nBuild ID: {metadata['invocationId']}", style="dim")
            if metadata.get("startedOn"):
                log(f"Built: {metadata['startedOn']}", style="dim")

        # Display results
        all_passed = display_verification_checks(
            checks=results["checks"],
            title="Verification Results",
            success_message="Provenance verification PASSED",
            failure_message="Provenance verification FAILED",
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
    """Attestation verification workflow.

    Args:
        attestation: Path to attestation file (evidence.b64)
        passport: Path to provenance JSON file

    Raises:
        typer.Exit: If verification fails
    """
    import typer

    try:
        log_section("Attestation Verification")
        log(f"Attestation: {attestation}", style="dim")
        log(f"Provenance: {passport}", style="dim")

        results = verify_attestation(
            attestation_path=attestation,
            provenance_path=passport,
        )

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
    project_dir: Path | None = None,
    binary: Path | None = None,
    strict: bool = False,
) -> None:
    """Combined attestation + provenance verification workflow.

    Args:
        build_dir: Directory containing provenance.json and optionally evidence.b64
        project_dir: Optional project directory to verify against
        binary: Optional binary to verify hash
        strict: Fail if optional checks cannot be performed

    Raises:
        typer.Exit: If verification fails
    """
    import typer

    try:
        provenance_path = build_dir / "provenance.json"
        attestation_path = build_dir / "evidence.b64"

        if not provenance_path.exists():
            log_error(f"provenance.json not found in {build_dir}")
            raise typer.Exit(1)

        log(f"Build directory: {build_dir}", style="dim")
        has_attestation = attestation_path.exists()

        attestation_passed = True
        provenance_passed = True

        # Phase 1: Attestation (if available)
        if has_attestation:
            log_section("Phase 1: Attestation Verification")

            attestation_results = verify_attestation(
                attestation_path=attestation_path,
                provenance_path=provenance_path,
            )

            attestation_passed = display_verification_checks(
                checks=attestation_results["checks"],
                title="Attestation Results",
                success_message="Attestation PASSED",
                failure_message="Attestation FAILED",
            )

            if not attestation_passed and strict:
                log_error("Stopping: attestation failed (strict mode)")
                raise typer.Exit(1)

        # Phase 2: Provenance
        phase_title = "Phase 2: Provenance Verification" if has_attestation else "Provenance Verification"
        log_section(phase_title)

        provenance_results = verify_provenance(
            provenance_path=provenance_path,
            project_dir=project_dir,
            binary_path=binary,
            strict=strict,
        )

        # Show metadata
        if provenance_results["provenance"]:
            metadata = provenance_results["provenance"].get("predicate", {}).get("runDetails", {}).get("metadata", {})
            if metadata.get("invocationId"):
                log(f"\nBuild ID: {metadata['invocationId']}", style="dim")

        provenance_passed = display_verification_checks(
            checks=provenance_results["checks"],
            title="Provenance Results",
            success_message="Provenance PASSED",
            failure_message="Provenance FAILED",
        )

        # Overall result
        overall_passed = attestation_passed and provenance_passed

        if has_attestation:
            log("\n")
            log_section("Overall Results")
            if overall_passed:
                log_success("VERIFICATION PASSED")
            else:
                log_error("VERIFICATION FAILED")

        if not overall_passed:
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)
