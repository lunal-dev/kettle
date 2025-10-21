"""Golden measurement management for TEE build runner.

The golden measurement is a cryptographic hash of the build runner code itself.
This proves which specific trusted code is executing inside the TEE.

In Phase 2, verifiers check the TEE's launch measurement against this published
golden measurement to ensure the correct build runner code produced the passport.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


class GoldenMeasurement(NamedTuple):
    """Represents a golden measurement of the build runner code."""
    version: str
    timestamp: str
    runner_hash: str
    modules: dict[str, str]


def hash_file(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def calculate_launch_measurement(package_dir: Path | None = None) -> GoldenMeasurement:
    """Calculate launch measurement by hashing all build runner modules.

    This generates the "launch measurement" - a cryptographic hash of the build
    runner code that proves which specific trusted code is executing in the TEE.

    Args:
        package_dir: Path to attestable_builds package. If None, auto-detect.

    Returns:
        GoldenMeasurement with hashes of all modules
    """
    if package_dir is None:
        # Auto-detect: find the package directory
        package_dir = Path(__file__).parent

    # Core modules that make up the build runner
    # These are the modules that execute inside the TEE
    core_modules = [
        "tee_runner.py",
        "build.py",
        "cargo.py",
        "git.py",
        "toolchain.py",
        "verify.py",
        "passport.py",
        "attestation.py",
        "golden.py",
    ]

    # Hash each module
    module_hashes = {}
    for module_name in core_modules:
        module_path = package_dir / module_name
        if module_path.exists():
            module_hashes[module_name] = hash_file(module_path)

    # Create combined hash of all modules (sorted for determinism)
    combined = "".join(
        f"{name}:{hash_val}"
        for name, hash_val in sorted(module_hashes.items())
    )
    runner_hash = hashlib.sha256(combined.encode()).hexdigest()

    return GoldenMeasurement(
        version="1.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
        runner_hash=runner_hash,
        modules=module_hashes,
    )


def save_golden_measurement(
    measurement: GoldenMeasurement,
    output_path: Path,
) -> None:
    """Save golden measurement to a manifest file.

    Args:
        measurement: The golden measurement to save
        output_path: Path to write the manifest JSON
    """
    manifest = {
        "version": measurement.version,
        "timestamp": measurement.timestamp,
        "runner_hash": measurement.runner_hash,
        "modules": measurement.modules,
        "description": "Golden measurement of attestable build runner code",
    }

    output_path.write_text(json.dumps(manifest, indent=2))


def load_golden_measurement(manifest_path: Path) -> GoldenMeasurement:
    """Load golden measurement from manifest file.

    Args:
        manifest_path: Path to the manifest JSON file

    Returns:
        GoldenMeasurement parsed from the manifest

    Raises:
        FileNotFoundError: If manifest file doesn't exist
        ValueError: If manifest format is invalid
    """
    if not manifest_path.exists():
        raise FileNotFoundError(f"Golden measurement manifest not found: {manifest_path}")

    try:
        data = json.loads(manifest_path.read_text())
        return GoldenMeasurement(
            version=data["version"],
            timestamp=data["timestamp"],
            runner_hash=data["runner_hash"],
            modules=data["modules"],
        )
    except (KeyError, json.JSONDecodeError) as e:
        raise ValueError(f"Invalid golden measurement manifest: {e}") from e


def verify_against_golden(
    current_measurement: GoldenMeasurement,
    golden_measurement: GoldenMeasurement,
) -> tuple[bool, str]:
    """Verify a runtime measurement against the golden measurement.

    Args:
        current_measurement: The measurement to verify
        golden_measurement: The trusted golden measurement

    Returns:
        Tuple of (verified, message)
    """
    if current_measurement.runner_hash != golden_measurement.runner_hash:
        return False, f"Runner hash mismatch: {current_measurement.runner_hash[:16]}... != {golden_measurement.runner_hash[:16]}..."

    # Check individual modules
    for module_name, current_hash in current_measurement.modules.items():
        golden_hash = golden_measurement.modules.get(module_name)
        if golden_hash is None:
            return False, f"Module {module_name} not in golden measurement"
        if current_hash != golden_hash:
            return False, f"Module {module_name} hash mismatch"

    return True, "Measurement matches golden measurement"
