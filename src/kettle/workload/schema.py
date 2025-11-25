"""Data structures and schemas for workload definitions."""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional


@dataclass
class WorkloadEnvironment:
    """Environment configuration for workload execution."""

    timeout_seconds: int
    max_memory_mb: int = 2048
    network_access: bool = False


@dataclass
class WorkloadInputs:
    """Input validation requirements for workload."""

    expected_input_root: str  # Expected merkle root of build inputs


@dataclass
class WorkloadStep:
    """Single execution step in workload."""

    name: str
    run: str  # Shell command to execute
    working_directory: str = "."  # Working directory, supports $BUILD_SOURCE etc.


@dataclass
class WorkloadFullResult:
    """Specification for full result files (Party A sees)."""

    path: str  # Path to result file
    description: str = ""


@dataclass
class WorkloadResultSummary:
    """Result summary specification (Party B sees)."""

    source: str  # Path to file containing result summary
    type: str = "string"  # Data type


@dataclass
class Workload:
    """Complete workload definition."""

    name: str
    description: str
    environment: WorkloadEnvironment
    inputs: WorkloadInputs
    steps: List[WorkloadStep]
    full_results: List[WorkloadFullResult]
    result_summary: Optional[WorkloadResultSummary] = None


@dataclass
class StepResult:
    """Result of executing a single step."""

    name: str
    status: Literal["SUCCESS", "FAILED", "TIMEOUT"]
    exit_code: int
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""


@dataclass
class WorkloadResult:
    """Complete result of workload execution."""

    status: Literal["SUCCESS", "FAILED", "TIMEOUT", "SANDBOX_VIOLATION"]
    exit_code: int
    execution_time_seconds: float
    steps: List[StepResult]
    full_results: Dict[str, Any]  # Full results for Party A
    full_results_hash: str  # Hash of full results directory
    summary: Dict[str, Any]  # Summary for Party B


@dataclass
class WorkloadPassport:
    """Workload passport extending build passport."""

    version: str

    # INHERITED from build passport
    inputs: Dict[str, Any]  # input_merkle_root, source, build_config_hash, toolchain_hash
    build_process: Dict[str, Any]  # command, timestamp
    outputs: Dict[str, Any]  # artifacts

    # NEW: Workload execution details
    workload: Dict[str, Any]  # name, description, workload_hash, tools, scripts, timestamp, sandbox
    execution: Dict[str, Any]  # status, exit_code, execution_time_seconds, steps, full_results_hash, summary


def validate_workload_schema(workload: Workload) -> List[str]:
    """Validate workload schema for correctness.

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    # Validate timeout is reasonable
    if workload.environment.timeout_seconds <= 0:
        errors.append("timeout_seconds must be positive")
    if workload.environment.timeout_seconds > 3600:
        errors.append("timeout_seconds cannot exceed 3600 (1 hour)")

    # Validate memory limit
    if workload.environment.max_memory_mb <= 0:
        errors.append("max_memory_mb must be positive")
    if workload.environment.max_memory_mb > 16384:
        errors.append("max_memory_mb cannot exceed 16384 (16GB)")

    # Validate steps exist
    if not workload.steps:
        errors.append("workload must have at least one step")

    # Validate step names are unique
    step_names = [step.name for step in workload.steps]
    if len(step_names) != len(set(step_names)):
        errors.append("step names must be unique")

    # Validate expected input root format
    if not workload.inputs.expected_input_root.startswith("sha256:"):
        errors.append("expected_input_root must start with 'sha256:'")

    return errors
