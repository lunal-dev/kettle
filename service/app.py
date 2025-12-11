"""Minimal attestable builds API."""

import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from kettle.workload.executor import WorkloadExecutor, generate_workload_provenance
from datetime import timezone

from kettle.build import run_build_workflow

app = FastAPI(title="Attestable Builds Service")

BUILDS = Path(os.getenv("KETTLE_STORAGE_DIR", "/tmp/kettle"))
BUILDS.mkdir(parents=True, exist_ok=True)


@app.post("/build")
async def build(source: UploadFile = File(...)):
    """Upload source.zip, build with attestation, return provenance and attestation data.

    Supports both Cargo and Nix projects via auto-detection.
    """
    build_id = str(uuid4())[:8]
    build_dir = BUILDS / build_id
    build_dir.mkdir()

    # Save source.zip (don't extract permanently)
    zip_path = build_dir / "source.zip"
    zip_path.write_bytes(await source.read())

    # Extract to temporary directory for building
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_source = Path(temp_dir) / "source"
        temp_source.mkdir()

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(temp_source)

        # Find project directory (might be in subdirectory)
        project_dir = temp_source
        # Look for Cargo.toml or flake.nix in subdirectories if not found at root
        if not (project_dir / "Cargo.toml").exists() and not (project_dir / "flake.nix").exists():
            subdirs = [d for d in temp_source.iterdir() if d.is_dir()]
            if len(subdirs) == 1:
                project_dir = subdirs[0]

        try:
            # Detect build system
            from kettle.build import detect_build_system
            import typer

            build_system = detect_build_system(project_dir)

            # Run unified build workflow (outputs to nested build/ directory)
            # This handles: verification, building, provenance generation, and attestation
            # Note: For service context, we can't use typer.Exit, so we catch and convert it
            try:
                run_build_workflow(
                    project_dir=project_dir,
                    output_dir=build_dir,  # Creates build_dir/build/ subdirectory
                    release=True,
                    verbose=True,
                    attestation=True,
                )
            except typer.Exit as e:
                # Convert typer.Exit to regular exception for API context
                raise RuntimeError(f"Build workflow failed with exit code {e.exit_code}")

            # Flatten structure: copy files from nested build/ to root for API response
            nested_build_dir = build_dir / "build"
            if (nested_build_dir / "provenance.json").exists():
                shutil.copy2(nested_build_dir / "provenance.json", build_dir / "provenance.json")
            if (nested_build_dir / "evidence.b64").exists():
                shutil.copy2(nested_build_dir / "evidence.b64", build_dir / "evidence.b64")

            # Load generated provenance and manifest (flat structure)
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

            # Find built artifacts based on build system
            if build_system == "nix":
                # Nix artifacts are in project_dir/build/
                nix_build_dir = project_dir / "build"
                if nix_build_dir.exists():
                    for artifact_file in nix_build_dir.iterdir():
                        if artifact_file.is_file():
                            shutil.copy2(artifact_file, artifacts_dir / artifact_file.name)
                            artifact_names.append(artifact_file.name)
            else:  # cargo
                # Cargo artifacts are in target/release/ or target/debug/
                target_dir = project_dir / "target" / "release"
                if target_dir.exists():
                    for item in target_dir.iterdir():
                        if item.is_file() and (not item.suffix or item.suffix == ".exe"):
                            if item.stat().st_mode & 0o111:  # Check if executable
                                shutil.copy2(item, artifacts_dir / item.name)
                                artifact_names.append(item.name)

            # Create build-config directory and copy lock files
            build_config_dir = build_dir / "build-config"
            build_config_dir.mkdir()
            build_config_files = []

            if build_system == "cargo":
                cargo_lock_path = project_dir / "Cargo.lock"
                if cargo_lock_path.exists():
                    shutil.copy2(cargo_lock_path, build_config_dir / "Cargo.lock")
                    build_config_files.append("Cargo.lock")
            elif build_system == "nix":
                flake_lock_path = project_dir / "flake.lock"
                if flake_lock_path.exists():
                    shutil.copy2(flake_lock_path, build_config_dir / "flake.lock")
                    build_config_files.append("flake.lock")

            # Return everything in one response
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

            # Add attestation status
            if attestation_b64:
                response["attestation_status"] = "success"
            else:
                response["attestation_status"] = "unavailable"

            return response

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Build error: {error_details}")  # Log to console

            # Return 500 error with details
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


@app.get("/builds/{build_id}/artifacts/{name}")
def get_artifact(build_id: str, name: str):
    """Download binary artifact."""
    from fastapi.responses import FileResponse
    path = BUILDS / build_id / "artifacts" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/builds/{build_id}/build-config/{name}")
def get_build_config(build_id: str, name: str):
    """Download build config file (e.g., Cargo.lock)."""
    from fastapi.responses import FileResponse
    path = BUILDS / build_id / "build-config" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/builds/{build_id}/run-workload")
async def run_workload(
    build_id: str,
    expected_input_root: str = Form(...),
    workload: UploadFile = File(...),
    tools: List[UploadFile] = File(default=[]),
    scripts: List[UploadFile] = File(default=[])
):
    """
    Upload and execute workload in one atomic operation.

    Party B calls this with:
    - expected_input_root: The input_merkle_root from Party A's build
    - workload: workload.yaml file
    - tools: Optional executable tools
    - scripts: Optional helper scripts

    Returns summary + workload_passport + attestation immediately.
    """

    # Verify build exists
    build_dir = BUILDS / build_id
    if not build_dir.exists():
        raise HTTPException(404, "Build not found")

    # Create workload directory
    workload_id = str(uuid4())[:8]
    workload_dir = build_dir / "workloads" / workload_id
    workload_dir.mkdir(parents=True)

    try:
        # 1. Save workload.yaml
        workload_yaml_path = workload_dir / "workload.yaml"
        workload_yaml_path.write_bytes(await workload.read())
        workload_content = workload_yaml_path.read_text()

        # Parse and validate workload
        from kettle.workload.parser import parse_workload_yaml, parse_workload_dict
        import hashlib

        workload_data = parse_workload_yaml(workload_content)
        workload_obj = parse_workload_dict(workload_data)

        workload_hash = hashlib.sha256(workload_content.encode()).hexdigest()

        # 2. Save tools
        tools_dir = None
        if tools:
            tools_dir = workload_dir / "tools"
            tools_dir.mkdir()
            for tool_file in tools:
                tool_path = tools_dir / tool_file.filename
                tool_path.write_bytes(await tool_file.read())
                tool_path.chmod(0o755)  # Make executable

        # 3. Save scripts
        scripts_dir = None
        if scripts:
            scripts_dir = workload_dir / "scripts"
            scripts_dir.mkdir()
            for script_file in scripts:
                script_path = scripts_dir / script_file.filename
                script_path.write_bytes(await script_file.read())

        # 4. Execute workload immediately

        executor = WorkloadExecutor(
            workload_path=workload_yaml_path,
            build_location=build_dir
        )

        # This will verify input_merkle_root matches expected
        result = executor.execute()

        # 5. Generate workload provenance
        # Look for provenance.json in build_dir (flat structure)
        provenance_path = build_dir / "provenance.json"

        workload_provenance = generate_workload_provenance(
            build_provenance_path=provenance_path,
            workload_path=workload_yaml_path,
            workload_result=result,
            tools_dir=tools_dir,
            scripts_dir=scripts_dir,
        )

        # Save provenance
        provenance_path_out = workload_dir / "workload-provenance.json"
        provenance_path_out.write_text(json.dumps(workload_provenance, indent=2))

        # 6. Generate attestation
        attestation_b64 = None
        attestation_error = None
        try:
            from kettle.build import generate_attestation
            attestation_path, _ = generate_attestation(workload_provenance, workload_dir)

            if attestation_path.exists():
                attestation_b64 = attestation_path.read_text().strip()
        except Exception as e:
            attestation_error = str(e)
            print(f"Warning: Attestation failed: {e}")

        # 7. Save full results (Party A can access these)
        full_results_dir = workload_dir / "full-results"
        full_results_dir.mkdir(exist_ok=True)
        for result_path_str, result_data in result.full_results.items():
            # Save each result file
            result_filename = Path(result_path_str).name
            result_file = full_results_dir / result_filename

            if result_data["type"] == "json":
                result_file.write_text(json.dumps(result_data["content"], indent=2))
            elif result_data["type"] == "text":
                result_file.write_text(result_data["content"])

        # 8. Save summary metadata
        summary_data = {
            "status": result.status,
            "execution_time_seconds": result.execution_time_seconds,
            "summary": result.summary,
            "workload_hash": workload_hash,
            "executed_at": datetime.now(timezone.utc).isoformat()
        }

        summary_path = workload_dir / "summary.json"
        summary_path.write_text(json.dumps(summary_data, indent=2))

        # 9. Return response (Party B gets this immediately)
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
        # Clean up on failure
        if workload_dir.exists():
            shutil.rmtree(workload_dir)

        raise HTTPException(500, f"Workload execution failed: {str(e)}")


@app.get("/builds/{build_id}/workloads/{workload_id}/results")
async def get_workload_results(build_id: str, workload_id: str):
    """
    Get full workload results.

    Party A calls this to see complete execution details including:
    - Full result files
    - Summary
    - Workload provenance
    - Attestation
    """

    workload_dir = BUILDS / build_id / "workloads" / workload_id

    if not workload_dir.exists():
        raise HTTPException(404, "Workload not found")

    # Read summary
    summary_path = workload_dir / "summary.json"
    if not summary_path.exists():
        raise HTTPException(400, "Workload not executed")

    summary = json.loads(summary_path.read_text())

    # Read workload provenance
    provenance_path = workload_dir / "workload-provenance.json"
    workload_provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else None

    # Read attestation
    attestation_path = workload_dir / "evidence.b64"
    attestation = attestation_path.read_text().strip() if attestation_path.exists() else None

    # Read full results
    full_results_dir = workload_dir / "full-results"
    full_results = {}
    if full_results_dir.exists():
        for file_path in full_results_dir.iterdir():
            if file_path.is_file():
                try:
                    # Try to parse as JSON
                    full_results[file_path.name] = {
                        "type": "json",
                        "content": json.loads(file_path.read_text())
                    }
                except json.JSONDecodeError:
                    # Fall back to text
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)