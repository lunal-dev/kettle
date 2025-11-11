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
import sys
from pathlib import Path

from rich.console import Console

from .constants import (
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
from .inputs import TrainingInputs
from .passport import TrainingPassport, create_training_passport
from .candle.tool import CandleTrainingTool

console = Console()


def train(
    config: Path,
    dataset_path: Path,
    output_dir: Path = Path("./training-output"),
    quick: bool = False,
    rebuild_binary: bool = False,
) -> Path:
    """
    Train a model with attestable training.

    Args:
        config: Path to model configuration JSON file
        dataset_path: Path to dataset directory
        output_dir: Output directory for checkpoints and passport
        quick: Quick test mode (1 epoch instead of 10)
        rebuild_binary: Force rebuild of training binary

    Returns:
        Path to the generated training passport
    """
    console.print("[bold cyan]Attestable Training Workflow[/bold cyan]")
    console.print()

    # Auto-download dataset if missing
    if not dataset_path.exists():
        download_script = config.parent / "download.py"
        if download_script.exists():
            console.print(f"[yellow]Dataset not found at {dataset_path}[/yellow]")
            console.print(f"[yellow]Running download script: {download_script}[/yellow]")
            console.print()

            result = subprocess.run([sys.executable, str(download_script)])

            if result.returncode != 0:
                raise RuntimeError(f"Download script failed with exit code {result.returncode}")

            console.print()

        # Validate dataset now exists
        if not dataset_path.exists():
            raise RuntimeError(
                f"Dataset directory not found: {dataset_path}\n"
                f"Expected download.py at: {download_script}"
            )

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

    from .inputs import hash_file
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

    # Display output artifacts
    for artifact in passport.output_artifacts:
        artifact_type = artifact.get("type", "unknown").replace("_", " ").title()
        artifact_hash = artifact["hash"]
        console.print(f"{artifact_type} hash: {artifact_hash[:16]}...")

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

    # Extract build passport info for display
    build_passport = passport.binary_build_passport
    git_source = build_passport.get("inputs", {}).get("git_source", {})
    commit_hash = git_source.get("commit_hash", "unknown")

    console.print(f"Passport version: {passport.version}")
    console.print(f"Binary commit: {commit_hash[:16]}...")
    console.print(f"Dataset hash: {passport.dataset_hash[:16]}...")
    console.print(f"Merkle root: {passport.merkle_verification.root[:16]}...")
    console.print()

    # Verify build passport structure
    console.print("[bold]Verifying build passport...[/bold]")
    if not build_passport:
        console.print(f"[red]✗ Build passport is empty")
        return False
    if build_passport.get("version") != "1.0":
        console.print(f"[red]✗ Invalid build passport version: {build_passport.get('version')}")
        return False
    console.print(f"[green]✓ Build passport structure is valid")
    console.print()

    # Verify inputs exist
    console.print("[bold]Verifying training inputs...[/bold]")

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
    console.print()

    # Verify input hashes
    console.print("[bold]Verifying input hashes...[/bold]")

    from ..passport_common import PassportVerifier

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
    console.print()

    # Verify all output artifacts
    console.print("[bold]Verifying output artifacts...[/bold]")

    if not passport.output_artifacts:
        console.print(f"[red]✗ No output artifacts found in passport")
        return False

    for artifact in passport.output_artifacts:
        artifact_path = Path(artifact["path"])
        artifact_hash = artifact["hash"]
        artifact_type = artifact.get("type", "unknown")

        # Check file exists
        if not artifact_path.exists():
            console.print(f"[red]✗ {artifact_type} not found: {artifact_path}")
            return False

        # Verify hash
        success, message = PassportVerifier.verify_file_hash(
            artifact_path, artifact_hash, artifact_type.replace("_", " ").title()
        )
        if not success:
            console.print(f"[red]✗ {message}")
            return False
        console.print(f"[green]✓ {artifact_type.replace('_', ' ').title()} hash matches")

    console.print()

    console.print("[bold green]✓ Passport verification successful![/bold green]")
    console.print()

    console.print("[bold]Training Details:[/bold]")
    console.print(f"Epochs: {passport.total_epochs}")
    console.print(f"Master seed: {passport.master_seed}")
    console.print(f"Backend: {passport.deterministic_backend}")

    return True
