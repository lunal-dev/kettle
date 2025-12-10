"""Generate SLSA v1.2 provenance for attestable builds."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import uuid

from urllib.parse import quote

from .merkle import calculate_input_merkle_root
from .slsa import (
    generate_slsa_statement,
    build_subject,
    build_source_descriptor,
    build_byproduct,
)
from .utils import hash_file


def convert_cargo_dep_to_purl(dep: dict) -> dict:
    """Convert Cargo dependency to SLSA ResourceDescriptor with PURL.

    Args:
        dep: Dependency dict with name, version, checksum

    Returns:
        ResourceDescriptor with PURL URI
    """
    name = dep["name"]
    version = dep["version"]
    checksum = dep["checksum"]

    # Build PURL: pkg:cargo/name@version?checksum=sha256:hash
    purl = f"pkg:cargo/{quote(name)}@{quote(version)}?checksum=sha256:{checksum}"

    return {
        "uri": purl,
        "digest": {"sha256": checksum},
        "name": name,
    }


def generate_provenance(
    git_source: Optional[dict],
    cargo_lock_hash: str,
    toolchain: dict,
    verification_results: Optional[list[dict]] = None,
    output_artifacts: Optional[list[tuple[Path, str]]] = None,
    output_path: Optional[Path] = None,
) -> dict:
    """Generate SLSA v1.2 provenance statement for Cargo builds.

    Args:
        git_source: Source code git information (optional)
        cargo_lock_hash: SHA256 hash of Cargo.lock
        toolchain: Rust toolchain information (rustc_hash, rustc_version, cargo_hash, cargo_version)
        verification_results: Optional results from dependency verification
        output_artifacts: Optional list of (path, hash) tuples for build outputs
        output_path: Optional path to write provenance JSON

    Returns:
        SLSA v1.2 provenance statement (in-toto format)
    """
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

    # Generate timestamps
    started_on = datetime.now(timezone.utc)
    invocation_id = f"build-{started_on.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # Build SLSA components (Cargo-specific logic)

    # 1. Subject (build outputs)
    subject = build_subject(output_artifacts or [])

    # 2. Build type
    build_type = "https://attestable-builds.dev/kettle/cargo@v1"

    # 3. External parameters (user-controlled)
    external_parameters = {
        "buildCommand": "cargo build --locked --release"
    }
    if git_source:
        external_parameters["source"] = build_source_descriptor(git_source)

    # 4. Internal parameters (platform-controlled)
    internal_parameters = {
        "toolchain": {
            "rustc": {
                "version": toolchain["rustc_version"],
                "digest": {"sha256": toolchain["rustc_hash"]},
            },
            "cargo": {
                "version": toolchain["cargo_version"],
                "digest": {"sha256": toolchain["cargo_hash"]},
            },
        },
        "lockfileHash": {"sha256": cargo_lock_hash},
    }

    # 5. Resolved dependencies (convert to PURL format)
    resolved_dependencies = []
    if verification_results:
        for result in verification_results:
            dep = result["dependency"]
            resolved_dependencies.append(convert_cargo_dep_to_purl(dep))

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

    # Write to file if requested
    if output_path:
        output_path.write_text(json.dumps(statement, indent=2))

    return statement


# Backward compatibility alias
generate_passport = generate_provenance


def _load_provenance(provenance_path: Path) -> tuple[Optional[dict], Optional[dict]]:
    """Load and validate SLSA provenance structure.

    Returns:
        Tuple of (provenance_data, error_check) where error_check is None on success.
    """
    try:
        provenance = json.loads(provenance_path.read_text())
    except Exception as e:
        return None, {"passed": False, "message": f"Failed to load provenance: {e}"}

    # Validate SLSA v1.2 structure
    required_fields = ["_type", "subject", "predicateType", "predicate"]
    missing_fields = [f for f in required_fields if f not in provenance]
    if missing_fields:
        return None, {
            "verified": False,
            "message": f"Missing required SLSA fields: {', '.join(missing_fields)}",
        }

    # Validate it's actually a SLSA provenance
    if provenance.get("predicateType") != "https://slsa.dev/provenance/v1":
        return None, {
            "verified": False,
            "message": f"Invalid predicateType: expected SLSA provenance v1",
        }

    return provenance, None


def _verify_git_commit(provenance: dict, expected_commit: str, strict: bool) -> dict:
    """Verify git commit hash matches expected value."""
    # Extract from SLSA structure: predicate.buildDefinition.externalParameters.source.digest.gitCommit
    source = provenance.get("predicate", {}).get("buildDefinition", {}).get("externalParameters", {}).get("source", {})
    provenance_commit = source.get("digest", {}).get("gitCommit")

    if not provenance_commit:
        return {
            "verified": False,
            "message": "No git commit in provenance",
            "fail": strict,
        }

    if provenance_commit == expected_commit:
        return {
            "verified": True,
            "message": f"Git commit matches: {expected_commit[:8]}...",
        }

    return {
        "verified": False,
        "message": f"Git commit mismatch: expected {expected_commit[:8]}..., got {provenance_commit[:8]}...",
        "fail": True,
    }


def _verify_git_tree(provenance: dict, expected_tree: str, strict: bool) -> dict:
    """Verify git tree hash matches expected value."""
    # Extract from SLSA structure: predicate.buildDefinition.externalParameters.source.digest.gitTree
    source = provenance.get("predicate", {}).get("buildDefinition", {}).get("externalParameters", {}).get("source", {})
    provenance_tree = source.get("digest", {}).get("gitTree")

    if not provenance_tree:
        return {
            "verified": False,
            "message": "No git tree in provenance",
            "fail": strict,
        }

    if provenance_tree == expected_tree:
        return {
            "verified": True,
            "message": f"Git tree matches: {expected_tree[:8]}...",
        }

    return {
        "verified": False,
        "message": f"Git tree mismatch: expected {expected_tree[:8]}..., got {provenance_tree[:8]}...",
        "fail": True,
    }


def _verify_input_merkle_root(provenance: dict, expected_root: str, strict: bool) -> dict:
    """Verify input merkle root matches expected value."""
    # Extract from SLSA structure: predicate.runDetails.byproducts (find input_merkle_root)
    byproducts = provenance.get("predicate", {}).get("runDetails", {}).get("byproducts", [])
    merkle_byproduct = next(
        (bp for bp in byproducts if bp.get("name") == "input_merkle_root"),
        None
    )

    provenance_root = None
    if merkle_byproduct:
        provenance_root = merkle_byproduct.get("digest", {}).get("sha256")

    if not provenance_root:
        return {
            "verified": False,
            "message": "No input merkle root in provenance",
            "fail": strict,
        }

    if provenance_root == expected_root:
        return {
            "verified": True,
            "message": f"Input merkle root matches: {expected_root[:16]}...",
        }

    return {
        "verified": False,
        "message": f"Input merkle root mismatch: expected {expected_root[:16]}..., got {provenance_root[:16]}...",
        "fail": True,
    }


def _verify_toolchain_hashes(
    provenance: dict, rustc_hash: Optional[str], cargo_hash: Optional[str], strict: bool
) -> dict:
    """Verify toolchain binary hashes match expected values."""
    # Extract from SLSA structure: predicate.buildDefinition.internalParameters.toolchain
    toolchain = provenance.get("predicate", {}).get("buildDefinition", {}).get("internalParameters", {}).get("toolchain", {})
    provenance_rustc = toolchain.get("rustc", {}).get("digest", {}).get("sha256")
    provenance_cargo = toolchain.get("cargo", {}).get("digest", {}).get("sha256")

    checks = []
    all_passed = True

    if rustc_hash:
        if not provenance_rustc:
            if strict:
                all_passed = False
            checks.append(f"rustc: not in provenance")
        elif provenance_rustc == rustc_hash:
            checks.append(f"rustc: {rustc_hash[:8]}... ✓")
        else:
            all_passed = False
            checks.append(
                f"rustc: expected {rustc_hash[:8]}..., got {provenance_rustc[:8]}..."
            )

    if cargo_hash:
        if not provenance_cargo:
            if strict:
                all_passed = False
            checks.append(f"cargo: not in provenance")
        elif provenance_cargo == cargo_hash:
            checks.append(f"cargo: {cargo_hash[:8]}... ✓")
        else:
            all_passed = False
            checks.append(
                f"cargo: expected {cargo_hash[:8]}..., got {provenance_cargo[:8]}..."
            )

    if not checks:
        return {
            "verified": False,
            "message": "No toolchain hashes provided",
            "fail": strict,
        }

    return {
        "verified": all_passed,
        "message": "; ".join(checks),
        "fail": not all_passed,
    }


def _verify_cargo_lock(provenance: dict, expected_hash: str, strict: bool) -> dict:
    """Verify Cargo.lock hash matches expected value."""
    # Extract from SLSA structure: predicate.buildDefinition.internalParameters.lockfileHash
    lockfile = provenance.get("predicate", {}).get("buildDefinition", {}).get("internalParameters", {}).get("lockfileHash", {})
    provenance_hash = lockfile.get("sha256")

    if not provenance_hash:
        return {
            "verified": False,
            "message": "No Cargo.lock hash in provenance",
            "fail": strict,
        }

    if provenance_hash == expected_hash:
        return {"verified": True, "message": "Cargo.lock hash matches"}

    return {
        "verified": False,
        "message": "Cargo.lock hash mismatch",
        "fail": True,
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


def _verify_input_merkle(provenance: dict, strict: bool) -> dict:
    """Verify input merkle root by recalculating from provenance data."""
    try:
        # Extract data from SLSA structure
        predicate = provenance.get("predicate", {})
        build_def = predicate.get("buildDefinition", {})
        run_details = predicate.get("runDetails", {})

        # External parameters
        ext_params = build_def.get("externalParameters", {})
        source = ext_params.get("source", {})
        source_digest = source.get("digest", {})

        # Internal parameters
        int_params = build_def.get("internalParameters", {})
        toolchain = int_params.get("toolchain", {})
        lockfile_hash = int_params.get("lockfileHash", {}).get("sha256")

        # Dependencies (resolvedDependencies)
        resolved_deps = build_def.get("resolvedDependencies", [])

        # Git binary hash from byproducts
        byproducts = run_details.get("byproducts", [])
        git_binary_byproduct = next(
            (bp for bp in byproducts if bp.get("name") == "git_binary_hash"),
            None
        )
        git_binary_hash = None
        if git_binary_byproduct:
            git_binary_hash = git_binary_byproduct.get("digest", {}).get("sha256")

        # Convert resolved dependencies back to the format expected by calculate_input_merkle_root
        # The PURL dependencies need to be converted back to name/version/checksum format
        dependencies = []
        for dep in resolved_deps:
            dep_digest = dep.get("digest", {}).get("sha256")
            dep_name = dep.get("name")
            # Extract version from PURL URI (pkg:cargo/name@version?checksum=...)
            uri = dep.get("uri", "")
            version = None
            if "@" in uri and "?" in uri:
                version = uri.split("@")[1].split("?")[0]

            if dep_name and version and dep_digest:
                dependencies.append({
                    "name": dep_name,
                    "version": version,
                    "checksum": dep_digest,
                })

        computed_root = calculate_input_merkle_root(
            git_commit_hash=source_digest.get("gitCommit"),
            git_tree_hash=source_digest.get("gitTree"),
            git_binary_hash=git_binary_hash,
            cargo_lock_hash=lockfile_hash,
            dependencies=dependencies,
            toolchain={
                "rustc": {
                    "binary_hash": toolchain.get("rustc", {}).get("digest", {}).get("sha256"),
                    "version": toolchain.get("rustc", {}).get("version"),
                },
                "cargo": {
                    "binary_hash": toolchain.get("cargo", {}).get("digest", {}).get("sha256"),
                    "version": toolchain.get("cargo", {}).get("version"),
                },
            },
        )

        # Get expected root from byproducts
        merkle_byproduct = next(
            (bp for bp in byproducts if bp.get("name") == "input_merkle_root"),
            None
        )
        expected_root = None
        if merkle_byproduct:
            expected_root = merkle_byproduct.get("digest", {}).get("sha256")

        if computed_root == expected_root:
            return {
                "verified": True,
                "message": f"Input merkle root verified: {computed_root[:16]}...",
            }

        expected_str = expected_root[:16] if expected_root else "None"
        return {
            "verified": False,
            "message": f"Input merkle root mismatch: expected {expected_str}..., computed {computed_root[:16]}...",
            "fail": True,
        }
    except Exception as e:
        return {
            "verified": False,
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


def verify_build_provenance(
    provenance_path: Path,
    manifest_path: Optional[Path] = None,
    git_commit: Optional[str] = None,
    cargo_lock_hash: Optional[str] = None,
    binary_path: Optional[Path] = None,
    strict: bool = False,
) -> dict:
    """Verify a SLSA provenance document against known values.

    Args:
        provenance_path: Path to SLSA provenance JSON file
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
                "provenance_format": {"verified": bool, "message": str},
                "git_commit": {"verified": bool, "message": str},
                "cargo_lock": {"verified": bool, "message": str},
                "binary_hash": {"verified": bool, "message": str},
                "input_merkle": {"verified": bool, "message": str},
            },
            "provenance": dict  # The loaded provenance data
        }
    """
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
                "verified": False,
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

    # Verify toolchain hashes from manifest if provided
    if rustc_hash or cargo_hash:
        check_result = _verify_toolchain_hashes(provenance, rustc_hash, cargo_hash, strict)
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
verify_build_passport = verify_build_provenance
