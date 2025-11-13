"""
YAML pipeline parser.

Loads pipeline YAML files and converts them to Pipeline objects.
"""

from pathlib import Path
from typing import Any, Dict

import yaml

from .schema import ActionType, Job, Pipeline


def parse_pipeline(pipeline_file: Path) -> Pipeline:
    """
    Parse a pipeline YAML file into a Pipeline object.

    Args:
        pipeline_file: Path to pipeline YAML file

    Returns:
        Parsed Pipeline object

    Raises:
        FileNotFoundError: If pipeline file doesn't exist
        ValueError: If YAML is invalid or schema validation fails
    """
    if not pipeline_file.exists():
        raise FileNotFoundError(f"Pipeline file not found: {pipeline_file}")

    # Load YAML
    try:
        with open(pipeline_file, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")

    if not isinstance(data, dict):
        raise ValueError("Pipeline YAML must be a dictionary")

    # Parse top-level fields
    name = data.get("name")
    version = data.get("version", "1.0")
    env = data.get("env", {})
    jobs_data = data.get("jobs", {})

    if not name:
        raise ValueError("Pipeline 'name' is required")

    if not jobs_data:
        raise ValueError("Pipeline must have at least one job")

    # Parse jobs
    jobs = {}
    for job_id, job_data in jobs_data.items():
        jobs[job_id] = _parse_job(job_id, job_data)

    # Create pipeline
    pipeline = Pipeline(
        name=name,
        version=version,
        jobs=jobs,
        env=env,
    )

    # Validate
    pipeline.validate()

    return pipeline


def _parse_job(job_id: str, job_data: Dict[str, Any]) -> Job:
    """
    Parse a single job from YAML data.

    Args:
        job_id: Job identifier
        job_data: Job YAML data

    Returns:
        Parsed Job object

    Raises:
        ValueError: If job data is invalid
    """
    if not isinstance(job_data, dict):
        raise ValueError(f"Job '{job_id}' must be a dictionary")

    # Required fields
    action_str = job_data.get("action")
    if not action_str:
        raise ValueError(f"Job '{job_id}': 'action' is required")

    try:
        action = ActionType(action_str)
    except ValueError:
        valid_actions = [a.value for a in ActionType]
        raise ValueError(
            f"Job '{job_id}': invalid action '{action_str}'. "
            f"Valid actions: {', '.join(valid_actions)}"
        )

    # Optional fields
    name = job_data.get("name", job_id)
    inputs = job_data.get("inputs", {})
    outputs = job_data.get("outputs")
    depends_on = job_data.get("depends_on", [])

    # Validate outputs is a list if provided
    if outputs is not None and not isinstance(outputs, list):
        raise ValueError(f"Job '{job_id}': 'outputs' must be a list of output names")

    # Normalize depends_on to list
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    elif not isinstance(depends_on, list):
        raise ValueError(f"Job '{job_id}': 'depends_on' must be a string or list")

    return Job(
        id=job_id,
        name=name,
        action=action,
        inputs=inputs,
        outputs=outputs,
        depends_on=depends_on,
    )
