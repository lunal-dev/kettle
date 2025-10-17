"""Tests for cargo.py."""

from pathlib import Path
from textwrap import dedent

import pytest

from attestable_builds.cargo import (
    Dependency,
    extract_git_commit,
    parse_cargo_lock,
)


def test_parse_cargo_lock(tmp_path):
    """Test parsing a Cargo.lock file."""
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text(dedent("""
        [[package]]
        name = "libc"
        version = "0.2.150"
        source = "registry+https://github.com/rust-lang/crates.io-index"
        checksum = "89d92a4743f9a61002fae18374ed11e7973f530cb3a3255fb354818118b2203c"

        [[package]]
        name = "my-crate"
        version = "0.1.0"

        [[package]]
        name = "serde"
        version = "1.0.195"
        source = "git+https://github.com/serde-rs/serde?rev=1234567890abcdef#1234567890abcdef"
    """))

    deps = parse_cargo_lock(cargo_lock)

    assert len(deps) == 2  # Excludes local package

    # Check registry dependency
    libc = next(d for d in deps if d.name == "libc")
    assert libc.version == "0.2.150"
    assert libc.source.startswith("registry+")
    assert libc.checksum == "89d92a4743f9a61002fae18374ed11e7973f530cb3a3255fb354818118b2203c"

    # Check git dependency
    serde = next(d for d in deps if d.name == "serde")
    assert serde.version == "1.0.195"
    assert serde.source.startswith("git+")


def test_extract_git_commit():
    """Test extracting commit hash from git source."""
    source = "git+https://github.com/serde-rs/serde?rev=123#1234567890abcdef"
    commit = extract_git_commit(source)
    assert commit == "1234567890abcdef"


def test_extract_git_commit_missing():
    """Test extracting commit when not present."""
    source = "git+https://github.com/serde-rs/serde"
    commit = extract_git_commit(source)
    assert commit is None
