"""Shared utility functions for attestable builds."""

import hashlib
import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import secrets

def hash_file(file_path: Path) -> str:
    """Calculate SHA256 hash of a file.

    Args:
        file_path: Path to the file to hash

    Returns:
        Hexadecimal SHA256 hash of the file contents
    """
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def hash_json(json_obj: Any) -> str:
    """Calculate SHA256 hash of a JSON object.

    Args:
        json_obj: JSON-serializable object (dict, list, str, int, float, bool, None)

    Returns:
        Hexadecimal SHA256 hash of the JSON object

    Raises:
        TypeError: If the object is not JSON serializable
    """
    # Convert to JSON string with consistent formatting for deterministic hashing
    json_string = json.dumps(json_obj, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(json_string.encode('utf-8')).hexdigest()


def hash_provenance_to_64bytes(provenance_data: dict) -> str:
    """Hash SLSA provenance and generate nonce for 64-byte attestation custom data.

    Creates custom data format matching attestation spec:
    - Bytes 0-31: SHA256(provenance)
    - Bytes 32-63: Nonce (8-byte timestamp + 24-byte random)

    Args:
        provenance_data: The SLSA provenance statement to hash

    Returns:
        64-byte (128 character) hex string: provenance_hash || nonce
    """
    # Hash provenance (bytes 0-31)
    provenance_json = json.dumps(provenance_data, sort_keys=True, separators=(",", ":"))
    provenance_hash = hashlib.sha256(provenance_json.encode()).digest()

    # Generate nonce (bytes 32-63): timestamp (8 bytes) + random (24 bytes)
    timestamp = int(datetime.now(timezone.utc).timestamp()).to_bytes(8, "big")
    random_bytes = secrets.token_bytes(24)
    nonce = timestamp + random_bytes

    # Combine: 32 bytes hash + 32 bytes nonce = 64 bytes
    custom_data_bytes = provenance_hash + nonce
    return custom_data_bytes.hex()

def hash_provenance_to_32bytes(provenance_data: dict) -> str:
    """Hash SLSA provenance for 32-byte attestation custom data.

    Args:
        provenance_data: The SLSA provenance statement to hash

    Returns:
        32-byte (64 character) hex string: SHA256(provenance)
    """
    # Hash provenance (32 bytes)
    provenance_json = json.dumps(provenance_data, sort_keys=True, separators=(",", ":"))
    provenance_hash = hashlib.sha256(provenance_json.encode()).digest()
    return provenance_hash.hex()

# Backward compatibility aliases
hash_passport_to_64bytes = hash_provenance_to_64bytes
hash_passport_to_32bytes = hash_provenance_to_32bytes