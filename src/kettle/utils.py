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


def hash_passport_to_64bytes(passport_data: dict) -> str:
    """Hash passport JSON and generate nonce for 64-byte attestation custom data.

    Creates custom data format matching attestation spec:
    - Bytes 0-31: SHA256(passport)
    - Bytes 32-63: Nonce (8-byte timestamp + 24-byte random)

    Args:
        passport_data: The passport dictionary to hash

    Returns:
        64-byte (128 character) hex string: passport_hash || nonce
    """
    # Hash passport (bytes 0-31)
    passport_json = json.dumps(passport_data, sort_keys=True, separators=(",", ":"))
    passport_hash = hashlib.sha256(passport_json.encode()).digest()

    # Generate nonce (bytes 32-63): timestamp (8 bytes) + random (24 bytes)
    timestamp = int(datetime.now(timezone.utc).timestamp()).to_bytes(8, "big")
    random_bytes = secrets.token_bytes(24)
    nonce = timestamp + random_bytes

    # Combine: 32 bytes hash + 32 bytes nonce = 64 bytes
    custom_data_bytes = passport_hash + nonce
    return custom_data_bytes.hex()

def hash_passport_to_32bytes(passport_data: dict) -> str:
    """Hash passport JSON for 32-byte attestation custom data.

    Args:
        passport_data: The passport dictionary to hash

    Returns:
        32-byte (64 character) hex string: SHA256(passport)
    """
    # Hash passport (32 bytes)
    passport_json = json.dumps(passport_data, sort_keys=True, separators=(",", ":"))
    passport_hash = hashlib.sha256(passport_json.encode()).digest()
    return passport_hash.hex()