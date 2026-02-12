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


