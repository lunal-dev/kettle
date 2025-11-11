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


@dataclass
class TrainingPassport:
    """
    Complete training passport for attestable ML training.

    This passport provides a complete record of:
    - The training binary and its build passport (full Phase 1 passport)
    - All training inputs (dataset, config, weights)
    - Training outputs (checkpoints, final model)
    - Training metrics and metadata
    - Merkle tree verification data

    Training is "an extra step after build" - the full build passport is composed
    into the training passport to provide complete provenance.
    """

    version: str = "1.0"

    # Binary information - full Phase 1 build passport
    binary_build_passport: dict = field(default_factory=dict)

    # Training inputs
    dataset_hash: str = ""
    dataset_path: str = ""
    model_config_hash: str = ""
    model_config_path: str = ""
    master_seed: int = DEFAULT_MASTER_SEED

    # Training outputs - mirrors build passport artifacts pattern
    output_artifacts: List[dict] = field(default_factory=list)

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
                    "build_passport": self.binary_build_passport,
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
                "artifacts": self.output_artifacts,
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

        deterministic = process.get("deterministic_proof", {})
        metrics = process.get("metrics", {})

        return cls(
            version=data.get("version", "1.0"),
            binary_build_passport=binary.get("build_passport", {}),
            dataset_hash=dataset.get("hash", ""),
            dataset_path=dataset.get("path", ""),
            model_config_hash=config.get("hash", ""),
            model_config_path=config.get("path", ""),
            master_seed=inputs.get("master_seed", DEFAULT_MASTER_SEED),
            output_artifacts=outputs.get("artifacts", []),
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
        binary_passport: Full Phase 1 build passport of the training binary
        training_inputs: Verified training inputs
        training_result: Results from training (metadata JSON)
        merkle_root: Merkle root of all inputs
        output_dir: Directory containing training outputs

    Returns:
        Complete training passport with full build passport composed
    """
    from ..utils import hash_file

    # Hash all training output artifacts (use absolute paths)
    final_weights_path = (output_dir / FINAL_CHECKPOINT_FILENAME).resolve()
    final_weights_hash = hash_file(final_weights_path)

    # Create artifacts list mirroring build passport pattern
    output_artifacts = [
        {
            "path": str(final_weights_path),
            "hash": final_weights_hash,
            "type": "model_weights",
        },
    ]

    passport = TrainingPassport(
        binary_build_passport=binary_passport,
        dataset_hash=training_inputs.dataset_hash,
        dataset_path=str(Path(training_inputs.dataset_dir).resolve()),
        model_config_hash=training_inputs.model_config_hash,
        model_config_path=str(Path(training_inputs.model_config_path).resolve()),
        master_seed=training_inputs.master_seed,
        output_artifacts=output_artifacts,
        total_epochs=training_result.get("total_epochs", 0),
        final_train_loss=training_result.get("final_train_loss", 0.0),
        merkle_verification=MerkleVerification(
            root=merkle_root,
            tree_size=4,  # binary, dataset, config, seed
        ),
    )

    return passport
