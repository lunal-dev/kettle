"""
Pipeline execution system for attestable builds.

This module provides a GitHub Actions-like pipeline system for orchestrating
multi-step attestable build and training workflows.
"""

from .executor import execute_pipeline
from .parser import parse_pipeline
from .schema import Pipeline, Job, JobOutput

__all__ = [
    "execute_pipeline",
    "parse_pipeline",
    "Pipeline",
    "Job",
    "JobOutput",
]
