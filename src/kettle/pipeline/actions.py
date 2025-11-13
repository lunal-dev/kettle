"""
Built-in pipeline actions.

Wrappers around kettle CLI functions for use in pipelines.
"""

import json
from pathlib import Path
from typing import Any, Dict

from ..cli import execute_build, generate_attestation, generate_passport, verify_inputs
from ..training.orchestrator import train as train_model, verify_training_passport
from .schema import ActionType, JobOutput


def _get_path_input(inputs: Dict[str, Any], key: str, must_exist: bool = False) -> Path:
    """
    Extract and validate a path input.

    Args:
        inputs: Input dictionary
        key: Input key to extract
        must_exist: Whether the path must exist

    Returns:
        Resolved absolute path

    Raises:
        ValueError: If input is missing, invalid type, or doesn't exist when required
    """
    value = inputs.get(key)
    if value is None:
        raise ValueError(f"Required path input '{key}' is missing")

    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"Input '{key}' must be a path string, got {type(value).__name__}"
        )

    path = Path(value).resolve()

    if must_exist and not path.exists():
        raise ValueError(f"Input '{key}' path does not exist: {path}")

    return path


def _write_json(data: dict, path: Path) -> Path:
    """
    Write dict to JSON file with consistent formatting.

    Args:
        data: Dictionary to write
        path: Path to write to (parent directories created if needed)

    Returns:
        Path to written file
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def _read_json(path: Path) -> dict:
    """
    Read and validate JSON config file.

    Args:
        path: Path to JSON file

    Returns:
        Parsed dictionary

    Raises:
        ValueError: If file not found or invalid JSON
    """
    if not path.exists():
        raise ValueError(f"File not found: {path}")

    with open(path, "r") as f:
        return json.load(f)


# Action handler registry - maps action types to handler functions
ACTION_HANDLERS = {
    ActionType.BUILD: lambda inputs, output_dir: _execute_build(inputs, output_dir),
    ActionType.TRAIN: lambda inputs, output_dir: _execute_train(inputs, output_dir),
    ActionType.VERIFY: lambda inputs, output_dir: _execute_verify(inputs),
    ActionType.TRAIN_VERIFY: lambda inputs, output_dir: _execute_train_verify(inputs),
}


def execute_action(
    action: ActionType, inputs: Dict[str, Any], job_output_dir: Path
) -> Dict[str, JobOutput]:
    """
    Execute a pipeline action.

    Args:
        action: Action type to execute
        inputs: Action inputs (already resolved by context)
        job_output_dir: Directory for job outputs

    Returns:
        Dictionary of output name -> JobOutput

    Raises:
        ValueError: If action fails or inputs are invalid
    """
    handler = ACTION_HANDLERS.get(action)
    if not handler:
        raise ValueError(f"Unknown action: {action}")

    return handler(inputs, job_output_dir)


def _execute_build(inputs: Dict[str, Any], job_output_dir: Path) -> Dict[str, JobOutput]:
    """Execute build action."""
    # Extract inputs and resolve paths to absolute
    project_dir = _get_path_input(inputs, "project_dir")
    release = inputs.get("release", True)
    attestation = inputs.get("attestation", False)
    verbose = inputs.get("verbose", False)
    allow_dirty = inputs.get("allow_dirty", False)

    # Set output path
    output_dir = job_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    passport_path = output_dir / "passport.json"

    # Verify inputs
    git_info, cargo_lock_hash, results, toolchain = verify_inputs(
        project_dir, verbose, allow_dirty=allow_dirty
    )

    # Execute build
    build_result = execute_build(project_dir, release)

    # Generate passport
    output_artifacts = [
        (artifact["path"], artifact["hash"]) for artifact in build_result["artifacts"]
    ]

    passport_data = generate_passport(
        git_source=git_info,
        cargo_lock_hash=cargo_lock_hash,
        toolchain=toolchain,
        verification_results=results,
        output_artifacts=output_artifacts,
        output_path=passport_path,
    )

    outputs = {
        "passport.json": JobOutput(name="passport.json", path=passport_path),
    }

    # Generate attestation if requested
    if attestation:
        attestation_path, _ = generate_attestation(passport_data, output_dir=output_dir)
        outputs["evidence.b64"] = JobOutput(name="evidence.b64", path=attestation_path)

    return outputs


def _execute_train(
    inputs: Dict[str, Any], job_output_dir: Path
) -> Dict[str, JobOutput]:
    """Execute train action."""
    # Extract inputs and resolve paths to absolute
    config_input = inputs["config"]

    # Handle inline dict config or external file path
    if isinstance(config_input, dict):
        # Inline config - write to job output dir
        config = _write_json(config_input, job_output_dir / "config.json")
    else:
        # External config file
        config = _get_path_input(inputs, "config")

    dataset = _get_path_input(inputs, "dataset")
    output_dir = job_output_dir
    quick = inputs.get("quick", False)
    rebuild_binary = inputs.get("rebuild_binary", False)
    attestation = inputs.get("attestation", False)

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Train model
    passport_path = train_model(
        config=config,
        dataset_path=dataset,
        output_dir=output_dir,
        quick=quick,
        rebuild_binary=rebuild_binary,
    )

    outputs = {
        "passport.json": JobOutput(name="passport.json", path=passport_path),
    }

    # Check for model weights
    model_path = output_dir / "checkpoints" / "final.safetensors"
    if model_path.exists():
        outputs["final.safetensors"] = JobOutput(name="final.safetensors", path=model_path)

    # Generate attestation if requested
    if attestation:
        passport_data = _read_json(passport_path)
        attestation_path, _ = generate_attestation(passport_data, output_dir=output_dir)
        outputs["evidence.b64"] = JobOutput(name="evidence.b64", path=attestation_path)

    return outputs


def _execute_verify(inputs: Dict[str, Any]) -> Dict[str, JobOutput]:
    """Execute verify action."""
    # Extract inputs and resolve paths to absolute
    passport_path = _get_path_input(inputs, "passport", must_exist=True)

    # For verify action, we would implement verification logic here
    # For now, just validate that the passport exists and is valid JSON
    passport_data = _read_json(passport_path)

    if "version" not in passport_data:
        raise ValueError(f"Invalid passport: missing 'version' field")

    # Return empty outputs (verify is a validation action)
    return {}


def _execute_train_verify(inputs: Dict[str, Any]) -> Dict[str, JobOutput]:
    """Execute train-verify action."""
    # Extract inputs and resolve paths to absolute
    passport_path = _get_path_input(inputs, "passport", must_exist=True)

    # Verify training passport
    try:
        success = verify_training_passport(passport_path)
        if not success:
            raise ValueError("Training verification failed")
    except Exception as e:
        raise ValueError(f"Training verification failed: {e}")

    # Return empty outputs (verify is a validation action)
    return {}
