#!/usr/bin/env python3
"""
Download and prepare Iris dataset for attestable training.

This script downloads the Iris dataset from UCI ML repository and converts to SafeTensors.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

# Import shared utilities
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
from kettle.training.dataset_utils import (
    DownloadError,
    VerificationError,
    download_file,
    save_safetensors_dataset,
)


IRIS_URL = "https://archive.ics.uci.edu/ml/machine-learning-databases/iris/iris.data"
IRIS_HASH = "6f608b71a7317216319b4d27b4d9bc84e6abd734eda7872b71a458569e2656c0"


def download_iris(output_dir: Path, verify: bool = True) -> None:
    """Download and convert Iris dataset to SafeTensors."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download
    csv_path = output_dir / "iris.data"
    if csv_path.exists():
        print(f"✓ {csv_path.name} already exists")
    else:
        print(f"Downloading Iris dataset...")
        download_file(IRIS_URL, csv_path, IRIS_HASH if verify else None)
        print(f"✓ {csv_path.name} downloaded and verified")

    print("\n✓ Iris dataset downloaded!")

    # Parse CSV and convert to SafeTensors
    print("\nConverting to SafeTensors format...")

    # Read data
    data = []
    labels_map = {"Iris-setosa": 0, "Iris-versicolor": 1, "Iris-virginica": 2}

    with open(csv_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 5:
                continue

            # 4 features + 1 label
            features = [float(x) for x in parts[:4]]
            label = labels_map[parts[4]]
            data.append((features, label))

    # Convert to numpy arrays
    features = np.array([x[0] for x in data], dtype=np.float32)
    labels = np.array([x[1] for x in data], dtype=np.uint32)

    # Normalize features to [0, 1]
    features = (features - features.min(axis=0)) / (features.max(axis=0) - features.min(axis=0))

    # Save to SafeTensors
    safetensors_path = output_dir / "train.safetensors"
    save_safetensors_dataset(features, labels, safetensors_path)

    print(f"✓ Converted to {safetensors_path.name}")
    print(f"  Features shape: {features.shape}")
    print(f"  Labels shape: {labels.shape}")
    print(f"\n✓ Dataset ready at: {output_dir.absolute()}")


def main():
    parser = argparse.ArgumentParser(
        description="Download and prepare Iris dataset for attestable training"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path(__file__).parent / "data",
        help="Output directory for dataset files (default: ./data)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip checksum verification (not recommended)",
    )

    args = parser.parse_args()

    print("Iris Dataset Downloader")
    print("=" * 50)
    print()

    try:
        download_iris(args.output, verify=not args.no_verify)
    except (DownloadError, VerificationError) as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
