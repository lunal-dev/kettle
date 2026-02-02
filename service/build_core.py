"""Core build logic for the attestable builds service."""

import json
import os
import re
import shutil
import traceback
import zipfile
from pathlib import Path
from uuid import uuid4

import typer

from kettle import core
from kettle.build import run_build_workflow
from kettle.core import UnsupportedToolchainError
from kettle.git import clone_repo


# =============================================================================
# Error Handling
# =============================================================================


class BuildError(Exception):
    """Raised when a build fails with a sanitized message."""

    def __init__(self, message: str, error_type: str = "BuildError"):
        self.error_type = error_type
        super().__init__(message)


def sanitize_traceback(tb: str, build_dir: Path | None = None) -> str:
    """Remove local file paths from traceback to prevent information leakage.

    Args:
        tb: Raw traceback string
        build_dir: Optional build directory to sanitize

    Returns:
        Sanitized traceback with paths replaced
    """
    # Remove or replace common sensitive path patterns
    sanitized = tb

    # Replace home directory paths
    home = str(Path.home())
    sanitized = sanitized.replace(home, "~")

    # Replace build directory with generic placeholder
    if build_dir:
        sanitized = sanitized.replace(str(build_dir), "<build_dir>")

    # Replace common temp directories
    sanitized = re.sub(r"/tmp/kettle/[a-f0-9-]+", "<build_dir>", sanitized)
    sanitized = re.sub(r"/var/folders/[^/]+/[^/]+/T/[^/]+", "<temp>", sanitized)

    # Replace absolute paths to site-packages (keep just the package path)
    sanitized = re.sub(
        r"/[^\s\"']+/site-packages/",
        "<site-packages>/",
        sanitized,
    )

    # Replace nix store paths (keep the hash for debugging)
    sanitized = re.sub(
        r"/nix/store/([a-z0-9]{32})-",
        r"<nix-store>/\1-",
        sanitized,
    )

    return sanitized


def sanitize_error_message(msg: str, build_dir: Path | None = None) -> str:
    """Sanitize an error message to remove local paths.

    Args:
        msg: Raw error message
        build_dir: Optional build directory to sanitize

    Returns:
        Sanitized error message
    """
    sanitized = msg

    # Replace build directory
    if build_dir:
        sanitized = sanitized.replace(str(build_dir), "<build_dir>")

    # Replace temp kettle directories
    sanitized = re.sub(r"/tmp/kettle/[a-f0-9-]+", "<build_dir>", sanitized)

    # Replace home directory
    home = str(Path.home())
    sanitized = sanitized.replace(home, "~")

    return sanitized


# =============================================================================
# Build Directory Management
# =============================================================================


def get_builds_dir() -> Path:
    """Get the builds storage directory."""
    builds = Path(os.getenv("KETTLE_STORAGE_DIR", "/tmp/kettle"))
    builds.mkdir(parents=True, exist_ok=True)
    return builds


def setup_build() -> tuple[str, Path]:
    """Create a new build directory with unique ID.

    Returns:
        Tuple of (build_id, build_dir)
    """
    builds_dir = get_builds_dir()
    build_id = str(uuid4())[:8]
    build_dir = builds_dir / build_id
    build_dir.mkdir()
    return build_id, build_dir


async def extract_zip_source(upload_file, build_dir: Path) -> Path:
    """Extract ZIP source and find project directory.

    Args:
        upload_file: FastAPI UploadFile
        build_dir: Build directory to extract into

    Returns:
        Path to project directory (with Cargo.toml or flake.nix)
    """
    zip_path = build_dir / "source.zip"
    zip_path.write_bytes(await upload_file.read())

    source_dir = build_dir / "source"
    source_dir.mkdir()

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(source_dir)

    return find_project_dir(source_dir)


def extract_git_source(repo_url: str, build_dir: Path, ref: str | None = None) -> Path:
    """Clone git repository and return project directory.

    Args:
        repo_url: Git repository URL
        build_dir: Build directory to clone into
        ref: Optional branch, tag, or commit

    Returns:
        Path to project directory

    Raises:
        RuntimeError: If git clone fails
    """
    import subprocess

    source_dir = build_dir / "source"
    try:
        clone_repo(repo_url, source_dir, ref=ref)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        raise RuntimeError(f"Git clone failed: {stderr.strip()}") from e
    except subprocess.TimeoutExpired:
        raise RuntimeError("Git clone timed out") from None

    return find_project_dir(source_dir)


def find_project_dir(source_dir: Path) -> Path:
    """Find the actual project directory within extracted source.

    Handles cases where project is in a subdirectory (e.g., GitHub archives).

    Args:
        source_dir: Root of extracted source

    Returns:
        Path to directory containing Cargo.toml or flake.nix
    """
    if (source_dir / "Cargo.toml").exists() or (source_dir / "flake.nix").exists():
        return source_dir

    subdirs = [d for d in source_dir.iterdir() if d.is_dir()]
    if len(subdirs) == 1:
        return subdirs[0]

    return source_dir


def run_build(build_id: str, build_dir: Path, project_dir: Path) -> dict:
    """Execute the build workflow and collect results.

    This is THE single source of truth for build logic.

    Args:
        build_id: Unique build identifier
        build_dir: Build output directory
        project_dir: Project source directory

    Returns:
        Build result dict with status, artifacts, provenance, etc.
    """
    try:
        # Raises UnsupportedToolchainError if no toolchain matches
        toolchain = core.detect(project_dir, raise_on_none=True)

        try:
            run_build_workflow(
                project_dir=project_dir,
                output_dir=build_dir,
                release=True,
                verbose=True,
                attestation=False,
            )
        except typer.Exit as e:
            raise BuildError(
                f"Build workflow failed with exit code {e.exit_code}",
                error_type="BuildWorkflowError",
            ) from None

        flatten_artifacts(build_dir)
        provenance, manifest, attestation = load_results(build_dir)
        artifact_names = collect_artifacts(toolchain, project_dir, build_dir)
        build_config_files = collect_build_config(toolchain, project_dir, build_dir)

        return {
            "build_id": build_id,
            "status": "success",
            "build_system": toolchain.name,
            "provenance": provenance,
            "manifest": manifest,
            "attestation": attestation,
            "attestation_status": "success" if attestation else "unavailable",
            "artifacts": artifact_names,
            "build_config_files": build_config_files,
        }

    except UnsupportedToolchainError as e:
        return {
            "build_id": build_id,
            "status": "failed",
            "error": str(e),
            "error_type": "UnsupportedToolchainError",
            "supported_toolchains": e.supported_toolchains,
            "found_files": e.found_files,
        }

    except BuildError as e:
        return {
            "build_id": build_id,
            "status": "failed",
            "error": str(e),
            "error_type": e.error_type,
        }

    except Exception as e:
        # Log full traceback server-side for debugging
        error_details = traceback.format_exc()
        print(f"Build error: {error_details}")

        # Sanitize error message for client response
        sanitized_msg = sanitize_error_message(str(e), build_dir)

        return {
            "build_id": build_id,
            "status": "failed",
            "error": sanitized_msg,
            "error_type": type(e).__name__,
        }


def flatten_artifacts(build_dir: Path) -> None:
    """Copy files from nested kettle-build/ to build root."""
    nested_build_dir = build_dir / "kettle-build"

    for filename in ["provenance.json", "evidence.b64", "manifest.json"]:
        src = nested_build_dir / filename
        if src.exists():
            shutil.copy2(src, build_dir / filename)


def load_results(build_dir: Path) -> tuple[dict | None, dict | None, str | None]:
    """Load provenance, manifest, and attestation from build directory.

    Returns:
        Tuple of (provenance_data, manifest_data, attestation_b64)
    """
    provenance_path = build_dir / "provenance.json"
    manifest_path = build_dir / "manifest.json"
    attestation_path = build_dir / "evidence.b64"

    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else None
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    attestation = attestation_path.read_text().strip() if attestation_path.exists() else None

    return provenance, manifest, attestation


def collect_artifacts(toolchain, project_dir: Path, build_dir: Path) -> list[str]:
    """Copy build artifacts to artifacts directory.

    Returns:
        List of artifact filenames
    """
    artifacts_dir = build_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    artifact_names = []

    for artifact_path in toolchain.get_build_artifacts(project_dir):
        shutil.copy2(artifact_path, artifacts_dir / artifact_path.name)
        artifact_names.append(artifact_path.name)

    return artifact_names


def collect_build_config(toolchain, project_dir: Path, build_dir: Path) -> list[str]:
    """Copy lockfile to build-config directory.

    Returns:
        List of config filenames (e.g., ["Cargo.lock"])
    """
    build_config_dir = build_dir / "build-config"
    build_config_dir.mkdir(exist_ok=True)
    build_config_files = []

    lock_path = project_dir / toolchain.lockfile_name
    if lock_path.exists():
        shutil.copy2(lock_path, build_config_dir / toolchain.lockfile_name)
        build_config_files.append(toolchain.lockfile_name)

    return build_config_files
