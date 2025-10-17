"""Generate evidence for verified build inputs and outputs.

This evidence is designed to link with TEE (Trusted Execution Environment)
runtime measurements, enabling cryptographic proof that code running in a TEE
matches verified build outputs.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .verify import VerificationResult


def generate_build_evidence(
    cargo_lock_path: Path,
    cargo_lock_hash: str,
    results: list[VerificationResult],
    output_artifacts: list[Path] | None = None,
    output_path: Path | None = None
) -> dict:
    """Generate JSON build evidence from verified inputs and build outputs."""
    evidence = {
        "version": "1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cargo_lock": {
            "path": str(cargo_lock_path),
            "sha256": cargo_lock_hash,
        },
        "verification_summary": {
            "total": len(results),
            "verified": sum(1 for r in results if r.verified),
            "failed": sum(1 for r in results if not r.verified),
        },
        "dependencies": [
            {
                "name": r.dependency.name,
                "version": r.dependency.version,
                "source": r.dependency.source,
                "checksum": r.dependency.checksum,
                "verified": r.verified,
                "message": r.message,
            }
            for r in results
        ],
    }

    # Add output artifacts if provided
    if output_artifacts:
        evidence["outputs"] = [
            {
                "path": str(artifact),
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
            for artifact in output_artifacts
        ]

    # Write to file if path provided
    if output_path:
        output_path.write_text(json.dumps(evidence, indent=2))

    return evidence
