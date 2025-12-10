"""Nix-specific input verification."""

import json
import subprocess
from pathlib import Path
from typing import Optional
from ..git import get_git_info
from ..logger import log, log_error, log_section, log_success, log_warning
from ..output import display_dependency_results
from ..verification import verify_git_source_strict
from .parser import parse_flake_lock, hash_flake_lock, extract_direct_inputs
from .toolchain import get_nix_toolchain_info


def _find_nix_store_path(input_data: dict) -> Optional[Path]:
    """Find /nix/store path for a flake input.

    Uses nix flake metadata to resolve the store path for the input.

    Args:
        input_data: Input dictionary from extract_direct_inputs()

    Returns:
        Path to /nix/store/... or None if not found
    """
    input_type = input_data.get("type")

    # Build flake ref based on type
    if input_type == "github":
        owner = input_data.get("owner")
        repo = input_data.get("repo")
        rev = input_data.get("rev")
        if not all([owner, repo, rev]):
            return None
        flake_ref = f"github:{owner}/{repo}/{rev}"
    elif input_type == "git":
        url = input_data.get("url")
        rev = input_data.get("rev")
        if not all([url, rev]):
            return None
        flake_ref = f"{url}?rev={rev}"
    elif input_type == "path":
        path = input_data.get("path")
        return Path(path) if path else None
    else:
        return None

    try:
        # Run nix flake metadata --json to get store path
        result = subprocess.run(
            ["nix", "flake", "metadata", "--json", flake_ref],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        metadata = json.loads(result.stdout)
        store_path = metadata.get("path")
        return Path(store_path) if store_path else None
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, subprocess.TimeoutExpired):
        return None


def _verify_store_path(store_path: Path, expected_narHash: str) -> bool:
    """Verify a Nix store path against expected narHash.

    Recalculates the narHash of the store path using 'nix hash path'
    and compares it to the expected value from flake.lock.

    This mirrors Cargo's approach of hashing actual .crate files.

    Args:
        store_path: Path to /nix/store/...
        expected_narHash: Expected narHash from flake.lock (e.g., "sha256-...")

    Returns:
        True if narHash matches, False otherwise
    """
    if not store_path.exists():
        return False

    try:
        result = subprocess.run(
            ["nix", "hash", "path", str(store_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        actual_narHash = result.stdout.strip()
        return actual_narHash == expected_narHash
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def verify_flake_input(input_data: dict) -> dict:
    """Verify a flake input by checking /nix/store contents.

    Similar to Cargo's verify_crate_checksum(), this function:
    1. Finds the /nix/store path for the input
    2. Recalculates the narHash using 'nix hash path'
    3. Compares against the narHash in flake.lock

    This ensures we verify actual stored dependencies, not just trust the lock file.

    Args:
        input_data: Input dictionary from extract_direct_inputs()

    Returns:
        Dict with verification result (standardized format matching Cargo):
        {
          "dependency": {"name": str, "version": str (optional), ...metadata},
          "verified": bool,
          "message": str
        }
    """
    input_name = input_data.get("name", "unknown")
    expected_narHash = input_data.get("narHash")

    # Standardized dependency format (matching Cargo output format)
    # Include all input metadata in the dependency object
    dependency = {
        "name": input_name,
        "narHash": expected_narHash,
        "type": input_data.get("type"),
    }

    if not expected_narHash:
        return {
            "dependency": dependency,
            "verified": False,
            "message": "No narHash in flake.lock",
        }

    # Find store path for this input
    store_path = _find_nix_store_path(input_data)
    if not store_path:
        return {
            "dependency": dependency,
            "verified": False,
            "message": f"Store path not found for {input_name}",
        }

    # Verify the store path against expected narHash
    verified = _verify_store_path(store_path, expected_narHash)

    if verified:
        # Show abbreviated store path (last component)
        store_name = store_path.name if len(store_path.name) < 40 else store_path.name[:37] + "..."
        return {
            "dependency": dependency,
            "verified": True,
            "message": f"narHash verified: {expected_narHash[:32]}... (store: {store_name})",
        }
    else:
        return {
            "dependency": dependency,
            "verified": False,
            "message": f"narHash mismatch for {input_name} (store: {store_path})",
        }


def verify_flake_inputs(inputs: list[dict]) -> list[dict]:
    """Verify all flake inputs.

    Args:
        inputs: List of input dicts from extract_direct_inputs()

    Returns:
        List of verification results
    """
    return [verify_flake_input(input_data) for input_data in inputs]


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

    results = verify_flake_inputs(inputs)
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
