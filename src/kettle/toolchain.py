"""Extract and hash Rust toolchain binaries."""

import hashlib
from pathlib import Path

from kettle.subprocess_utils import run_command_stdout


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
    rustc_path = Path(run_command_stdout(["rustup", "which", "rustc"]))

    # Find cargo binary path
    cargo_path = Path(run_command_stdout(["rustup", "which", "cargo"]))

    # Get rustc version (full version string)
    rustc_version = run_command_stdout(["rustc", "--version"])

    # Get cargo version
    cargo_version = run_command_stdout(["cargo", "--version"])

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
