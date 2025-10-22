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
from typing import NamedTuple


class AttestationReport(NamedTuple):
    """Attestation report structure matching TEE format."""

    report_id: str
    timestamp: str
    launch_measurement: str
    custom_data: str  # Hex-encoded 64 bytes
    signature: str
    platform_info: dict


def load_attestation(attestation_path: Path) -> AttestationReport:
    """Load an attestation report from JSON file.

    Args:
        attestation_path: Path to attestation JSON file

    Returns:
        AttestationReport with parsed data

    Raises:
        ValueError: If attestation format is invalid
    """
    try:
        data = json.loads(attestation_path.read_text())
    except Exception as e:
        raise ValueError(f"Failed to load attestation: {e}")

    required_fields = [
        "report_id",
        "timestamp",
        "launch_measurement",
        "custom_data",
        "signature",
    ]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    # Validate custom_data is 64 bytes (128 hex chars)
    if len(data["custom_data"]) != 128:
        raise ValueError(
            f"Invalid custom_data length: {len(data['custom_data'])} chars (expected 128)"
        )

    return AttestationReport(
        report_id=data["report_id"],
        timestamp=data["timestamp"],
        launch_measurement=data["launch_measurement"],
        custom_data=data["custom_data"],
        signature=data["signature"],
        platform_info=data.get("platform_info", {}),
    )


def extract_custom_data(attestation: AttestationReport) -> tuple[str, str]:
    """Extract passport hash and nonce from attestation custom data.

    Custom data format (64 bytes):
    - Bytes 0-31: SHA256(passport)
    - Bytes 32-63: Nonce

    Args:
        attestation: Attestation report

    Returns:
        Tuple of (passport_hash, nonce) as hex strings (64 chars each)
    """
    custom_data_bytes = bytes.fromhex(attestation.custom_data)
    return custom_data_bytes[:32].hex(), custom_data_bytes[32:64].hex()


def verify_passport_binding(
    attestation: AttestationReport,
    passport: dict,
) -> tuple[bool, str]:
    """Verify that an attestation report is bound to a passport.

    This checks that the passport hash in the attestation custom data
    matches the actual passport document.

    Args:
        attestation: Attestation report to verify
        passport: Passport document to check

    Returns:
        Tuple of (verified, message)
    """
    # Extract passport hash from custom data (first 32 bytes)
    attestation_passport_hash, _ = extract_custom_data(attestation)

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
    attestation: AttestationReport,
    max_age_seconds: int = 3600,
) -> tuple[bool, str]:
    """Verify that the nonce is fresh (timestamp-based approach for POC).

    Extracts timestamp from first 8 bytes of nonce and checks age.

    Args:
        attestation: Attestation report
        max_age_seconds: Maximum age in seconds (default 1 hour)

    Returns:
        Tuple of (verified, message)
    """
    try:
        _, nonce_hex = extract_custom_data(attestation)
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
    passport_path: Path,
    max_age_seconds: int = 3600,
) -> dict:
    """Comprehensive attestation verification.

    Performs cryptographic verification via attest-amd, then verifies
    application-specific properties (passport binding, nonce freshness).

    Args:
        attestation_path: Path to attestation report JSON
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
            "attestation": AttestationReport,
            "passport": dict
        }

    Raises:
        subprocess.CalledProcessError: If attest-amd verify fails
        FileNotFoundError: If attest-amd is not installed
    """
    results = {"valid": True, "checks": {}, "attestation": None, "passport": None}

    # Step 1: Cryptographic verification via attest-amd
    # TODO this doesn't error out when it fails.
    try:
        subprocess.run(
            ["attest-amd", "verify", str(attestation_path)],
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

    # Step 2: Load attestation
    try:
        attestation = load_attestation(attestation_path)
        results["attestation"] = attestation
    except ValueError as e:
        results["valid"] = False
        results["checks"]["cryptographic"] = {
            "verified": False,
            "message": f"Failed to load attestation: {e}",
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
    verified, message = verify_passport_binding(attestation, passport)
    results["checks"]["passport_binding"] = {"verified": verified, "message": message}
    if not verified:
        results["valid"] = False

    # Step 5: Verify nonce freshness
    verified, message = verify_nonce_freshness(attestation, max_age_seconds)
    results["checks"]["nonce_freshness"] = {"verified": verified, "message": message}
    if not verified:
        results["valid"] = False

    return results
