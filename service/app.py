"""Attestable builds API service."""

import asyncio
import json
import queue
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from kettle.logger import set_progress_callback
from kettle.workload.executor import WorkloadExecutor, generate_workload_provenance

from .build_core import (
    extract_git_source,
    extract_zip_source,
    get_builds_dir,
    run_build,
    sanitize_error_message,
    setup_build,
)

app = FastAPI(title="Attestable Builds Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Build Routes
# =============================================================================


@app.post("/build")
async def build(
    source: UploadFile | None = File(None),
    repo_url: str | None = Form(None),
    ref: str | None = Form(None),
):
    """Build with attestation from ZIP upload or git repository.

    Supports both Cargo and Nix projects via auto-detection.

    Args:
        source: ZIP file upload (mutually exclusive with repo_url)
        repo_url: Git repository URL (mutually exclusive with source)
        ref: Git ref (branch, tag, or commit) - only used with repo_url
    """
    build_id, build_dir = setup_build()

    try:
        project_dir = await _extract_source(source, repo_url, ref, build_dir)
    except HTTPException:
        shutil.rmtree(build_dir)
        raise

    result = run_build(build_id, build_dir, project_dir)

    if result["status"] == "failed":
        # Client errors (bad input) get 400, server errors get 500
        status_code = 400 if result.get("error_type") == "UnsupportedToolchainError" else 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@app.post("/build/stream")
async def build_stream(
    source: UploadFile | None = File(None),
    repo_url: str | None = Form(None),
    ref: str | None = Form(None),
):
    """Build with streaming progress via SSE.

    Returns Server-Sent Events with build progress updates.
    Final event contains the complete build result.
    """
    build_id, build_dir = setup_build()

    try:
        project_dir = await _extract_source(source, repo_url, ref, build_dir)
    except HTTPException:
        shutil.rmtree(build_dir)
        raise

    progress_queue: queue.Queue = queue.Queue()

    def on_progress(event_type: str, message: str):
        progress_queue.put({"type": event_type, "message": message})

    def do_build():
        set_progress_callback(on_progress)
        try:
            result = run_build(build_id, build_dir, project_dir)
            progress_queue.put({"type": "complete", "result": result})
        finally:
            set_progress_callback(None)
            progress_queue.put(None)

    async def event_generator():
        thread = threading.Thread(target=do_build, daemon=True)
        thread.start()

        loop = asyncio.get_event_loop()
        while True:
            try:
                item = await loop.run_in_executor(
                    None, lambda: progress_queue.get(timeout=0.1)
                )
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
            except queue.Empty:
                continue

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _extract_source(
    source: UploadFile | None,
    repo_url: str | None,
    ref: str | None,
    build_dir: Path,
) -> Path:
    """Extract source from either ZIP upload or git clone.

    Raises:
        HTTPException: If validation fails or extraction errors
    """
    if source and repo_url:
        raise HTTPException(400, "Provide source OR repo_url, not both")

    if source:
        return await extract_zip_source(source, build_dir)

    if repo_url:
        try:
            return extract_git_source(repo_url, build_dir, ref)
        except RuntimeError as e:
            raise HTTPException(400, str(e))

    raise HTTPException(400, "Must provide source (ZIP) or repo_url (git)")


# =============================================================================
# Download Routes
# =============================================================================


@app.get("/builds/{build_id}/artifacts/{name}")
def get_artifact(build_id: str, name: str):
    """Download binary artifact."""
    path = get_builds_dir() / build_id / "artifacts" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/builds/{build_id}/build-config/{name}")
def get_build_config(build_id: str, name: str):
    """Download build config file (e.g., Cargo.lock)."""
    path = get_builds_dir() / build_id / "build-config" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# =============================================================================
# Workload Routes
# =============================================================================


@app.post("/builds/{build_id}/run-workload")
async def run_workload(
    build_id: str,
    expected_input_root: str = Form(...),
    workload: UploadFile = File(...),
    tools: List[UploadFile] = File(default=[]),
    scripts: List[UploadFile] = File(default=[]),
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
        from kettle.workload.parser import parse_workload_dict, parse_workload_yaml
        import hashlib

        workload_yaml_path = workload_dir / "workload.yaml"
        workload_yaml_path.write_bytes(await workload.read())
        workload_content = workload_yaml_path.read_text()

        workload_data = parse_workload_yaml(workload_content)
        parse_workload_dict(workload_data)
        workload_hash = hashlib.sha256(workload_content.encode()).hexdigest()

        tools_dir = None
        if tools:
            tools_dir = workload_dir / "tools"
            tools_dir.mkdir()
            for tool_file in tools:
                tool_path = tools_dir / tool_file.filename
                tool_path.write_bytes(await tool_file.read())
                tool_path.chmod(0o755)

        scripts_dir = None
        if scripts:
            scripts_dir = workload_dir / "scripts"
            scripts_dir.mkdir()
            for script_file in scripts:
                script_path = scripts_dir / script_file.filename
                script_path.write_bytes(await script_file.read())

        executor = WorkloadExecutor(
            workload_path=workload_yaml_path,
            build_location=build_dir,
        )
        result = executor.execute()

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

        attestation_b64 = None
        attestation_error = None
        try:
            from kettle.build import generate_attestation

            attestation_path = generate_attestation(workload_provenance, workload_dir)
            if attestation_path.exists():
                attestation_b64 = attestation_path.read_text().strip()
        except Exception as e:
            attestation_error = sanitize_error_message(str(e), workload_dir)
            print(f"Warning: Attestation failed: {e}")

        full_results_dir = workload_dir / "full-results"
        full_results_dir.mkdir(exist_ok=True)
        for result_path_str, result_data in result.full_results.items():
            result_filename = Path(result_path_str).name
            result_file = full_results_dir / result_filename
            if result_data["type"] == "json":
                result_file.write_text(json.dumps(result_data["content"], indent=2))
            elif result_data["type"] == "text":
                result_file.write_text(result_data["content"])

        summary_data = {
            "status": result.status,
            "execution_time_seconds": result.execution_time_seconds,
            "summary": result.summary,
            "workload_hash": workload_hash,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
        summary_path = workload_dir / "summary.json"
        summary_path.write_text(json.dumps(summary_data, indent=2))

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
        sanitized_msg = sanitize_error_message(str(e), workload_dir)
        raise HTTPException(500, f"Workload execution failed: {sanitized_msg}")


@app.get("/builds/{build_id}/workloads/{workload_id}/results")
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
    workload_provenance = (
        json.loads(provenance_path.read_text()) if provenance_path.exists() else None
    )

    attestation_path = workload_dir / "evidence.b64"
    attestation = (
        attestation_path.read_text().strip() if attestation_path.exists() else None
    )

    full_results_dir = workload_dir / "full-results"
    full_results = {}
    if full_results_dir.exists():
        for file_path in full_results_dir.iterdir():
            if file_path.is_file():
                try:
                    full_results[file_path.name] = {
                        "type": "json",
                        "content": json.loads(file_path.read_text()),
                    }
                except json.JSONDecodeError:
                    full_results[file_path.name] = {
                        "type": "text",
                        "content": file_path.read_text(),
                    }

    return {
        "summary": summary,
        "full_results": full_results,
        "workload_provenance": workload_provenance,
        "attestation": attestation,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
