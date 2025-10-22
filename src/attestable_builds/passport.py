"""Generate passport document for attestable builds (Phase 1 inputs)."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .merkle import calculate_input_merkle_root


def generate_passport(
    git_source: Optional[dict],
    cargo_lock_hash: str,
    toolchain: dict,
    verification_results: Optional[list[dict]] = None,
    output_artifacts: Optional[list[tuple[Path, str]]] = None,
    output_path: Optional[Path] = None,
) -> dict:
    """Generate a passport document according to Phase 1 specification.

    Args:
        git_source: Source code git information (optional)
        cargo_lock_path: Path to Cargo.lock file
        cargo_lock_hash: SHA256 hash of Cargo.lock
        toolchain: Rust toolchain information
        verification_results: Optional results from dependency verification
        output_artifacts: Optional list of (path, hash) tuples for build outputs
        output_path: Optional path to write passport JSON

    Returns:
        Passport dictionary matching the design spec
    """
    passport = {
        "version": "1.0",
        "inputs": {
            "cargo_lock_hash": cargo_lock_hash,
            "toolchain": {
                "rustc": {
                    "binary_hash": toolchain["rustc_hash"],
                    "version": toolchain["rustc_version"],
                },
                "cargo": {
                    "binary_hash": toolchain["cargo_hash"],
                    "version": toolchain["cargo_version"],
                },
            },
        },
        "build_process": {
            "command": "cargo build --locked --release",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

    # Add dependencies if provided
    if verification_results:
        passport["inputs"]["dependencies"] = [
            {
                "name": r["dependency"]["name"],
                "version": r["dependency"]["version"],
                "source": r["dependency"]["source"],
                "checksum": r["dependency"]["checksum"],
                "verified": r["verified"],
            }
            for r in verification_results
        ]

    # Calculate input merkle root
    input_merkle_root = calculate_input_merkle_root(
        git_commit_hash=git_source["commit_hash"] if git_source else None,
        git_tree_hash=git_source["tree_hash"] if git_source else None,
        git_binary_hash=git_source["git_binary_hash"] if git_source else None,
        cargo_lock_hash=cargo_lock_hash,
        dependencies=[
            {
                "name": r["dependency"]["name"],
                "version": r["dependency"]["version"],
                "checksum": r["dependency"]["checksum"],
            }
            for r in verification_results
        ] if verification_results else [],
        toolchain={
            "rustc": {
                "binary_hash": toolchain["rustc_hash"],
                "version": toolchain["rustc_version"],
            },
            "cargo": {
                "binary_hash": toolchain["cargo_hash"],
                "version": toolchain["cargo_version"],
            },
        },
    )
    passport["inputs"]["input_merkle_root"] = input_merkle_root

    # Add git source if available
    if git_source:
        passport["inputs"]["source"] = {
            "type": "git",
            "commit_hash": git_source["commit_hash"],
            "tree_hash": git_source["tree_hash"],
            "git_binary_hash": git_source["git_binary_hash"],
        }
        if git_source.get("repository_url"):
            passport["inputs"]["source"]["repository"] = git_source["repository_url"]

    # Add outputs if provided
    if output_artifacts:
        passport["outputs"] = {
            "artifacts": [
                {
                    "path": str(path),
                    "hash": hash_value,
                    "name": Path(path).name,
                }
                for path, hash_value in output_artifacts
            ]
        }

    # Write to file if requested
    if output_path:
        output_path.write_text(json.dumps(passport, indent=2))

    return passport


def hash_binary(binary_path: Path) -> str:
    """Calculate SHA256 hash of a binary file."""
    return hashlib.sha256(binary_path.read_bytes()).hexdigest()
