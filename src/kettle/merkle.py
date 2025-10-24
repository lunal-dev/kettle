"""Merkle tree calculation for input verification using pymerkle."""

from pymerkle import InmemoryTree as MerkleTree


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
