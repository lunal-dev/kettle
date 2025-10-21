"""Merkle tree calculation for input verification.

This module implements a Merkle tree to combine all build inputs into a single
cryptographic root hash. This allows efficient verification that all inputs
are accounted for without needing to hash everything individually.

Structure (enhanced from plan.md):
    Input Root Hash
    ├─── Source Code Subtree
    │    ├─── Git commit hash
    │    ├─── Git tree hash
    │    └─── Git binary hash
    ├─── Cargo.lock Hash
    ├─── Dependencies Subtree
    │    ├─── Dependency 1 (verified checksum)
    │    ├─── Dependency 2 (verified checksum)
    │    └─── ...
    └─── Toolchain Subtree
         ├─── rustc binary hash
         ├─── rustc version string
         ├─── cargo binary hash
         └─── cargo version string
"""

import hashlib


def hash_leaf(data: str) -> bytes:
    """Hash a leaf node in the Merkle tree.

    Args:
        data: String data to hash

    Returns:
        32-byte SHA256 hash
    """
    return hashlib.sha256(data.encode()).digest()


def hash_pair(left: bytes, right: bytes) -> bytes:
    """Hash two child nodes together.

    Args:
        left: Left child hash (32 bytes)
        right: Right child hash (32 bytes)

    Returns:
        32-byte SHA256 hash of concatenated children
    """
    return hashlib.sha256(left + right).digest()


def build_merkle_tree(leaves: list[bytes]) -> bytes:
    """Build Merkle tree from leaves and return root hash.

    Args:
        leaves: List of leaf hashes (each 32 bytes)

    Returns:
        Root hash (32 bytes)
    """
    if not leaves:
        return hash_leaf("")
    if len(leaves) == 1:
        return leaves[0]

    # Build tree level by level, bottom-up
    current_level = leaves[:]

    while len(current_level) > 1:
        next_level = []
        for i in range(0, len(current_level), 2):
            if i + 1 < len(current_level):
                # Pair exists - hash together
                next_level.append(hash_pair(current_level[i], current_level[i + 1]))
            else:
                # Odd number of nodes - promote last node up
                next_level.append(current_level[i])
        current_level = next_level

    return current_level[0]


def calculate_input_merkle_root(
    git_commit_hash: str | None,
    git_tree_hash: str | None,
    git_binary_hash: str | None,
    cargo_lock_hash: str,
    dependencies: list[dict],
    toolchain: dict,
) -> str:
    """Calculate Merkle root hash of all build inputs.

    This creates a single hash representing all verified inputs, making it
    easy to verify that nothing was tampered with.

    Args:
        git_commit_hash: Git commit hash (optional)
        git_tree_hash: Git tree hash (optional)
        git_binary_hash: Git binary hash (optional)
        cargo_lock_hash: SHA256 of Cargo.lock
        dependencies: List of dependency dicts with name, version, checksum
        toolchain: Dict with rustc and cargo info

    Returns:
        Hex-encoded Merkle root hash (64 chars)
    """
    leaves = []

    # 1. Source code subtree (if git info available)
    if git_commit_hash or git_tree_hash or git_binary_hash:
        source_leaves = []
        if git_commit_hash:
            source_leaves.append(hash_leaf(git_commit_hash))
        if git_tree_hash:
            source_leaves.append(hash_leaf(git_tree_hash))
        if git_binary_hash:
            source_leaves.append(hash_leaf(git_binary_hash))

        source_root = build_merkle_tree(source_leaves)
        leaves.append(source_root)

    # 2. Cargo.lock hash
    leaves.append(hash_leaf(cargo_lock_hash))

    # 3. Dependencies subtree
    # Sort for determinism
    dep_hashes = [
        hash_leaf(f"{d['name']}:{d['version']}:{d['checksum']}")
        for d in sorted(dependencies, key=lambda x: (x['name'], x['version']))
    ]
    if dep_hashes:
        deps_root = build_merkle_tree(dep_hashes)
        leaves.append(deps_root)

    # 4. Toolchain subtree
    toolchain_leaves = [
        hash_leaf(toolchain['rustc']['binary_hash']),
        hash_leaf(toolchain['rustc']['version']),
        hash_leaf(toolchain['cargo']['binary_hash']),
        hash_leaf(toolchain['cargo']['version']),
    ]
    toolchain_root = build_merkle_tree(toolchain_leaves)
    leaves.append(toolchain_root)

    # Build final tree and return hex-encoded root
    root = build_merkle_tree(leaves)
    return root.hex()
