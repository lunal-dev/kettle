"""Subprocess utilities for consistent command execution and error handling."""

import os
import subprocess
from pathlib import Path
from typing import Optional


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
