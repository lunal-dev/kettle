"""API route handlers for attestable builds service."""

import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from kettle import core
from kettle.build import run_build_workflow
from kettle.workload.executor import WorkloadExecutor, generate_workload_provenance


router = APIRouter()


def get_builds_dir() -> Path:
    """Get the builds storage directory."""
    builds = Path(os.getenv("KETTLE_STORAGE_DIR", "/tmp/kettle"))
    builds.mkdir(parents=True, exist_ok=True)
    return builds


# =============================================================================
# Build Routes
# =============================================================================


@router.post("/build")
async def build(source: UploadFile = File(...)):
    """Upload source.zip, build with attestation, return provenance and attestation data.

    Supports both Cargo and Nix projects via auto-detection.
    """
    builds_dir = get_builds_dir()
    build_id = str(uuid4())[:8]
    build_dir = builds_dir / build_id
    build_dir.mkdir()

    # Save source.zip and extract source directory
    zip_path = build_dir / "source.zip"
    zip_path.write_bytes(await source.read())

    # Extract source directory for workloads to use
    source_dir = build_dir / "source"
    source_dir.mkdir()
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(source_dir)

    # Find project directory (might be in subdirectory)
    project_dir = source_dir
    if not (project_dir / "Cargo.toml").exists() and not (project_dir / "flake.nix").exists():
        subdirs = [d for d in source_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1:
            project_dir = subdirs[0]

    try:
        import typer

        # Detect build system
        toolchain = core.detect(project_dir)
        if not toolchain:
            return JSONResponse(
                status_code=400,
                content={
                    "build_id": build_id,
                    "status": "failed",
                    "error": "No supported build system detected (expected Cargo.toml or flake.nix)",
                }
            )
        build_system = toolchain.name

        # Run unified build workflow
        try:
            run_build_workflow(
                project_dir=project_dir,
                output_dir=build_dir,
                release=True,
                verbose=True,
                attestation=False,
            )
        except typer.Exit as e:
            raise RuntimeError(f"Build workflow failed with exit code {e.exit_code}")

        # Flatten structure: copy files from nested kettle-build/ to root
        nested_build_dir = build_dir / "kettle-build"
        if (nested_build_dir / "provenance.json").exists():
            shutil.copy2(nested_build_dir / "provenance.json", build_dir / "provenance.json")
        if (nested_build_dir / "evidence.b64").exists():
            shutil.copy2(nested_build_dir / "evidence.b64", build_dir / "evidence.b64")
        if (nested_build_dir / "manifest.json").exists():
            shutil.copy2(nested_build_dir / "manifest.json", build_dir / "manifest.json")

        # Load generated provenance and manifest
        provenance_path = build_dir / "provenance.json"
        manifest_path = build_dir / "manifest.json"
        attestation_path = build_dir / "evidence.b64"

        provenance_data = json.loads(provenance_path.read_text()) if provenance_path.exists() else None
        manifest_data = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        attestation_b64 = attestation_path.read_text().strip() if attestation_path.exists() else None

        # Copy artifacts to artifacts directory
        artifacts_dir = build_dir / "artifacts"
        artifacts_dir.mkdir()
        artifact_names = []

        for artifact_path in toolchain.get_build_artifacts(project_dir):
            shutil.copy2(artifact_path, artifacts_dir / artifact_path.name)
            artifact_names.append(artifact_path.name)

        # Copy lock file to build-config directory
        build_config_dir = build_dir / "build-config"
        build_config_dir.mkdir()
        build_config_files = []

        lock_path = project_dir / toolchain.lockfile_name
        if lock_path.exists():
            shutil.copy2(lock_path, build_config_dir / toolchain.lockfile_name)
            build_config_files.append(toolchain.lockfile_name)

        # Build response
        response = {
            "build_id": build_id,
            "status": "success",
            "build_system": build_system,
            "provenance": provenance_data,
            "manifest": manifest_data,
            "attestation": attestation_b64,
            "artifacts": artifact_names,
            "build_config_files": build_config_files,
        }

        if attestation_b64:
            response["attestation_status"] = "success"
        else:
            response["attestation_status"] = "unavailable"

        return response

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Build error: {error_details}")

        return JSONResponse(
            status_code=500,
            content={
                "build_id": build_id,
                "status": "failed",
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": error_details
            }
        )


# =============================================================================
# Download Routes
# =============================================================================


@router.get("/builds/{build_id}/artifacts/{name}")
def get_artifact(build_id: str, name: str):
    """Download binary artifact."""
    path = get_builds_dir() / build_id / "artifacts" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@router.get("/builds/{build_id}/build-config/{name}")
def get_build_config(build_id: str, name: str):
    """Download build config file (e.g., Cargo.lock)."""
    path = get_builds_dir() / build_id / "build-config" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# =============================================================================
# Workload Routes
# =============================================================================


@router.post("/builds/{build_id}/run-workload")
async def run_workload(
    build_id: str,
    expected_input_root: str = Form(...),
    workload: UploadFile = File(...),
    tools: List[UploadFile] = File(default=[]),
    scripts: List[UploadFile] = File(default=[])
):
    """Upload and execute workload in one atomic operation."""
    builds_dir = get_builds_dir()
    build_dir = builds_dir / build_id
    if not build_dir.exists():
        raise HTTPException(404, "Build not found")

    workload_id = str(uuid4())[:8]
    workload_dir = build_dir / "workloads" / workload_id
    workload_dir.mkdir(parents=True)

    try:
        from kettle.workload.parser import parse_workload_yaml, parse_workload_dict
        import hashlib

        # Save workload.yaml
        workload_yaml_path = workload_dir / "workload.yaml"
        workload_yaml_path.write_bytes(await workload.read())
        workload_content = workload_yaml_path.read_text()

        workload_data = parse_workload_yaml(workload_content)
        workload_obj = parse_workload_dict(workload_data)
        workload_hash = hashlib.sha256(workload_content.encode()).hexdigest()

        # Save tools
        tools_dir = None
        if tools:
            tools_dir = workload_dir / "tools"
            tools_dir.mkdir()
            for tool_file in tools:
                tool_path = tools_dir / tool_file.filename
                tool_path.write_bytes(await tool_file.read())
                tool_path.chmod(0o755)

        # Save scripts
        scripts_dir = None
        if scripts:
            scripts_dir = workload_dir / "scripts"
            scripts_dir.mkdir()
            for script_file in scripts:
                script_path = scripts_dir / script_file.filename
                script_path.write_bytes(await script_file.read())

        # Execute workload
        executor = WorkloadExecutor(
            workload_path=workload_yaml_path,
            build_location=build_dir
        )
        result = executor.execute()

        # Generate workload provenance
        provenance_path = build_dir / "provenance.json"
        workload_provenance = generate_workload_provenance(
            build_provenance_path=provenance_path,
            workload_path=workload_yaml_path,
            workload_result=result,
            tools_dir=tools_dir,
            scripts_dir=scripts_dir,
        )

        provenance_path_out = workload_dir / "workload-provenance.json"
        provenance_path_out.write_text(json.dumps(workload_provenance, indent=2))

        # Generate attestation
        attestation_b64 = None
        attestation_error = None
        try:
            from kettle.build import generate_attestation
            attestation_path = generate_attestation(workload_provenance, workload_dir)
            if attestation_path.exists():
                attestation_b64 = attestation_path.read_text().strip()
        except Exception as e:
            attestation_error = str(e)
            print(f"Warning: Attestation failed: {e}")

        # Save full results
        full_results_dir = workload_dir / "full-results"
        full_results_dir.mkdir(exist_ok=True)
        for result_path_str, result_data in result.full_results.items():
            result_filename = Path(result_path_str).name
            result_file = full_results_dir / result_filename
            if result_data["type"] == "json":
                result_file.write_text(json.dumps(result_data["content"], indent=2))
            elif result_data["type"] == "text":
                result_file.write_text(result_data["content"])

        # Save summary
        summary_data = {
            "status": result.status,
            "execution_time_seconds": result.execution_time_seconds,
            "summary": result.summary,
            "workload_hash": workload_hash,
            "executed_at": datetime.now(timezone.utc).isoformat()
        }
        summary_path = workload_dir / "summary.json"
        summary_path.write_text(json.dumps(summary_data, indent=2))

        # Build response
        response = {
            "build_id": build_id,
            "workload_id": workload_id,
            "status": result.status,
            "execution_time_seconds": result.execution_time_seconds,
            "summary": result.summary,
            "workload_hash": workload_hash,
            "workload_provenance": workload_provenance,
            "attestation": attestation_b64,
        }

        if attestation_error:
            response["attestation_status"] = "failed"
            response["attestation_error"] = attestation_error
        elif attestation_b64:
            response["attestation_status"] = "success"
        else:
            response["attestation_status"] = "unavailable"

        return response

    except Exception as e:
        if workload_dir.exists():
            shutil.rmtree(workload_dir)
        raise HTTPException(500, f"Workload execution failed: {str(e)}")


@router.get("/builds/{build_id}/workloads/{workload_id}/results")
async def get_workload_results(build_id: str, workload_id: str):
    """Get full workload results."""
    workload_dir = get_builds_dir() / build_id / "workloads" / workload_id

    if not workload_dir.exists():
        raise HTTPException(404, "Workload not found")

    summary_path = workload_dir / "summary.json"
    if not summary_path.exists():
        raise HTTPException(400, "Workload not executed")

    summary = json.loads(summary_path.read_text())

    provenance_path = workload_dir / "workload-provenance.json"
    workload_provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else None

    attestation_path = workload_dir / "evidence.b64"
    attestation = attestation_path.read_text().strip() if attestation_path.exists() else None

    full_results_dir = workload_dir / "full-results"
    full_results = {}
    if full_results_dir.exists():
        for file_path in full_results_dir.iterdir():
            if file_path.is_file():
                try:
                    full_results[file_path.name] = {
                        "type": "json",
                        "content": json.loads(file_path.read_text())
                    }
                except json.JSONDecodeError:
                    full_results[file_path.name] = {
                        "type": "text",
                        "content": file_path.read_text()
                    }

    return {
        "summary": summary,
        "full_results": full_results,
        "workload_provenance": workload_provenance,
        "attestation": attestation
    }
