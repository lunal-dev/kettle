"""Extract git source information for build provenance."""

import subprocess
from pathlib import Path
from typing import NamedTuple


class GitSource(NamedTuple):
    """Git source information."""
    commit_hash: str
    repository_url: str | None


def get_git_info(repo_path: Path) -> GitSource | None:
    """Extract git commit hash and repository URL from a git repository.

    Args:
        repo_path: Path to the git repository (project root)

    Returns:
        GitSource with commit hash and optional repository URL, or None if not a git repo

    Raises:
        FileNotFoundError: If git is not installed
    """
    try:
        # Get current commit hash
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # Get remote URL (origin by default, may be None if no remote)
        try:
            repository_url = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            repository_url = None

        return GitSource(commit_hash=commit_hash, repository_url=repository_url)
    except subprocess.CalledProcessError:
        # Not a git repository
        return None
