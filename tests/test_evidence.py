"""Tests for evidence.py."""

from pathlib import Path

from attestable_builds.cargo import Dependency
from attestable_builds.evidence import generate_build_evidence
from attestable_builds.verify import VerificationResult, hash_cargo_lock


def test_generate_attestation_inputs_only(tmp_path):
    """Test generating build evidence for inputs only."""
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text("test content")
    cargo_lock_hash = hash_cargo_lock(cargo_lock)

    dep = Dependency("libc", "0.2.150", "registry+https://...", "abc123")
    results = [
        VerificationResult(dep, True, "Checksum matches"),
    ]

    output = tmp_path / "build-evidence.json"
    evidence = generate_build_evidence(cargo_lock, cargo_lock_hash, results, output_path=output)

    assert evidence["version"] == "1"
    assert "timestamp" in evidence
    assert evidence["cargo_lock"]["sha256"] == cargo_lock_hash
    assert evidence["verification_summary"]["total"] == 1
    assert evidence["verification_summary"]["verified"] == 1
    assert evidence["verification_summary"]["failed"] == 0
    assert len(evidence["dependencies"]) == 1
    assert "outputs" not in evidence

    # Check file was written
    assert output.exists()
    content = output.read_text()
    assert "libc" in content


def test_generate_attestation_with_outputs(tmp_path):
    """Test generating build evidence with output artifacts."""
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text("test content")
    cargo_lock_hash = hash_cargo_lock(cargo_lock)

    # Create fake artifact
    artifact = tmp_path / "my-binary"
    artifact.write_bytes(b"binary content")

    dep = Dependency("libc", "0.2.150", "registry+https://...", "abc123")
    results = [VerificationResult(dep, True, "Checksum matches")]

    evidence = generate_build_evidence(
        cargo_lock, cargo_lock_hash, results, output_artifacts=[artifact]
    )

    assert "outputs" in evidence
    assert len(evidence["outputs"]) == 1
    assert evidence["outputs"][0]["path"] == str(artifact)
    assert "sha256" in evidence["outputs"][0]
