"""Verify flake inputs by checking actual /nix/store contents."""

import json
import subprocess
from pathlib import Path


def _find_nix_store_path(input_data: dict) -> Path | None:
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
dd
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
        Dict with verification result:
        {
          "input": {...},
          "verified": bool,
          "message": str
        }
    """
    input_name = input_data.get("name", "unknown")
    expected_narHash = input_data.get("narHash")

    if not expected_narHash:
        return {
            "input": input_data,
            "verified": False,
            "message": "No narHash in flake.lock",
        }

    # Find store path for this input
    store_path = _find_nix_store_path(input_data)
    if not store_path:
        return {
            "input": input_data,
            "verified": False,
            "message": f"Store path not found for {input_name}",
        }

    # Verify the store path against expected narHash
    verified = _verify_store_path(store_path, expected_narHash)

    if verified:
        # Show abbreviated store path (last component)
        store_name = store_path.name if len(store_path.name) < 40 else store_path.name[:37] + "..."
        return {
            "input": input_data,
            "verified": True,
            "message": f"narHash verified: {expected_narHash[:32]}... (store: {store_name})",
        }
    else:
        return {
            "input": input_data,
            "verified": False,
            "message": f"narHash mismatch for {input_name} (store: {store_path})",
        }


def verify_all(inputs: list[dict]) -> list[dict]:
    """Verify all flake inputs.

    Similar to cargo.verify_all() but for Nix inputs.

    Args:
        inputs: List of input dicts from extract_direct_inputs()

    Returns:
        List of verification results
    """
    return [verify_flake_input(input_data) for input_data in inputs]
