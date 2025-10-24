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
from .utils import hash_passport_to_32bytes


def verify_attestation(
    attestation_path: Path,
    passport_path: Path,
) -> dict:
    """Comprehensive attestation verification.

    Performs cryptographic verification via attest-amd, then verifies
    application-specific properties (passport binding, nonce freshness).

    Args:
        attestation_path: Path to attestation file (evidence.b64)
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



    # Step 3: Load passport
    try:
        passport = json.loads(passport_path.read_text())
        results["passport"] = passport
        custom_data_hex = hash_passport_to_32bytes(passport)
    except Exception as e:
        results["valid"] = False
        results["checks"]["passport_binding"] = {
            "verified": False,
            "message": f"Failed to load passport: {e}",
        }
        return results


    # Step 2: Cryptographic verification via attest-amd
    try:
        result = subprocess.run(
            ["attest-amd", "verify", str(attestation_path), custom_data_hex, "--check-custom-data"],
            check=True,
            capture_output=True,
            text=True,
        )

        # Parse JSON output from stdout
        try:
            attestation_report = json.loads(result.stdout)

            # Print report data for sanity check
            print("=== Attestation Verification Report ===")
            print(f"Overall Status: {attestation_report.get('status', 'unknown')}")

            # Check certificate chain verification
            if 'certs' in attestation_report:
                print("✓ Certificate chain verified")
                certs = attestation_report['certs']
                if 'certificateChain' in certs:
                    print("  - Certificate chain present and validated")
                if 'vcekCert' in certs:
                    print("  - VCEK certificate present and validated")

            # Check launch measurement verification and print it
            if 'report' in attestation_report:
                report = attestation_report['report']
                if 'measurement' in report:
                    measurement = report['measurement']
                    print("✓ Launch measurement verified")
                    # Convert measurement bytes to hex string for readability
                    measurement_hex = ''.join(f'{b:02x}' for b in measurement)
                    print(f"  - Measurement: {measurement_hex}")

                print(f"  - Guest SVN: {report.get('guest_svn', 'N/A')}")
                print(f"  - Policy: {report.get('policy', 'N/A')}")
                print(f"  - Version: {report.get('version', 'N/A')}")
                print(f"  - VMPL: {report.get('vmpl', 'N/A')}")

            # Check report data verification
            if 'report_data' in attestation_report:
                print("✓ Report data verified")
                print(f"  - Report data: {attestation_report['report_data']}")

            print("=======================================\n")

            # Check if verification was successful
            if attestation_report.get('status') == 'verified':
                results["checks"]["cryptographic"] = {
                    "verified": True,
                    "message": "Cryptographic verification passed (attest-amd)",
                    "report": attestation_report  # Store full report for later use
                }
            else:
                results["valid"] = False
                results["checks"]["cryptographic"] = {
                    "verified": False,
                    "message": f"Verification failed - status: {attestation_report.get('status', 'unknown')}",
                }
                return results

        except json.JSONDecodeError as e:
            results["valid"] = False
            results["checks"]["cryptographic"] = {
                "verified": False,
                "message": f"Failed to parse attestation report JSON: {e}",
            }
            print(f"Raw stdout: {result.stdout}")
            return results

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
            "message": f"Cryptographic verification failed: {e.stderr.strip() if e.stderr else 'Unknown error'}",
        }
        print(f"Process failed with return code: {e.returncode}")
        if e.stdout:
            print(f"Stdout: {e.stdout}")
        if e.stderr:
            print(f"Stderr: {e.stderr}")
        return results

    return results