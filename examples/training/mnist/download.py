#!/usr/bin/env python3
"""
Download and verify MNIST dataset for attestable training.

This script downloads the MNIST dataset, verifies checksums, and converts to SafeTensors format.
"""

import argparse
import gzip
import struct
import sys
from pathlib import Path

import numpy as np

# Import shared utilities
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
from kettle.training.dataset_utils import DownloadError, VerificationError, save_safetensors_dataset


# MNIST dataset file checksums
MNIST_FILES = {
    "train-images-idx3-ubyte": "ba891046e6505d7aadcbbe25680a0738ad16aec93bde7f9b65e87a2fc25776db",
    "train-labels-idx1-ubyte": "65a50cbbf4e906d70832878ad85ccda5333a97f0f4c3dd2ef09a8a9eef7101c5",
    "t10k-images-idx3-ubyte": "0fa7898d509279e482958e8ce81c8e77db3f2f8254e26661ceb7762c4d494ce7",
    "t10k-labels-idx1-ubyte": "ff7bcfd416de33731a308c3f266cc351222c34898ecbeaf847f06e48f7ec33f2",
}

MNIST_DOWNLOAD_URL = "https://ossci-datasets.s3.amazonaws.com/mnist/"


def download_mnist(output_dir: Path, verify: bool = True) -> None:
    """
    Download and optionally verify MNIST dataset.

    Args:
        output_dir: Directory to save dataset files
        verify: Whether to verify checksums (default: True)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename, expected_hash in MNIST_FILES.items():
        gz_filename = f"{filename}.gz"
        gz_path = output_dir / gz_filename
        output_path = output_dir / filename

        # Skip if already downloaded
        if output_path.exists():
            print(f"✓ {filename} already exists")
            if verify:
                print(f"  Verifying {filename}...")
                with open(output_path, "rb") as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()
                if actual_hash != expected_hash:
                    print(f"  ✗ Hash mismatch! Redownloading...")
                    output_path.unlink()
                else:
                    print(f"  ✓ Verified")
                    continue
            else:
                continue

        # Download
        print(f"Downloading {gz_filename}...")
        try:
            from kettle.training.dataset_utils import download_file
            download_file(MNIST_DOWNLOAD_URL + gz_filename, gz_path)
        except (DownloadError, Exception) as e:
            raise DownloadError(f"Failed to download {gz_filename}: {e}")

        # Extract
        print(f"Extracting {gz_filename}...")
        try:
            with gzip.open(gz_path, "rb") as f_in:
                with open(output_path, "wb") as f_out:
                    f_out.write(f_in.read())
        except Exception as e:
            raise DownloadError(f"Failed to extract {gz_filename}: {e}")

        # Verify hash
        if verify:
            print(f"Verifying {filename}...")
            import hashlib
            with open(output_path, "rb") as f:
                actual_hash = hashlib.sha256(f.read()).hexdigest()

            if actual_hash != expected_hash:
                output_path.unlink()
                raise VerificationError(
                    f"Hash mismatch for {filename}!\n"
                    f"  Expected: {expected_hash}\n"
                    f"  Got:      {actual_hash}"
                )

            print(f"✓ {filename} verified")

        # Remove gz file
        gz_path.unlink()

    print("\n✓ MNIST IDX files downloaded and verified!")

    # Convert to SafeTensors format
    print("\nConverting to SafeTensors format...")

    def read_idx(path: Path) -> np.ndarray:
        """Read IDX format file."""
        with open(path, "rb") as f:
            magic = struct.unpack(">I", f.read(4))[0]
            dims = magic & 0xFF
            shape = struct.unpack(">" + "I" * dims, f.read(4 * dims))
            return np.frombuffer(f.read(), dtype=np.uint8).reshape(shape)

    images = read_idx(output_dir / "train-images-idx3-ubyte").astype(np.float32).reshape(-1, 784) / 255.0
    labels = read_idx(output_dir / "train-labels-idx1-ubyte").astype(np.uint32)

    safetensors_path = output_dir / "train.safetensors"
    save_safetensors_dataset(images, labels, safetensors_path, feature_key="features", label_key="labels")

    print(f"✓ Converted to {safetensors_path.name}")
    print(f"  Features shape: {images.shape}")
    print(f"  Labels shape: {labels.shape}")

    # Clean up .idx files (they're large and no longer needed)
    print("\nCleaning up temporary .idx files...")
    for filename in MNIST_FILES.keys():
        idx_path = output_dir / filename
        if idx_path.exists():
            idx_path.unlink()
    print("✓ Cleanup complete")

    print(f"\n✓ Dataset ready at: {output_dir.absolute()}")


def main():
    parser = argparse.ArgumentParser(
        description="Download and verify MNIST dataset for attestable training"
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

    print("MNIST Dataset Downloader")
    print("=" * 50)
    print()

    try:
        download_mnist(args.output, verify=not args.no_verify)
    except (DownloadError, VerificationError) as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
