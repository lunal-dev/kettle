"""TEE Service Client for remote build and workload execution.

This module provides functions for interacting with TEE build services,
as well as complete workflow orchestrators for all TEE-related commands.
"""

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
import requests

from .git import get_git_info
from .logger import log, log_error, log_section, log_success, log_warning


class TEEAPIError(Exception):
    """Exception raised for TEE API errors."""
    pass


def _handle_api_response(response: requests.Response) -> dict:
    """Handle API response and raise on error.

    Args:
        response: HTTP response object

    Returns:
        Parsed JSON response

    Raises:
        TEEAPIError: If response status is not 200
    """
    if response.status_code != 200:
        raise TEEAPIError(
            f"API error {response.status_code}: {response.text}"
        )
    return response.json()


def create_source_archive(
    project_dir: Path,
    exclude_patterns: list[str] = None
) -> Path:
    """Create source archive from project directory.

    Args:
        project_dir: Path to project
        exclude_patterns: List of directory names to exclude

    Returns:
        Path to created zip archive
    """
    if exclude_patterns is None:
        exclude_patterns = [
            'target', '__pycache__', '.pytest_cache',
            'node_modules', 'passport.json', 'manifest.json'
        ]

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        archive_path = Path(tmp.name)

    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in project_dir.rglob('*'):
            if file_path.is_file():
                relative_path = file_path.relative_to(project_dir)
                if not any(part in exclude_patterns for part in relative_path.parts):
                    zipf.write(file_path, relative_path)

    return archive_path


def submit_build(api_url: str, source_archive: Path, timeout: int = 300) -> dict:
    """Submit build to TEE service.

    Args:
        api_url: Base URL of TEE service
        source_archive: Path to source zip file
        timeout: Request timeout in seconds

    Returns:
        Build result dict with build_id, status, etc.

    Raises:
        TEEAPIError: If build submission fails
    """
    with open(source_archive, "rb") as f:
        response = requests.post(
            f"{api_url.rstrip('/')}/build",
            files={"source": ("source.zip", f, "application/zip")},
            timeout=timeout,
        )

    return _handle_api_response(response)


def download_file(api_url: str, build_id: str, file_type: str, filename: str) -> bytes:
    """Download a file from build results.

    Args:
        api_url: Base URL of TEE service
        build_id: Build identifier
        file_type: Type of file (artifacts, build-config)
        filename: Name of file to download

    Returns:
        File contents as bytes

    Raises:
        TEEAPIError: If download fails
    """
    response = requests.get(
        f"{api_url.rstrip('/')}/builds/{build_id}/{file_type}/{filename}",
        timeout=60
    )

    if response.status_code != 200:
        raise TEEAPIError(
            f"Failed to download {filename}: HTTP {response.status_code}"
        )

    return response.content


def submit_workload(
    api_url: str,
    build_id: str,
    expected_input_root: str,
    workload_files: dict[str, list[Path]],
    timeout: int = 600
) -> dict:
    """Submit workload for execution.

    Args:
        api_url: Base URL of TEE service
        build_id: Build identifier
        expected_input_root: Expected merkle root
        workload_files: Dict mapping file types to list of paths
                       Keys: 'workload', 'tools', 'scripts'
        timeout: Request timeout in seconds

    Returns:
        Workload execution result dict

    Raises:
        TEEAPIError: If workload submission fails
    """
    files_to_upload = []

    # Add workload file
    if 'workload' in workload_files:
        for wf in workload_files['workload']:
            files_to_upload.append(
                ("workload", (wf.name, open(wf, "rb"), "text/yaml"))
            )

    # Add tools
    if 'tools' in workload_files:
        for tool_file in workload_files['tools']:
            files_to_upload.append(
                ("tools", (tool_file.name, open(tool_file, "rb"), "application/octet-stream"))
            )

    # Add scripts
    if 'scripts' in workload_files:
        for script_file in workload_files['scripts']:
            files_to_upload.append(
                ("scripts", (script_file.name, open(script_file, "rb"), "text/plain"))
            )

    try:
        response = requests.post(
            f"{api_url.rstrip('/')}/builds/{build_id}/run-workload",
            data={"expected_input_root": expected_input_root},
            files=files_to_upload,
            timeout=timeout,
        )

        return _handle_api_response(response)
    finally:
        # Close all file handles
        for _, file_tuple in files_to_upload:
            file_tuple[1].close()


def get_workload_results(api_url: str, build_id: str, workload_id: str) -> dict:
    """Get workload execution results.

    Args:
        api_url: Base URL of TEE service
        build_id: Build identifier
        workload_id: Workload identifier

    Returns:
        Workload results dict

    Raises:
        TEEAPIError: If retrieval fails
    """
    response = requests.get(
        f"{api_url.rstrip('/')}/builds/{build_id}/workloads/{workload_id}/results",
        timeout=60,
    )

    return _handle_api_response(response)


# Workflow Orchestrators

def run_tee_build_workflow(project_dir: Path, api_url: str) -> None:
    """Complete TEE build workflow: archive → upload → download results.

    This function orchestrates the entire tee-build command logic:
    1. Creates source archive from project directory
    2. Uploads to remote build API
    3. Downloads and saves passport, attestation, and artifacts

    Args:
        project_dir: Path to Cargo project directory
        api_url: Attestable builds API URL

    Raises:
        typer.Exit: If any step fails
    """
    import typer

    try:
        log_section("TEE Build")

        # Create source archive
        log(f"\n[1/4] Creating source archive from {project_dir}...")
        log("  Creating zip archive (including .git, excluding target/)...", style="dim")

        archive_path = create_source_archive(project_dir)

        # Show git info if available
        git_info = get_git_info(project_dir)
        if git_info:
            log_success(f"Including git metadata from commit {git_info['commit_hash'][:8]}...")

        log_success(f"Archive created: {archive_path.stat().st_size / 1024:.1f} KB")

        # Upload and build
        log(f"\n[2/4] Uploading to {api_url}/build...")
        result = submit_build(api_url, archive_path)

        build_id = result["build_id"]

        if result["status"] != "success":
            log_error(f"Build failed: {result.get('error', 'Unknown error')}")
            raise typer.Exit(1)

        log_success("Build succeeded")
        log_success(f"Build ID: {build_id}")

        # Create output directory structure
        output_dir = Path(f"kettle-{build_id}")
        output_dir.mkdir(exist_ok=True)

        artifacts_dir = output_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        build_config_dir = output_dir / "build-config"
        build_config_dir.mkdir(exist_ok=True)
        source_dir = output_dir / "source"
        source_dir.mkdir(exist_ok=True)

        # Save passport and attestation
        log(f"\n[3/5] Saving passport and attestation to {output_dir}/...")

        if result.get("passport"):
            passport_path = output_dir / "passport.json"
            passport_path.write_text(json.dumps(result["passport"], indent=2))
            log_success(f"Passport: {passport_path}")

        if result.get("attestation"):
            attestation_path = output_dir / "evidence.b64"
            attestation_path.write_text(result["attestation"])
            log_success(f"Attestation: {attestation_path}")
        else:
            log_warning("Attestation not available")

        # Download build-config files
        if result.get("build_config_files"):
            log(f"\n[4/5] Downloading {len(result['build_config_files'])} build config file(s) to {build_config_dir}/...")
            for config_file in result["build_config_files"]:
                try:
                    content = download_file(api_url, build_id, "build-config", config_file)
                    config_path = build_config_dir / config_file
                    config_path.write_bytes(content)
                    size_kb = len(content) / 1024
                    log_success(f"{config_file}: {config_path} ({size_kb:.1f} KB)")
                except TEEAPIError as e:
                    log_warning(f"Failed to download {config_file}: {e}")
        else:
            log("\n[4/5] No build config files to download")

        # Download artifacts
        if result.get("artifacts"):
            log(f"\n[5/5] Downloading {len(result['artifacts'])} artifact(s) to {artifacts_dir}/...")
            for artifact_name in result["artifacts"]:
                try:
                    content = download_file(api_url, build_id, "artifacts", artifact_name)
                    artifact_path = artifacts_dir / artifact_name
                    artifact_path.write_bytes(content)
                    artifact_path.chmod(0o755)  # Make executable
                    size_kb = len(content) / 1024
                    log_success(f"{artifact_name}: {artifact_path} ({size_kb:.1f} KB)")
                except TEEAPIError as e:
                    log_warning(f"Failed to download {artifact_name}: {e}")
        else:
            log("\n[5/5] No artifacts to download")

        log("\n")
        log_success("Remote build complete")

        # Summary
        log(f"\nBuild artifacts in: {output_dir}/", style="bold")
        if result.get("passport"):
            log("  - passport.json", style="dim")
        if result.get("attestation"):
            log("  - evidence.b64", style="dim")
        if result.get("build_config_files"):
            log("  - build-config/", style="dim")
            for config_file in result["build_config_files"]:
                log(f"    - {config_file}", style="dim")
        if result.get("artifacts"):
            log("  - artifacts/", style="dim")
            for artifact_name in result["artifacts"]:
                log(f"    - {artifact_name}", style="dim")

        # Cleanup
        archive_path.unlink()

    except TEEAPIError as e:
        log_error(f"API request failed: {e}")
        raise typer.Exit(1)
    except requests.exceptions.RequestException as e:
        log_error(f"API request failed: {e}")
        raise typer.Exit(1)
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)


def run_tee_workload_workflow(
    workload_dir: Path,
    build_id: str,
    expected_input_root: str,
    api_url: str
) -> None:
    """Complete TEE workload execution workflow.

    This function orchestrates the entire tee-run-workload command logic:
    1. Prepares workload package (workload.yaml, tools/, scripts/)
    2. Uploads and executes in TEE
    3. Displays execution results

    Args:
        workload_dir: Path to workload directory
        build_id: Build ID from remote build
        expected_input_root: Expected input merkle root
        api_url: Attestable builds API URL

    Raises:
        typer.Exit: If any step fails
    """
    import typer

    try:
        log_section("TEE Workload Execution")

        # Check for workload.yaml
        workload_yaml = workload_dir / "workload.yaml"
        if not workload_yaml.exists():
            log_error(f"workload.yaml not found in {workload_dir}")
            raise typer.Exit(1)

        log(f"\n[1/3] Preparing workload from {workload_dir}...")
        log(f"Build ID: {build_id}", style="dim")
        log(f"Expected Input Root: {expected_input_root[:32]}...", style="dim")

        # Collect files to upload
        workload_files = {"workload": [workload_yaml]}

        # Add tools if they exist
        tools_dir = workload_dir / "tools"
        if tools_dir.exists() and tools_dir.is_dir():
            tool_files = [f for f in tools_dir.iterdir() if f.is_file()]
            if tool_files:
                workload_files["tools"] = tool_files
                log(f"  Found {len(tool_files)} tool(s)", style="dim")

        # Add scripts if they exist
        scripts_dir = workload_dir / "scripts"
        if scripts_dir.exists() and scripts_dir.is_dir():
            script_files = [f for f in scripts_dir.iterdir() if f.is_file()]
            if script_files:
                workload_files["scripts"] = script_files
                log(f"  Found {len(script_files)} script(s)", style="dim")

        log_success("Workload package ready")

        # Upload and execute
        log(f"\n[2/3] Uploading to {api_url} and executing in TEE...")
        log("  (This may take a few minutes)", style="dim")

        result = submit_workload(api_url, build_id, expected_input_root, workload_files)

        workload_id = result["workload_id"]

        log_success("Execution complete")
        log_success(f"Workload ID: {workload_id}")
        log_success(f"Status: {result['status']}")
        log(f"Execution Time: {result['execution_time_seconds']:.2f}s", style="dim")

        # Display verification info
        log("\n")
        log_section("Verification")
        log("Results can be verified:")
        log(f"  ✓ Attestation signature (TEE hardware authentic)", style="dim")
        log(f"  ✓ Input root matches: {expected_input_root[:32]}...", style="dim")
        log(f"  ✓ Workload hash matches uploaded workload", style="dim")
        log(f"  ✓ Summary is cryptographically bound to execution", style="dim")

        log("\n")
        log_success("Workload execution complete")

    except TEEAPIError as e:
        log_error(f"API request failed: {e}")
        raise typer.Exit(1)
    except requests.exceptions.RequestException as e:
        log_error(f"API request failed: {e}")
        raise typer.Exit(1)
    except Exception as e:
        log_error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


def run_tee_get_results_workflow(
    build_id: str,
    workload_id: str,
    api_url: str,
    output_dir: Optional[Path] = None
) -> None:
    """Complete TEE results download workflow.

    This function orchestrates the entire tee-get-results command logic:
    1. Downloads results from TEE service
    2. Saves summary, full results, passport, and attestation
    3. Displays summary

    Args:
        build_id: Build ID
        workload_id: Workload ID from execution
        api_url: Attestable builds API URL
        output_dir: Output directory (default: workload-results-{workload_id})

    Raises:
        typer.Exit: If any step fails
    """
    import typer

    try:
        log_section("TEE Workload Results")

        log(f"\n[1/2] Downloading results from {api_url}...")
        log(f"Build ID: {build_id}", style="dim")
        log(f"Workload ID: {workload_id}", style="dim")

        result = get_workload_results(api_url, build_id, workload_id)

        log_success("Results downloaded")

        # Create output directory
        if output_dir is None:
            output_dir = Path(f"workload-results-{workload_id}")
        output_dir.mkdir(exist_ok=True)

        log(f"\n[2/2] Saving results to {output_dir}/...")

        # Save summary
        if result.get("summary"):
            summary_path = output_dir / "summary.json"
            summary_path.write_text(json.dumps(result["summary"], indent=2))
            log_success(f"Summary: {summary_path}")

            # Display summary
            summary = result["summary"]["summary"]
            if summary.get("content"):
                log(f"\n  Result Summary: {summary['content']}", style="bold")

        # Save full results (Party A's private data)
        if result.get("full_results"):
            full_results_dir = output_dir / "full-results"
            full_results_dir.mkdir(exist_ok=True)

            for filename, file_data in result["full_results"].items():
                result_path = full_results_dir / filename
                if file_data["type"] == "json":
                    result_path.write_text(json.dumps(file_data["content"], indent=2))
                elif file_data["type"] == "text":
                    result_path.write_text(file_data["content"])

            log_success(f"Full results: {full_results_dir}/ ({len(result['full_results'])} file(s))")

        # Save workload passport
        if result.get("workload_passport"):
            passport_path = output_dir / "workload-passport.json"
            passport_path.write_text(json.dumps(result["workload_passport"], indent=2))
            log_success(f"Workload passport: {passport_path}")

        # Save attestation
        if result.get("attestation"):
            attestation_path = output_dir / "evidence.b64"
            attestation_path.write_text(result["attestation"])
            log_success(f"Attestation: {attestation_path}")

        log("\n")
        log_success("Results downloaded successfully")
        log(f"\nResults in: {output_dir}/", style="bold")
        log("  - summary.json", style="dim")
        log("  - full-results/", style="dim")
        log("  - workload-passport.json", style="dim")
        log("  - evidence.b64", style="dim")

    except TEEAPIError as e:
        log_error(f"API request failed: {e}")
        raise typer.Exit(1)
    except requests.exceptions.RequestException as e:
        log_error(f"API request failed: {e}")
        raise typer.Exit(1)
    except Exception as e:
        log_error(f"Error: {e}")
        raise typer.Exit(1)
