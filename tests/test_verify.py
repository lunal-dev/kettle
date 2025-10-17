"""Tests for verify.py."""

import pytest

from attestable_builds.cargo import Dependency
from attestable_builds.verify import verify_all


@pytest.mark.asyncio
async def test_verify_git_commit_pinned():
    """Test verifying git dependency with pinned commit."""
    dep = Dependency(
        name="serde",
        version="1.0.195",
        source="git+https://github.com/serde-rs/serde#1234567890abcdef",
        checksum=None,
    )
    results = await verify_all([dep])
    assert len(results) == 1
    assert results[0].verified
    assert "12345678" in results[0].message


@pytest.mark.asyncio
async def test_verify_git_commit_not_pinned():
    """Test verifying git dependency without pinned commit."""
    dep = Dependency(
        name="serde",
        version="1.0.195",
        source="git+https://github.com/serde-rs/serde",
        checksum=None,
    )
    results = await verify_all([dep])
    assert len(results) == 1
    assert not results[0].verified
    assert "not pinned" in results[0].message
