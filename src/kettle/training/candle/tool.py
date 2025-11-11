"""
Training tool manager for Candle-based attestable training.

This module manages the kettle-train binary:
- Auto-builds from integrated Rust source on first use
- Caches binary and build passport
- Provides interface for Python orchestration

The binary automatically installs when you run `kettle train`.
Use `--rebuild-binary` flag to force rebuild during development.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional
import shutil

from rich.console import Console

console = Console()


# Cache and binary constants
DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "kettle"
DEFAULT_BINARY_CACHE = DEFAULT_CACHE_ROOT / "training"
TRAINING_BINARY_NAME = "kettle-train"
BIN_SUBDIR = "bin"
CONFIGS_SUBDIR = "configs"


class CandleTrainingTool:
    """Manages the Candle training binary.

    The binary auto-installs on first use via `ensure_binary()`.
    Cached at ~/.cache/kettle/training/ for subsequent runs.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        """
        Initialize the training tool manager.

        Args:
            cache_dir: Directory to cache binary. Defaults to DEFAULT_BINARY_CACHE
        """
        if cache_dir is None:
            cache_dir = DEFAULT_BINARY_CACHE

        self.cache_dir = Path(cache_dir)
        self.bin_dir = self.cache_dir / BIN_SUBDIR
        self.binary_path = self.bin_dir / TRAINING_BINARY_NAME
        self.build_passport_path = self.cache_dir / "build-passport.json"

        # Source is integrated in the package
        self.source_dir = Path(__file__).parent

        # Ensure directories exist
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)

    def is_installed(self) -> bool:
        """Check if the training binary is installed and cached."""
        return self.binary_path.exists() and self.build_passport_path.exists()

    def get_binary_path(self) -> Path:
        """Get path to the training binary, ensuring it's installed."""
        if not self.is_installed():
            raise RuntimeError(
                "Training binary not installed. Run 'kettle train-tool install' first."
            )
        return self.binary_path

    def get_build_passport(self) -> dict:
        """Load the build passport for the training binary."""
        if not self.build_passport_path.exists():
            raise RuntimeError("Build passport not found. Binary may not be properly installed.")

        with open(self.build_passport_path, "r") as f:
            return json.load(f)


    def build_with_attestation(self) -> None:
        """
        Build the training binary from integrated source.

        This creates a build passport proving the binary's provenance.
        """
        cargo_toml = self.source_dir / "Cargo.toml"
        if not cargo_toml.exists():
            raise RuntimeError(f"Cargo.toml not found at {cargo_toml}")

        console.print("[cyan]Building kettle-train from integrated source...")

        # Build release binary
        cmd = ["cargo", "build", "--release", "--manifest-path", str(cargo_toml)]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.source_dir))

        if result.returncode != 0:
            console.print("[red]Build failed:")
            console.print(result.stderr)
            raise RuntimeError(f"Failed to build training binary")

        # Copy binary to cache
        source_binary = self.source_dir / "target" / "release" / TRAINING_BINARY_NAME
        if not source_binary.exists():
            raise RuntimeError(f"Built binary not found at {source_binary}")

        # Ensure cache directories exist (in case they were removed)
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy(source_binary, self.binary_path)
        self.binary_path.chmod(0o755)

        console.print(f"[green]✓ Binary built and cached at {self.binary_path}")

        # Generate build passport
        self._generate_build_passport()

    def _generate_build_passport(self) -> None:
        """Generate a build passport for the binary."""
        # Get git commit hash from the main repo
        repo_root = Path(__file__).parent.parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        commit_hash = result.stdout.strip() if result.returncode == 0 else "unknown"

        # Create passport
        passport = {
            "version": "1.0",
            "binary_path": str(self.binary_path),
            "source_dir": str(self.source_dir),
            "commit_hash": commit_hash,
            "build_method": "cargo",
        }

        with open(self.build_passport_path, "w") as f:
            json.dump(passport, f, indent=2)

        console.print(f"[green]✓ Build passport saved to {self.build_passport_path}")

    def ensure_binary(self) -> Path:
        """
        Ensure the training binary is available. Build if needed.

        Returns:
            Path to the training binary.
        """
        if self.is_installed():
            console.print("[green]✓ Training binary already installed")
            return self.binary_path

        console.print("[yellow]Training binary not found. Building...")
        self.build_with_attestation()

        return self.binary_path

    def remove(self) -> None:
        """Remove the cached binary and build passport.

        This is mainly for manual cleanup. The binary will auto-rebuild
        on next `kettle train` run.
        """
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            console.print("[green]✓ Training tool removed")
        else:
            console.print("[yellow]Training tool not installed")

    def info(self) -> dict:
        """Get information about the installed training tool.

        Returns installation status, binary path, size, and build passport info.
        """
        if not self.is_installed():
            return {"installed": False}

        passport = self.get_build_passport()
        binary_size = self.binary_path.stat().st_size

        return {
            "installed": True,
            "binary_path": str(self.binary_path),
            "binary_size_mb": round(binary_size / (1024 * 1024), 2),
            "build_passport": str(self.build_passport_path),
            "commit_hash": passport.get("commit_hash", "unknown"),
            "cache_dir": str(self.cache_dir),
        }
