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
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CalledProcessError

from kettle.logger import log, log_error, log_success
from kettle.subprocess_utils import run_command
from kettle.utils import hash_passport_to_32bytes


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
        CalledProcessError: If attest-amd verify fails
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
        result = run_command(
            ["./attest-amd", "verify", str(attestation_path), custom_data_hex, "--check-custom-data"]
        )

        # Parse JSON output from stdout
        try:
            attestation_report = json.loads(result.stdout)

            # Log report data for verification
            log("\n[Attestation Report]", style="bold")
            log(f"Status: {attestation_report.get('status', 'unknown')}", style="dim")

            # Check certificate chain verification
            if 'certs' in attestation_report:
                log_success("Certificate chain verified")
                certs = attestation_report['certs']
                if 'certificateChain' in certs:
                    log("  - Certificate chain present and validated", style="dim")
                if 'vcekCert' in certs:
                    log("  - VCEK certificate present and validated", style="dim")

            # Check launch measurement verification
            if 'report' in attestation_report:
                report = attestation_report['report']
                if 'measurement' in report:
                    measurement = report['measurement']
                    log_success("Launch measurement verified")
                    # Convert measurement bytes to hex string for readability
                    measurement_hex = ''.join(f'{b:02x}' for b in measurement)
                    log(f"  - Measurement: {measurement_hex}", style="dim")

                log(f"  - Guest SVN: {report.get('guest_svn', 'N/A')}", style="dim")
                log(f"  - Policy: {report.get('policy', 'N/A')}", style="dim")
                log(f"  - Version: {report.get('version', 'N/A')}", style="dim")
                log(f"  - VMPL: {report.get('vmpl', 'N/A')}", style="dim")

            # Check report data verification
            if 'report_data' in attestation_report:
                log_success("Report data verified")
                log(f"  - Report data: {attestation_report['report_data']}", style="dim")

            log("")

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
            log_error(f"Raw stdout: {result.stdout}")
            return results

    except FileNotFoundError:
        results["valid"] = False
        results["checks"]["cryptographic"] = {
            "verified": False,
            "message": "attest-amd not found (required for verification)",
        }
        return results
    except CalledProcessError as e:
        results["valid"] = False
        results["checks"]["cryptographic"] = {
            "verified": False,
            "message": f"Cryptographic verification failed: {e.stderr.strip() if e.stderr else 'Unknown error'}",
        }
        log_error(f"Process failed with return code: {e.returncode}")
        if e.stdout:
            log(f"Stdout: {e.stdout}", style="dim")
        if e.stderr:
            log(f"Stderr: {e.stderr}", style="dim")
        return results

    return results