"""Shared utility functions for attestable builds."""

import hashlib
import json
from pathlib import Path


def hash_file(file_path: Path) -> str:
    """Calculate SHA256 hash of a file.

    Args:
        file_path: Path to the file to hash

    Returns:
        Hexadecimal SHA256 hash of the file contents
    """
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def hash_provenance_to_32bytes(provenance_data: dict) -> str:
    """Hash SLSA provenance for 32-byte attestation custom data.

    Args:
        provenance_data: The SLSA provenance statement to hash

    Returns:
        32-byte (64 character) hex string: SHA256(provenance)
    """
    provenance_json = json.dumps(provenance_data, sort_keys=True, separators=(",", ":"))
    provenance_hash = hashlib.sha256(provenance_json.encode()).digest()
    return provenance_hash.hex()
