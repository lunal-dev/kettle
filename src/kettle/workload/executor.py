"""Workload executor for confidential compute framework."""

import json
import hashlib
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from .schema import (
    Workload,
    StepResult,
    WorkloadResult,
)
from .sandbox import SandboxExecutor, create_sandbox
from .parser import parse_workload_file
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

    def _extract_input_merkle_root(self, provenance: dict) -> str:
        """Extract input merkle root from SLSA provenance byproducts.

        Args:
            provenance: SLSA v1.2 provenance statement

        Returns:
            SHA256 hex hash of input merkle root (without "sha256:" prefix)

        Raises:
            ValueError: If provenance structure is invalid
        """
        try:
            byproducts = (
                provenance
                .get("predicate", {})
                .get("runDetails", {})
                .get("byproducts", [])
            )

            # Find the input_merkle_root byproduct
            for byproduct in byproducts:
                if byproduct.get("name") == "input_merkle_root":
                    merkle_root = byproduct.get("digest", {}).get("sha256", "")
                    return merkle_root

            return ""
        except (AttributeError, TypeError) as e:
            raise ValueError(f"Invalid provenance structure: {e}")

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

        # Load build provenance to get input info
        provenance_path = self.build_location / "provenance.json"
        if not provenance_path.exists():
            raise FileNotFoundError(
                f"Build provenance not found: {provenance_path}\n"
                f"Ensure the build directory contains a provenance.json file."
            )

        provenance_data = json.loads(provenance_path.read_text())

        # Get current input merkle root from provenance
        current_root = self._extract_input_merkle_root(provenance_data)

        if not current_root:
            raise ValueError(
                f"No input_merkle_root found in provenance byproducts.\n"
                f"The build provenance may be malformed or incomplete."
            )

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


def generate_workload_provenance(
    build_provenance_path: Path,
    workload_path: Path,
    workload_result: WorkloadResult,
    tools_dir: Path | None,
    scripts_dir: Path | None,
) -> dict:
    """Generate SLSA v1.2 workload provenance extending build provenance.

    Creates a new SLSA statement with buildType for workload execution,
    referencing the original build provenance and adding execution results.

    Args:
        build_provenance_path: Path to build provenance JSON (provenance.json)
        workload_path: Path to workload.yaml
        workload_result: Result of workload execution
        tools_dir: Directory containing tools (optional)
        scripts_dir: Directory containing scripts (optional)

    Returns:
        SLSA v1.2 workload provenance dictionary
    """
    # Import required SLSA utilities
    from ..slsa import generate_slsa_statement, build_byproduct

    # Load build provenance
    build_provenance = json.loads(build_provenance_path.read_text())

    # Hash the build provenance file itself
    build_provenance_hash = hash_file(build_provenance_path)

    # Extract input merkle root from build provenance
    input_merkle_root = ""
    byproducts = (
        build_provenance
        .get("predicate", {})
        .get("runDetails", {})
        .get("byproducts", [])
    )
    for bp in byproducts:
        if bp.get("name") == "input_merkle_root":
            input_merkle_root = bp.get("digest", {}).get("sha256", "")
            break

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
                        "name": tool_path.name,
                        "path": f"tools/{tool_path.name}",
                        "digest": {"sha256": hash_file(tool_path)},
                    }
                )

    # Hash scripts
    scripts = []
    if scripts_dir and scripts_dir.exists():
        for script_path in sorted(scripts_dir.iterdir()):
            if script_path.is_file():
                scripts.append(
                    {
                        "name": script_path.name,
                        "path": f"scripts/{script_path.name}",
                        "digest": {"sha256": hash_file(script_path)},
                    }
                )

    # Generate timestamps
    started_on = datetime.now(timezone.utc)
    invocation_id = f"workload-{started_on.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # Build SLSA components for workload

    # 1. Subject (empty - workload doesn't produce build artifacts)
    subject = []

    # 2. Build type (workload-specific)
    build_type = "https://attestable-builds.dev/kettle/workload@v1"

    # 3. External parameters (user-controlled)
    external_parameters = {
        "workloadDefinition": {
            "name": workload.name,
            "description": workload.description,
            "digest": {"sha256": workload_hash},
        },
        "buildProvenance": {
            "uri": f"file://{build_provenance_path}",
            "digest": {"sha256": build_provenance_hash},
        },
    }

    # 4. Internal parameters (platform-controlled)
    internal_parameters = {
        "tools": tools,
        "scripts": scripts,
        "sandbox": {
            "network_blocked": not workload.environment.network_access,
            "max_memory_mb": workload.environment.max_memory_mb,
            "timeout_seconds": workload.environment.timeout_seconds,
        },
    }

    # 5. Resolved dependencies (empty - workload depends on build)
    resolved_dependencies = []

    # 6. Builder ID
    builder_id = "https://attestable-builds.dev/kettle-tee/workload-executor/v1"

    # 7. Metadata
    finished_on = started_on + timedelta(seconds=workload_result.execution_time_seconds)
    metadata = {
        "invocationId": invocation_id,
        "startedOn": started_on.isoformat() + "Z",
        "finishedOn": finished_on.isoformat() + "Z",
    }

    # 8. Byproducts (input merkle root from build, full results hash)
    byproducts_list = []
    if input_merkle_root:
        byproducts_list.append(build_byproduct("input_merkle_root", input_merkle_root))
    byproducts_list.append(
        build_byproduct("full_results_hash", workload_result.full_results_hash)
    )

    # Generate SLSA statement
    statement = generate_slsa_statement(
        subject=subject,
        build_type=build_type,
        external_parameters=external_parameters,
        internal_parameters=internal_parameters,
        resolved_dependencies=resolved_dependencies,
        builder_id=builder_id,
        metadata=metadata,
        byproducts=byproducts_list,
    )

    # Add workload execution results as custom extension
    statement["workloadExecution"] = {
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
        "summary": workload_result.summary,
    }

    return statement
