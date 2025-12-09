"""Nix-specific input verification."""

from pathlib import Path
from ..git import get_git_info
from ..logger import log, log_error, log_section, log_success, log_warning
from ..output import display_dependency_results
from .parser import parse_flake_lock, hash_flake_lock, extract_direct_inputs
from .verifier import verify_all
from .toolchain import get_nix_toolchain_info


def verify_git_source_strict(project_dir: Path):
    """Verify git source (reuse from main module)."""
    from ..verification import verify_git_source_strict as _verify_git
    return _verify_git(project_dir)


def verify_nix_inputs(
    project_dir: Path, verbose: bool = False
) -> tuple[dict | None, str, list[dict], dict]:
    """Verify all Nix build inputs (git, flake.lock, inputs, toolchain).

    Mirrors verify_inputs() from verification.py but for Nix.

    Returns:
        Tuple of (git_info, flake_lock_hash, verification_results, toolchain)

    Raises:
        typer.Exit: If verification fails
    """
    import typer

    flake_lock = project_dir / "flake.lock"
    if not flake_lock.exists():
        log_error(f"flake.lock not found in {project_dir}")
        raise typer.Exit(1)

    log_section("Verifying Build Inputs (Nix)")

    # Git source verification
    log("\n[1/4] Verifying git source...")
    git_info = verify_git_source_strict(project_dir)

    # flake.lock hash
    log("\n[2/4] Hashing flake.lock...")
    flake_lock_hash = hash_flake_lock(flake_lock)
    log_success(f"SHA256: {flake_lock_hash}")

    # Flake inputs verification
    log("\n[3/4] Verifying flake inputs...")
    flake_data = parse_flake_lock(flake_lock)
    inputs = extract_direct_inputs(flake_data)
    log(f"Found {len(inputs)} direct flake inputs", style="dim")

    results = verify_all(inputs)
    display_dependency_results(results, verbose=verbose)

    if any(not r["verified"] for r in results):
        log_error("Some flake inputs failed verification")
        raise typer.Exit(1)

    # Toolchain verification
    log("\n[4/4] Verifying Nix toolchain...")
    try:
        toolchain = get_nix_toolchain_info()
        log_success(f"nix: {toolchain['nix_version']}")
        log(f"  Hash: {toolchain['nix_hash'][:16]}...", style="dim")
        log(f"  Path: {toolchain['nix_path']}", style="dim")
    except Exception as e:
        log_error(f"Toolchain verification failed: {e}")
        raise typer.Exit(1)

    log("\n")
    log_success("All inputs verified successfully")

    return git_info, flake_lock_hash, results, toolchain
