"""Shared utilities for dataset downloading and preparation."""

import hashlib
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np
from safetensors.numpy import save_file


class DownloadError(Exception):
    """Raised when dataset download fails."""

    pass


class VerificationError(Exception):
    """Raised when checksum verification fails."""

    pass


def download_file(url: str, output_path: Path, expected_hash: Optional[str] = None) -> None:
    """
    Download a file with optional checksum verification.

    Args:
        url: URL to download from
        output_path: Path to save the file
        expected_hash: Optional SHA256 hash for verification

    Raises:
        DownloadError: If download fails
        VerificationError: If checksum doesn't match
    """
    try:
        urllib.request.urlretrieve(url, output_path)
    except Exception as e:
        raise DownloadError(f"Failed to download {url}: {e}")

    if expected_hash:
        with open(output_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()

        if actual_hash != expected_hash:
            output_path.unlink()  # Clean up corrupted file
            raise VerificationError(
                f"Hash mismatch for {output_path.name}\n"
                f"Expected: {expected_hash}\n"
                f"Got:      {actual_hash}"
            )


def save_safetensors_dataset(
    features: np.ndarray,
    labels: np.ndarray,
    output_path: Path,
    feature_key: str = "features",
    label_key: str = "labels",
) -> None:
    """
    Save dataset to SafeTensors format with validation.

    Args:
        features: Feature array (num_samples, num_features) as float32
        labels: Label array (num_samples,) as uint32
        output_path: Path to save train.safetensors
        feature_key: Key name for features (default: "features")
        label_key: Key name for labels (default: "labels")

    Raises:
        ValueError: If shapes or types are invalid
    """
    # Validate types
    if features.dtype != np.float32:
        raise ValueError(f"Features must be float32, got {features.dtype}")
    if labels.dtype != np.uint32:
        raise ValueError(f"Labels must be uint32, got {labels.dtype}")

    # Validate shapes
    if features.ndim != 2:
        raise ValueError(f"Features must be 2D (samples×features), got shape {features.shape}")
    if labels.ndim != 1:
        raise ValueError(f"Labels must be 1D (samples), got shape {labels.shape}")

    num_samples_features = features.shape[0]
    num_samples_labels = labels.shape[0]

    if num_samples_features != num_samples_labels:
        raise ValueError(
            f"Sample count mismatch: {num_samples_features} features vs {num_samples_labels} labels"
        )

    # Save
    save_file({feature_key: features, label_key: labels}, output_path)
