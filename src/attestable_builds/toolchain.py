"""Extract and hash Rust toolchain binaries."""

import hashlib
import subprocess
from pathlib import Path


def get_toolchain_info() -> dict:
    """Get information about the current Rust toolchain.

    Uses rustup/cargo/rustc in PATH to find binaries and extract version info.

    Returns:
        Dict with toolchain info (paths, hashes, versions)

    Raises:
        subprocess.CalledProcessError: If commands fail
        FileNotFoundError: If rustup/cargo/rustc not found
    """
    # Find rustc binary path
    rustc_path_str = subprocess.run(
        ["rustup", "which", "rustc"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    rustc_path = Path(rustc_path_str)

    # Find cargo binary path
    cargo_path_str = subprocess.run(
        ["rustup", "which", "cargo"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    cargo_path = Path(cargo_path_str)

    # Get rustc version (full version string)
    rustc_version = subprocess.run(
        ["rustc", "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Get cargo version
    cargo_version = subprocess.run(
        ["cargo", "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Hash the binaries
    rustc_hash = hashlib.sha256(rustc_path.read_bytes()).hexdigest()
    cargo_hash = hashlib.sha256(cargo_path.read_bytes()).hexdigest()

    return {
        "rustc_path": rustc_path,
        "rustc_hash": rustc_hash,
        "rustc_version": rustc_version,
        "cargo_path": cargo_path,
        "cargo_hash": cargo_hash,
        "cargo_version": cargo_version,
    }
