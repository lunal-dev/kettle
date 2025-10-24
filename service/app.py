"""Attestable builds API - calls existing CLI functions."""

import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from attestable_builds.cli import execute_build, generate_attestation, verify_inputs
from attestable_builds.passport import generate_passport
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

app = FastAPI(title="Attestable Builds Service")

BUILDS = Path("/var/lib/attestable-builds")
BUILDS.mkdir(parents=True, exist_ok=True)


@app.post("/build")
async def build(source: UploadFile = File(...)):
    """Upload zip, build with attestation."""
    build_id = str(uuid4())
    build_dir = BUILDS / build_id
    build_dir.mkdir()

    # Extract source
    source_dir = build_dir / "source"
    source_dir.mkdir()
    zip_path = build_dir / "source.zip"
    zip_path.write_bytes(await source.read())

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(source_dir)

    try:
        # Reuse CLI functions
        git_info, cargo_lock_hash, results, toolchain = verify_inputs(source_dir)
        build_result = execute_build(source_dir, release=True)

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

        # Generate attestation
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(build_dir)
            attestation_path, custom_data_path = generate_attestation(passport_data)
        finally:
            os.chdir(old_cwd)

        return {"build_id": build_id, "success": True}

    except Exception as e:
        return {"build_id": build_id, "success": False, "error": str(e)}


@app.get("/build/{build_id}/{file}")
def download(build_id: str, file: str):
    """Download passport.json, evidence.b64, custom_data.hex"""
    path = BUILDS / build_id / file
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
