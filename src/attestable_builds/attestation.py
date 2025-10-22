"""Attestation report parsing and verification for Phase 2.

This module handles verification of attestation reports from TEE systems.
Cryptographic verification is delegated to attest-amd, while application-specific
verification (passport binding, nonce freshness) is handled here.

The attestation report cryptographically binds:
- The passport document (via hash in bytes 0-31)
- Freshness/replay protection (via nonce in bytes 32-63)
"""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def extract_custom_data(custom_data_hex: str) -> tuple[str, str]:
    """Extract passport hash and nonce from custom data hex string.

    Custom data format (64 bytes):
    - Bytes 0-31: SHA256(passport)
    - Bytes 32-63: Nonce

    Args:
        custom_data_hex: Hex-encoded custom data (128 chars)

    Returns:
        Tuple of (passport_hash, nonce) as hex strings (64 chars each)
    """
    if len(custom_data_hex) != 128:
        raise ValueError(
            f"Invalid custom_data length: {len(custom_data_hex)} chars (expected 128)"
        )
    custom_data_bytes = bytes.fromhex(custom_data_hex)
    return custom_data_bytes[:32].hex(), custom_data_bytes[32:64].hex()


def verify_passport_binding(
    custom_data_hex: str,
    passport: dict,
) -> tuple[bool, str]:
    """Verify that an attestation report is bound to a passport.

    This checks that the passport hash in the attestation custom data
    matches the actual passport document.

    Args:
        custom_data_hex: Hex-encoded custom data (128 chars)
        passport: Passport document to check

    Returns:
        Tuple of (verified, message)
    """
    # Extract passport hash from custom data (first 32 bytes)
    attestation_passport_hash, _ = extract_custom_data(custom_data_hex)

    # Hash the actual passport (canonical JSON for determinism)
    passport_json = json.dumps(passport, sort_keys=True, separators=(",", ":"))
    actual_passport_hash = hashlib.sha256(passport_json.encode()).hexdigest()

    if attestation_passport_hash != actual_passport_hash:
        return (
            False,
            f"Passport hash mismatch: {attestation_passport_hash[:16]}... != {actual_passport_hash[:16]}...",
        )

    return True, f"Passport binding verified: {attestation_passport_hash[:16]}..."


def verify_nonce_freshness(
    custom_data_hex: str,
    max_age_seconds: int = 3600,
) -> tuple[bool, str]:
    """Verify that the nonce is fresh (timestamp-based approach for POC).

    Extracts timestamp from first 8 bytes of nonce and checks age.

    Args:
        custom_data_hex: Hex-encoded custom data (128 chars)
        max_age_seconds: Maximum age in seconds (default 1 hour)

    Returns:
        Tuple of (verified, message)
    """
    try:
        _, nonce_hex = extract_custom_data(custom_data_hex)
        nonce_bytes = bytes.fromhex(nonce_hex)

        # Extract timestamp from first 8 bytes
        timestamp_bytes = nonce_bytes[:8]
        nonce_timestamp = int.from_bytes(timestamp_bytes, "big")
        current_timestamp = int(datetime.now(timezone.utc).timestamp())

        age = current_timestamp - nonce_timestamp
        if age > max_age_seconds:
            return False, f"Nonce is too old: {age}s (max {max_age_seconds}s)"

        if age < 0:
            return False, "Nonce timestamp is in the future"

        return True, f"Nonce is fresh: {age}s old"

    except Exception as e:
        return False, f"Error verifying nonce: {e}"


def verify_attestation(
    attestation_path: Path,
    custom_data_path: Path,
    passport_path: Path,
    max_age_seconds: int = 3600,
) -> dict:
    """Comprehensive attestation verification.

    Performs cryptographic verification via attest-amd, then verifies
    application-specific properties (passport binding, nonce freshness).

    Args:
        attestation_path: Path to attestation file (evidence.b64)
        custom_data_path: Path to custom_data.hex file
        passport_path: Path to passport JSON
        max_age_seconds: Maximum nonce age in seconds (default 1 hour)

    Returns:
        Dictionary with verification results:
        {
            "valid": bool,
            "checks": {
                "cryptographic": {"verified": bool, "message": str},
                "passport_binding": {"verified": bool, "message": str},
                "nonce_freshness": {"verified": bool, "message": str},
            },
            "custom_data": str,
            "passport": dict
        }

    Raises:
        subprocess.CalledProcessError: If attest-amd verify fails
        FileNotFoundError: If attest-amd is not installed
    """
    results = {"valid": True, "checks": {}, "custom_data": None, "passport": None}

    # Step 1: Load custom data
    try:
        custom_data_hex = custom_data_path.read_text().strip()
        results["custom_data"] = custom_data_hex
    except Exception as e:
        results["valid"] = False
        results["checks"]["cryptographic"] = {
            "verified": False,
            "message": f"Failed to load custom data: {e}",
        }
        return results

    # Step 2: Cryptographic verification via attest-amd
    # TODO this doesn't error out when it fails.
    try:
        subprocess.run(
            ["attest-amd", "verify", str(attestation_path), custom_data_hex, "--check-custom-data"],
            check=True,
            capture_output=True,
            text=True,
        )
        results["checks"]["cryptographic"] = {
            "verified": True,
            "message": "Cryptographic verification passed (attest-amd)",
        }
    except FileNotFoundError:
        results["valid"] = False
        results["checks"]["cryptographic"] = {
            "verified": False,
            "message": "attest-amd not found (required for verification)",
        }
        return results
    except subprocess.CalledProcessError as e:
        results["valid"] = False
        results["checks"]["cryptographic"] = {
            "verified": False,
            "message": f"Cryptographic verification failed: {e.stderr.strip()}",
        }
        return results

    # Step 3: Load passport
    try:
        passport = json.loads(passport_path.read_text())
        results["passport"] = passport
    except Exception as e:
        results["valid"] = False
        results["checks"]["passport_binding"] = {
            "verified": False,
            "message": f"Failed to load passport: {e}",
        }
        return results

    # Step 4: Verify passport binding
    verified, message = verify_passport_binding(custom_data_hex, passport)
    results["checks"]["passport_binding"] = {"verified": verified, "message": message}
    if not verified:
        results["valid"] = False

    # Step 5: Verify nonce freshness
    verified, message = verify_nonce_freshness(custom_data_hex, max_age_seconds)
    results["checks"]["nonce_freshness"] = {"verified": verified, "message": message}
    if not verified:
        results["valid"] = False

    return results
