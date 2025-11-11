"""Minimal attestable builds API."""

import json
import os
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, FileResponse

from kettle.cli import execute_build, generate_attestation, verify_inputs
from kettle.passport import generate_passport

app = FastAPI(title="Attestable Builds Service")

STORAGE = Path(os.getenv("KETTLE_STORAGE_DIR", "/tmp/kettle"))
STORAGE.mkdir(parents=True, exist_ok=True)

BUILDS = STORAGE / "builds"
BUILDS.mkdir(parents=True, exist_ok=True)

TRAININGS = STORAGE / "trainings"
TRAININGS.mkdir(parents=True, exist_ok=True)


@app.post("/build")
async def build(source: UploadFile = File(...)):
    """Upload source.zip, build with attestation, return passport and attestation data."""
    build_id = str(uuid4())[:8]
    build_dir = BUILDS / build_id
    build_dir.mkdir()

    # Extract source
    source_dir = build_dir / "source"
    source_dir.mkdir()
    zip_path = build_dir / "source.zip"
    zip_path.write_bytes(await source.read())

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(source_dir)

    # Find Cargo.toml (might be in subdirectory)
    project_dir = source_dir
    if not (project_dir / "Cargo.toml").exists():
        subdirs = [d for d in source_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1:
            project_dir = subdirs[0]

    try:
        # Build
        git_info, cargo_lock_hash, results, toolchain = verify_inputs(project_dir, verbose=False)
        build_result = execute_build(project_dir, release=True)

        # Generate passport
        output_artifacts = [(a["path"], a["hash"]) for a in build_result["artifacts"]]
        passport_path = build_dir / "passport.json"

        passport_data = generate_passport(
            git_source=git_info,
            cargo_lock_hash=cargo_lock_hash,
            toolchain=toolchain,
            verification_results=results,
            output_artifacts=output_artifacts,
            output_path=passport_path,
        )

        # Copy artifacts
        artifacts_dir = build_dir / "artifacts"
        artifacts_dir.mkdir()
        artifact_names = []
        for artifact in build_result["artifacts"]:
            artifact_path = Path(artifact["path"])
            shutil.copy2(artifact_path, artifacts_dir / artifact_path.name)
            artifact_names.append(artifact_path.name)

        # Generate attestation
        import os
        old_cwd = Path.cwd()
        attestation_b64 = None
        attestation_error = None
        try:
            os.chdir(build_dir)
            generate_attestation(passport_data)

            # Read attestation if it was created
            attestation_path = build_dir / "evidence.b64"
            if attestation_path.exists():
                attestation_b64 = attestation_path.read_text().strip()
        except Exception as e:
            attestation_error = str(e)
            print(f"Warning: Attestation failed: {e}")
        finally:
            os.chdir(old_cwd)

        # Return everything in one response
        response = {
            "build_id": build_id,
            "status": "success",
            "passport": passport_data,
            "attestation": attestation_b64,
            "artifacts": artifact_names,
        }

        # Add attestation status if it failed
        if attestation_error:
            response["attestation_status"] = "failed"
            response["attestation_error"] = attestation_error
        elif attestation_b64:
            response["attestation_status"] = "success"
        else:
            response["attestation_status"] = "unavailable"

        return response

    except Exception as e:
        return {
            "build_id": build_id,
            "status": "failed",
            "error": str(e)
        }


@app.get("/builds/{build_id}/artifacts/{name}")
def get_artifact(build_id: str, name: str):
    """Download binary artifact."""
    from fastapi.responses import FileResponse
    path = BUILDS / build_id / "artifacts" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


def _update_training_status(status_file: Path, status: str, **updates) -> None:
    """Update training job status (helper for DRY)."""
    data = json.loads(status_file.read_text()) if status_file.exists() else {}
    data["status"] = status
    data.update(updates)
    status_file.write_text(json.dumps(data, indent=2))


def _run_training_job(training_id: str, config_path: Path, dataset_dir: Path,
                      output_dir: Path, quick: bool, attestation: bool) -> None:
    """Execute training job in background thread."""
    from kettle.training.orchestrator import train

    training_dir = TRAININGS / training_id
    status_file = training_dir / "status.json"

    _update_training_status(status_file, "running", started_at=datetime.utcnow().isoformat())

    try:
        # Reuse existing train() orchestrator
        passport_path = train(
            config=config_path,
            dataset_path=dataset_dir,
            output_dir=output_dir,
            quick=quick,
            rebuild_binary=False
        )

        # Load passport for metrics
        passport_data = json.loads(passport_path.read_text())
        metrics = passport_data.get("process", {}).get("metrics", {})

        # Reuse attestation pattern from /build endpoint
        attestation_b64 = None
        attestation_error = None
        if attestation:
            try:
                old_cwd = Path.cwd()
                os.chdir(training_dir)
                generate_attestation(passport_data)

                attestation_path = training_dir / "evidence.b64"
                if attestation_path.exists():
                    attestation_b64 = attestation_path.read_text().strip()
            except Exception as e:
                attestation_error = str(e)
            finally:
                os.chdir(old_cwd)

        # Success
        updates = {
            "completed_at": datetime.utcnow().isoformat(),
            "artifacts": ["final.safetensors"],
            "metrics": metrics
        }

        if attestation:
            updates["attestation_status"] = "success" if attestation_b64 else "failed"
            if attestation_error:
                updates["attestation_error"] = attestation_error

        _update_training_status(status_file, "success", **updates)

    except Exception as e:
        _update_training_status(
            status_file, "failed",
            failed_at=datetime.utcnow().isoformat(),
            error=str(e)
        )


@app.on_event("startup")
async def startup():
    """Pre-cache training binary on startup."""
    from kettle.training.candle.tool import CandleTrainingTool

    tool = CandleTrainingTool()
    if not tool.is_installed():
        print("Building training binary on first startup...")
        tool.ensure_binary()
    print(f"Training binary ready at {tool.get_binary_path()}")


@app.post("/train")
async def train_model(
    config: UploadFile = File(..., description="Model configuration JSON"),
    dataset: UploadFile = File(..., description="Dataset zip file"),
    quick: bool = Form(False, description="Quick mode (1 epoch)"),
    attestation: bool = Form(False, description="Generate TEE attestation")
):
    """Submit training job (async)."""
    training_id = str(uuid4())[:8]
    training_dir = TRAININGS / training_id
    training_dir.mkdir()

    # Save config
    config_data = json.loads(await config.read())
    config_path = training_dir / "config.json"
    config_path.write_text(json.dumps(config_data, indent=2))

    # Save and extract dataset
    dataset_zip = training_dir / "dataset.zip"
    dataset_zip.write_bytes(await dataset.read())

    dataset_dir = training_dir / "dataset"
    dataset_dir.mkdir()

    with zipfile.ZipFile(dataset_zip, "r") as z:
        z.extractall(dataset_dir)

    output_dir = training_dir / "output"
    output_dir.mkdir()

    # Initialize status
    status_file = training_dir / "status.json"
    _update_training_status(
        status_file, "queued",
        training_id=training_id,
        created_at=datetime.utcnow().isoformat(),
        quick=quick,
        attestation=attestation
    )

    # Submit job
    thread = threading.Thread(
        target=_run_training_job,
        args=(training_id, config_path, dataset_dir, output_dir, quick, attestation),
        daemon=True
    )
    thread.start()

    return JSONResponse(
        status_code=202,
        content={
            "training_id": training_id,
            "status": "queued",
            "message": f"Training job queued. Check status at /trainings/{training_id}"
        }
    )


@app.get("/trainings/{training_id}")
def get_training_status(training_id: str):
    """Get training job status."""
    training_dir = TRAININGS / training_id
    status_file = training_dir / "status.json"

    if not status_file.exists():
        raise HTTPException(404, detail="Training job not found")

    status = json.loads(status_file.read_text())

    # Include full results if completed
    if status["status"] == "success":
        passport_path = training_dir / "output" / "passport.json"
        if passport_path.exists():
            status["passport"] = json.loads(passport_path.read_text())

        attestation_path = training_dir / "evidence.b64"
        if attestation_path.exists():
            status["attestation"] = attestation_path.read_text().strip()

    return status


@app.get("/trainings/{training_id}/artifacts/{name}")
def get_training_artifact(training_id: str, name: str):
    """Download training artifact."""
    artifacts_dir = TRAININGS / training_id / "output" / "checkpoints"
    artifact_path = artifacts_dir / name

    if not artifact_path.exists():
        raise HTTPException(404, detail="Artifact not found")

    return FileResponse(artifact_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)