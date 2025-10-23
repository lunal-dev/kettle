"""Shared utility functions for attestable builds."""

import hashlib
from pathlib import Path


def hash_file(file_path: Path) -> str:
    """Calculate SHA256 hash of a file.

    Args:
        file_path: Path to the file to hash

    Returns:
        Hexadecimal SHA256 hash of the file contents
    """
    return hashlib.sha256(file_path.read_bytes()).hexdigest()
