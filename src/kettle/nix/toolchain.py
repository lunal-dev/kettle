"""Extract and hash Nix toolchain binary."""

import hashlib
from pathlib import Path

from kettle.subprocess_utils import run_command_stdout


def get_nix_toolchain_info() -> dict:
    """Get information about the Nix toolchain.

    Uses `which nix` to find the nix binary and extracts version info.

    Returns:
        Dict with toolchain info:
        {
            "nix_path": Path("/nix/store/.../bin/nix"),
            "nix_hash": "sha256:...",
            "nix_version": "nix (Nix) 2.18.1"
        }

    Raises:
        subprocess.CalledProcessError: If commands fail
        FileNotFoundError: If nix not found in PATH
    """
    # Find nix binary path
    nix_path = Path(run_command_stdout(["which", "nix"]))

    # Get nix version (full version string)
    nix_version = run_command_stdout(["nix", "--version"])

    # Hash the binary
    nix_hash = hashlib.sha256(nix_path.read_bytes()).hexdigest()

    return {
        "nix_path": nix_path,
        "nix_hash": nix_hash,
        "nix_version": nix_version,
    }
