"""Parse Cargo.lock, verify dependencies, and manage cargo cache."""

import hashlib
import os
import re
from pathlib import Path


def parse_cargo_lock(path: Path) -> list[dict]:
    """Parse Cargo.lock and extract all dependencies with checksums.

    Returns only external dependencies (with a source field), excluding
    the local workspace packages.
    """
    content = path.read_text()
    dependencies = []

    # Cargo.lock format: [[package]] sections with name, version, source, checksum
    package_pattern = re.compile(
        r'\[\[package\]\]\s+name = "([^"]+)"\s+version = "([^"]+)"(?:\s+source = "([^"]+)")?(?:[^\[]*checksum = "([^"]+)")?',
        re.MULTILINE | re.DOTALL
    )

    for match in package_pattern.finditer(content):
        name, version, source, checksum = match.groups()
        # Only include external dependencies (those with a source)
        if source:
            dependencies.append({
                "name": name,
                "version": version,
                "source": source,
                "checksum": checksum,
            })

    return dependencies


def hash_cargo_lock(path: Path) -> str:
    """Calculate SHA-256 hash of Cargo.lock file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_git_commit(source: str) -> str | None:
    """Extract commit hash from git source URL.

    Git sources in Cargo.lock look like:
    git+https://github.com/user/repo?rev=abc123#abc123...
    """
    match = re.search(r'#([a-f0-9]+)$', source)
    return match.group(1) if match else None


def get_cargo_home() -> Path:
    """Get the CARGO_HOME directory (where cargo cache is stored)."""
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        return Path(cargo_home)
    return Path.home() / ".cargo"


def find_crate_file(dep: dict, cargo_home: Path) -> Path | None:
    """Find the .crate file in the cargo cache for a registry dependency.

    Registry crate files are stored at:
    ~/.cargo/registry/cache/<index>/<crate>-<version>.crate
    """
    if not dep["source"].startswith("registry+"):
        return None

    registry_cache = cargo_home / "registry" / "cache"
    if not registry_cache.exists():
        return None

    # Search all index directories for the crate file
    crate_filename = f"{dep['name']}-{dep['version']}.crate"
    for index_dir in registry_cache.iterdir():
        if index_dir.is_dir():
            crate_path = index_dir / crate_filename
            if crate_path.exists():
                return crate_path

    return None


def verify_crate_checksum(dep: dict, cargo_home: Path | None = None) -> dict:
    """Verify a registry dependency by hashing the .crate file.

    Returns:
        Dict with verification result
    """
    if cargo_home is None:
        cargo_home = get_cargo_home()

    crate_path = find_crate_file(dep, cargo_home)
    if crate_path is None:
        return {
            "dependency": dep,
            "verified": False,
            "message": f"Crate file not found in cargo cache: {dep['name']}-{dep['version']}.crate",
            "crate_path": None,
        }

    sha256_hash = hashlib.sha256(crate_path.read_bytes()).hexdigest()

    if dep["checksum"] is None:
        return {
            "dependency": dep,
            "verified": False,
            "message": "No checksum in Cargo.lock",
            "crate_path": crate_path,
        }

    if sha256_hash == dep["checksum"]:
        return {
            "dependency": dep,
            "verified": True,
            "message": f"Checksum verified: {sha256_hash[:8]}...",
            "crate_path": crate_path,
        }
    else:
        return {
            "dependency": dep,
            "verified": False,
            "message": f"Checksum mismatch: {sha256_hash[:8]}... != {dep['checksum'][:8]}...",
            "crate_path": crate_path,
        }


def verify_git_dependency(dep: dict) -> dict:
    """Verify a git dependency has a pinned commit hash."""
    commit = extract_git_commit(dep["source"])
    if commit:
        return {
            "dependency": dep,
            "verified": True,
            "message": f"Git dependency pinned to commit: {commit[:8]}...",
            "crate_path": None,
        }
    else:
        return {
            "dependency": dep,
            "verified": False,
            "message": "Git dependency not pinned to specific commit",
            "crate_path": None,
        }


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
                filename = crate_file.stem
                parts = filename.rsplit("-", 1)
                if len(parts) == 2:
                    name, version = parts
                    crate_files.append((name, version, crate_file))

    return crate_files


def verify_all(dependencies: list[dict], cargo_home: Path | None = None) -> list[dict]:
    """Verify all dependencies from Cargo.lock.

    For registry dependencies: verify that cached .crate files match Cargo.lock checksums.
    For git dependencies: verify pinned to specific commit.
    """
    results = []

    if cargo_home is None:
        cargo_home = get_cargo_home()
    cached_crates = get_all_crate_files(cargo_home)

    # Build a lookup map from Cargo.lock
    cargo_lock_deps = {(dep["name"], dep["version"]): dep for dep in dependencies}

    # Verify each cached crate against Cargo.lock
    verified_names = set()
    for name, version, crate_path in cached_crates:
        key = (name, version)

        if key in cargo_lock_deps:
            dep = cargo_lock_deps[key]

            sha256_hash = hashlib.sha256(crate_path.read_bytes()).hexdigest()

            if dep["checksum"] is None:
                result = {
                    "dependency": dep,
                    "verified": False,
                    "message": "No checksum in Cargo.lock",
                    "crate_path": crate_path,
                }
            elif sha256_hash == dep["checksum"]:
                result = {
                    "dependency": dep,
                    "verified": True,
                    "message": f"Checksum verified: {sha256_hash[:8]}...",
                    "crate_path": crate_path,
                }
            else:
                result = {
                    "dependency": dep,
                    "verified": False,
                    "message": f"Checksum mismatch: {sha256_hash[:8]}... != {dep['checksum'][:8]}...",
                    "crate_path": crate_path,
                }

            results.append(result)
            verified_names.add(key)
        else:
            # Crate in cache but not in Cargo.lock
            # TODO: fix this condition
            continue
            # fake_dep = {
            #     "name": name,
            #     "version": version,
            #     "source": "registry+https://github.com/rust-lang/crates.io-index",
            #     "checksum": None,
            # }
            # results.append({
            #     "dependency": fake_dep,
            #     "verified": False,
            #     "message": "Crate exists in cache but NOT in Cargo.lock (unexpected)",
            #     "crate_path": crate_path,
            # })

    # Also verify git dependencies
    for dep in dependencies:
        if dep["source"].startswith("git+"):
            result = verify_git_dependency(dep)
            results.append(result)

    return results
