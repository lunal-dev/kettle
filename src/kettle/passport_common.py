"""
Shared components for build and training passports.

This module provides common data structures and utilities used by both
BuildPassport and TrainingPassport to reduce duplication.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class MerkleVerification:
    """Merkle tree verification data."""

    root: str = ""
    tree_size: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "root": self.root,
            "tree_size": self.tree_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MerkleVerification":
        """Load from dictionary."""
        return cls(
            root=data.get("root", ""),
            tree_size=data.get("tree_size", 0),
        )


class PassportVerifier:
    """Common verification patterns for passports."""

    @staticmethod
    def verify_file_hash(
        file_path: Path, expected_hash: str, label: str
    ) -> tuple[bool, str]:
        """Verify a file hash matches expected value.

        Args:
            file_path: Path to file to verify
            expected_hash: Expected SHA256 hash
            label: Human-readable label for error messages

        Returns:
            Tuple of (success: bool, message: str)
        """
        from .utils import hash_file

        if not file_path.exists():
            return False, f"{label} not found: {file_path}"

        actual_hash = hash_file(file_path)
        if actual_hash != expected_hash:
            return (
                False,
                f"{label} hash mismatch\n"
                f"  Expected: {expected_hash}\n"
                f"  Got: {actual_hash}",
            )

        return True, f"{label} hash matches"

    @staticmethod
    def verify_directory_hash(
        dir_path: Path, expected_hash: str, label: str
    ) -> tuple[bool, str]:
        """Verify a directory hash matches expected value.

        Args:
            dir_path: Path to directory to verify
            expected_hash: Expected SHA256 hash
            label: Human-readable label for error messages

        Returns:
            Tuple of (success: bool, message: str)
        """
        from .training.inputs import hash_directory_combined

        if not dir_path.exists():
            return False, f"{label} not found: {dir_path}"

        actual_hash = hash_directory_combined(dir_path)
        if actual_hash != expected_hash:
            return (
                False,
                f"{label} hash mismatch\n"
                f"  Expected: {expected_hash}\n"
                f"  Got: {actual_hash}",
            )

        return True, f"{label} hash matches"
