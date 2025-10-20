"""Extract git source information for build provenance."""

import subprocess
from pathlib import Path
from typing import NamedTuple


class GitSource(NamedTuple):
    """Git source information with verification data."""
    commit_hash: str
    repository_url: str | None
    tree_hash: str
    git_version: str
    is_clean: bool
    dirty_files: list[str]


def get_git_version() -> str:
    """Get the git version string.

    Returns:
        Git version string (e.g., "git version 2.39.2")

    Raises:
        FileNotFoundError: If git is not installed
    """
    result = subprocess.run(
        ["git", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_tree_hash(repo_path: Path) -> str:
    """Get the git tree hash for HEAD commit.

    The tree hash is a cryptographic hash of the entire source tree at a commit,
    providing verifiable proof of the exact source state.

    Args:
        repo_path: Path to the git repository

    Returns:
        Git tree object hash (40-character hex string)

    Raises:
        subprocess.CalledProcessError: If not a git repo or git command fails
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def check_working_tree_clean(repo_path: Path) -> tuple[bool, list[str]]:
    """Check if the git working tree is clean (no uncommitted changes).

    Args:
        repo_path: Path to the git repository

    Returns:
        Tuple of (is_clean, dirty_files):
        - is_clean: True if no uncommitted changes, False otherwise
        - dirty_files: List of files with uncommitted changes (empty if clean)

    Raises:
        subprocess.CalledProcessError: If not a git repo or git command fails
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    # git status --porcelain returns empty string if clean
    status_output = result.stdout.strip()
    if not status_output:
        return True, []

    # Parse dirty files from porcelain output
    # Format: "XY filename" where XY are status codes
    dirty_files = [line[3:] for line in status_output.split("\n") if line]
    return False, dirty_files


def get_git_info(repo_path: Path) -> GitSource | None:
    """Extract comprehensive git source information from a repository.

    This collects all git metadata needed for verifiable builds:
    - Commit hash (exact source version)
    - Repository URL (origin)
    - Tree hash (cryptographic proof of source tree state)
    - Git version (tool version for reproducibility)
    - Working tree status (clean/dirty)
    - List of uncommitted files (if any)

    Args:
        repo_path: Path to the git repository (project root)

    Returns:
        GitSource with complete git metadata, or None if not a git repo

    Raises:
        FileNotFoundError: If git is not installed
    """
    try:
        # Get git version
        git_version = get_git_version()

        # Get current commit hash
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # Get tree hash
        tree_hash = get_tree_hash(repo_path)

        # Check working tree cleanliness
        is_clean, dirty_files = check_working_tree_clean(repo_path)

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

        return GitSource(
            commit_hash=commit_hash,
            repository_url=repository_url,
            tree_hash=tree_hash,
            git_version=git_version,
            is_clean=is_clean,
            dirty_files=dirty_files,
        )
    except subprocess.CalledProcessError:
        # Not a git repository
        return None
