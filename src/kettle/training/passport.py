"""
Training passport schema and generation.

This module defines the training passport format that extends the build passport
to include training-specific information and verification data.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..passport_common import MerkleVerification
from .constants import DEFAULT_MASTER_SEED, FINAL_CHECKPOINT_FILENAME


def _get_candle_version() -> str:
    """Read Candle version from Cargo.toml."""
    cargo_toml_path = Path(__file__).parent / "candle" / "Cargo.toml"
    if not cargo_toml_path.exists():
        return "unknown"

    content = cargo_toml_path.read_text()
    for line in content.splitlines():
        if line.strip().startswith("candle-core"):
            # Extract version like: candle-core = "0.9.1"
            parts = line.split('"')
            if len(parts) >= 2:
                return parts[1]
    return "unknown"


@dataclass
class TrainingPassport:
    """
    Complete training passport for attestable ML training.

    This passport provides a complete record of:
    - The training binary and its build passport
    - All training inputs (dataset, config, weights)
    - Training outputs (checkpoints, final model)
    - Training metrics and metadata
    - Merkle tree verification data
    """

    version: str = "1.0"

    # Binary information
    binary_build_passport_hash: str = ""
    binary_commit_hash: str = ""
    candle_version: str = ""

    # Training inputs
    dataset_hash: str = ""
    dataset_path: str = ""
    model_config_hash: str = ""
    model_config_path: str = ""
    master_seed: int = DEFAULT_MASTER_SEED

    # Training outputs
    final_weights_hash: str = ""
    final_weights_path: str = ""

    # Training metrics
    total_epochs: int = 0
    final_train_loss: float = 0.0

    # Deterministic proof
    deterministic_backend: str = "cpu"
    single_threaded: bool = True

    # Shared components
    merkle_verification: MerkleVerification = field(default_factory=MerkleVerification)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "version": self.version,
            "inputs": {
                "binary": {
                    "build_passport_hash": self.binary_build_passport_hash,
                    "commit_hash": self.binary_commit_hash,
                    "candle_version": self.candle_version,
                },
                "dataset": {
                    "path": self.dataset_path,
                    "hash": self.dataset_hash,
                },
                "model_config": {
                    "path": self.model_config_path,
                    "hash": self.model_config_hash,
                },
                "master_seed": self.master_seed,
            },
            "process": {
                "deterministic_proof": {
                    "backend": self.deterministic_backend,
                    "single_threaded": self.single_threaded,
                    "seed": self.master_seed,
                },
                "metrics": {
                    "total_epochs": self.total_epochs,
                    "final_train_loss": self.final_train_loss,
                },
            },
            "outputs": {
                "final_weights": {
                    "path": self.final_weights_path,
                    "hash": self.final_weights_hash,
                },
            },
            "merkle_verification": self.merkle_verification.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainingPassport":
        """Load from dictionary."""
        inputs = data.get("inputs", {})
        process = data.get("process", {})
        outputs = data.get("outputs", {})

        # Load shared components
        merkle_verification = MerkleVerification.from_dict(data.get("merkle_verification", {}))

        binary = inputs.get("binary", {})
        dataset = inputs.get("dataset", {})
        config = inputs.get("model_config", {})
        final_weights = outputs.get("final_weights", {})

        deterministic = process.get("deterministic_proof", {})
        metrics = process.get("metrics", {})

        return cls(
            version=data.get("version", "1.0"),
            binary_build_passport_hash=binary.get("build_passport_hash", ""),
            binary_commit_hash=binary.get("commit_hash", ""),
            candle_version=binary.get("candle_version", _get_candle_version()),
            dataset_hash=dataset.get("hash", ""),
            dataset_path=dataset.get("path", ""),
            model_config_hash=config.get("hash", ""),
            model_config_path=config.get("path", ""),
            master_seed=inputs.get("master_seed", DEFAULT_MASTER_SEED),
            final_weights_hash=final_weights.get("hash", ""),
            final_weights_path=final_weights.get("path", ""),
            total_epochs=metrics.get("total_epochs", 0),
            final_train_loss=metrics.get("final_train_loss", 0.0),
            deterministic_backend=deterministic.get("backend", "cpu"),
            single_threaded=deterministic.get("single_threaded", True),
            merkle_verification=merkle_verification,
        )

    def save(self, path: Path) -> None:
        """Save passport to JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "TrainingPassport":
        """Load passport from JSON file."""
        with open(path, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)


def create_training_passport(
    binary_passport: dict,
    training_inputs: "TrainingInputs",  # type: ignore
    training_result: dict,
    merkle_root: str,
    output_dir: Path,
) -> TrainingPassport:
    """
    Create a training passport from training results.

    Args:
        binary_passport: Build passport of the training binary
        training_inputs: Verified training inputs
        training_result: Results from training (metadata JSON)
        merkle_root: Merkle root of all inputs
        output_dir: Directory containing training outputs

    Returns:
        Complete training passport
    """
    import hashlib

    # Hash binary passport
    passport_hash = hashlib.sha256(
        json.dumps(binary_passport, sort_keys=True).encode()
    ).hexdigest()

    # Get final weights hash from training_result (already computed in training.py)
    final_weights_path = output_dir / FINAL_CHECKPOINT_FILENAME
    final_weights_hash = training_result.get("final_checkpoint_hash", "")

    # Get Candle version from Cargo.toml
    candle_version = _get_candle_version()

    passport = TrainingPassport(
        binary_build_passport_hash=passport_hash,
        binary_commit_hash=binary_passport.get("commit_hash", "unknown"),
        candle_version=candle_version,
        dataset_hash=training_inputs.dataset_hash,
        dataset_path=str(training_inputs.dataset_dir),
        model_config_hash=training_inputs.model_config_hash,
        model_config_path=str(training_inputs.model_config_path),
        master_seed=training_inputs.master_seed,
        final_weights_hash=final_weights_hash,
        final_weights_path=str(final_weights_path),
        total_epochs=training_result.get("total_epochs", 0),
        final_train_loss=training_result.get("final_train_loss", 0.0),
        merkle_verification=MerkleVerification(
            root=merkle_root,
            tree_size=4,  # binary, dataset, config, seed
        ),
    )

    return passport
