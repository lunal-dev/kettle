"""
Pipeline context manager for variable interpolation.

Handles resolution of variables like:
- ${{ env.VAR }} - Environment variables
- ${{ jobs.job_id.outputs.output_name }} - Job outputs
"""

import re
from pathlib import Path
from typing import Any, Dict

from .schema import JobOutput, Pipeline


class PipelineContext:
    """Manages pipeline execution context and variable interpolation."""

    def __init__(self, pipeline: Pipeline):
        """
        Initialize pipeline context.

        Args:
            pipeline: Pipeline definition with env vars and jobs
        """
        self.pipeline = pipeline
        self.env = pipeline.env
        self.job_outputs: Dict[str, Dict[str, JobOutput]] = {}

    def register_job_outputs(self, job_id: str, outputs: Dict[str, JobOutput]):
        """
        Register outputs from a completed job.

        Args:
            job_id: Job identifier
            outputs: Dictionary of output name -> JobOutput
        """
        self.job_outputs[job_id] = outputs

    def resolve_value(self, value: Any) -> Any:
        """
        Resolve variables in a value.

        Supports:
        - Strings with interpolation: "${{ env.VAR }}" or "${{ jobs.X.outputs.Y }}"
        - Nested dict/list structures
        - Non-string values (returned as-is)

        Args:
            value: Value to resolve (can be str, dict, list, or primitive)

        Returns:
            Resolved value with all interpolations replaced

        Raises:
            ValueError: If variable reference is invalid or not found
        """
        if isinstance(value, str):
            return self._resolve_string(value)
        elif isinstance(value, dict):
            return {k: self.resolve_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self.resolve_value(item) for item in value]
        else:
            # Primitives (int, bool, etc.) - return as-is
            return value

    def _resolve_string(self, value: str) -> Any:
        """
        Resolve variables in a string.

        Args:
            value: String that may contain ${{ ... }} expressions

        Returns:
            Resolved value (string or other type if full interpolation)
        """
        # Pattern: ${{ expr }}
        pattern = r"\$\{\{\s*([^}]+)\s*\}\}"
        matches = list(re.finditer(pattern, value))

        if not matches:
            # No interpolation needed
            return value

        # If the entire string is a single interpolation, return the resolved type directly
        if len(matches) == 1 and matches[0].group(0) == value:
            expr = matches[0].group(1).strip()
            return self._resolve_expression(expr)

        # Multiple interpolations or mixed text - return as string
        result = value
        for match in matches:
            expr = match.group(1).strip()
            resolved = self._resolve_expression(expr)
            # Convert to string for substitution
            result = result.replace(match.group(0), str(resolved))

        return result

    def _resolve_expression(self, expr: str) -> Any:
        """
        Resolve a single expression.

        Supported formats:
        - env.VAR_NAME - Environment variable
        - jobs.job_id.outputs.output_name - Job output

        Args:
            expr: Expression without ${{ }} wrapper

        Returns:
            Resolved value

        Raises:
            ValueError: If expression is invalid or reference not found
        """
        parts = expr.split(".")

        if len(parts) < 2:
            raise ValueError(f"Invalid expression: '{expr}'")

        if parts[0] == "env":
            # Environment variable: env.VAR_NAME
            var_name = ".".join(parts[1:])
            if var_name not in self.env:
                raise ValueError(f"Environment variable not found: '{var_name}'")
            return self.env[var_name]

        elif parts[0] == "jobs":
            # Job output: jobs.job_id.outputs.output_name
            if len(parts) < 4 or parts[2] != "outputs":
                raise ValueError(
                    f"Invalid job output reference: '{expr}'. "
                    "Expected format: jobs.job_id.outputs.output_name"
                )

            job_id = parts[1]
            # Join remaining parts to support output names with dots (e.g., passport.json)
            output_name = ".".join(parts[3:])

            if job_id not in self.job_outputs:
                raise ValueError(
                    f"Job '{job_id}' outputs not available. "
                    "Job may not have completed yet."
                )

            if output_name not in self.job_outputs[job_id]:
                available = ", ".join(self.job_outputs[job_id].keys())
                raise ValueError(
                    f"Output '{output_name}' not found for job '{job_id}'. "
                    f"Available outputs: {available}"
                )

            # Return the path as a string
            return str(self.job_outputs[job_id][output_name].path)

        else:
            raise ValueError(
                f"Invalid expression: '{expr}'. "
                "Must start with 'env.' or 'jobs.'"
            )
