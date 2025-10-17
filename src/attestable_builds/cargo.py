"""Parse Cargo.lock and extract dependency information."""

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
    """Parse Cargo.lock and extract all dependencies with checksums."""
    content = path.read_text()
    dependencies = []

    # Cargo.lock format: [[package]] sections with name, version, source, checksum
    package_pattern = re.compile(
        r'\[\[package\]\]\s+name = "([^"]+)"\s+version = "([^"]+)"(?:\s+source = "([^"]+)")?(?:\s+checksum = "([^"]+)")?',
        re.MULTILINE
    )

    for match in package_pattern.finditer(content):
        name, version, source, checksum = match.groups()
        # Skip local packages (no source)
        if source:
            dependencies.append(Dependency(
                name=name,
                version=version,
                source=source or "",
                checksum=checksum
            ))

    return dependencies


def extract_git_commit(source: str) -> str | None:
    """Extract commit hash from git source URL."""
    # Format: git+https://github.com/user/repo?rev=HASH#HASH
    match = re.search(r'#([a-f0-9]+)$', source)
    return match.group(1) if match else None
