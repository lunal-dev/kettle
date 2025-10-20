"""Verify dependencies by checking actual .crate files in cargo cache."""

import hashlib
import os
from pathlib import Path
from typing import NamedTuple

from .cargo import Dependency, extract_git_commit


class VerificationResult(NamedTuple):
    """Result of verifying a dependency."""
    dependency: Dependency
    verified: bool
    message: str
    crate_path: Path | None = None


def get_cargo_home() -> Path:
    """Get the CARGO_HOME directory (where cargo cache is stored)."""
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        return Path(cargo_home)
    return Path.home() / ".cargo"


def find_crate_file(dep: Dependency, cargo_home: Path) -> Path | None:
    """Find the .crate file in the cargo cache for a registry dependency.

    Registry crate files are stored at:
    ~/.cargo/registry/cache/<index>/<crate>-<version>.crate

    The index is typically "index.crates.io-<hash>" but can vary.
    """
    if not dep.source.startswith("registry+"):
        return None

    registry_cache = cargo_home / "registry" / "cache"
    if not registry_cache.exists():
        return None

    # Search all index directories for the crate file
    crate_filename = f"{dep.name}-{dep.version}.crate"
    for index_dir in registry_cache.iterdir():
        if index_dir.is_dir():
            crate_path = index_dir / crate_filename
            if crate_path.exists():
                return crate_path

    return None


def verify_crate_checksum(dep: Dependency, cargo_home: Path | None = None) -> VerificationResult:
    """Verify a registry dependency by hashing the .crate file and comparing to Cargo.lock.

    Args:
        dep: Dependency from Cargo.lock
        cargo_home: Optional custom CARGO_HOME path

    Returns:
        VerificationResult indicating if the crate file hash matches Cargo.lock
    """
    if cargo_home is None:
        cargo_home = get_cargo_home()

    # Find the .crate file
    crate_path = find_crate_file(dep, cargo_home)
    if crate_path is None:
        return VerificationResult(
            dep,
            False,
            f"Crate file not found in cargo cache: {dep.name}-{dep.version}.crate",
            None
        )

    # Hash the .crate file
    sha256_hash = hashlib.sha256(crate_path.read_bytes()).hexdigest()

    # Compare to Cargo.lock checksum
    if dep.checksum is None:
        return VerificationResult(
            dep,
            False,
            "No checksum in Cargo.lock",
            crate_path
        )

    if sha256_hash == dep.checksum:
        return VerificationResult(
            dep,
            True,
            f"Checksum verified: {sha256_hash[:8]}...",
            crate_path
        )
    else:
        return VerificationResult(
            dep,
            False,
            f"Checksum mismatch: {sha256_hash[:8]}... != {dep.checksum[:8]}...",
            crate_path
        )


def verify_git_dependency(dep: Dependency) -> VerificationResult:
    """Verify a git dependency has a pinned commit hash.

    For git dependencies, we verify that the source includes a specific
    commit hash (not a branch or tag that could change).
    """
    commit = extract_git_commit(dep.source)
    if commit:
        return VerificationResult(
            dep,
            True,
            f"Git dependency pinned to commit: {commit[:8]}...",
            None
        )
    else:
        return VerificationResult(
            dep,
            False,
            "Git dependency not pinned to specific commit",
            None
        )


def get_all_crate_files(cargo_home: Path | None = None) -> list[tuple[str, str, Path]]:
    """Get all .crate files in the cargo cache.

    Returns:
        List of tuples: (name, version, crate_path)
    """
    if cargo_home is None:
        cargo_home = get_cargo_home()

    registry_cache = cargo_home / "registry" / "cache"
    if not registry_cache.exists():
        return []

    crate_files = []
    for index_dir in registry_cache.iterdir():
        if index_dir.is_dir():
            for crate_file in index_dir.glob("*.crate"):
                # Parse filename: name-version.crate
                filename = crate_file.stem  # removes .crate extension
                # Split on last dash to handle crate names with dashes
                parts = filename.rsplit("-", 1)
                if len(parts) == 2:
                    name, version = parts
                    crate_files.append((name, version, crate_file))

    return crate_files


def verify_all(dependencies: list[Dependency], cargo_home: Path | None = None) -> list[VerificationResult]:
    """Verify all dependencies from Cargo.lock.

    Strategy: For registry dependencies, we verify that IF a .crate file exists
    in the cache, it matches the Cargo.lock checksum. We don't require all
    Cargo.lock dependencies to be present (platform-specific deps may be missing).

    Registry dependencies: If .crate exists in cache, verify hash matches Cargo.lock
    Git dependencies: Verify pinned to specific commit
    Other sources: Mark as unsupported
    """
    results = []

    # Get all .crate files in cache
    if cargo_home is None:
        cargo_home = get_cargo_home()
    cached_crates = get_all_crate_files(cargo_home)

    # Build a lookup map from Cargo.lock for quick access
    cargo_lock_deps = {(dep.name, dep.version): dep for dep in dependencies}

    # Verify each cached crate against Cargo.lock
    verified_names = set()
    for name, version, crate_path in cached_crates:
        key = (name, version)

        if key in cargo_lock_deps:
            dep = cargo_lock_deps[key]

            # Hash the .crate file
            sha256_hash = hashlib.sha256(crate_path.read_bytes()).hexdigest()

            # Compare to Cargo.lock checksum
            if dep.checksum is None:
                result = VerificationResult(
                    dep,
                    False,
                    "No checksum in Cargo.lock",
                    crate_path
                )
            elif sha256_hash == dep.checksum:
                result = VerificationResult(
                    dep,
                    True,
                    f"Checksum verified: {sha256_hash[:8]}...",
                    crate_path
                )
            else:
                result = VerificationResult(
                    dep,
                    False,
                    f"Checksum mismatch: {sha256_hash[:8]}... != {dep.checksum[:8]}...",
                    crate_path
                )

            results.append(result)
            verified_names.add(key)
        else:
            # Crate in cache but not in Cargo.lock - this is suspicious!
            # Create a fake Dependency for reporting
            fake_dep = Dependency(
                name=name,
                version=version,
                source="registry+https://github.com/rust-lang/crates.io-index",
                checksum=None
            )
            results.append(VerificationResult(
                fake_dep,
                False,
                f"Crate exists in cache but NOT in Cargo.lock (unexpected)",
                crate_path
            ))

    # Also verify git dependencies (they don't have .crate files)
    for dep in dependencies:
        if dep.source.startswith("git+"):
            result = verify_git_dependency(dep)
            results.append(result)

    return results
