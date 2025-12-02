"""Nix-specific passport generation."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..merkle import MerkleTree
from .parser import parse_flake_lock, hash_flake_lock, extract_direct_inputs
from .verifier import verify_all
from .toolchain import get_nix_toolchain_info
from .build import run_nix_build


def generate_nix_passport(
    project_dir: Path,
    output_path: Path,
    git_source: Optional[dict] = None,
    verbose: bool = False,
) -> dict:
    """Generate passport for Nix build.

    This mirrors generate_passport() but for Nix flakes.

    Args:
        project_dir: Path to project directory containing flake.nix/flake.lock
        output_path: Path where passport.json will be written
        git_source: Optional git source info from verify_git_source_strict()
        verbose: Enable verbose output

    Returns:
        Dict containing the generated passport data

    Raises:
        RuntimeError: If Nix build fails
        FileNotFoundError: If flake.lock not found
    """

    # 1. Parse flake.lock
    flake_lock_path = project_dir / "flake.lock"
    flake_lock_hash = hash_flake_lock(flake_lock_path)
    flake_data = parse_flake_lock(flake_lock_path)
    inputs = extract_direct_inputs(flake_data)

    if verbose:
        print(f"Parsed {len(inputs)} direct flake inputs")

    # 2. Verify inputs
    verification_results = verify_all(inputs)
    verified_count = sum(1 for v in verification_results if v["verified"])

    if verbose:
        print(f"Verified {verified_count}/{len(inputs)} inputs")

    # 3. Get toolchain info
    toolchain = get_nix_toolchain_info()

    if verbose:
        print(f"Nix version: {toolchain['nix_version']}")

    # 4. Execute build
    build_result = run_nix_build(project_dir)

    if not build_result["success"]:
        raise RuntimeError(f"Nix build failed:\n{build_result['stderr']}")

    if verbose:
        print(f"Built {len(build_result['artifacts'])} artifacts")

    # 5. Calculate input merkle root
    merkle_tree = MerkleTree()

    # Add git info if present
    if git_source:
        merkle_tree.append_entry(git_source["commit_hash"].encode())
        merkle_tree.append_entry(git_source["tree_hash"].encode())
        merkle_tree.append_entry(git_source["git_binary_hash"].encode())

    # Add flake.lock hash
    merkle_tree.append_entry(flake_lock_hash.encode())

    # Add input narHashes (sorted by name for determinism)
    for input_data in sorted(inputs, key=lambda x: x["name"]):
        if input_data.get("narHash"):
            merkle_tree.append_entry(input_data["narHash"].encode())

    # Add nix toolchain
    merkle_tree.append_entry(toolchain["nix_hash"].encode())
    merkle_tree.append_entry(toolchain["nix_version"].encode())

    input_merkle_root = merkle_tree.get_root()

    # 6. Build passport structure
    passport = {
        "version": "1.0",
        "build_system": "nix",
        "inputs": {
            "flake_lock_hash": flake_lock_hash,
            "toolchain": {
                "nix": {
                    "binary_hash": toolchain["nix_hash"],
                    "version": toolchain["nix_version"],
                    "path": str(toolchain["nix_path"]),
                }
            },
            "flake_inputs": [
                {
                    "name": v["input"]["name"],
                    "narHash": v["input"].get("narHash"),
                    "type": v["input"].get("type"),
                    "verified": v["verified"],
                }
                for v in verification_results
            ],
            "input_merkle_root": input_merkle_root,
        },
        "build_process": {
            "command": "nix build --no-link --print-out-paths",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "outputs": {
            "artifacts": build_result["artifacts"],
            "store_paths": build_result["store_paths"],
        },
    }

    # Add git source if present
    if git_source:
        passport["inputs"]["source"] = git_source

    # Write to disk
    output_path.write_text(json.dumps(passport, indent=2))

    if verbose:
        print(f"Passport written to {output_path}")

    return passport
