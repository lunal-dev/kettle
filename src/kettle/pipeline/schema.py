"""
Pipeline schema definitions using dataclasses.

Defines the structure of pipeline YAML files including jobs, inputs, outputs,
and dependencies.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class ActionType(str, Enum):
    """Supported built-in actions."""

    BUILD = "build"
    TRAIN = "train"
    VERIFY = "verify"
    TRAIN_VERIFY = "train-verify"


# Required inputs for each action type
ACTION_REQUIREMENTS: Dict[ActionType, List[str]] = {
    ActionType.BUILD: ["project_dir"],
    ActionType.TRAIN: ["config", "dataset"],
    ActionType.VERIFY: ["passport"],
    ActionType.TRAIN_VERIFY: ["passport"],
}


def _validate_job_id(job_id: str) -> None:
    """
    Validate job ID format.

    Job IDs must contain only alphanumeric characters, hyphens, and underscores.

    Args:
        job_id: Job identifier to validate

    Raises:
        ValueError: If job ID format is invalid
    """
    if not re.match(r'^[a-zA-Z0-9_-]+$', job_id):
        raise ValueError(
            f"Invalid job ID: '{job_id}'. "
            "Job IDs must contain only letters, numbers, hyphens, and underscores"
        )


def _validate_name(name: str, field: str) -> None:
    """
    Validate name format for filesystem safety.

    Args:
        name: Name to validate
        field: Field name for error messages

    Raises:
        ValueError: If name is invalid
    """
    if not name or not name.strip():
        raise ValueError(f"{field} is required and cannot be empty")

    # Check for filesystem-unsafe characters
    invalid_chars = '<>:"/\\|?*'
    if any(c in name for c in invalid_chars):
        raise ValueError(
            f"{field} '{name}' contains invalid characters. "
            f"Avoid: {invalid_chars}"
        )


class JobStatus(str, Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class JobOutput:
    """Output artifact from a job."""

    name: str  # Output name (e.g., "passport", "model")
    path: Path  # Path to the output file


@dataclass
class Job:
    """A single job in the pipeline."""

    id: str  # Unique job identifier
    name: str  # Human-readable name
    action: ActionType  # Action to execute
    inputs: Dict[str, Any]  # Action inputs
    outputs: Optional[List[str]] = None  # Output names that will be produced
    depends_on: List[str] = field(default_factory=list)  # Job dependencies

    # Runtime state (set during execution)
    status: JobStatus = JobStatus.PENDING
    resolved_outputs: Dict[str, JobOutput] = field(default_factory=dict)
    error_message: Optional[str] = None

    def __post_init__(self):
        """Validate and normalize fields."""
        # Convert action string to enum if needed
        if isinstance(self.action, str):
            self.action = ActionType(self.action)

        # Convert status string to enum if needed
        if isinstance(self.status, str):
            self.status = JobStatus(self.status)


@dataclass
class Pipeline:
    """Complete pipeline definition."""

    name: str  # Pipeline name
    version: str  # Schema version
    jobs: Dict[str, Job]  # Jobs mapped by ID
    env: Dict[str, Any] = field(default_factory=dict)  # Environment variables

    def get_job_order(self) -> List[str]:
        """
        Get jobs in dependency order using topological sort.

        Returns:
            List of job IDs in execution order

        Raises:
            ValueError: If circular dependencies detected
        """
        # Build adjacency list
        graph: Dict[str, List[str]] = {job_id: [] for job_id in self.jobs}
        in_degree: Dict[str, int] = {job_id: 0 for job_id in self.jobs}

        for job_id, job in self.jobs.items():
            for dep in job.depends_on:
                if dep not in self.jobs:
                    raise ValueError(f"Job '{job_id}' depends on unknown job '{dep}'")
                graph[dep].append(job_id)
                in_degree[job_id] += 1

        # Kahn's algorithm for topological sort
        queue = [job_id for job_id, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            # Sort for deterministic ordering
            queue.sort()
            current = queue.pop(0)
            result.append(current)

            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self.jobs):
            raise ValueError("Circular dependency detected in pipeline")

        return result

    def validate(self):
        """
        Validate pipeline structure.

        Raises:
            ValueError: If validation fails
        """
        if not self.name:
            raise ValueError("Pipeline name is required")

        # Validate pipeline name format
        _validate_name(self.name, "Pipeline name")

        if not self.version:
            raise ValueError("Pipeline version is required")

        if not self.jobs:
            raise ValueError("Pipeline must have at least one job")

        # Validate each job
        for job_id, job in self.jobs.items():
            # Validate job ID format
            _validate_job_id(job_id)

            # Validate job name format
            _validate_name(job.name, "Job name")

            if job.id != job_id:
                raise ValueError(
                    f"Job ID mismatch: key '{job_id}' != job.id '{job.id}'"
                )

            # Validate action-specific inputs
            self._validate_job_inputs(job)

            # Validate dependencies reference valid job IDs
            for dep_id in job.depends_on:
                _validate_job_id(dep_id)

        # Validate dependency graph (checks for circular dependencies)
        self.get_job_order()

    def _validate_job_inputs(self, job: Job):
        """Validate inputs for specific action types."""
        required = ACTION_REQUIREMENTS.get(job.action, [])
        missing = [req for req in required if req not in job.inputs]

        if missing:
            raise ValueError(
                f"Job '{job.id}': action '{job.action.value}' requires inputs: {', '.join(missing)}"
            )
