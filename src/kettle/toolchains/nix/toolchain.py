"""Nix toolchain implementation."""

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from kettle.core import Toolchain
from kettle.utils import hash_file


class NixToolchain(Toolchain):
    """Nix flake toolchain for building Nix projects."""

    @property
    def name(self) -> str:
        return "nix"

    @property
    def build_type_uri(self) -> str:
        return "https://attestable-builds.dev/kettle/nix@v1"

    def detect(self, project_dir: Path) -> bool:
        return (project_dir / "flake.nix").exists()

    def parse_lockfile(self, project_dir: Path) -> dict:
        """Parse flake.lock and return lockfile info with inputs."""
        lock_path = project_dir / "flake.lock"
        flake_data = json.loads(lock_path.read_text())

        # Extract direct inputs from root node
        nodes = flake_data.get("nodes", {})
        root = nodes.get("root", {})
        root_inputs = root.get("inputs", {})

        deps = []
        for input_name, input_ref in root_inputs.items():
            if isinstance(input_ref, dict):
                input_ref = input_ref.get("id", input_ref)

            if input_ref in nodes:
                node = nodes[input_ref]
                locked = node.get("locked", {})

                dep = {
                    "name": input_name,
                    "type": locked.get("type"),
                    "narHash": locked.get("narHash"),
                }

                # Type-specific fields
                if locked.get("type") == "github":
                    dep["owner"] = locked.get("owner")
                    dep["repo"] = locked.get("repo")
                    dep["rev"] = locked.get("rev")
                elif locked.get("type") == "git":
                    dep["url"] = locked.get("url")
                    dep["rev"] = locked.get("rev")
                elif locked.get("type") == "path":
                    dep["path"] = locked.get("path")

                if "lastModified" in locked:
                    dep["lastModified"] = locked["lastModified"]

                deps.append(dep)

        return {
            "path": lock_path,
            "hash": hash_file(lock_path),
            "deps": deps,
        }

    def verify_deps(self, deps: list[dict]) -> list[dict]:
        """Verify flake inputs against nix store."""
        results = []

        for dep in deps:
            name = dep.get("name", "unknown")
            expected_hash = dep.get("narHash")

            if not expected_hash:
                results.append({"dep": dep, "ok": False, "msg": "No narHash in flake.lock"})
                continue

            store_path = self._find_store_path(dep)
            if not store_path:
                results.append({"dep": dep, "ok": False, "msg": f"Store path not found"})
                continue

            if self._verify_store_hash(store_path, expected_hash):
                results.append({"dep": dep, "ok": True, "msg": f"Verified: {expected_hash[:24]}..."})
            else:
                results.append({"dep": dep, "ok": False, "msg": f"narHash mismatch"})

        return results

    def get_info(self) -> dict:
        """Get nix binary info."""
        which_result = subprocess.run(["which", "nix"], capture_output=True, text=True, check=True)
        nix_path = Path(which_result.stdout.strip())

        version_result = subprocess.run(["nix", "--version"], capture_output=True, text=True, check=True)

        return {
            "nix_path": str(nix_path),
            "nix_hash": hashlib.sha256(nix_path.read_bytes()).hexdigest(),
            "nix_version": version_result.stdout.strip(),
        }

    def build(self, project_dir: Path, **kwargs) -> dict:
        """Execute nix build."""
        cmd = ["nix", "build", "--no-link", "--print-out-paths"]

        try:
            result = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, check=True)

            # Parse store paths from stdout
            store_paths = [
                line.strip() for line in result.stdout.strip().split("\n") if line.strip()
            ]

            # Create build directory and copy artifacts
            project_dir = project_dir.resolve()
            build_dir = project_dir / "build"
            build_dir.mkdir(parents=True, exist_ok=True)

            artifacts = []
            for store_path_str in store_paths:
                store_path = Path(store_path_str)
                if not store_path.exists():
                    continue

                bin_dir = store_path / "bin"
                if bin_dir.exists() and bin_dir.is_dir():
                    for item in bin_dir.iterdir():
                        if item.is_file() and item.stat().st_mode & 0o111:
                            local_path = build_dir / item.name
                            shutil.copy(item, local_path)
                            local_path.chmod(item.stat().st_mode)

                            artifacts.append({
                                "path": str(local_path),
                                "hash": hash_file(local_path),
                                "name": item.name,
                                "store_path": str(store_path),
                            })

            return {
                "ok": True,
                "artifacts": artifacts,
                "store_paths": store_paths,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        except subprocess.CalledProcessError as e:
            return {"ok": False, "artifacts": [], "store_paths": [], "stdout": e.stdout or "", "stderr": e.stderr or ""}
        except FileNotFoundError:
            return {"ok": False, "artifacts": [], "store_paths": [], "stdout": "", "stderr": "nix not found"}

    def dep_to_purl(self, dep: dict) -> dict:
        """Convert flake input to SLSA ResourceDescriptor with PURL."""
        name = dep["name"]
        nar_hash = dep.get("narHash", "")
        input_type = dep.get("type", "")

        # Build PURL: pkg:nix/name?narHash=...&type=...
        qualifiers = []
        if nar_hash:
            qualifiers.append(f"narHash={quote(nar_hash)}")
        if input_type:
            qualifiers.append(f"type={quote(input_type)}")

        purl = f"pkg:nix/{quote(name)}"
        if qualifiers:
            purl += "?" + "&".join(qualifiers)

        descriptor = {"uri": purl, "name": name}

        # Convert narHash (sha256-base64) to hex for SLSA digest
        if nar_hash and nar_hash.startswith("sha256-"):
            try:
                base64_hash = nar_hash[7:]
                hash_bytes = base64.b64decode(base64_hash)
                descriptor["digest"] = {"sha256": hash_bytes.hex()}
            except Exception:
                pass
            descriptor["annotations"] = {"narHash": nar_hash}

        return descriptor

    def internal_params(self, info: dict, lock_hash: str) -> dict:
        """Build SLSA internalParameters section."""
        return {
            "toolchain": {
                "nix": {
                    "version": info["nix_version"],
                    "digest": {"sha256": info["nix_hash"]},
                }
            },
            "lockfileHash": {"sha256": lock_hash},
        }

    def merkle_entries(self, git: dict | None, lock: dict, info: dict) -> list[bytes]:
        """Return ordered entries for merkle tree calculation."""
        entries = []

        # Git source info
        if git:
            if git.get("commit_hash"):
                entries.append(git["commit_hash"].encode())
            if git.get("tree_hash"):
                entries.append(git["tree_hash"].encode())
            if git.get("git_binary_hash"):
                entries.append(git["git_binary_hash"].encode())

        # Lockfile hash
        entries.append(lock["hash"].encode())

        # Flake inputs (sorted by name for determinism)
        for dep in sorted(lock["deps"], key=lambda x: x["name"]):
            if dep.get("narHash"):
                entries.append(dep["narHash"].encode())

        # Toolchain info
        entries.append(info["nix_hash"].encode())
        entries.append(info["nix_version"].encode())

        return entries

    def merkle_entries_labeled(self, git: dict | None, lock: dict, info: dict) -> list[tuple[str, str, bytes]]:
        """Return labeled entries for inclusion proofs."""
        entries = []

        if git:
            if git.get("commit_hash"):
                v = git["commit_hash"]
                entries.append(("git_commit", v, v.encode()))
            if git.get("tree_hash"):
                v = git["tree_hash"]
                entries.append(("git_tree", v, v.encode()))
            if git.get("git_binary_hash"):
                v = git["git_binary_hash"]
                entries.append(("git_binary", v, v.encode()))

        entries.append(("lockfile_hash", lock["hash"], lock["hash"].encode()))

        for dep in sorted(lock["deps"], key=lambda x: x["name"]):
            if dep.get("narHash"):
                v = dep["narHash"]
                entries.append((f"input:{dep['name']}", v, v.encode()))

        entries.append(("nix_hash", info["nix_hash"], info["nix_hash"].encode()))
        entries.append(("nix_version", info["nix_version"], info["nix_version"].encode()))

        return entries

    # Private helpers

    def _find_store_path(self, dep: dict) -> Path | None:
        """Find /nix/store path for a flake input."""
        input_type = dep.get("type")

        if input_type == "github":
            owner, repo, rev = dep.get("owner"), dep.get("repo"), dep.get("rev")
            if not all([owner, repo, rev]):
                return None
            flake_ref = f"github:{owner}/{repo}/{rev}"
        elif input_type == "git":
            url, rev = dep.get("url"), dep.get("rev")
            if not all([url, rev]):
                return None
            flake_ref = f"{url}?rev={rev}"
        elif input_type == "path":
            path = dep.get("path")
            return Path(path) if path else None
        else:
            return None

        try:
            result = subprocess.run(
                ["nix", "flake", "metadata", "--json", flake_ref],
                capture_output=True, text=True, check=True, timeout=30,
            )
            metadata = json.loads(result.stdout)
            store_path = metadata.get("path")
            return Path(store_path) if store_path else None
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def _verify_store_hash(self, store_path: Path, expected_hash: str) -> bool:
        """Verify store path against expected narHash."""
        if not store_path.exists():
            return False

        try:
            result = subprocess.run(
                ["nix", "hash", "path", str(store_path)],
                capture_output=True, text=True, check=True, timeout=30,
            )
            return result.stdout.strip() == expected_hash
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
