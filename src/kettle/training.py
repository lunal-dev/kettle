"""
Training orchestration for attestable ML training.

This module orchestrates the complete attestable training workflow:
1. Ensure training binary is available (download/build if needed)
2. Verify and hash all training inputs
3. Build merkle tree of inputs
4. Execute training via subprocess
5. Generate training passport
6. Optionally verify in TEE with attestation
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

from .training_constants import (
    DEFAULT_MASTER_SEED,
    DEFAULT_EPOCHS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_LEARNING_RATE,
    DEFAULT_LOG_INTERVAL,
    QUICK_MODE_EPOCHS,
    QUICK_MODE_LOG_INTERVAL,
    CHECKPOINTS_SUBDIR,
    FINAL_CHECKPOINT_FILENAME,
    TRAINING_RESULTS_FILENAME,
    TRAINING_PASSPORT_FILENAME,
)
from .training_inputs import TrainingInputs
from .training_passport import TrainingPassport, create_training_passport
from .training_tool import CandleTrainingTool

console = Console()


# MNIST dataset constants
MNIST_FILES = {
    "train-images-idx3-ubyte": "ba891046e6505d7aadcbbe25680a0738ad16aec93bde7f9b65e87a2fc25776db",
    "train-labels-idx1-ubyte": "65a50cbbf4e906d70832878ad85ccda5333a97f0f4c3dd2ef09a8a9eef7101c5",
    "t10k-images-idx3-ubyte": "0fa7898d509279e482958e8ce81c8e77db3f2f8254e26661ceb7762c4d494ce7",
    "t10k-labels-idx1-ubyte": "ff7bcfd416de33731a308c3f266cc351222c34898ecbeaf847f06e48f7ec33f2",
}
MNIST_DOWNLOAD_URL = "https://ossci-datasets.s3.amazonaws.com/mnist/"
DEFAULT_DATASET_CACHE = Path.home() / ".cache" / "kettle" / "datasets" / "mnist"


def _mnist_exists(dataset_dir: Path) -> bool:
    """Check if MNIST dataset files exist."""
    return all((dataset_dir / f).exists() for f in MNIST_FILES.keys())


def _download_mnist(dataset_dir: Path):
    """Download and verify MNIST dataset files."""
    import gzip
    import hashlib
    import urllib.request

    for filename, expected_hash in MNIST_FILES.items():
        gz_filename = f"{filename}.gz"
        gz_path = dataset_dir / gz_filename
        output_path = dataset_dir / filename

        # Download
        console.print(f"  Downloading {gz_filename}...")
        urllib.request.urlretrieve(MNIST_DOWNLOAD_URL + gz_filename, gz_path)

        # Extract
        with gzip.open(gz_path, "rb") as f_in:
            with open(output_path, "wb") as f_out:
                f_out.write(f_in.read())

        # Verify hash
        console.print(f"  Verifying {filename}...")
        with open(output_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()

        if actual_hash != expected_hash:
            raise RuntimeError(
                f"Hash mismatch for {filename}!\n"
                f"Expected: {expected_hash}\n"
                f"Got:      {actual_hash}\n"
                f"Dataset may be corrupted or tampered with."
            )

        console.print(f"  ✓ {filename} verified")

        # Remove gz file
        gz_path.unlink()


def train(
    config: Path,
    dataset_path: Optional[Path] = None,
    output_dir: Path = Path("./training-output"),
    quick: bool = False,
    rebuild_binary: bool = False,
) -> Path:
    """
    Train a model with attestable training.

    Args:
        config: Path to model configuration JSON file
        dataset_path: Dataset directory (auto-downloads if not provided)
        output_dir: Output directory for checkpoints and passport
        quick: Quick test mode (1 epoch instead of 10)
        rebuild_binary: Force rebuild of training binary

    Returns:
        Path to the generated training passport
    """
    console.print("[bold cyan]Attestable Training Workflow[/bold cyan]")
    console.print()

    # Default dataset location
    if dataset_path is None:
        dataset_path = Path.home() / ".cache" / "kettle" / "datasets" / "mnist"
        dataset_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[dim]Using dataset cache: {dataset_path}[/dim]")

    # Download MNIST if not present
    if not _mnist_exists(dataset_path):
        console.print("[yellow]MNIST dataset not found. Downloading...[/yellow]")
        _download_mnist(dataset_path)
        console.print("[green]✓ Dataset downloaded[/green]")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Ensure training binary is available (auto-install if needed)
    console.print("[bold]Step 1: Ensuring training binary...[/bold]")
    tool = CandleTrainingTool()

    if rebuild_binary:
        console.print("[yellow]Rebuilding training binary...")
        tool.remove()

    # Auto-install if not present
    if not tool.is_installed():
        console.print("[yellow]Training binary not found. Installing automatically...")
        tool.ensure_binary()

    binary_path = tool.get_binary_path()
    binary_passport = tool.get_build_passport()
    console.print(f"[green]✓ Binary ready: {binary_path}")
    console.print()

    # Step 2: Hash training inputs
    console.print("[bold]Step 2: Hashing training inputs...[/bold]")
    seed = DEFAULT_MASTER_SEED
    training_inputs = TrainingInputs(
        dataset_dir=dataset_path,
        model_config_path=config,
        binary_passport=binary_passport,
        master_seed=seed,
    )

    console.print(f"Dataset hash: {training_inputs.dataset_hash[:16]}...")
    console.print(f"Model config hash: {training_inputs.model_config_hash[:16]}...")
    console.print(f"Master seed: {seed}")
    console.print("[green]✓ All inputs hashed")
    console.print()

    # Step 3: Build merkle tree
    console.print("[bold]Step 3: Building merkle tree of inputs...[/bold]")
    merkle_root = training_inputs.get_merkle_root()
    console.print(f"Merkle root: {merkle_root[:16]}...")
    console.print("[green]✓ Merkle tree built")
    console.print()

    # Step 4: Execute training
    console.print("[bold]Step 4: Executing training...[/bold]")
    checkpoints_dir = output_dir / CHECKPOINTS_SUBDIR
    checkpoints_dir.mkdir(exist_ok=True)

    # Python determines all training parameters based on mode
    if quick:
        epochs = QUICK_MODE_EPOCHS
        log_interval = QUICK_MODE_LOG_INTERVAL
    else:
        epochs = DEFAULT_EPOCHS
        log_interval = DEFAULT_LOG_INTERVAL

    cmd = [
        str(binary_path),
        "--config",
        str(config),
        "--dataset",
        str(dataset_path),
        "--output",
        str(checkpoints_dir),
        "--seed",
        str(seed),
        "--epochs",
        str(epochs),
        "--batch-size",
        str(DEFAULT_BATCH_SIZE),
        "--learning-rate",
        str(DEFAULT_LEARNING_RATE),
        "--log-interval",
        str(log_interval),
    ]

    console.print(f"[dim]Command: {' '.join(cmd)}[/dim]")
    console.print()

    # Execute training
    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Training failed with exit code {result.returncode}")

    console.print()
    console.print("[green]✓ Training completed successfully")
    console.print()

    # Step 5: Load training results and generate passport
    console.print("[bold]Step 5: Generating training passport...[/bold]")

    # Hash final checkpoint
    final_checkpoint = checkpoints_dir / FINAL_CHECKPOINT_FILENAME
    if not final_checkpoint.exists():
        raise RuntimeError(f"Final checkpoint not found at {final_checkpoint}")

    from .training_inputs import hash_file
    final_checkpoint_hash = hash_file(final_checkpoint)

    # Load training results from Rust output
    results_path = checkpoints_dir / TRAINING_RESULTS_FILENAME
    if not results_path.exists():
        raise RuntimeError(f"Training results not found at {results_path}")

    with open(results_path, "r") as f:
        training_result = json.load(f)

    # Add checkpoint hash to results
    training_result["final_checkpoint_hash"] = final_checkpoint_hash

    # Step 6: Create training passport
    passport = create_training_passport(
        binary_passport=binary_passport,
        training_inputs=training_inputs,
        training_result=training_result,
        merkle_root=merkle_root,
        output_dir=checkpoints_dir,
    )

    # Save passport
    passport_path = output_dir / TRAINING_PASSPORT_FILENAME
    passport.save(passport_path)

    console.print(f"[green]✓ Training passport saved: {passport_path}")
    console.print()

    # Print summary
    console.print("[bold green]Training Summary:[/bold green]")
    console.print(f"Total epochs: {passport.total_epochs}")
    console.print(f"Final train loss: {passport.final_train_loss:.4f}")
    console.print(f"Final weights hash: {passport.final_weights_hash[:16]}...")
    console.print(f"Merkle root: {passport.merkle_verification.root[:16]}...")
    console.print()

    return passport_path


def verify_training_passport(passport_path: Path) -> bool:
    """
    Verify a training passport.

    Args:
        passport_path: Path to training passport JSON

    Returns:
        True if verification succeeds, False otherwise
    """
    console.print("[bold cyan]Verifying Training Passport[/bold cyan]")
    console.print()

    # Load passport
    passport = TrainingPassport.load(passport_path)

    console.print(f"Passport version: {passport.version}")
    console.print(f"Binary commit: {passport.binary_commit_hash[:16]}...")
    console.print(f"Dataset hash: {passport.dataset_hash[:16]}...")
    console.print(f"Merkle root: {passport.merkle_verification.root[:16]}...")
    console.print()

    # Verify inputs exist
    console.print("[bold]Verifying inputs...[/bold]")

    dataset_path = Path(passport.dataset_path)
    if not dataset_path.exists():
        console.print(f"[red]✗ Dataset not found: {dataset_path}")
        return False
    console.print(f"[green]✓ Dataset found")

    config_path = Path(passport.model_config_path)
    if not config_path.exists():
        console.print(f"[red]✗ Model config not found: {config_path}")
        return False
    console.print(f"[green]✓ Model config found")

    final_weights = Path(passport.final_weights_path)
    if not final_weights.exists():
        console.print(f"[red]✗ Final weights not found: {final_weights}")
        return False
    console.print(f"[green]✓ Final weights found")
    console.print()

    # Verify hashes
    console.print("[bold]Verifying hashes...[/bold]")

    from .passport_common import PassportVerifier

    # Verify dataset hash
    success, message = PassportVerifier.verify_directory_hash(
        dataset_path, passport.dataset_hash, "Dataset"
    )
    if not success:
        console.print(f"[red]✗ {message}")
        return False
    console.print(f"[green]✓ Dataset hash matches")

    # Verify config hash
    success, message = PassportVerifier.verify_file_hash(
        config_path, passport.model_config_hash, "Model config"
    )
    if not success:
        console.print(f"[red]✗ {message}")
        return False
    console.print(f"[green]✓ Model config hash matches")

    # Verify weights hash
    success, message = PassportVerifier.verify_file_hash(
        final_weights, passport.final_weights_hash, "Final weights"
    )
    if not success:
        console.print(f"[red]✗ {message}")
        return False
    console.print(f"[green]✓ Final weights hash matches")
    console.print()

    console.print("[bold green]✓ Passport verification successful![/bold green]")
    console.print()

    console.print("[bold]Training Details:[/bold]")
    console.print(f"Epochs: {passport.total_epochs}")
    console.print(f"Master seed: {passport.master_seed}")
    console.print(f"Backend: {passport.deterministic_backend}")

    return True
