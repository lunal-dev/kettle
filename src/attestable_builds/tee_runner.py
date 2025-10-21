"""TEE Build Runner - Phase 2 orchestration.

This module orchestrates the complete Phase 2 build process that would
execute inside a Trusted Execution Environment (TEE).

For POC, this simulates TEE behavior with mock attestation. In production,
this code would run inside an Azure Confidential Computing VM.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .attestation import create_attestation_report, AttestationReport
from .build import run_cargo_build, BuildResult
from .cargo import hash_cargo_lock, parse_cargo_lock
from .git import get_git_info, GitSource
from .golden import calculate_runner_measurement, GoldenMeasurement
from .passport import generate_passport
from .toolchain import get_toolchain_info, ToolchainInfo
from .verify import verify_all, VerificationResult


class TEEBuildResult(NamedTuple):
    """Complete result of a TEE build execution."""
    success: bool
    launch_measurement: GoldenMeasurement
    git_source: GitSource | None
    cargo_lock_hash: str
    toolchain: ToolchainInfo
    verification_results: list[VerificationResult]
    build_result: BuildResult
    passport: dict
    attestation: AttestationReport
    output_artifacts: list[tuple[Path, str]]
    error_message: str | None = None


def hash_artifact(artifact_path: Path) -> str:
    """Calculate SHA256 hash of a build artifact."""
    return hashlib.sha256(artifact_path.read_bytes()).hexdigest()


def run_tee_build(
    project_dir: Path,
    passport_output: Path | None = None,
    attestation_output: Path | None = None,
    release: bool = True,
) -> TEEBuildResult:
    """Execute complete Phase 2 attestable build in TEE environment.

    This orchestrates the full build process:
    1. Generate launch measurement (hash of runner code)
    2. Verify Phase 1 inputs (git, cargo.lock, deps, toolchain)
    3. Execute cargo build
    4. Measure output artifacts
    5. Generate passport with outputs
    6. Create attestation report binding everything

    Args:
        project_dir: Path to Rust project directory
        passport_output: Optional path for passport JSON
        attestation_output: Optional path for attestation JSON
        release: Whether to build in release mode (default True)

    Returns:
        TEEBuildResult with all build data and attestation
    """
    try:
        # Phase 2 Step 1: Generate launch measurement
        # This proves which specific build runner code is executing
        launch_measurement = calculate_runner_measurement()

        # Phase 1 Step 1: Verify git source
        git_source = get_git_info(project_dir)
        if git_source and not git_source.is_clean:
            return TEEBuildResult(
                success=False,
                launch_measurement=launch_measurement,
                git_source=git_source,
                cargo_lock_hash="",
                toolchain=None,
                verification_results=[],
                build_result=BuildResult(False, [], "", ""),
                passport={},
                attestation=None,
                output_artifacts=[],
                error_message=f"Working tree has uncommitted changes: {git_source.dirty_files}",
            )

        # Phase 1 Step 2: Hash Cargo.lock
        cargo_lock = project_dir / "Cargo.lock"
        if not cargo_lock.exists():
            return TEEBuildResult(
                success=False,
                launch_measurement=launch_measurement,
                git_source=git_source,
                cargo_lock_hash="",
                toolchain=None,
                verification_results=[],
                build_result=BuildResult(False, [], "", ""),
                passport={},
                attestation=None,
                output_artifacts=[],
                error_message=f"Cargo.lock not found in {project_dir}",
            )

        cargo_lock_hash = hash_cargo_lock(cargo_lock)

        # Phase 1 Step 3: Verify dependencies
        dependencies = parse_cargo_lock(cargo_lock)
        verification_results = verify_all(dependencies)

        # Check if any verification failed
        failed_verifications = [r for r in verification_results if not r.verified]
        if failed_verifications:
            return TEEBuildResult(
                success=False,
                launch_measurement=launch_measurement,
                git_source=git_source,
                cargo_lock_hash=cargo_lock_hash,
                toolchain=None,
                verification_results=verification_results,
                build_result=BuildResult(False, [], "", ""),
                passport={},
                attestation=None,
                output_artifacts=[],
                error_message=f"{len(failed_verifications)} dependencies failed verification",
            )

        # Phase 1 Step 4: Verify toolchain
        toolchain = get_toolchain_info()

        # Phase 2 Step 2: Execute cargo build inside TEE
        build_result = run_cargo_build(project_dir, release=release)

        if not build_result.success:
            return TEEBuildResult(
                success=False,
                launch_measurement=launch_measurement,
                git_source=git_source,
                cargo_lock_hash=cargo_lock_hash,
                toolchain=toolchain,
                verification_results=verification_results,
                build_result=build_result,
                passport={},
                attestation=None,
                output_artifacts=[],
                error_message=f"Build failed: {build_result.stderr}",
            )

        # Phase 2 Step 3: Measure output artifacts
        output_artifacts = [
            (artifact, hash_artifact(artifact))
            for artifact in build_result.artifacts
        ]

        # Phase 2 Step 4: Generate passport with all inputs and outputs
        passport = generate_passport(
            git_source=git_source,
            cargo_lock_path=cargo_lock,
            cargo_lock_hash=cargo_lock_hash,
            toolchain=toolchain,
            verification_results=verification_results,
            output_artifacts=output_artifacts,
            output_path=passport_output,
        )

        # Phase 2 Step 5: Create attestation report
        # This binds the passport to the TEE's launch measurement
        attestation = create_attestation_report(
            launch_measurement=launch_measurement.runner_hash,
            passport=passport,
            output_path=attestation_output,
        )

        return TEEBuildResult(
            success=True,
            launch_measurement=launch_measurement,
            git_source=git_source,
            cargo_lock_hash=cargo_lock_hash,
            toolchain=toolchain,
            verification_results=verification_results,
            build_result=build_result,
            passport=passport,
            attestation=attestation,
            output_artifacts=output_artifacts,
        )

    except Exception as e:
        # Return error result with as much information as we have
        return TEEBuildResult(
            success=False,
            launch_measurement=launch_measurement if 'launch_measurement' in locals() else None,
            git_source=git_source if 'git_source' in locals() else None,
            cargo_lock_hash=cargo_lock_hash if 'cargo_lock_hash' in locals() else "",
            toolchain=toolchain if 'toolchain' in locals() else None,
            verification_results=verification_results if 'verification_results' in locals() else [],
            build_result=build_result if 'build_result' in locals() else BuildResult(False, [], "", ""),
            passport={},
            attestation=None,
            output_artifacts=[],
            error_message=str(e),
        )
