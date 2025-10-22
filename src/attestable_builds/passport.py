"""Generate passport document for attestable builds (Phase 1 inputs)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .merkle import calculate_input_merkle_root
from .utils import hash_file


def generate_passport(
    git_source: Optional[dict],
    cargo_lock_hash: str,
    toolchain: dict,
    verification_results: Optional[list[dict]] = None,
    output_artifacts: Optional[list[tuple[Path, str]]] = None,
    output_path: Optional[Path] = None,
) -> dict:
    """Generate a passport document according to Phase 1 specification.

    Args:
        git_source: Source code git information (optional)
        cargo_lock_path: Path to Cargo.lock file
        cargo_lock_hash: SHA256 hash of Cargo.lock
        toolchain: Rust toolchain information
        verification_results: Optional results from dependency verification
        output_artifacts: Optional list of (path, hash) tuples for build outputs
        output_path: Optional path to write passport JSON

    Returns:
        Passport dictionary matching the design spec
    """
    passport = {
        "version": "1.0",
        "inputs": {
            "cargo_lock_hash": cargo_lock_hash,
            "toolchain": {
                "rustc": {
                    "binary_hash": toolchain["rustc_hash"],
                    "version": toolchain["rustc_version"],
                },
                "cargo": {
                    "binary_hash": toolchain["cargo_hash"],
                    "version": toolchain["cargo_version"],
                },
            },
        },
        "build_process": {
            "command": "cargo build --locked --release",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

    # Add dependencies if provided
    if verification_results:
        passport["inputs"]["dependencies"] = [
            {
                "name": r["dependency"]["name"],
                "version": r["dependency"]["version"],
                "source": r["dependency"]["source"],
                "checksum": r["dependency"]["checksum"],
                "verified": r["verified"],
            }
            for r in verification_results
        ]

    # Calculate input merkle root
    input_merkle_root = calculate_input_merkle_root(
        git_commit_hash=git_source["commit_hash"] if git_source else None,
        git_tree_hash=git_source["tree_hash"] if git_source else None,
        git_binary_hash=git_source["git_binary_hash"] if git_source else None,
        cargo_lock_hash=cargo_lock_hash,
        dependencies=[
            {
                "name": r["dependency"]["name"],
                "version": r["dependency"]["version"],
                "checksum": r["dependency"]["checksum"],
            }
            for r in verification_results
        ] if verification_results else [],
        toolchain={
            "rustc": {
                "binary_hash": toolchain["rustc_hash"],
                "version": toolchain["rustc_version"],
            },
            "cargo": {
                "binary_hash": toolchain["cargo_hash"],
                "version": toolchain["cargo_version"],
            },
        },
    )
    passport["inputs"]["input_merkle_root"] = input_merkle_root

    # Add git source if available
    if git_source:
        passport["inputs"]["source"] = {
            "type": "git",
            "commit_hash": git_source["commit_hash"],
            "tree_hash": git_source["tree_hash"],
            "git_binary_hash": git_source["git_binary_hash"],
        }
        if git_source.get("repository_url"):
            passport["inputs"]["source"]["repository"] = git_source["repository_url"]

    # Add outputs if provided
    if output_artifacts:
        passport["outputs"] = {
            "artifacts": [
                {
                    "path": str(path),
                    "hash": hash_value,
                    "name": Path(path).name,
                }
                for path, hash_value in output_artifacts
            ]
        }

    # Write to file if requested
    if output_path:
        output_path.write_text(json.dumps(passport, indent=2))

    return passport


def _load_passport(passport_path: Path) -> tuple[Optional[dict], Optional[dict]]:
    """Load and validate passport structure.

    Returns:
        Tuple of (passport_data, error_check) where error_check is None on success.
    """
    try:
        passport = json.loads(passport_path.read_text())
    except Exception as e:
        return None, {"passed": False, "message": f"Failed to load passport: {e}"}

    required_fields = ["version", "inputs", "build_process"]
    missing_fields = [f for f in required_fields if f not in passport]
    if missing_fields:
        return None, {
            "passed": False,
            "message": f"Missing required fields: {', '.join(missing_fields)}",
        }

    return passport, None


def _verify_git_commit(passport: dict, expected_commit: str, strict: bool) -> dict:
    """Verify git commit hash matches expected value."""
    passport_commit = passport.get("inputs", {}).get("source", {}).get("commit_hash")

    if not passport_commit:
        return {
            "passed": False,
            "message": "No git commit in passport",
            "fail": strict,
        }

    if passport_commit == expected_commit:
        return {
            "passed": True,
            "message": f"Git commit matches: {expected_commit[:8]}...",
        }

    return {
        "passed": False,
        "message": f"Git commit mismatch: expected {expected_commit[:8]}..., got {passport_commit[:8]}...",
        "fail": True,
    }


def _verify_git_tree(passport: dict, expected_tree: str, strict: bool) -> dict:
    """Verify git tree hash matches expected value."""
    passport_tree = passport.get("inputs", {}).get("source", {}).get("tree_hash")

    if not passport_tree:
        return {
            "passed": False,
            "message": "No git tree in passport",
            "fail": strict,
        }

    if passport_tree == expected_tree:
        return {
            "passed": True,
            "message": f"Git tree matches: {expected_tree[:8]}...",
        }

    return {
        "passed": False,
        "message": f"Git tree mismatch: expected {expected_tree[:8]}..., got {passport_tree[:8]}...",
        "fail": True,
    }


def _verify_input_merkle_root(passport: dict, expected_root: str, strict: bool) -> dict:
    """Verify input merkle root matches expected value."""
    passport_root = passport.get("inputs", {}).get("input_merkle_root")

    if not passport_root:
        return {
            "passed": False,
            "message": "No input merkle root in passport",
            "fail": strict,
        }

    if passport_root == expected_root:
        return {
            "passed": True,
            "message": f"Input merkle root matches: {expected_root[:16]}...",
        }

    return {
        "passed": False,
        "message": f"Input merkle root mismatch: expected {expected_root[:16]}..., got {passport_root[:16]}...",
        "fail": True,
    }


def _verify_toolchain_hashes(
    passport: dict, rustc_hash: Optional[str], cargo_hash: Optional[str], strict: bool
) -> dict:
    """Verify toolchain binary hashes match expected values."""
    toolchain = passport.get("inputs", {}).get("toolchain", {})
    passport_rustc = toolchain.get("rustc", {}).get("binary_hash")
    passport_cargo = toolchain.get("cargo", {}).get("binary_hash")

    checks = []
    all_passed = True

    if rustc_hash:
        if not passport_rustc:
            if strict:
                all_passed = False
            checks.append(f"rustc: not in passport")
        elif passport_rustc == rustc_hash:
            checks.append(f"rustc: {rustc_hash[:8]}... ✓")
        else:
            all_passed = False
            checks.append(
                f"rustc: expected {rustc_hash[:8]}..., got {passport_rustc[:8]}..."
            )

    if cargo_hash:
        if not passport_cargo:
            if strict:
                all_passed = False
            checks.append(f"cargo: not in passport")
        elif passport_cargo == cargo_hash:
            checks.append(f"cargo: {cargo_hash[:8]}... ✓")
        else:
            all_passed = False
            checks.append(
                f"cargo: expected {cargo_hash[:8]}..., got {passport_cargo[:8]}..."
            )

    if not checks:
        return {
            "passed": False,
            "message": "No toolchain hashes provided",
            "fail": strict,
        }

    return {
        "passed": all_passed,
        "message": "; ".join(checks),
        "fail": not all_passed,
    }


def _verify_cargo_lock(passport: dict, expected_hash: str, strict: bool) -> dict:
    """Verify Cargo.lock hash matches expected value."""
    passport_hash = passport.get("inputs", {}).get("cargo_lock_hash")

    if not passport_hash:
        return {
            "passed": False,
            "message": "No Cargo.lock hash in passport",
            "fail": strict,
        }

    if passport_hash == expected_hash:
        return {"passed": True, "message": "Cargo.lock hash matches"}

    return {
        "passed": False,
        "message": "Cargo.lock hash mismatch",
        "fail": True,
    }


def _verify_binary_hash(passport: dict, binary_path: Path, strict: bool) -> dict:
    """Verify binary artifact hash matches passport record."""
    if not binary_path.exists():
        return {
            "passed": False,
            "message": f"Binary not found: {binary_path}",
            "fail": True,
        }

    actual_hash = hash_file(binary_path)
    artifacts = passport.get("outputs", {}).get("artifacts", [])
    binary_name = binary_path.name

    # Find matching artifact by name
    matching_artifact = next(
        (a for a in artifacts if a.get("name") == binary_name), None
    )

    if not matching_artifact:
        return {
            "passed": False,
            "message": f"No artifact named '{binary_name}' in passport",
            "fail": strict,
        }

    expected_hash = matching_artifact.get("hash")
    if actual_hash == expected_hash:
        return {
            "passed": True,
            "message": f"Binary hash matches: {actual_hash[:16]}...",
        }

    return {
        "passed": False,
        "message": f"Binary hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}...",
        "fail": True,
    }


def _verify_input_merkle(passport: dict, strict: bool) -> dict:
    """Verify input merkle root by recalculating from passport data."""
    try:
        inputs = passport.get("inputs", {})
        source = inputs.get("source", {})
        toolchain = inputs.get("toolchain", {})
        dependencies = inputs.get("dependencies", [])

        computed_root = calculate_input_merkle_root(
            git_commit_hash=source.get("commit_hash"),
            git_tree_hash=source.get("tree_hash"),
            git_binary_hash=source.get("git_binary_hash"),
            cargo_lock_hash=inputs.get("cargo_lock_hash"),
            dependencies=[
                {
                    "name": dep["name"],
                    "version": dep["version"],
                    "checksum": dep["checksum"],
                }
                for dep in dependencies
            ]
            if dependencies
            else [],
            toolchain={
                "rustc": {
                    "binary_hash": toolchain.get("rustc", {}).get("binary_hash"),
                    "version": toolchain.get("rustc", {}).get("version"),
                },
                "cargo": {
                    "binary_hash": toolchain.get("cargo", {}).get("binary_hash"),
                    "version": toolchain.get("cargo", {}).get("version"),
                },
            },
        )

        expected_root = inputs.get("input_merkle_root")
        if computed_root == expected_root:
            return {
                "passed": True,
                "message": f"Input merkle root verified: {computed_root[:16]}...",
            }

        expected_str = expected_root[:16] if expected_root else "None"
        return {
            "passed": False,
            "message": f"Input merkle root mismatch: expected {expected_str}..., computed {computed_root[:16]}...",
            "fail": True,
        }
    except Exception as e:
        return {
            "passed": False,
            "message": f"Failed to verify input merkle root: {e}",
            "fail": strict,
        }


def load_verification_manifest(manifest_path: Path) -> dict:
    """Load a verification manifest JSON file.

    Manifest format:
    {
        "git_commit": "abc123...",
        "git_tree": "def456...",
        "cargo_lock_hash": "ghi789...",
        "input_merkle_root": "jkl012...",
        "toolchain": {
            "rustc_hash": "mno345...",
            "cargo_hash": "pqr678..."
        },
        "binary_artifacts": [
            {"name": "my-app", "hash": "stu901..."}
        ]
    }

    Args:
        manifest_path: Path to verification manifest JSON file

    Returns:
        Dictionary containing expected values for verification
    """
    try:
        return json.loads(manifest_path.read_text())
    except Exception as e:
        raise ValueError(f"Failed to load verification manifest: {e}")


def verify_passport(
    passport_path: Path,
    manifest_path: Optional[Path] = None,
    git_commit: Optional[str] = None,
    cargo_lock_hash: Optional[str] = None,
    binary_path: Optional[Path] = None,
    strict: bool = False,
) -> dict:
    """Verify a passport document against known values.

    Args:
        passport_path: Path to passport JSON file
        manifest_path: Path to verification manifest JSON (optional, takes precedence)
        git_commit: Expected git commit hash (optional)
        cargo_lock_hash: Expected Cargo.lock hash (optional)
        binary_path: Path to binary to verify hash (optional)
        strict: If True, fail if any optional check cannot be performed

    Returns:
        Dictionary with verification results:
        {
            "valid": bool,
            "checks": {
                "passport_format": {"passed": bool, "message": str},
                "git_commit": {"passed": bool, "message": str},
                "cargo_lock": {"passed": bool, "message": str},
                "binary_hash": {"passed": bool, "message": str},
                "input_merkle": {"passed": bool, "message": str},
            },
            "passport": dict  # The loaded passport data
        }
    """
    results = {"valid": True, "checks": {}, "passport": None}

    # Load and validate passport structure
    passport, error = _load_passport(passport_path)
    if error or passport is None:
        results["valid"] = False
        results["checks"]["passport_format"] = error or {
            "passed": False,
            "message": "Unknown error loading passport",
        }
        return results

    results["passport"] = passport
    results["checks"]["passport_format"] = {
        "passed": True,
        "message": "Passport loaded successfully",
    }

    # Load verification manifest if provided
    manifest = None
    git_tree = None
    input_merkle_root = None
    rustc_hash = None
    cargo_hash = None

    if manifest_path:
        try:
            manifest = load_verification_manifest(manifest_path)
            git_commit = git_commit or manifest.get("git_commit")
            git_tree = manifest.get("git_tree")
            cargo_lock_hash = cargo_lock_hash or manifest.get("cargo_lock_hash")
            input_merkle_root = manifest.get("input_merkle_root")

            # Extract toolchain hashes
            toolchain = manifest.get("toolchain", {})
            rustc_hash = toolchain.get("rustc_hash")
            cargo_hash = toolchain.get("cargo_hash")
        except ValueError as e:
            results["valid"] = False
            results["checks"]["manifest_format"] = {
                "passed": False,
                "message": str(e),
            }
            return results

    # Run optional verifications
    verifications = [
        ("git_commit", git_commit, _verify_git_commit),
        ("git_tree", git_tree, _verify_git_tree),
        ("cargo_lock", cargo_lock_hash, _verify_cargo_lock),
        ("binary_hash", binary_path, _verify_binary_hash),
    ]

    for check_name, value, verify_func in verifications:
        if value is not None:
            check_result = verify_func(passport, value, strict)
            if check_result.pop("fail", False):
                results["valid"] = False
            results["checks"][check_name] = check_result

    # Verify input merkle root from manifest if provided
    if input_merkle_root:
        check_result = _verify_input_merkle_root(passport, input_merkle_root, strict)
        if check_result.pop("fail", False):
            results["valid"] = False
        results["checks"]["input_merkle_root"] = check_result

    # Verify toolchain hashes from manifest if provided
    if rustc_hash or cargo_hash:
        check_result = _verify_toolchain_hashes(passport, rustc_hash, cargo_hash, strict)
        if check_result.pop("fail", False):
            results["valid"] = False
        results["checks"]["toolchain_hashes"] = check_result

    # Verify binary artifacts from manifest if provided
    if manifest and manifest.get("binary_artifacts"):
        for expected_artifact in manifest["binary_artifacts"]:
            artifact_name = expected_artifact.get("name")
            expected_hash = expected_artifact.get("hash")

            if not artifact_name or not expected_hash:
                continue

            # Find matching artifact in passport
            passport_artifacts = passport.get("outputs", {}).get("artifacts", [])
            matching = next(
                (a for a in passport_artifacts if a.get("name") == artifact_name), None
            )

            if matching:
                actual_hash = matching.get("hash")
                if actual_hash != expected_hash:
                    results["valid"] = False
                    results["checks"][f"artifact_{artifact_name}"] = {
                        "passed": False,
                        "message": f"Artifact '{artifact_name}' hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}...",
                    }
                else:
                    results["checks"][f"artifact_{artifact_name}"] = {
                        "passed": True,
                        "message": f"Artifact '{artifact_name}' hash matches",
                    }
            elif strict:
                results["valid"] = False
                results["checks"][f"artifact_{artifact_name}"] = {
                    "passed": False,
                    "message": f"Artifact '{artifact_name}' not found in passport",
                }

    # Always verify input merkle root
    merkle_result = _verify_input_merkle(passport, strict)
    if merkle_result.pop("fail", False):
        results["valid"] = False
    results["checks"]["input_merkle"] = merkle_result

    return results
