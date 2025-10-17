"""Verify dependencies against crates.io registry and git sources."""

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

import httpx

from .cargo import Dependency, extract_git_commit


class VerificationResult(NamedTuple):
    """Result of verifying a dependency."""
    dependency: Dependency
    verified: bool
    message: str


async def verify_registry_checksum(client: httpx.AsyncClient, dep: Dependency) -> VerificationResult:
    """Verify a registry dependency checksum against crates.io."""
    try:
        # Fetch crate metadata from crates.io API
        url = f"https://crates.io/api/v1/crates/{dep.name}/{dep.version}"
        response = await client.get(url)
        response.raise_for_status()

        data = response.json()
        expected_checksum = data["version"]["checksum"]

        if dep.checksum == expected_checksum:
            return VerificationResult(dep, True, "Checksum matches registry")
        else:
            return VerificationResult(
                dep, False, f"Checksum mismatch: {dep.checksum} != {expected_checksum}"
            )

    except httpx.HTTPError as e:
        return VerificationResult(dep, False, f"Registry error: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        return VerificationResult(dep, False, f"Parse error: {e}")


def hash_cargo_lock(path: Path) -> str:
    """Calculate SHA-256 hash of Cargo.lock file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def verify_all(dependencies: list[Dependency]) -> list[VerificationResult]:
    """Verify all dependencies."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        results = []
        for dep in dependencies:
            # Verify based on source type
            if dep.source.startswith("registry+"):
                result = await verify_registry_checksum(client, dep)
            elif dep.source.startswith("git+"):
                commit = extract_git_commit(dep.source)
                if commit:
                    result = VerificationResult(dep, True, f"Pinned to commit {commit[:8]}")
                else:
                    result = VerificationResult(dep, False, "Git dependency not pinned to commit")
            else:
                result = VerificationResult(dep, False, f"Unsupported source: {dep.source}")

            results.append(result)
        return results
