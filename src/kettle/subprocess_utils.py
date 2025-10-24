"""Subprocess utilities for consistent command execution and error handling."""

import os
import subprocess
from pathlib import Path
from typing import Optional


def _set_umask():
    """Set umask to 0o002 to ensure cargo build-scripts get execute permissions.

    The umask (user file creation mask) defines which permission bits are turned
    off when creating new files or directories. Setting umask to 0o002 ensures:

    - New files get permissions 0o664 (rw-rw-r--):
      - Owner: read + write
      - Group: read + write
      - Others: read only

    - New directories get permissions 0o775 (rwxrwxr-x):
      - Owner: read + write + execute
      - Group: read + write + execute
      - Others: read + execute

    This is required for cargo to create build-script binaries with execute
    permissions (775). Cargo creates build-scripts with mode 0o777, which
    with umask 0o002 results in 0o775 (rwxrwxr-x).
    """
    os.umask(0o002)


def run_command(
    cmd: list[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """
    Run a subprocess command with consistent error handling.

    Args:
        cmd: Command and arguments as list
        cwd: Working directory for command execution
        check: If True, raise CalledProcessError on non-zero exit
        capture_output: If True, capture stdout and stderr
        text: If True, decode output as text

    Returns:
        CompletedProcess instance with stdout, stderr, returncode

    Raises:
        subprocess.CalledProcessError: If command fails and check=True
        FileNotFoundError: If command not found
    """
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
        preexec_fn=_set_umask,
    )


def run_command_stdout(
    cmd: list[str],
    cwd: Optional[Path] = None,
) -> Optional[str]:
    """
    Run command and return stripped stdout, or None on failure.

    Args:
        cmd: Command and arguments as list
        cwd: Working directory for command execution

    Returns:
        Stripped stdout string, or None if command fails

    Raises:
        FileNotFoundError: If command not found
    """
    try:
        result = run_command(cmd, cwd=cwd)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None
