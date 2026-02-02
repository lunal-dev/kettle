"""Extract git source information for build provenance."""

import hashlib
import subprocess
from pathlib import Path


def get_git_binary_path() -> Path:
    """Get the path to the git binary.

    Returns:
        Path to the git executable

    Raises:
        FileNotFoundError: If git is not installed or not in PATH
    """
    result = subprocess.run(["which", "git"], capture_output=True, text=True, check=True)
    return Path(result.stdout.strip())


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
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo_path, capture_output=True, text=True, check=True
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
        ["git", "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True, check=True
    )
    status_output = result.stdout.strip()

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
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
        )
        commit_hash = result.stdout.strip()

        # Get tree hash
        tree_hash = get_tree_hash(repo_path)

        # Check working tree cleanliness
        is_clean, dirty_files = check_working_tree_clean(repo_path)

        # Get remote URL (origin by default, may be None if no remote)
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"], cwd=repo_path, capture_output=True, text=True, check=True
            )
            repository_url = result.stdout.strip()
        except subprocess.CalledProcessError:
            repository_url = None

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


def clone_repo(
    repo_url: str,
    dest: Path,
    ref: str | None = None,
    depth: int = 1,
    timeout: int = 300,
) -> dict | None:
    """Clone a git repository and return its metadata.

    Args:
        repo_url: URL of the git repository to clone
        dest: Destination path for the cloned repository
        ref: Optional branch, tag, or commit to checkout
        depth: Clone depth (default 1 for shallow clone, 0 for full)
        timeout: Timeout in seconds for the clone operation

    Returns:
        Git metadata dict from get_git_info(), or None on failure

    Raises:
        subprocess.CalledProcessError: If git clone fails
        subprocess.TimeoutExpired: If clone times out
    """
    cmd = ["git", "clone"]

    if depth > 0:
        cmd.extend(["--depth", str(depth)])

    if ref:
        cmd.extend(["--branch", ref])

    cmd.extend([repo_url, str(dest)])

    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)

    return get_git_info(dest)
