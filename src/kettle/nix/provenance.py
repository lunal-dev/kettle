"""Nix-specific SLSA v1.2 provenance generation and verification."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import uuid

from urllib.parse import quote

from ..merkle import MerkleTree
from ..slsa import (
    generate_slsa_statement,
    build_subject,
    build_source_descriptor,
    build_byproduct,
)
from ..utils import hash_file
from .parser import parse_flake_lock, hash_flake_lock, extract_direct_inputs
from .verification import verify_flake_inputs
from .toolchain import get_nix_toolchain_info
from .build import run_nix_build


def convert_nix_input_to_purl(dep: dict) -> dict:
    """Convert Nix flake input to SLSA ResourceDescriptor with PURL.

    Args:
        dep: Dependency dict with name, narHash, type

    Returns:
        ResourceDescriptor with PURL URI
    """
    name = dep["name"]
    nar_hash = dep.get("narHash", "")
    input_type = dep.get("type", "")

    # Build PURL: pkg:nix/name?narHash=sha256:hash&type=github
    qualifiers = []
    if nar_hash:
        qualifiers.append(f"narHash={quote(nar_hash)}")
    if input_type:
        qualifiers.append(f"type={quote(input_type)}")

    purl = f"pkg:nix/{quote(name)}"
    if qualifiers:
        purl += "?" + "&".join(qualifiers)

    descriptor = {
        "uri": purl,
        "name": name,
    }

    # Extract hash from narHash if available (format: sha256-base64)
    if nar_hash and nar_hash.startswith("sha256-"):
        # Note: narHash uses base64, not hex. Store as-is in annotations
        descriptor["annotations"] = {"narHash": nar_hash}

    return descriptor


def generate_nix_provenance(
    project_dir: Path,
    output_path: Path,
    git_source: Optional[dict] = None,
    verbose: bool = False,
) -> dict:
    """Generate SLSA v1.2 provenance for Nix build.

    This mirrors generate_provenance() but for Nix flakes.

    Args:
        project_dir: Path to project directory containing flake.nix/flake.lock
        output_path: Path where provenance.json will be written
        git_source: Optional git source info from verify_git_source_strict()
        verbose: Enable verbose output

    Returns:
        SLSA v1.2 provenance statement (in-toto format)

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

    # 6. Generate timestamps
    started_on = datetime.now(timezone.utc)
    invocation_id = f"build-{started_on.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # 7. Convert artifacts to (path, hash) tuples
    output_artifacts = [
        (artifact["path"], artifact["hash"])
        for artifact in build_result["artifacts"]
    ]

    # Build SLSA components (Nix-specific logic)

    # 1. Subject (build outputs)
    subject = build_subject(output_artifacts)

    # 2. Build type
    build_type = "https://attestable-builds.dev/kettle/nix@v1"

    # 3. External parameters (user-controlled)
    external_parameters = {
        "buildCommand": "nix build --no-link --print-out-paths"
    }
    if git_source:
        external_parameters["source"] = build_source_descriptor(git_source)

    # 4. Internal parameters (platform-controlled)
    internal_parameters = {
        "toolchain": {
            "nix": {
                "version": toolchain["nix_version"],
                "digest": {"sha256": toolchain["nix_hash"]},
            }
        },
        "lockfileHash": {"sha256": flake_lock_hash},
    }

    # 5. Resolved dependencies (convert to PURL format)
    resolved_dependencies = []
    for input_data in inputs:
        resolved_dependencies.append(convert_nix_input_to_purl(input_data))

    # 6. Builder ID
    builder_id = "https://attestable-builds.dev/kettle-tee/v1"

    # 7. Metadata
    metadata = {
        "invocationId": invocation_id,
        "startedOn": started_on.isoformat() + "Z",
    }

    # 8. Byproducts (input merkle root, git binary hash)
    byproducts = [
        build_byproduct("input_merkle_root", input_merkle_root)
    ]
    if git_source and git_source.get("git_binary_hash"):
        byproducts.append(build_byproduct("git_binary_hash", git_source["git_binary_hash"]))

    # Generate SLSA statement with generic interface
    statement = generate_slsa_statement(
        subject=subject,
        build_type=build_type,
        external_parameters=external_parameters,
        internal_parameters=internal_parameters,
        resolved_dependencies=resolved_dependencies,
        builder_id=builder_id,
        metadata=metadata,
        byproducts=byproducts,
    )

    # Add store_paths to annotations (Nix-specific)
    if build_result.get("store_paths"):
        if "annotations" not in statement["predicate"]["runDetails"]:
            statement["predicate"]["runDetails"]["annotations"] = {}
        statement["predicate"]["runDetails"]["annotations"]["nix_store_paths"] = build_result["store_paths"]

    # Write to disk
    output_path.write_text(json.dumps(statement, indent=2))

    if verbose:
        print(f"Provenance written to {output_path}")

    return statement


# Backward compatibility alias
generate_nix_passport = generate_nix_provenance


# ============================================================================
# Nix Provenance Verification Functions
# ============================================================================


def _load_provenance(provenance_path: Path) -> tuple[dict | None, dict | None]:
    """Load and validate SLSA provenance structure."""
    try:
        provenance = json.loads(provenance_path.read_text())

        # Validate SLSA v1.2 structure
        required_fields = ["_type", "subject", "predicateType", "predicate"]
        missing = [f for f in required_fields if f not in provenance]

        if missing:
            return None, {
                "verified": False,
                "message": f"Missing required SLSA fields: {', '.join(missing)}",
            }

        # Validate it's actually a SLSA provenance
        if provenance.get("predicateType") != "https://slsa.dev/provenance/v1":
            return None, {
                "verified": False,
                "message": f"Invalid predicateType: expected SLSA provenance v1",
            }

        return provenance, None
    except json.JSONDecodeError as e:
        return None, {"verified": False, "message": f"Invalid JSON: {e}"}
    except Exception as e:
        return None, {"verified": False, "message": f"Error loading provenance: {e}"}


def _verify_flake_lock(provenance: dict, expected_hash: str, strict: bool) -> dict:
    """Verify flake.lock hash matches expected value.

    Mirrors _verify_cargo_lock() but for Nix.
    """
    # Extract from SLSA structure: predicate.buildDefinition.internalParameters.lockfileHash
    lockfile = provenance.get("predicate", {}).get("buildDefinition", {}).get("internalParameters", {}).get("lockfileHash", {})
    provenance_hash = lockfile.get("sha256")

    if not provenance_hash:
        return {
            "verified": False,
            "message": "No flake.lock hash in provenance",
            "fail": strict,
        }

    if provenance_hash == expected_hash:
        return {"verified": True, "message": "flake.lock hash matches"}

    return {
        "verified": False,
        "message": "flake.lock hash mismatch",
        "fail": True,
    }


def _verify_nix_toolchain(provenance: dict, nix_hash: Optional[str], strict: bool) -> dict:
    """Verify Nix toolchain hash matches expected value.

    Mirrors _verify_toolchain_hashes() but for Nix (single binary instead of rustc+cargo).
    """
    # Extract from SLSA structure: predicate.buildDefinition.internalParameters.toolchain.nix
    toolchain = provenance.get("predicate", {}).get("buildDefinition", {}).get("internalParameters", {}).get("toolchain", {}).get("nix", {})
    provenance_nix_hash = toolchain.get("digest", {}).get("sha256")

    if not provenance_nix_hash:
        return {
            "verified": False,
            "message": "No nix binary hash in provenance",
            "fail": strict,
        }

    if nix_hash and provenance_nix_hash != nix_hash:
        return {
            "verified": False,
            "message": f"Nix binary hash mismatch",
            "fail": True,
        }

    return {
        "verified": True,
        "message": f"Nix toolchain verified: {provenance_nix_hash[:16]}...",
    }


def _verify_binary_hash(provenance: dict, binary_path: Path, strict: bool) -> dict:
    """Verify binary artifact hash matches provenance record."""
    if not binary_path.exists():
        return {
            "verified": False,
            "message": f"Binary not found: {binary_path}",
            "fail": True,
        }

    actual_hash = hash_file(binary_path)
    # Extract from SLSA structure: subject[] array
    subjects = provenance.get("subject", [])
    binary_name = binary_path.name

    # Find matching subject by name
    matching_subject = next(
        (s for s in subjects if s.get("name") == binary_name), None
    )

    if not matching_subject:
        return {
            "verified": False,
            "message": f"No artifact named '{binary_name}' in provenance",
            "fail": strict,
        }

    expected_hash = matching_subject.get("digest", {}).get("sha256")
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


def verify_nix_build_provenance(
    provenance_path: Path,
    manifest_path: Optional[Path] = None,
    git_commit: Optional[str] = None,
    flake_lock_hash: Optional[str] = None,
    binary_path: Optional[Path] = None,
    strict: bool = False,
) -> dict:
    """Verify a Nix SLSA provenance document against known values.

    Mirrors verify_build_provenance() from provenance.py but for Nix provenance.

    Args:
        provenance_path: Path to SLSA provenance JSON file
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
                "provenance_format": {"verified": bool, "message": str},
                "git_commit": {"verified": bool, "message": str},
                "flake_lock": {"verified": bool, "message": str},
                "binary_hash": {"verified": bool, "message": str},
                "input_merkle": {"verified": bool, "message": str},
            },
            "provenance": dict  # The loaded provenance data
        }
    """
    from ..provenance import _verify_git_commit, _verify_git_tree, _verify_input_merkle_root, load_verification_manifest

    results = {"valid": True, "checks": {}, "provenance": None}

    # Load and validate provenance structure
    provenance, error = _load_provenance(provenance_path)
    if error or provenance is None:
        results["valid"] = False
        results["checks"]["provenance_format"] = error or {
            "verified": False,
            "message": "Unknown error loading provenance",
        }
        return results

    results["provenance"] = provenance
    results["checks"]["provenance_format"] = {
        "verified": True,
        "message": "Provenance loaded successfully",
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

    # Run optional verifications (reuse git verification from main provenance module)
    verifications = [
        ("git_commit", git_commit, _verify_git_commit),
        ("git_tree", git_tree, _verify_git_tree),
        ("flake_lock", flake_lock_hash, _verify_flake_lock),
        ("binary_hash", binary_path, _verify_binary_hash),
    ]

    for check_name, value, verify_func in verifications:
        if value is not None:
            check_result = verify_func(provenance, value, strict)
            if check_result.pop("fail", False):
                results["valid"] = False
            results["checks"][check_name] = check_result

    # Verify input merkle root from manifest if provided
    if input_merkle_root:
        check_result = _verify_input_merkle_root(provenance, input_merkle_root, strict)
        if check_result.pop("fail", False):
            results["valid"] = False
        results["checks"]["input_merkle_root"] = check_result

    # Verify toolchain hash from manifest if provided
    if nix_hash:
        check_result = _verify_nix_toolchain(provenance, nix_hash, strict)
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

            # Find matching artifact in SLSA provenance (subject array)
            subjects = provenance.get("subject", [])
            matching = next(
                (s for s in subjects if s.get("name") == artifact_name), None
            )

            if matching:
                actual_hash = matching.get("digest", {}).get("sha256")
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
                    "message": f"Artifact '{artifact_name}' not found in provenance",
                }

    # Always verify input merkle root
    merkle_result = _verify_input_merkle(provenance, strict)
    if merkle_result.pop("fail", False):
        results["valid"] = False
    results["checks"]["input_merkle"] = merkle_result

    return results


# Backward compatibility alias
verify_nix_build_passport = verify_nix_build_provenance
