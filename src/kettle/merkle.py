"""Merkle tree calculation for input verification using pymerkle."""

from pymerkle import InmemoryTree as MerkleTree
from pymerkle import verify_inclusion, MerkleProof


def calculate_input_merkle_root(
    git_commit_hash: str | None,
    git_tree_hash: str | None,
    git_binary_hash: str | None,
    cargo_lock_hash: str,
    dependencies: list[dict],
    toolchain: dict,
) -> str:
    """Calculate Merkle root hash of all build inputs using pymerkle.

    Args:
        git_commit_hash: Git commit hash (optional)
        git_tree_hash: Git tree hash (optional)
        git_binary_hash: Git binary hash (optional)
        cargo_lock_hash: SHA256 of Cargo.lock
        dependencies: List of dependency dicts with name, version, checksum
        toolchain: Dict with rustc and cargo info

    Returns:
        Hex-encoded Merkle root hash
    """
    tree = MerkleTree(algorithm='sha256')

    # Add git source info if available
    if git_commit_hash:
        tree.append_entry(git_commit_hash.encode())
    if git_tree_hash:
        tree.append_entry(git_tree_hash.encode())
    if git_binary_hash:
        tree.append_entry(git_binary_hash.encode())

    # Add Cargo.lock hash
    tree.append_entry(cargo_lock_hash.encode())

    # Add dependencies (sorted for determinism)
    for dep in sorted(dependencies, key=lambda x: (x['name'], x['version'])):
        entry = f"{dep['name']}:{dep['version']}:{dep['checksum']}"
        tree.append_entry(entry.encode())

    # Add toolchain info
    tree.append_entry(toolchain['rustc']['binary_hash'].encode())
    tree.append_entry(toolchain['rustc']['version'].encode())
    tree.append_entry(toolchain['cargo']['binary_hash'].encode())
    tree.append_entry(toolchain['cargo']['version'].encode())

    return tree.get_state().hex()


def rebuild_merkle_tree_from_passport(passport_data: dict) -> MerkleTree:
    """Rebuild the Merkle tree from passport data in the exact same order.

    Args:
        passport_data: Complete passport dictionary

    Returns:
        Reconstructed MerkleTree instance with all entries
    """
    tree = MerkleTree(algorithm='sha256')
    inputs = passport_data['inputs']

    # Add git source info if available (same order as build)
    if 'source' in inputs and inputs['source']:
        if 'commit_hash' in inputs['source'] and inputs['source']['commit_hash']:
            tree.append_entry(inputs['source']['commit_hash'].encode())
        if 'tree_hash' in inputs['source'] and inputs['source']['tree_hash']:
            tree.append_entry(inputs['source']['tree_hash'].encode())
        if 'git_binary_hash' in inputs['source'] and inputs['source']['git_binary_hash']:
            tree.append_entry(inputs['source']['git_binary_hash'].encode())

    # Add Cargo.lock hash
    tree.append_entry(inputs['cargo_lock_hash'].encode())

    # Add dependencies (sorted for determinism)
    for dep in sorted(inputs['dependencies'], key=lambda x: (x['name'], x['version'])):
        entry = f"{dep['name']}:{dep['version']}:{dep['checksum']}"
        tree.append_entry(entry.encode())

    # Add toolchain info
    tree.append_entry(inputs['toolchain']['rustc']['binary_hash'].encode())
    tree.append_entry(inputs['toolchain']['rustc']['version'].encode())
    tree.append_entry(inputs['toolchain']['cargo']['binary_hash'].encode())
    tree.append_entry(inputs['toolchain']['cargo']['version'].encode())

    return tree


def _collect_tree_entries(passport_data: dict) -> list[tuple[str, str, bytes]]:
    """Collect all tree entries in order with labels.

    Args:
        passport_data: Complete passport dictionary

    Returns:
        List of tuples: (label, value_string, value_bytes)
    """
    entries = []
    inputs = passport_data['inputs']

    # Collect git source info if available
    if 'source' in inputs and inputs['source']:
        if 'commit_hash' in inputs['source'] and inputs['source']['commit_hash']:
            val = inputs['source']['commit_hash']
            entries.append(('git_commit_hash', val, val.encode()))
        if 'tree_hash' in inputs['source'] and inputs['source']['tree_hash']:
            val = inputs['source']['tree_hash']
            entries.append(('git_tree_hash', val, val.encode()))
        if 'git_binary_hash' in inputs['source'] and inputs['source']['git_binary_hash']:
            val = inputs['source']['git_binary_hash']
            entries.append(('git_binary_hash', val, val.encode()))

    # Cargo.lock hash
    val = inputs['cargo_lock_hash']
    entries.append(('cargo_lock_hash', val, val.encode()))

    # Dependencies (sorted for determinism)
    for dep in sorted(inputs['dependencies'], key=lambda x: (x['name'], x['version'])):
        entry_str = f"{dep['name']}:{dep['version']}:{dep['checksum']}"
        label = f"dependency:{dep['name']}:{dep['version']}"
        entries.append((label, entry_str, entry_str.encode()))

    # Toolchain info
    val = inputs['toolchain']['rustc']['binary_hash']
    entries.append(('rustc_binary_hash', val, val.encode()))

    val = inputs['toolchain']['rustc']['version']
    entries.append(('rustc_version', val, val.encode()))

    val = inputs['toolchain']['cargo']['binary_hash']
    entries.append(('cargo_binary_hash', val, val.encode()))

    val = inputs['toolchain']['cargo']['version']
    entries.append(('cargo_version', val, val.encode()))

    return entries


def generate_inclusion_proofs(passport_data: dict, target_hashes: list[str]) -> dict:
    """Generate Merkle inclusion proofs for multiple hashes in the passport.

    Args:
        passport_data: Complete passport dictionary
        target_hashes: List of hash values to prove inclusion for (can be partial matches)

    Returns:
        Dict with structure:
        {
            "merkle_root": str,
            "tree_size": int,
            "proofs": [
                {
                    "target_hash": str,
                    "label": str,
                    "leaf_index": int,
                    "leaf_value": bytes (hex),
                    "proof": dict (serialized proof)
                },
                ...
            ],
            "not_found": [str, ...]  # Hashes that weren't found
        }
    """
    tree = rebuild_merkle_tree_from_passport(passport_data)
    entries = _collect_tree_entries(passport_data)

    proofs = []
    not_found = []

    for target_hash in target_hashes:
        # Find matching entry (supports partial matching)
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

        label, value_str, value_bytes = matched_entry
        # Leaf indices in pymerkle are 1-indexed
        leaf_index = matched_index + 1

        # Generate inclusion proof
        # prove_inclusion(leaf_index, tree_size) - prove leaf is in subtree of size tree_size
        tree_size = tree.get_size()
        proof = tree.prove_inclusion(leaf_index, tree_size)

        proofs.append({
            "target_hash": target_hash,
            "label": label,
            "leaf_index": leaf_index,
            "leaf_value": tree.get_leaf(leaf_index).hex(),
            "proof": proof.serialize()
        })

    return {
        "merkle_root": tree.get_state().hex(),
        "tree_size": tree.get_size(),
        "proofs": proofs,
        "not_found": not_found
    }


def verify_inclusion_proof_from_data(proof_data: dict, expected_root: bytes) -> bool:
    """Verify a single Merkle inclusion proof.

    Args:
        proof_data: Proof dictionary from generate_inclusion_proofs
        expected_root: Expected merkle root as bytes

    Returns:
        True if proof is valid, False otherwise
    """
    try:
        leaf_hash = bytes.fromhex(proof_data['leaf_value'])
        # Deserialize the proof from dictionary format
        proof = MerkleProof.deserialize(proof_data['proof'])
        # verify_inclusion returns None on success, raises exception on failure
        verify_inclusion(leaf_hash, expected_root, proof)
        return True
    except Exception as e:
        print(f"Verification error: {e}")
        return False
