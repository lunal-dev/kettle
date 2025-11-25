"""YAML parser and validator for workload definitions."""

import yaml
from pathlib import Path
from typing import Dict, Any, Union

from .schema import (
    Workload,
    WorkloadEnvironment,
    WorkloadInputs,
    WorkloadStep,
    WorkloadFullResult,
    WorkloadResultSummary,
    validate_workload_schema,
)


class WorkloadParseError(Exception):
    """Error raised when workload YAML parsing fails."""

    pass


class WorkloadValidationError(Exception):
    """Error raised when workload validation fails."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Workload validation failed: {', '.join(errors)}")


def parse_workload_yaml(yaml_content: str) -> Dict[str, Any]:
    """Parse YAML content into dictionary.

    Args:
        yaml_content: YAML string content

    Returns:
        Parsed dictionary

    Raises:
        WorkloadParseError: If YAML is invalid
    """
    try:
        data = yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            raise WorkloadParseError("Workload YAML must be a dictionary")
        return data
    except yaml.YAMLError as e:
        raise WorkloadParseError(f"Invalid YAML: {e}")


def parse_environment(data: Dict[str, Any]) -> WorkloadEnvironment:
    """Parse environment section from YAML data.

    Args:
        data: Environment dictionary from YAML

    Returns:
        WorkloadEnvironment object

    Raises:
        WorkloadParseError: If required fields are missing or invalid
    """
    if "timeout_seconds" not in data:
        raise WorkloadParseError("environment.timeout_seconds is required")

    try:
        return WorkloadEnvironment(
            timeout_seconds=int(data["timeout_seconds"]),
            max_memory_mb=int(data.get("max_memory_mb", 2048)),
            network_access=bool(data.get("network_access", False)),
        )
    except (ValueError, TypeError) as e:
        raise WorkloadParseError(f"Invalid environment configuration: {e}")


def parse_inputs(data: Dict[str, Any]) -> WorkloadInputs:
    """Parse inputs section from YAML data.

    Args:
        data: Inputs dictionary from YAML

    Returns:
        WorkloadInputs object

    Raises:
        WorkloadParseError: If required fields are missing
    """
    if "expected_input_root" not in data:
        raise WorkloadParseError("inputs.expected_input_root is required")

    return WorkloadInputs(expected_input_root=data["expected_input_root"])


def parse_steps(data: list[Dict[str, Any]]) -> list[WorkloadStep]:
    """Parse steps section from YAML data.

    Args:
        data: List of step dictionaries from YAML

    Returns:
        List of WorkloadStep objects

    Raises:
        WorkloadParseError: If steps are invalid
    """
    if not isinstance(data, list):
        raise WorkloadParseError("steps must be a list")

    if not data:
        raise WorkloadParseError("steps cannot be empty")

    steps = []
    for i, step_data in enumerate(data):
        if not isinstance(step_data, dict):
            raise WorkloadParseError(f"step {i} must be a dictionary")

        if "name" not in step_data:
            raise WorkloadParseError(f"step {i}: name is required")
        if "run" not in step_data:
            raise WorkloadParseError(f"step {i}: run is required")

        steps.append(
            WorkloadStep(
                name=step_data["name"],
                run=step_data["run"],
                working_directory=step_data.get("working_directory", "."),
            )
        )

    return steps


def parse_full_results(data: list[Dict[str, Any]]) -> list[WorkloadFullResult]:
    """Parse full_results section from YAML data.

    Args:
        data: List of full result dictionaries from YAML

    Returns:
        List of WorkloadFullResult objects

    Raises:
        WorkloadParseError: If full_results are invalid
    """
    if not isinstance(data, list):
        raise WorkloadParseError("full_results must be a list")

    results = []
    for i, result_data in enumerate(data):
        if not isinstance(result_data, dict):
            raise WorkloadParseError(f"full_results {i} must be a dictionary")

        if "path" not in result_data:
            raise WorkloadParseError(f"full_results {i}: path is required")

        results.append(
            WorkloadFullResult(
                path=result_data["path"],
                description=result_data.get("description", ""),
            )
        )

    return results


def parse_result_summary(data: Dict[str, Any]) -> WorkloadResultSummary:
    """Parse result_summary section from YAML data.

    Args:
        data: Result summary dictionary from YAML

    Returns:
        WorkloadResultSummary object

    Raises:
        WorkloadParseError: If result_summary is invalid
    """
    if not isinstance(data, dict):
        raise WorkloadParseError("result_summary must be a dictionary")

    if "source" not in data:
        raise WorkloadParseError("result_summary.source is required")

    return WorkloadResultSummary(
        source=data["source"],
        type=data.get("type", "string"),
    )


def parse_workload_dict(data: Dict[str, Any]) -> Workload:
    """Parse workload dictionary into Workload object.

    Args:
        data: Parsed YAML dictionary

    Returns:
        Workload object

    Raises:
        WorkloadParseError: If required fields are missing or invalid
    """
    # Validate required top-level fields
    required_fields = ["name", "description", "environment", "inputs", "steps"]
    for field in required_fields:
        if field not in data:
            raise WorkloadParseError(f"Required field '{field}' is missing")

    # Parse each section
    environment = parse_environment(data["environment"])
    inputs = parse_inputs(data["inputs"])
    steps = parse_steps(data["steps"])

    # Optional sections with defaults
    full_results = (
        parse_full_results(data["full_results"]) if "full_results" in data else []
    )
    result_summary = (
        parse_result_summary(data["result_summary"]) if "result_summary" in data else None
    )

    return Workload(
        name=data["name"],
        description=data["description"],
        environment=environment,
        inputs=inputs,
        steps=steps,
        full_results=full_results,
        result_summary=result_summary,
    )


def parse_workload_file(workload_path: Union[Path, str]) -> Workload:
    """Parse and validate a workload YAML file.

    Args:
        workload_path: Path to workload.yaml file

    Returns:
        Validated Workload object

    Raises:
        WorkloadParseError: If parsing fails
        WorkloadValidationError: If validation fails
        FileNotFoundError: If file doesn't exist
    """
    path = Path(workload_path)
    if not path.exists():
        raise FileNotFoundError(f"Workload file not found: {path}")

    # Read and parse YAML
    yaml_content = path.read_text()
    data = parse_workload_yaml(yaml_content)

    # Parse into Workload object
    workload = parse_workload_dict(data)

    # Validate
    errors = validate_workload_schema(workload)
    if errors:
        raise WorkloadValidationError(errors)

    return workload


def validate_workload(workload: Workload) -> None:
    """Validate a workload object.

    Args:
        workload: Workload object to validate

    Raises:
        WorkloadValidationError: If validation fails
    """
    errors = validate_workload_schema(workload)
    if errors:
        raise WorkloadValidationError(errors)
