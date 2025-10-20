"""Parse Cargo.lock and extract dependency information."""

import hashlib
import re
from pathlib import Path
from typing import NamedTuple


class Dependency(NamedTuple):
    """A locked dependency from Cargo.lock."""
    name: str
    version: str
    source: str  # "registry+https://...", "git+https://...", etc.
    checksum: str | None


def parse_cargo_lock(path: Path) -> list[Dependency]:
    """Parse Cargo.lock and extract all dependencies with checksums.

    Returns only external dependencies (with a source field), excluding
    the local workspace packages.
    """
    content = path.read_text()
    dependencies = []

    # Cargo.lock format: [[package]] sections with name, version, source, checksum
    # Note: This regex needs to handle multi-line matching for complete package blocks
    package_pattern = re.compile(
        r'\[\[package\]\]\s+name = "([^"]+)"\s+version = "([^"]+)"(?:\s+source = "([^"]+)")?(?:[^\[]*checksum = "([^"]+)")?',
        re.MULTILINE | re.DOTALL
    )

    for match in package_pattern.finditer(content):
        name, version, source, checksum = match.groups()
        # Only include external dependencies (those with a source)
        if source:
            dependencies.append(Dependency(
                name=name,
                version=version,
                source=source,
                checksum=checksum
            ))

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
