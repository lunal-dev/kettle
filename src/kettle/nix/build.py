"""Execute nix build and collect output artifacts."""

from pathlib import Path
from subprocess import CalledProcessError

from kettle.subprocess_utils import run_command
from kettle.utils import hash_file


def run_nix_build(project_dir: Path) -> dict:
    """Execute nix build and return artifacts with measurements.

    Runs: nix build --no-link --print-out-paths

    This builds the default flake package and prints the /nix/store/
    output paths to stdout without creating a 'result' symlink.

    Returns:
        dict with:
            - success: bool
            - artifacts: list of dicts with 'path', 'hash', 'name', 'store_path'
            - store_paths: list of /nix/store/ paths
            - stdout: str
            - stderr: str
    """
    cmd = ["nix", "build", "--no-link", "--print-out-paths"]

    try:
        result = run_command(cmd, cwd=project_dir)

        # Parse output paths from stdout
        # Each line is a /nix/store/... path
        store_paths = [
            line.strip()
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ]

        # Find all binaries in the store paths
        artifacts = []
        for store_path_str in store_paths:
            store_path = Path(store_path_str)
            if not store_path.exists():
                continue

            # Look for binaries in {store_path}/bin/
            bin_dir = store_path / "bin"
            if bin_dir.exists() and bin_dir.is_dir():
                for item in bin_dir.iterdir():
                    if item.is_file():
                        # Check if executable (has any execute bit set)
                        if item.stat().st_mode & 0o111:
                            artifacts.append({
                                "path": str(item),
                                "hash": hash_file(item),
                                "name": item.name,
                                "store_path": str(store_path),
                            })

        return {
            "success": True,
            "artifacts": artifacts,
            "store_paths": store_paths,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except CalledProcessError as e:
        return {
            "success": False,
            "artifacts": [],
            "store_paths": [],
            "stdout": e.stdout,
            "stderr": e.stderr,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "artifacts": [],
            "store_paths": [],
            "stdout": "",
            "stderr": "nix command not found",
        }
