"""
Pipeline storage management.

Manages directories and files for pipeline execution artifacts.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .schema import Job, JobStatus, Pipeline


class PipelineStorage:
    """Manages storage for pipeline execution artifacts."""

    def __init__(self, base_dir: Optional[Path] = None):
        """
        Initialize pipeline storage.

        Args:
            base_dir: Base directory for pipeline storage.
                     Defaults to ~/.cache/kettle/pipelines
        """
        if base_dir is None:
            base_dir = Path.home() / ".cache" / "kettle" / "pipelines"

        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_pipeline_run(self, pipeline: Pipeline) -> str:
        """
        Create a new pipeline run directory.

        Args:
            pipeline: Pipeline to execute

        Returns:
            Pipeline run ID (UUID)
        """
        run_id = str(uuid.uuid4())[:8]  # Short UUID for readability
        run_dir = self.get_run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        # Write pipeline metadata
        metadata = {
            "pipeline_name": pipeline.name,
            "pipeline_version": pipeline.version,
            "run_id": run_id,
            "started_at": datetime.utcnow().isoformat(),
            "status": "running",
        }

        metadata_path = run_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        return run_id

    def get_run_dir(self, run_id: str) -> Path:
        """Get the directory for a pipeline run."""
        return self.base_dir / run_id

    def get_job_dir(self, run_id: str, job_id: str) -> Path:
        """Get the directory for a specific job."""
        return self.get_run_dir(run_id) / "jobs" / job_id

    def update_job_status(
        self, run_id: str, job: Job, error_message: Optional[str] = None
    ):
        """
        Update job status on disk.

        Args:
            run_id: Pipeline run ID
            job: Job with updated status
            error_message: Optional error message if job failed
        """
        job_dir = self.get_job_dir(run_id, job.id)
        job_dir.mkdir(parents=True, exist_ok=True)

        status_data = {
            "job_id": job.id,
            "job_name": job.name,
            "action": job.action.value,
            "status": job.status.value,
            "updated_at": datetime.utcnow().isoformat(),
        }

        if error_message:
            status_data["error_message"] = error_message

        if job.resolved_outputs:
            status_data["outputs"] = {
                name: str(output.path) for name, output in job.resolved_outputs.items()
            }

        status_path = job_dir / "status.json"
        with open(status_path, "w") as f:
            json.dump(status_data, f, indent=2)

    def update_pipeline_status(
        self, run_id: str, status: str, error_message: Optional[str] = None
    ):
        """
        Update overall pipeline status.

        Args:
            run_id: Pipeline run ID
            status: Pipeline status (running, success, failed)
            error_message: Optional error message if pipeline failed
        """
        run_dir = self.get_run_dir(run_id)
        metadata_path = run_dir / "metadata.json"

        # Read existing metadata
        with open(metadata_path, "r") as f:
            metadata = json.load(f)

        # Update status
        metadata["status"] = status
        metadata["updated_at"] = datetime.utcnow().isoformat()

        if status in ["success", "failed"]:
            metadata["completed_at"] = datetime.utcnow().isoformat()

        if error_message:
            metadata["error_message"] = error_message

        # Write back
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

    def get_pipeline_status(self, run_id: str) -> Dict:
        """
        Get pipeline status.

        Args:
            run_id: Pipeline run ID

        Returns:
            Dictionary with pipeline status information
        """
        run_dir = self.get_run_dir(run_id)
        metadata_path = run_dir / "metadata.json"

        if not metadata_path.exists():
            raise ValueError(f"Pipeline run not found: {run_id}")

        with open(metadata_path, "r") as f:
            return json.load(f)
