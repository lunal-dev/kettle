"""Extract git source information for build provenance."""

import hashlib
from pathlib import Path

from kettle.subprocess_utils import run_command, run_command_stdout


def get_git_binary_path() -> Path:
    """Get the path to the git binary.

    Returns:
        Path to the git executable

    Raises:
        FileNotFoundError: If git is not installed or not in PATH
    """
    result = run_command_stdout(["which", "git"])
    return Path(result)


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
    return run_command_stdout(["git", "rev-parse", "HEAD^{tree}"], cwd=repo_path)


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
    status_output = run_command_stdout(["git", "status", "--porcelain"], cwd=repo_path)

    # git status --porcelain returns empty string if clean
    if not status_output:
        return True, []

    # Parse dirty files from porcelain output
    # Format: "XY filename" where XY are status codes
    dirty_files = [line[3:] for line in status_output.split("\n") if line]
    return False, dirty_files


def get_git_info(repo_path: Path) -> dict | None:
    """Extract comprehensive git source information from a repository.

    This collects all git metadata needed for verifiable builds:
    - Commit hash (exact source version)
    - Repository URL (origin)
    - Tree hash (cryptographic proof of source tree state)
    - Git binary path and hash (cryptographic proof of git tool)
    - Working tree status (clean/dirty)
    - List of uncommitted files (if any)

    Args:
        repo_path: Path to the git repository (project root)

    Returns:
        Dict with git metadata, or None if not a git repo

    Raises:
        FileNotFoundError: If git is not installed
    """
    try:
        # Get git binary path and hash
        git_path = get_git_binary_path()
        git_binary_hash = hashlib.sha256(git_path.read_bytes()).hexdigest()

        # Get current commit hash
        commit_hash = run_command_stdout(["git", "rev-parse", "HEAD"], cwd=repo_path)

        # Get tree hash
        tree_hash = get_tree_hash(repo_path)

        # Check working tree cleanliness
        is_clean, dirty_files = check_working_tree_clean(repo_path)

        # Get remote URL (origin by default, may be None if no remote)
        repository_url = run_command_stdout(["git", "remote", "get-url", "origin"], cwd=repo_path)

        return {
            "commit_hash": commit_hash,
            "repository_url": repository_url,
            "tree_hash": tree_hash,
            "git_path": str(git_path),
            "git_binary_hash": git_binary_hash,
            "is_clean": is_clean,
            "dirty_files": dirty_files,
        }
    except Exception:
        # Not a git repository or git command failed
        return None
