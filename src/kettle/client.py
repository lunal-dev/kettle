"""TEE Service Client for remote build.

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
            'node_modules', 'provenance.json'
        ]

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        archive_path = Path(tmp.name)

    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, strict_timestamps=False) as zipf:
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

        # Save provenance, and attestation
        log(f"\n[3/5] Saving provenance and attestation to {output_dir}/...")

        # Save provenance (new format) or passport (backward compatibility)
        if result.get("provenance"):
            provenance_path = output_dir / "provenance.json"
            provenance_path.write_text(json.dumps(result["provenance"], indent=2))
            log_success(f"Provenance: {provenance_path}")

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
        if result.get("provenance"):
            log("  - provenance.json", style="dim")
        elif result.get("passport"):
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


