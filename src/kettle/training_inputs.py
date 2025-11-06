"""
Training input verification and hashing.

This module handles verification and cryptographic hashing of all training inputs:
- Dataset files
- Model configuration
- Training binary
- Initial weights (for fine-tuning)
"""

import hashlib
import json
from pathlib import Path
from typing import Dict

from pymerkle import InmemoryTree as MerkleTree

from .training_constants import DEFAULT_MASTER_SEED
from .utils import hash_file


def hash_directory(dir_path: Path) -> Dict[str, str]:
    """
    Recursively hash all files in a directory.

    Args:
        dir_path: Directory to hash

    Returns:
        Dictionary mapping relative paths to their hashes
    """
    file_hashes = {}

    for file_path in sorted(dir_path.rglob("*")):
        if file_path.is_file():
            relative_path = str(file_path.relative_to(dir_path))
            file_hashes[relative_path] = hash_file(file_path)

    return file_hashes


def hash_directory_combined(dir_path: Path) -> str:
    """
    Compute a single combined hash for all files in a directory.

    Args:
        dir_path: Directory to hash

    Returns:
        Combined SHA256 hash of all files
    """
    file_hashes = hash_directory(dir_path)

    # Combine hashes in deterministic order
    combined = hashlib.sha256()
    for path in sorted(file_hashes.keys()):
        combined.update(path.encode())
        combined.update(b":")
        combined.update(file_hashes[path].encode())
        combined.update(b"\n")

    return combined.hexdigest()


class TrainingInputs:
    """Container for all training inputs with hashes."""

    def __init__(
        self,
        dataset_dir: Path,
        model_config_path: Path,
        binary_passport: dict,
        master_seed: int = DEFAULT_MASTER_SEED,
    ):
        """
        Initialize training inputs.

        Args:
            dataset_dir: Path to dataset directory
            model_config_path: Path to model configuration JSON
            binary_passport: Build passport of the training binary
            master_seed: Master seed for deterministic training
        """
        self.dataset_dir = dataset_dir
        self.model_config_path = model_config_path
        self.binary_passport = binary_passport
        self.master_seed = master_seed

        # Compute hashes
        self.dataset_hash = hash_directory_combined(dataset_dir)
        self.model_config_hash = hash_file(model_config_path)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "dataset": {
                "path": str(self.dataset_dir),
                "hash": self.dataset_hash,
            },
            "model_config": {
                "path": str(self.model_config_path),
                "hash": self.model_config_hash,
            },
            "binary": {
                "passport_hash": hashlib.sha256(
                    json.dumps(self.binary_passport, sort_keys=True).encode()
                ).hexdigest(),
                "commit_hash": self.binary_passport.get("commit_hash", "unknown"),
            },
            "master_seed": self.master_seed,
        }

    def build_merkle_tree(self) -> MerkleTree:
        """
        Build a merkle tree from all training inputs.

        Returns:
            Merkle tree containing all input hashes
        """
        tree = MerkleTree(algorithm="sha256")

        # Add inputs in deterministic order
        tree.append_entry(f"binary:{self.binary_passport.get('commit_hash', 'unknown')}".encode())
        tree.append_entry(f"dataset:{self.dataset_hash}".encode())
        tree.append_entry(f"model_config:{self.model_config_hash}".encode())
        tree.append_entry(f"seed:{self.master_seed}".encode())

        return tree

    def get_merkle_root(self) -> str:
        """
        Get the merkle root hash of all training inputs.

        Returns:
            Hex-encoded merkle root
        """
        tree = self.build_merkle_tree()
        return tree.get_state().hex()

