"""Merkle tree calculation for input verification using pymerkle."""

import json
from pathlib import Path

from pymerkle import InmemoryTree as MerkleTree
from pymerkle import verify_inclusion, MerkleProof

from kettle.logger import log, log_error, log_section, log_success, log_warning


def merkle_root(entries: list[bytes]) -> str:
    """Calculate Merkle root from ordered byte entries.

    Args:
        entries: Ordered list of byte entries for the tree

    Returns:
        Hex-encoded Merkle root hash
    """
    tree = MerkleTree(algorithm='sha256')
    for entry in entries:
        tree.append_entry(entry)
    return tree.get_state().hex()


def build_tree(entries: list[bytes]) -> MerkleTree:
    """Build Merkle tree from ordered byte entries.

    Args:
        entries: Ordered list of byte entries

    Returns:
        MerkleTree instance
    """
    tree = MerkleTree(algorithm='sha256')
    for entry in entries:
        tree.append_entry(entry)
    return tree


def generate_inclusion_proofs(
    tree: MerkleTree,
    entries: list[tuple[str, str, bytes]],
    target_hashes: list[str],
) -> dict:
    """Generate Merkle inclusion proofs for target hashes.

    Args:
        tree: Built MerkleTree instance
        entries: List of (label, value_string, value_bytes) tuples in tree order
        target_hashes: Hash values to prove inclusion for (supports partial match)

    Returns:
        Dict with merkle_root, tree_size, proofs list, and not_found list
    """
    proofs = []
    not_found = []

    for target_hash in target_hashes:
        matched_entry = None
        matched_index = None

        for idx, (label, value_str, value_bytes) in enumerate(entries):
            if target_hash in value_str or value_str == target_hash:
                matched_entry = (label, value_str, value_bytes)
                matched_index = idx
                break

        if matched_entry is None or matched_index is None:
            not_found.append(target_hash)
            continue

        label, value_str, _ = matched_entry
        leaf_index = matched_index + 1  # pymerkle is 1-indexed

        tree_size = tree.get_size()
        proof = tree.prove_inclusion(leaf_index, tree_size)

        proofs.append({
            "target_hash": target_hash,
            "label": label,
            "leaf_index": leaf_index,
            "leaf_value": tree.get_leaf(leaf_index).hex(),
            "proof": proof.serialize(),
        })

    return {
        "merkle_root": tree.get_state().hex(),
        "tree_size": tree.get_size(),
        "proofs": proofs,
        "not_found": not_found,
    }


def verify_inclusion_proof(proof_data: dict, expected_root: bytes) -> bool:
    """Verify a single Merkle inclusion proof.

    Args:
        proof_data: Proof dict from generate_inclusion_proofs
        expected_root: Expected merkle root as bytes

    Returns:
        True if valid, False otherwise
    """
    try:
        leaf_hash = bytes.fromhex(proof_data['leaf_value'])
        proof = MerkleProof.deserialize(proof_data['proof'])
        verify_inclusion(leaf_hash, expected_root, proof)
        return True
    except Exception:
        return False


def prove_inclusion(
    entries: list[tuple[str, str, bytes]],
    target_hashes: list[str],
    output: Path | None = None,
) -> dict:
    """Generate and verify Merkle inclusion proofs.

    Args:
        entries: List of (label, value_string, value_bytes) tuples
        target_hashes: Hash values to prove inclusion for
        output: Optional path to save proofs JSON

    Returns:
        Result dict with proofs and verification status
    """
    log_section("Merkle Inclusion Proof")

    # Build tree
    tree = build_tree([e[2] for e in entries])
    log(f"\\nTree built with {len(entries)} entries")

    # Generate proofs
    result = generate_inclusion_proofs(tree, entries, target_hashes)

    log(f"Merkle Root: {result['merkle_root']}", style="bold")
    log(f"Tree Size: {result['tree_size']} leaves\\n")

    if result['proofs']:
        log_success(f"Generated {len(result['proofs'])} proof(s):")
        for proof in result['proofs']:
            log(f"\\n  Target: {proof['target_hash']}", style="bold")
            log(f"  Label: {proof['label']}", style="dim")
            log(f"  Leaf Index: {proof['leaf_index']}", style="dim")

    if result['not_found']:
        log("\\n")
        log_warning(f"{len(result['not_found'])} hash(es) not found:")
        for missing in result['not_found']:
            log(f"  - {missing}", style="dim")

    # Verify proofs
    if result['proofs']:
        log("\\n")
        log_section("Verifying Proofs")

        merkle_root_bytes = bytes.fromhex(result['merkle_root'])
        all_valid = True

        for i, proof in enumerate(result['proofs'], 1):
            log(f"\\n[{i}/{len(result['proofs'])}] {proof['label']}", style="bold")
            is_valid = verify_inclusion_proof(proof, merkle_root_bytes)

            if is_valid:
                log_success("  Proof VALID")
            else:
                log_error("  Proof INVALID")
                all_valid = False

        log("\\n")
        if all_valid:
            log_success(f"All {len(result['proofs'])} proof(s) verified")
        else:
            log_error("Some proofs failed verification")

        result['all_valid'] = all_valid

    # Save if requested
    if output and result['proofs']:
        output.write_text(json.dumps(result, indent=2))
        log_success(f"Proofs saved to: {output}")

    return result


def prove_inclusion_from_provenance(
    provenance_path: Path,
    target_hashes: list[str],
    output: Path | None = None,
) -> dict:
    """Generate and verify Merkle inclusion proofs from a provenance file.

    CLI-friendly wrapper that loads provenance, detects toolchain, and
    generates proofs for the given hashes.

    Args:
        provenance_path: Path to SLSA provenance JSON file
        target_hashes: Hash values to prove inclusion for
        output: Optional path to save proofs JSON

    Returns:
        Result dict with proofs and verification status
    """
    from kettle.core import from_build_type
    from kettle.git import get_git_info

    # Load provenance
    provenance = json.loads(provenance_path.read_text())

    # Get toolchain from buildType
    build_type = provenance.get("predicate", {}).get("buildDefinition", {}).get("buildType", "")
    toolchain = from_build_type(build_type)

    if not toolchain:
        log_error(f"Unknown build type: {build_type}")
        return {"error": "Unknown build type", "proofs": [], "not_found": target_hashes}

    # Extract info from provenance to rebuild entries
    internal_params = provenance.get("predicate", {}).get("buildDefinition", {}).get("internalParameters", {})
    lock_hash = internal_params.get("lockfileHash", {}).get("sha256", "")

    # Get dependencies from provenance
    deps = provenance.get("predicate", {}).get("buildDefinition", {}).get("resolvedDependencies", [])

    # Build lock dict from provenance
    lock = {
        "hash": lock_hash,
        "deps": [{"name": d.get("name"), "uri": d.get("uri")} for d in deps],
    }

    # Get git info from provenance
    source = provenance.get("predicate", {}).get("buildDefinition", {}).get("externalParameters", {}).get("source", {})
    git = None
    if source.get("digest", {}).get("gitCommit"):
        git = {
            "commit_hash": source["digest"].get("gitCommit"),
            "tree_hash": source["digest"].get("gitTree"),
        }

    # Get toolchain info from provenance
    info = internal_params.get("toolchain", {})

    # Build labeled entries
    entries = toolchain.merkle_entries_labeled(git, lock, info)

    return prove_inclusion(entries, target_hashes, output)
