"""Generate passport document for attestable builds (Phase 1 inputs)."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .git import GitSource
from .toolchain import ToolchainInfo
from .verify import VerificationResult


def generate_passport(
    git_source: GitSource | None,
    cargo_lock_path: Path,
    cargo_lock_hash: str,
    toolchain: ToolchainInfo,
    verification_results: list[VerificationResult],
    output_artifacts: list[tuple[Path, str]] | None = None,
    output_path: Path | None = None,
) -> dict:
    """Generate a passport document according to Phase 1 specification.

    Args:
        git_source: Source code git information (optional)
        cargo_lock_path: Path to Cargo.lock file
        cargo_lock_hash: SHA256 hash of Cargo.lock
        toolchain: Rust toolchain information
        verification_results: Results from dependency verification
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
                    "binary_hash": toolchain.rustc_hash,
                    "version": toolchain.rustc_version,
                },
                "cargo": {
                    "binary_hash": toolchain.cargo_hash,
                    "version": toolchain.cargo_version,
                },
            },
            "dependencies": [
                {
                    "name": r.dependency.name,
                    "version": r.dependency.version,
                    "source": r.dependency.source,
                    "checksum": r.dependency.checksum,
                    "verified": r.verified,
                }
                for r in verification_results
            ],
        },
        "build_process": {
            "command": "cargo build --release",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

    # Add git source if available
    if git_source:
        passport["inputs"]["source"] = {
            "type": "git",
            "commit_hash": git_source.commit_hash,
            "tree_hash": git_source.tree_hash,
            "git_version": git_source.git_version,
            "git_binary_hash": git_source.git_binary_hash,
        }
        if git_source.repository_url:
            passport["inputs"]["source"]["repository"] = git_source.repository_url

    # Add outputs if provided
    if output_artifacts:
        passport["outputs"] = {
            "binary": [
                {
                    "path": str(path),
                    "hash": hash_value,
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
