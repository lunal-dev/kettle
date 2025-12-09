"""Nix-specific passport generation and verification."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..merkle import MerkleTree
from ..utils import hash_file
from .parser import parse_flake_lock, hash_flake_lock, extract_direct_inputs
from .verification import verify_flake_inputs
from .toolchain import get_nix_toolchain_info
from .build import run_nix_build
from ..logger import log, log_error, log_section, log_success, log_warning


def generate_nix_passport(
    project_dir: Path,
    output_path: Path,
    git_source: Optional[dict] = None,
    verbose: bool = False,
) -> dict:
    """Generate passport for Nix build.

    This mirrors generate_passport() but for Nix flakes.

    Args:
        project_dir: Path to project directory containing flake.nix/flake.lock
        output_path: Path where passport.json will be written
        git_source: Optional git source info from verify_git_source_strict()
        verbose: Enable verbose output

    Returns:
        Dict containing the generated passport data

    Raises:
        RuntimeError: If Nix build fails
        FileNotFoundError: If flake.lock not found
    """

    # 1. Parse flake.lock
    flake_lock_path = project_dir / "flake.lock"
    flake_lock_hash = hash_flake_lock(flake_lock_path)
    flake_data = parse_flake_lock(flake_lock_path)
    inputs = extract_direct_inputs(flake_data)

    if verbose:
        print(f"Parsed {len(inputs)} direct flake inputs")

    # 2. Verify inputs
    verification_results = verify_flake_inputs(inputs)
    verified_count = sum(1 for v in verification_results if v["verified"])

    if verbose:
        print(f"Verified {verified_count}/{len(inputs)} inputs")

    # 3. Get toolchain info
    toolchain = get_nix_toolchain_info()

    if verbose:
        print(f"Nix version: {toolchain['nix_version']}")

    # 4. Execute build
    build_result = run_nix_build(project_dir)

    if not build_result["success"]:
        raise RuntimeError(f"Nix build failed:\n{build_result['stderr']}")

    if verbose:
        print(f"Built {len(build_result['artifacts'])} artifacts")

    # 5. Calculate input merkle root
    merkle_tree = MerkleTree(algorithm='sha256')

    # Add git info if present
    if git_source:
        merkle_tree.append_entry(git_source["commit_hash"].encode())
        merkle_tree.append_entry(git_source["tree_hash"].encode())
        merkle_tree.append_entry(git_source["git_binary_hash"].encode())

    # Add flake.lock hash
    merkle_tree.append_entry(flake_lock_hash.encode())

    # Add input narHashes (sorted by name for determinism)
    for input_data in sorted(inputs, key=lambda x: x["name"]):
        if input_data.get("narHash"):
            merkle_tree.append_entry(input_data["narHash"].encode())

    # Add nix toolchain
    merkle_tree.append_entry(toolchain["nix_hash"].encode())
    merkle_tree.append_entry(toolchain["nix_version"].encode())

    input_merkle_root = merkle_tree.get_state().hex()

    log("building passport structure...")
    # 6. Build passport structure
    passport = {
        "version": "1.0",
        "build_system": "nix",
        "inputs": {
            "flake_lock_hash": flake_lock_hash,
            "toolchain": {
                "nix": {
                    "binary_hash": toolchain["nix_hash"],
                    "version": toolchain["nix_version"],
                    "path": toolchain["nix_path"],
                }
            },
            "flake_inputs": [
                {
                    "name": v["input"]["name"],
                    "narHash": v["input"].get("narHash"),
                    "type": v["input"].get("type"),
                    "verified": v["verified"],
                }
                for v in verification_results
            ],
            "input_merkle_root": input_merkle_root,
        },
        "build_process": {
            "command": "nix build --no-link --print-out-paths",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "outputs": {
            "artifacts": build_result["artifacts"],
            "store_paths": build_result["store_paths"],
        },
    }



    log(f"built passport :{passport}")

    # Add git source if present
    if git_source:
        passport["inputs"]["source"] = git_source

    # Write to disk
    output_path.write_text(json.dumps(passport, indent=2))

    if verbose:
        print(f"Passport written to {output_path}")

    return passport


# ============================================================================
# Nix Passport Verification Functions
# ============================================================================


def _load_passport(passport_path: Path) -> tuple[dict | None, dict | None]:
    """Load and validate basic passport structure."""
    try:
        passport = json.loads(passport_path.read_text())
        required_fields = ["version", "inputs", "outputs"]
        missing = [f for f in required_fields if f not in passport]

        if missing:
            return None, {
                "verified": False,
                "message": f"Missing required fields: {', '.join(missing)}",
            }

        return passport, None
    except json.JSONDecodeError as e:
        return None, {"verified": False, "message": f"Invalid JSON: {e}"}
    except Exception as e:
        return None, {"verified": False, "message": f"Error loading passport: {e}"}


def _verify_flake_lock(passport: dict, expected_hash: str, strict: bool) -> dict:
    """Verify flake.lock hash matches expected value.

    Mirrors _verify_cargo_lock() but for Nix.
    """
    passport_hash = passport.get("inputs", {}).get("flake_lock_hash")

    if not passport_hash:
        return {
            "verified": False,
            "message": "No flake.lock hash in passport",
            "fail": strict,
        }

    if passport_hash == expected_hash:
        return {"verified": True, "message": "flake.lock hash matches"}

    return {
        "verified": False,
        "message": "flake.lock hash mismatch",
        "fail": True,
    }


def _verify_nix_toolchain(passport: dict, nix_hash: Optional[str], strict: bool) -> dict:
    """Verify Nix toolchain hash matches expected value.

    Mirrors _verify_toolchain_hashes() but for Nix (single binary instead of rustc+cargo).
    """
    toolchain = passport.get("inputs", {}).get("toolchain", {}).get("nix", {})
    passport_nix_hash = toolchain.get("binary_hash")

    if not passport_nix_hash:
        return {
            "verified": False,
            "message": "No nix binary hash in passport",
            "fail": strict,
        }

    if nix_hash and passport_nix_hash != nix_hash:
        return {
            "verified": False,
            "message": f"Nix binary hash mismatch",
            "fail": True,
        }

    return {
        "verified": True,
        "message": f"Nix toolchain verified: {passport_nix_hash[:16]}...",
    }


def _verify_binary_hash(passport: dict, binary_path: Path, strict: bool) -> dict:
    """Verify binary artifact hash matches passport record."""
    if not binary_path.exists():
        return {
            "verified": False,
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
            "verified": False,
            "message": f"No artifact named '{binary_name}' in passport",
            "fail": strict,
        }

    expected_hash = matching_artifact.get("hash")
    if actual_hash == expected_hash:
        return {
            "verified": True,
            "message": f"Binary hash matches: {actual_hash[:16]}...",
        }

    return {
        "verified": False,
        "message": f"Binary hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}...",
        "fail": True,
    }


def _verify_input_merkle(passport: dict, strict: bool) -> dict:
    """Verify input merkle root can be recalculated from inputs.

    Mirrors the Cargo version but uses Nix-specific fields.
    """
    recorded_root = passport.get("inputs", {}).get("input_merkle_root")
    if not recorded_root:
        return {
            "verified": False,
            "message": "No input_merkle_root in passport",
            "fail": strict,
        }

    # Recalculate merkle root
    merkle_tree = MerkleTree(algorithm='sha256')
    inputs_data = passport.get("inputs", {})

    # Add git info if present
    git_source = inputs_data.get("source")
    if git_source:
        merkle_tree.append_entry(git_source["commit_hash"].encode())
        merkle_tree.append_entry(git_source["tree_hash"].encode())
        merkle_tree.append_entry(git_source["git_binary_hash"].encode())

    # Add flake.lock hash
    flake_lock_hash = inputs_data.get("flake_lock_hash")
    if flake_lock_hash:
        merkle_tree.append_entry(flake_lock_hash.encode())

    # Add flake input narHashes (sorted by name)
    flake_inputs = inputs_data.get("flake_inputs", [])
    for input_data in sorted(flake_inputs, key=lambda x: x.get("name", "")):
        narHash = input_data.get("narHash")
        if narHash:
            merkle_tree.append_entry(narHash.encode())

    # Add nix toolchain
    toolchain = inputs_data.get("toolchain", {}).get("nix", {})
    if toolchain.get("binary_hash"):
        merkle_tree.append_entry(toolchain["binary_hash"].encode())
    if toolchain.get("version"):
        merkle_tree.append_entry(toolchain["version"].encode())

    recalculated_root = merkle_tree.get_state().hex()

    if recalculated_root == recorded_root:
        return {
            "verified": True,
            "message": f"Input merkle root verified: {recorded_root[:16]}...",
        }

    return {
        "verified": False,
        "message": f"Input merkle root mismatch: expected {recorded_root[:16]}..., got {recalculated_root[:16]}...",
        "fail": True,
    }


def verify_nix_build_passport(
    passport_path: Path,
    manifest_path: Optional[Path] = None,
    git_commit: Optional[str] = None,
    flake_lock_hash: Optional[str] = None,
    binary_path: Optional[Path] = None,
    strict: bool = False,
) -> dict:
    """Verify a Nix passport document against known values.

    Mirrors verify_build_passport() from passport.py but for Nix passports.

    Args:
        passport_path: Path to passport JSON file
        manifest_path: Path to verification manifest JSON (optional)
        git_commit: Expected git commit hash (optional)
        flake_lock_hash: Expected flake.lock hash (optional)
        binary_path: Path to binary to verify hash (optional)
        strict: If True, fail if any optional check cannot be performed

    Returns:
        Dictionary with verification results:
        {
            "valid": bool,
            "checks": {
                "passport_format": {"verified": bool, "message": str},
                "git_commit": {"verified": bool, "message": str},
                "flake_lock": {"verified": bool, "message": str},
                "binary_hash": {"verified": bool, "message": str},
                "input_merkle": {"verified": bool, "message": str},
            },
            "passport": dict  # The loaded passport data
        }
    """
    from ..passport import _verify_git_commit, _verify_git_tree, _verify_input_merkle_root, load_verification_manifest

    results = {"valid": True, "checks": {}, "passport": None}

    # Load and validate passport structure
    passport, error = _load_passport(passport_path)
    if error or passport is None:
        results["valid"] = False
        results["checks"]["passport_format"] = error or {
            "verified": False,
            "message": "Unknown error loading passport",
        }
        return results

    results["passport"] = passport
    results["checks"]["passport_format"] = {
        "verified": True,
        "message": "Passport loaded successfully",
    }

    # Load verification manifest if provided
    manifest = None
    git_tree = None
    input_merkle_root = None
    nix_hash = None

    if manifest_path:
        try:
            manifest = load_verification_manifest(manifest_path)
            git_commit = git_commit or manifest.get("git_commit")
            git_tree = manifest.get("git_tree")
            flake_lock_hash = flake_lock_hash or manifest.get("flake_lock_hash")
            input_merkle_root = manifest.get("input_merkle_root")

            # Extract toolchain hash
            toolchain = manifest.get("toolchain", {})
            nix_hash = toolchain.get("nix_hash")
        except ValueError as e:
            results["valid"] = False
            results["checks"]["manifest_format"] = {
                "verified": False,
                "message": str(e),
            }
            return results

    # Run optional verifications (reuse git verification from main passport module)
    verifications = [
        ("git_commit", git_commit, _verify_git_commit),
        ("git_tree", git_tree, _verify_git_tree),
        ("flake_lock", flake_lock_hash, _verify_flake_lock),
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

    # Verify toolchain hash from manifest if provided
    if nix_hash:
        check_result = _verify_nix_toolchain(passport, nix_hash, strict)
        if check_result.pop("fail", False):
            results["valid"] = False
        results["checks"]["toolchain_hash"] = check_result

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
                        "verified": False,
                        "message": f"Artifact '{artifact_name}' hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}...",
                    }
                else:
                    results["checks"][f"artifact_{artifact_name}"] = {
                        "verified": True,
                        "message": f"Artifact '{artifact_name}' hash matches",
                    }
            elif strict:
                results["valid"] = False
                results["checks"][f"artifact_{artifact_name}"] = {
                    "verified": False,
                    "message": f"Artifact '{artifact_name}' not found in passport",
                }

    # Always verify input merkle root
    merkle_result = _verify_input_merkle(passport, strict)
    if merkle_result.pop("fail", False):
        results["valid"] = False
    results["checks"]["input_merkle"] = merkle_result

    return results
