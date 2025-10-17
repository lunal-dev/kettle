"""Integration tests with real Cargo.lock examples."""

from pathlib import Path
from textwrap import dedent

import pytest

from attestable_builds.evidence import generate_build_evidence
from attestable_builds.cargo import parse_cargo_lock
from attestable_builds.verify import verify_all


@pytest.mark.asyncio
async def test_full_workflow(tmp_path):
    """Test complete workflow: parse -> verify -> attest."""
    # Create realistic Cargo.lock with actual crates.io package
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text(dedent("""
        version = 3

        [[package]]
        name = "my-project"
        version = "0.1.0"

        [[package]]
        name = "libc"
        version = "0.2.150"
        source = "registry+https://github.com/rust-lang/crates.io-index"
        checksum = "89d92a4743f9a61002fae18374ed11e7973f530cb3a3255fb354818118b2203c"

        [[package]]
        name = "git-dep"
        version = "0.1.0"
        source = "git+https://github.com/example/repo#1234567890abcdef1234567890abcdef12345678"
    """))

    # Parse dependencies
    deps = parse_cargo_lock(cargo_lock)
    assert len(deps) == 2

    # Verify all dependencies
    results = await verify_all(deps)
    assert len(results) == 2

    # Generate build evidence
    from attestable_builds.verify import hash_cargo_lock
    output = tmp_path / "build-evidence.json"
    cargo_lock_hash = hash_cargo_lock(cargo_lock)
    evidence = generate_build_evidence(cargo_lock, cargo_lock_hash, results, output_path=output)

    # Verify evidence structure
    assert evidence["version"] == "1"
    assert evidence["verification_summary"]["total"] == 2
    assert output.exists()

    # Check individual results
    libc_result = next(r for r in results if r.dependency.name == "libc")
    git_result = next(r for r in results if r.dependency.name == "git-dep")

    # Git dependency should verify (has commit hash)
    assert git_result.verified

    # Note: libc verification requires network call to crates.io
    # It may pass or fail depending on network/API availability
