"""Nix toolchain implementation."""

import base64
import hashlib
import json
import shutil
import subprocess
import warnings
from pathlib import Path
from urllib.parse import quote

from kettle.core import Toolchain
from kettle.utils import hash_file


class NixEvaluationError(Exception):
    """Raised when nix derivation evaluation fails."""
    pass


class NixToolchain(Toolchain):
    """Nix flake toolchain for building Nix projects."""

    @property
    def name(self) -> str:
        return "nix"

    @property
    def build_type_uri(self) -> str:
        return "https://attestable-builds.dev/kettle/nix@v1"

    @property
    def lockfile_name(self) -> str:
        return "flake.lock"

    def detect(self, project_dir: Path) -> bool:
        return (project_dir / "flake.nix").exists()

    def evaluate_derivation_graph(
        self, project_dir: Path, output: str = "default", timeout: int = 300
    ) -> dict:
        """Evaluate the full derivation graph for a flake output.

        Args:
            project_dir: Path to project containing flake.nix
            output: Flake output to evaluate (default: "default")
            timeout: Evaluation timeout in seconds

        Returns:
            dict: Derivation graph keyed by derivation path

        Raises:
            NixEvaluationError: If nix evaluation fails
        """
        cmd = ["nix", "derivation", "show", f".#{output}", "--recursive"]

        try:
            result = subprocess.run(
                cmd,
                cwd=project_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            raise NixEvaluationError(f"nix derivation show failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise NixEvaluationError(f"Derivation evaluation timed out after {timeout}s")
        except json.JSONDecodeError as e:
            raise NixEvaluationError(f"Failed to parse derivation JSON: {e}")
        except FileNotFoundError:
            raise NixEvaluationError("nix command not found")

    def extract_fixed_output_hashes(self, graph: dict) -> list[dict]:
        """Extract fixed-output derivations from derivation graph.

        Fixed-output derivations (FODs) are identified by presence of
        'outputHash' in their env section. These represent content-addressed
        network fetches.

        Args:
            graph: Derivation graph from evaluate_derivation_graph()

        Returns:
            list[dict]: Sorted list of fixed-output derivations with:
                - name: derivation name
                - drv_path: /nix/store/xxx.drv path
                - outputHash: content hash
                - outputHashAlgo: hash algorithm (usually sha256)
                - outputHashMode: "flat" or "recursive"
                - url: source URL if available
                - urls: multiple URLs if available
        """
        fetches = []

        for drv_path, drv_data in graph.items():
            env = drv_data.get("env", {})

            # Fixed-output derivations have outputHash in env
            if "outputHash" not in env:
                continue

            fetch = {
                "name": drv_data.get("name", env.get("name", "unknown")),
                "drv_path": drv_path,
                "outputHash": env["outputHash"],
                "outputHashAlgo": env.get("outputHashAlgo", "sha256"),
            }

            # Optional fields
            if "outputHashMode" in env:
                fetch["outputHashMode"] = env["outputHashMode"]
            if "url" in env:
                fetch["url"] = env["url"]
            if "urls" in env:
                fetch["urls"] = env["urls"]

            fetches.append(fetch)

        # Sort by name for determinism
        return sorted(fetches, key=lambda x: x["name"])

    def parse_lockfile(self, project_dir: Path, deep: bool = True) -> dict:
        """Parse flake.lock and optionally evaluate derivation graph.

        Args:
            project_dir: Path to project directory
            deep: If True, evaluate full derivation graph for FOD hashes (default: True)

        Returns:
            dict with:
                - path: Path to flake.lock
                - hash: SHA256 of flake.lock
                - deps: list of flake inputs (always populated)
                - fetches: list of fixed-output derivations (only if deep=True succeeds)
                - derivation_count: total derivations evaluated (only if deep=True)
                - evaluation_mode: "deep" or "shallow"
        """
        lock_path = project_dir / "flake.lock"
        flake_data = json.loads(lock_path.read_text())

        # Extract direct inputs from root node (shallow parsing)
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

        result = {
            "path": lock_path,
            "hash": hash_file(lock_path),
            "deps": deps,
            "evaluation_mode": "shallow",
        }

        # Deep evaluation: get full derivation graph and extract FODs
        if deep:
            try:
                graph = self.evaluate_derivation_graph(project_dir)
                fetches = self.extract_fixed_output_hashes(graph)
                result["fetches"] = fetches
                result["derivation_count"] = len(graph)
                result["evaluation_mode"] = "deep"
            except NixEvaluationError as e:
                warnings.warn(f"Deep evaluation failed, using shallow mode: {e}")
            except Exception as e:
                warnings.warn(f"Unexpected error in deep evaluation, using shallow mode: {e}")

        return result

    def verify_deps(self, deps: list[dict]) -> list[dict]:
        """Verify flake inputs against nix store."""
        results = []

        for dep in deps:
            name = dep.get("name", "unknown")
            expected_hash = dep.get("narHash")

            if not expected_hash:
                results.append({"dependency": dep, "verified": False, "message": "No narHash in flake.lock"})
                continue

            store_path = self._find_store_path(dep)
            if not store_path:
                results.append({"dependency": dep, "verified": False, "message": "Store path not found"})
                continue

            if self._verify_store_hash(store_path, expected_hash):
                results.append({"dependency": dep, "verified": True, "message": f"Verified: {expected_hash[:24]}..."})
            else:
                results.append({"dependency": dep, "verified": False, "message": "narHash mismatch"})

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
            build_dir = project_dir / "kettle-build"
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
                            # Remove existing file if present (may have read-only permissions from previous nix build)
                            if local_path.exists():
                                local_path.chmod(0o644)
                                local_path.unlink()
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

    def get_build_artifacts(self, project_dir: Path) -> list[Path]:
        """Return paths to built executable artifacts."""
        build_dir = project_dir / "kettle-build"
        artifacts = []

        if build_dir.exists():
            for item in build_dir.iterdir():
                if item.is_file() and item.stat().st_mode & 0o111:  # Executable
                    artifacts.append(item)

        return artifacts

    def dep_to_purl(self, dep: dict) -> dict:
        """Convert dependency to SLSA ResourceDescriptor with PURL.

        Handles both flake inputs (shallow mode) and fetch entries (deep mode).
        """
        # Check if this is a fetch entry (from deep evaluation)
        if "outputHash" in dep:
            return self._fetch_to_purl(dep)
        # Otherwise it's a flake input (shallow evaluation)
        return self._flake_input_to_purl(dep)

    def _fetch_to_purl(self, fetch: dict) -> dict:
        """Convert fixed-output derivation to PURL ResourceDescriptor."""
        name = fetch["name"]
        algo = fetch.get("outputHashAlgo", "sha256")
        hash_value = fetch["outputHash"]

        # PURL format: pkg:nix-fetch/{name}?hash={algo}:{hash}
        purl = f"pkg:nix-fetch/{quote(name)}?hash={algo}:{quote(hash_value)}"

        descriptor = {
            "uri": purl,
            "name": name,
            "digest": {algo: hash_value},
        }

        # Add annotations for additional context
        annotations = {}
        if fetch.get("url"):
            annotations["url"] = fetch["url"]
        if fetch.get("urls"):
            annotations["urls"] = ",".join(fetch["urls"]) if isinstance(fetch["urls"], list) else fetch["urls"]
        if fetch.get("outputHashMode"):
            annotations["outputHashMode"] = fetch["outputHashMode"]
        if fetch.get("drv_path"):
            annotations["drvPath"] = fetch["drv_path"]

        if annotations:
            descriptor["annotations"] = annotations

        return descriptor

    def _flake_input_to_purl(self, dep: dict) -> dict:
        """Convert flake input to PURL ResourceDescriptor."""
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

    def internal_params(self, info: dict, lock_hash: str, lock: dict | None = None) -> dict:
        """Build SLSA internalParameters section.

        Args:
            info: Toolchain info from get_info()
            lock_hash: SHA256 of lockfile
            lock: Optional lock dict with evaluation metadata
        """
        params = {
            "toolchain": {
                "nix": {
                    "version": info["nix_version"],
                    "digest": {"sha256": info["nix_hash"]},
                }
            },
            "lockfileHash": {"sha256": lock_hash},
        }

        # Add evaluation metadata if available
        if lock:
            params["evaluation"] = {
                "mode": lock.get("evaluation_mode", "shallow"),
            }
            if lock.get("derivation_count"):
                params["evaluation"]["derivationCount"] = lock["derivation_count"]
            if lock.get("fetches"):
                params["evaluation"]["fetchCount"] = len(lock["fetches"])

            # Keep flake inputs in internalParameters for human context
            if lock.get("deps"):
                params["flakeInputs"] = [
                    {"name": d["name"], "narHash": d.get("narHash")}
                    for d in lock["deps"]
                    if d.get("narHash")
                ]

        return params

    def merkle_entries(self, git: dict | None, lock: dict, info: dict) -> list[bytes]:
        """Return ordered entries for merkle tree calculation.

        Uses fetches (FOD hashes) if available from deep evaluation,
        otherwise falls back to flake input narHashes.
        """
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

        # Use fetches if available (deep mode), otherwise use deps (shallow mode)
        if lock.get("fetches"):
            # Deep mode: fixed-output derivation hashes
            for fetch in sorted(lock["fetches"], key=lambda x: x["name"]):
                # Format: fetch:{name}:{algo}:{hash}
                entry = f"fetch:{fetch['name']}:{fetch['outputHashAlgo']}:{fetch['outputHash']}"
                entries.append(entry.encode())
        else:
            # Shallow mode: flake input narHashes
            for dep in sorted(lock["deps"], key=lambda x: x["name"]):
                if dep.get("narHash"):
                    entries.append(dep["narHash"].encode())

        # Toolchain info
        entries.append(info["nix_hash"].encode())
        entries.append(info["nix_version"].encode())

        return entries

    def merkle_entries_labeled(self, git: dict | None, lock: dict, info: dict) -> list[tuple[str, str, bytes]]:
        """Return labeled entries for inclusion proofs.

        Uses fetches (FOD hashes) if available from deep evaluation,
        otherwise falls back to flake input narHashes.
        """
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

        # Use fetches if available (deep mode), otherwise use deps (shallow mode)
        if lock.get("fetches"):
            # Deep mode: fixed-output derivation hashes
            for fetch in sorted(lock["fetches"], key=lambda x: x["name"]):
                v = f"fetch:{fetch['name']}:{fetch['outputHashAlgo']}:{fetch['outputHash']}"
                label = f"fetch:{fetch['name']}"
                entries.append((label, v, v.encode()))
        else:
            # Shallow mode: flake input narHashes
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
