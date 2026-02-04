"""Generate and verify SLSA v1.2 provenance for attestable builds."""

import json
from datetime import datetime, timezone
from pathlib import Path
import uuid

from kettle.core import Toolchain, from_build_type
from kettle.merkle import merkle_root
from .slsa import (
    generate_slsa_statement,
    build_subject,
    build_source_descriptor,
    build_byproduct,
)
from kettle.utils import hash_file


def generate(
    toolchain: Toolchain,
    git: dict | None,
    lock: dict,
    info: dict,
    artifacts: list[dict],
    output_path: Path | None = None,
) -> dict:
    """Generate SLSA v1.2 provenance statement.

    Args:
        toolchain: Toolchain instance (CargoToolchain, NixToolchain, etc.)
        git: Git source info dict or None
        lock: Lockfile info from toolchain.parse_lockfile()
        info: Toolchain info from toolchain.get_info()
        artifacts: Build artifacts list [{"path": str, "hash": str, "name": str}, ...]
        output_path: Optional path to write provenance JSON

    Returns:
        SLSA v1.2 provenance statement (in-toto format)
    """
    # Calculate input merkle root
    entries = toolchain.merkle_entries(git, lock, info)
    input_merkle = merkle_root(entries)

    # Timestamps
    started_on = datetime.now(timezone.utc)
    invocation_id = f"build-{started_on.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # Subject (build outputs)
    output_tuples = [(a["path"], a["hash"]) for a in artifacts]
    subject = build_subject(output_tuples)

    # External parameters
    external_params = {"buildCommand": f"{toolchain.name} build"}
    if git:
        external_params["source"] = build_source_descriptor(git)

    # Internal parameters (toolchain-specific, pass full lock for metadata)
    internal_params = toolchain.internal_params(info, lock["hash"], lock)

    # Resolved dependencies: use fetches if available (deep mode), otherwise deps (shallow)
    if lock.get("fetches"):
        resolved_deps = [toolchain.dep_to_purl(fetch) for fetch in lock["fetches"]]
    else:
        resolved_deps = [toolchain.dep_to_purl(dep) for dep in lock["deps"]]

    # Byproducts (only the input merkle root - build artifacts are in subject)
    byproducts = [build_byproduct("input_merkle_root", input_merkle)]

    # Generate statement
    statement = generate_slsa_statement(
        subject=subject,
        build_type=toolchain.build_type_uri,
        external_parameters=external_params,
        internal_parameters=internal_params,
        resolved_dependencies=resolved_deps,
        builder_id="https://attestable-builds.dev/kettle-tee/v1",
        metadata={
            "invocationId": invocation_id,
            "startedOn": started_on.isoformat() + "Z",
        },
        byproducts=byproducts,
    )

    if output_path:
        output_path.write_text(json.dumps(statement, indent=2))

    return statement


def verify(
    provenance_path: Path,
    project_dir: Path | None = None,
    binary_path: Path | None = None,
    strict: bool = False,
) -> dict:
    """Verify SLSA provenance document.

    Args:
        provenance_path: Path to provenance JSON file
        project_dir: Optional project directory to verify against current state
        binary_path: Optional binary path to verify hash
        strict: Fail if optional checks cannot be performed

    Returns:
        {"valid": bool, "checks": dict, "provenance": dict}
    """
    results = {"valid": True, "checks": {}, "provenance": None}

    # Load provenance
    provenance, error = _load_provenance(provenance_path)
    if error:
        results["valid"] = False
        results["checks"]["provenance_format"] = error
        return results

    results["provenance"] = provenance
    results["checks"]["provenance_format"] = {"verified": True, "message": "Valid SLSA v1.2"}

    # Get toolchain from build type
    build_type = provenance.get("predicate", {}).get("buildDefinition", {}).get("buildType", "")
    toolchain = from_build_type(build_type)

    if not toolchain:
        results["checks"]["build_type"] = {
            "verified": False,
            "message": f"Unknown build type: {build_type}",
        }
        if strict:
            results["valid"] = False
        return results

    results["checks"]["build_type"] = {
        "verified": True,
        "message": f"Toolchain: {toolchain.name}",
    }

    # Verify binary hash if provided
    if binary_path:
        check = _verify_binary(provenance, binary_path)
        if not check["verified"]:
            results["valid"] = False
        results["checks"]["binary_hash"] = check

    # Verify against current project state if provided
    if project_dir:
        # Git verification
        git_check = _verify_git(provenance, project_dir, strict)
        if git_check:
            if not git_check.get("verified", True):
                results["valid"] = False
            results["checks"]["git_source"] = git_check

        # Lockfile verification
        lock_check = _verify_lockfile(provenance, toolchain, project_dir)
        if not lock_check["verified"]:
            results["valid"] = False
        results["checks"]["lockfile"] = lock_check

        # Merkle root verification (recalculate from current state)
        merkle_check = _verify_merkle_recalc(provenance, toolchain, project_dir)
        if not merkle_check["verified"]:
            results["valid"] = False
        results["checks"]["input_merkle"] = merkle_check
    else:
        # Without project_dir, just check merkle root exists
        merkle_check = _verify_merkle_exists(provenance)
        results["checks"]["input_merkle"] = merkle_check

    return results


# --- Private helpers ---

def _load_provenance(path: Path) -> tuple[dict | None, dict | None]:
    """Load and validate SLSA provenance structure."""
    try:
        provenance = json.loads(path.read_text())
    except Exception as e:
        return None, {"verified": False, "message": f"Failed to load: {e}"}

    required = ["_type", "subject", "predicateType", "predicate"]
    missing = [f for f in required if f not in provenance]
    if missing:
        return None, {"verified": False, "message": f"Missing fields: {', '.join(missing)}"}

    if provenance.get("predicateType") != "https://slsa.dev/provenance/v1":
        return None, {"verified": False, "message": "Invalid predicateType"}

    return provenance, None


def _verify_binary(provenance: dict, binary_path: Path) -> dict:
    """Verify binary hash against provenance subject."""
    if not binary_path.exists():
        return {"verified": False, "message": f"Binary not found: {binary_path}"}

    actual_hash = hash_file(binary_path)
    subjects = provenance.get("subject", [])
    binary_name = binary_path.name

    matching = next((s for s in subjects if s.get("name") == binary_name), None)
    if not matching:
        return {"verified": False, "message": f"'{binary_name}' not in provenance"}

    expected = matching.get("digest", {}).get("sha256")
    if actual_hash == expected:
        return {"verified": True, "message": f"Hash matches: {actual_hash[:16]}..."}

    return {"verified": False, "message": f"Hash mismatch: {actual_hash[:16]}... vs {expected[:16]}..."}


def _verify_git(provenance: dict, project_dir: Path, strict: bool) -> dict | None:
    """Verify git source against provenance."""
    from kettle.git import get_git_info

    try:
        git = get_git_info(project_dir)
    except Exception:
        if strict:
            return {"verified": False, "message": "Failed to get git info"}
        return None

    source = provenance.get("predicate", {}).get("buildDefinition", {}).get("externalParameters", {}).get("source", {})
    digest = source.get("digest", {})

    prov_commit = digest.get("gitCommit")
    prov_tree = digest.get("gitTree")

    if not prov_commit:
        return {"verified": False, "message": "No git commit in provenance"} if strict else None

    if git["commit_hash"] != prov_commit:
        return {"verified": False, "message": f"Commit mismatch: {git['commit_hash'][:8]}... vs {prov_commit[:8]}..."}

    if prov_tree and git["tree_hash"] != prov_tree:
        return {"verified": False, "message": "Tree hash mismatch"}

    return {"verified": True, "message": f"Git matches: {prov_commit[:8]}..."}


def _verify_lockfile(provenance: dict, toolchain: Toolchain, project_dir: Path) -> dict:
    """Verify lockfile hash against provenance."""
    try:
        lock = toolchain.parse_lockfile(project_dir)
    except Exception as e:
        return {"verified": False, "message": f"Failed to parse lockfile: {e}"}

    prov_hash = provenance.get("predicate", {}).get("buildDefinition", {}).get("internalParameters", {}).get("lockfileHash", {}).get("sha256")

    if not prov_hash:
        return {"verified": False, "message": "No lockfile hash in provenance"}

    if lock["hash"] == prov_hash:
        return {"verified": True, "message": "Lockfile hash matches"}

    return {"verified": False, "message": f"Lockfile mismatch: {lock['hash'][:16]}... vs {prov_hash[:16]}..."}


def _verify_merkle_recalc(provenance: dict, toolchain: Toolchain, project_dir: Path) -> dict:
    """Verify input merkle root by recalculating from current project state."""
    try:
        from kettle.git import get_git_info

        # Get expected root from byproducts
        byproducts = provenance.get("predicate", {}).get("runDetails", {}).get("byproducts", [])
        merkle_bp = next((bp for bp in byproducts if bp.get("name") == "input_merkle_root"), None)
        expected = merkle_bp.get("digest", {}).get("sha256") if merkle_bp else None

        if not expected:
            return {"verified": False, "message": "No merkle root in provenance"}

        # Detect evaluation mode from provenance to use same mode for verification
        internal_params = provenance.get("predicate", {}).get("buildDefinition", {}).get("internalParameters", {})
        evaluation_mode = internal_params.get("evaluation", {}).get("mode", "shallow")
        deep = evaluation_mode == "deep"

        # Recalculate from current state
        try:
            git = get_git_info(project_dir)
        except Exception:
            git = None

        # Parse lockfile with same mode as original build
        # Handle toolchains that don't support the deep parameter
        try:
            lock = toolchain.parse_lockfile(project_dir, deep=deep)
        except TypeError:
            # Fallback for toolchains that don't accept deep parameter
            lock = toolchain.parse_lockfile(project_dir)

        info = toolchain.get_info()
        entries = toolchain.merkle_entries(git, lock, info)
        computed = merkle_root(entries)

        if computed == expected:
            mode_info = f" (mode={evaluation_mode})" if evaluation_mode == "deep" else ""
            return {"verified": True, "message": f"Merkle root verified: {computed[:16]}...{mode_info}"}

        return {"verified": False, "message": f"Merkle mismatch: {computed[:16]}... vs {expected[:16]}..."}

    except Exception as e:
        return {"verified": False, "message": f"Merkle verification failed: {e}"}


def _verify_merkle_exists(provenance: dict) -> dict:
    """Check that merkle root exists in provenance."""
    byproducts = provenance.get("predicate", {}).get("runDetails", {}).get("byproducts", [])
    merkle_bp = next((bp for bp in byproducts if bp.get("name") == "input_merkle_root"), None)
    expected = merkle_bp.get("digest", {}).get("sha256") if merkle_bp else None

    if expected:
        return {"verified": True, "message": f"Merkle root present: {expected[:16]}..."}
    return {"verified": False, "message": "No merkle root in provenance"}
