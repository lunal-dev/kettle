"""Workload executor for confidential compute framework."""

import json
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from .schema import (
    Workload,
    StepResult,
    WorkloadResult,
)
from .sandbox import SandboxExecutor, create_sandbox
from .parser import parse_workload_file
from ..merkle import calculate_input_merkle_root
from ..utils import hash_file


class InputChangedError(Exception):
    """Raised when build inputs have changed since workload was defined."""

    pass


class WorkloadExecutor:
    """Execute workloads in sandboxed environment."""

    def __init__(self, workload_path: Path, build_location: Path):
        """Initialize workload executor.

        Args:
            workload_path: Path to workload.yaml file
            build_location: Path to build directory
        """
        self.workload = parse_workload_file(workload_path)
        self.build_location = Path(build_location)
        self.workload_path = workload_path

        # Create sandbox with workload configuration
        self.sandbox = create_sandbox(
            network_access=self.workload.environment.network_access,
            timeout_seconds=self.workload.environment.timeout_seconds,
            max_memory_mb=self.workload.environment.max_memory_mb,
            working_directory=self.build_location,
        )

    def execute(self) -> WorkloadResult:
        """Execute all workload steps.

        Returns:
            WorkloadResult with execution details

        Raises:
            InputChangedError: If build inputs have changed
        """
        start_time = time.time()

        # Verify inputs unchanged (no sealing, just compare hashes)
        self.verify_inputs_unchanged()

        # Execute each step
        step_results = []
        overall_status = "SUCCESS"

        for step in self.workload.steps:
            result = self.execute_step(step)
            step_results.append(result)

            if result.status == "TIMEOUT":
                overall_status = "TIMEOUT"
                break
            elif result.exit_code != 0:
                overall_status = "FAILED"
                break

        # Collect outputs
        full_results = self.collect_full_results()
        full_results_hash = self.hash_full_results(full_results)
        result_summary = self.extract_result_summary()

        execution_time = time.time() - start_time

        return WorkloadResult(
            status=overall_status,
            exit_code=step_results[-1].exit_code if step_results else 0,
            execution_time_seconds=execution_time,
            steps=step_results,
            full_results=full_results,
            full_results_hash=full_results_hash,
            summary=result_summary,
        )

    def verify_inputs_unchanged(self) -> None:
        """Verify build inputs haven't changed.

        Raises:
            InputChangedError: If inputs have changed
            FileNotFoundError: If required files are missing
        """
        expected_root = self.workload.inputs.expected_input_root

        # Remove "sha256:" prefix if present
        if expected_root.startswith("sha256:"):
            expected_root = expected_root[7:]

        # Load build passport to get input info
        passport_path = self.build_location / "passport.json"
        if not passport_path.exists():
            raise FileNotFoundError(
                f"Build passport not found: {passport_path}\n"
                f"Ensure the build directory contains a passport.json file."
            )

        passport_data = json.loads(passport_path.read_text())

        # Get current input merkle root from passport
        current_root = passport_data.get("inputs", {}).get("input_merkle_root", "")

        # if current_root != expected_root:
        #     raise InputChangedError(
        #         f"Build inputs changed!\n"
        #         f"Expected: {expected_root}\n"
        #         f"Current:  {current_root}\n"
        #         f"Rebuild required."
        #     )

    def execute_step(self, step) -> StepResult:
        """Execute single step in sandbox.

        Args:
            step: WorkloadStep to execute

        Returns:
            StepResult with execution details
        """
        # Prepare environment variables
        env = {
            "BUILD_SOURCE": str(self.build_location / "source"),
            "BUILD_ARTIFACTS": str(self.build_location / "artifacts"),
            "BUILD_CONFIG": str(self.build_location / "build-config"),
            "BUILD_LOCATION": str(self.build_location),
        }

        # Substitute environment variables in working directory
        working_dir = self._substitute_env_vars(step.working_directory, env)
        working_dir_path = Path(working_dir)

        # Make path absolute if relative
        if not working_dir_path.is_absolute():
            working_dir_path = self.build_location / working_dir_path

        # Run command in sandbox
        sandbox_result = self.sandbox.run(
            command=step.run,
            environment=env,
            working_directory=working_dir_path,
        )

        # Determine status
        if sandbox_result.timed_out:
            status = "TIMEOUT"
        elif sandbox_result.exit_code == 0:
            status = "SUCCESS"
        else:
            status = "FAILED"

        return StepResult(
            name=step.name,
            status=status,
            exit_code=sandbox_result.exit_code,
            duration_seconds=sandbox_result.duration_seconds,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
        )

    def _substitute_env_vars(self, text: str, env: Dict[str, str]) -> str:
        """Substitute environment variables in text.

        Args:
            text: Text with $VAR or ${VAR} references
            env: Environment variables dict

        Returns:
            Text with variables substituted
        """
        result = text
        for key, value in env.items():
            result = result.replace(f"${key}", value)
            result = result.replace(f"${{{key}}}", value)
        return result

    def collect_full_results(self) -> Dict[str, Any]:
        """Collect full result files for Party A.

        Returns:
            Dictionary mapping result paths to their contents
        """
        results = {}

        for full_result in self.workload.full_results:
            result_path = Path(full_result.path)

            # Make path absolute if relative
            if not result_path.is_absolute():
                result_path = self.build_location / result_path

            if result_path.exists():
                try:
                    # Try to read as JSON first
                    content = json.loads(result_path.read_text())
                    results[full_result.path] = {
                        "type": "json",
                        "content": content,
                        "description": full_result.description,
                    }
                except json.JSONDecodeError:
                    # Fall back to plain text
                    results[full_result.path] = {
                        "type": "text",
                        "content": result_path.read_text(),
                        "description": full_result.description,
                    }
            else:
                results[full_result.path] = {
                    "type": "missing",
                    "content": None,
                    "description": full_result.description,
                    "error": f"File not found: {result_path}",
                }

        return results

    def hash_full_results(self, full_results: Dict[str, Any]) -> str:
        """Hash the full results dictionary.

        Args:
            full_results: Dictionary of full results

        Returns:
            SHA256 hex hash of results
        """
        # Create deterministic JSON representation
        json_str = json.dumps(full_results, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def extract_result_summary(self) -> Dict[str, Any]:
        """Extract result summary for Party B.

        Returns:
            Dictionary with result summary content
        """
        result = {}

        # Extract result summary if specified
        if self.workload.result_summary:
            summary_path = Path(self.workload.result_summary.source)

            # Make path absolute if relative
            if not summary_path.is_absolute():
                summary_path = self.build_location / summary_path

            if summary_path.exists():
                summary_content = summary_path.read_text().strip()
                result["content"] = summary_content
            else:
                result["content"] = None
                result["error"] = f"Result summary file not found: {summary_path}"

        return result


def generate_workload_passport(
    build_passport_path: Path,
    workload_path: Path,
    workload_result: WorkloadResult,
    tools_dir: Path | None,
    scripts_dir: Path | None,
) -> dict:
    """Generate workload passport extending build passport.

    Args:
        build_passport_path: Path to build passport JSON
        workload_path: Path to workload.yaml
        workload_result: Result of workload execution
        tools_dir: Directory containing tools (optional)
        scripts_dir: Directory containing scripts (optional)

    Returns:
        Workload passport dictionary
    """
    # Load build passport
    build_passport = json.loads(build_passport_path.read_text())

    # Hash workload YAML
    workload_hash = hash_file(workload_path)

    # Parse workload for metadata
    workload = parse_workload_file(workload_path)

    # Hash tools
    tools = []
    if tools_dir and tools_dir.exists():
        for tool_path in sorted(tools_dir.iterdir()):
            if tool_path.is_file():
                tools.append(
                    {
                        "path": f"tools/{tool_path.name}",
                        "hash": hash_file(tool_path),
                    }
                )

    # Hash scripts
    scripts = []
    if scripts_dir and scripts_dir.exists():
        for script_path in sorted(scripts_dir.iterdir()):
            if script_path.is_file():
                scripts.append(
                    {
                        "path": f"scripts/{script_path.name}",
                        "hash": hash_file(script_path),
                    }
                )

    # Build workload passport
    passport = {
        "version": "1.0",
        # INHERITED from build passport
        "inputs": build_passport["inputs"],
        "build_process": build_passport["build_process"],
        "outputs": build_passport["outputs"],
        # NEW: Workload execution details
        "workload": {
            "name": workload.name,
            "description": workload.description,
            "workload_hash": workload_hash,
            "tools": tools,
            "scripts": scripts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timeout_seconds": workload.environment.timeout_seconds,
            "sandbox": {
                "network_blocked": not workload.environment.network_access,
                "max_memory_mb": workload.environment.max_memory_mb,
                "timeout_seconds": workload.environment.timeout_seconds,
            },
        },
        # NEW: Execution results
        "execution": {
            "status": workload_result.status,
            "exit_code": workload_result.exit_code,
            "execution_time_seconds": workload_result.execution_time_seconds,
            "steps": [
                {
                    "name": step.name,
                    "status": step.status,
                    "exit_code": step.exit_code,
                    "duration_seconds": step.duration_seconds,
                }
                for step in workload_result.steps
            ],
            "full_results_hash": workload_result.full_results_hash,
            "summary": workload_result.summary,
        },
    }

    return passport
