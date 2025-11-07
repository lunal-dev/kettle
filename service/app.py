"""Minimal attestable builds API."""

import json
import os
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from kettle.cli import execute_build, generate_attestation, verify_inputs
from kettle.passport import generate_passport

app = FastAPI(title="Attestable Builds Service")

BUILDS = Path(os.getenv("KETTLE_STORAGE_DIR", "/tmp/kettle"))
BUILDS.mkdir(parents=True, exist_ok=True)


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

        # Create build-config directory and copy Cargo.lock
        build_config_dir = build_dir / "build-config"
        build_config_dir.mkdir()
        cargo_lock_path = project_dir / "Cargo.lock"
        if cargo_lock_path.exists():
            shutil.copy2(cargo_lock_path, build_config_dir / "Cargo.lock")

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

        # List build-config files
        build_config_files = [f.name for f in build_config_dir.iterdir() if f.is_file()]

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
            "build_config_files": build_config_files,
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


@app.get("/builds/{build_id}/build-config/{name}")
def get_build_config(build_id: str, name: str):
    """Download build config file (e.g., Cargo.lock)."""
    from fastapi.responses import FileResponse
    path = BUILDS / build_id / "build-config" / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)