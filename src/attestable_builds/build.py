"""Execute cargo build and collect output artifacts."""

import subprocess
from pathlib import Path
from typing import NamedTuple


class BuildResult(NamedTuple):
    """Result of executing cargo build."""
    success: bool
    artifacts: list[Path]
    stdout: str
    stderr: str


def run_cargo_build(project_dir: Path, release: bool = True) -> BuildResult:
    """Execute cargo build and return artifacts."""
    cmd = ["cargo", "build", "--locked"]
    if release:
        cmd.append("--release")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Find built artifacts
        target_dir = project_dir / "target"
        build_type = "release" if release else "debug"
        bin_dir = target_dir / build_type

        artifacts = []
        if bin_dir.exists():
            # Find all executables (no extension on Unix, .exe on Windows)
            for item in bin_dir.iterdir():
                if item.is_file() and (not item.suffix or item.suffix == ".exe"):
                    # Check if executable
                    if item.stat().st_mode & 0o111:
                        artifacts.append(item)

        return BuildResult(
            success=True,
            artifacts=artifacts,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    except subprocess.CalledProcessError as e:
        return BuildResult(
            success=False,
            artifacts=[],
            stdout=e.stdout,
            stderr=e.stderr,
        )
    except FileNotFoundError:
        return BuildResult(
            success=False,
            artifacts=[],
            stdout="",
            stderr="cargo command not found",
        )
