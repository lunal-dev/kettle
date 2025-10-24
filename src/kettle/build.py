"""Execute cargo build and collect output artifacts."""

from pathlib import Path
from subprocess import CalledProcessError

from kettle.subprocess_utils import run_command
from kettle.utils import hash_file


def run_cargo_build(project_dir: Path, release: bool = True) -> dict:
    """Execute cargo build and return artifacts with measurements.

    Returns:
        dict with:
            - success: bool
            - artifacts: list of dicts with 'path' and 'hash' keys
            - stdout: str
            - stderr: str
    """
    cmd = ["cargo", "build", "--locked"]
    if release:
        cmd.append("--release")

    try:
        result = run_command(cmd, cwd=project_dir)

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
                        artifacts.append({
                            "path": str(item),
                            "hash": hash_file(item),
                            "name": item.name,
                        })

        return {
            "success": True,
            "artifacts": artifacts,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except CalledProcessError as e:
        return {
            "success": False,
            "artifacts": [],
            "stdout": e.stdout,
            "stderr": e.stderr,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "artifacts": [],
            "stdout": "",
            "stderr": "cargo command not found",
        }
