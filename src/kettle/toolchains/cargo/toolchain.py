"""Cargo toolchain implementation."""

import hashlib
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from kettle.core import Toolchain
from kettle.utils import hash_file


class CargoToolchain(Toolchain):
    """Rust/Cargo toolchain for building Cargo projects."""

    @property
    def name(self) -> str:
        return "cargo"

    @property
    def build_type_uri(self) -> str:
        return "https://attestable-builds.dev/kettle/cargo@v1"

    def detect(self, project_dir: Path) -> bool:
        return (project_dir / "Cargo.toml").exists()

    def parse_lockfile(self, project_dir: Path) -> dict:
        """Parse Cargo.lock and return lockfile info with dependencies."""
        lock_path = project_dir / "Cargo.lock"
        content = lock_path.read_text()
        deps = []

        # Parse [[package]] sections
        pattern = re.compile(
            r'\[\[package\]\]\s+name = "([^"]+)"\s+version = "([^"]+)"'
            r'(?:\s+source = "([^"]+)")?(?:[^\[]*checksum = "([^"]+)")?',
            re.MULTILINE | re.DOTALL
        )

        for match in pattern.finditer(content):
            name, version, source, checksum = match.groups()
            if source:  # Only external dependencies
                deps.append({
                    "name": name,
                    "version": version,
                    "source": source,
                    "checksum": checksum,
                })

        return {
            "path": lock_path,
            "hash": hash_file(lock_path),
            "deps": deps,
        }

    def verify_deps(self, deps: list[dict]) -> list[dict]:
        """Verify dependencies against cargo cache."""
        results = []
        cargo_home = self._get_cargo_home()
        cached_crates = self._get_cached_crates(cargo_home)
        cargo_lock_deps = {(d["name"], d["version"]): d for d in deps}

        # Verify registry crates
        for name, version, crate_path in cached_crates:
            key = (name, version)
            if key not in cargo_lock_deps:
                continue

            dep = cargo_lock_deps[key]
            actual_hash = hashlib.sha256(crate_path.read_bytes()).hexdigest()

            if dep["checksum"] is None:
                results.append({"dep": dep, "ok": False, "msg": "No checksum in Cargo.lock"})
            elif actual_hash == dep["checksum"]:
                results.append({"dep": dep, "ok": True, "msg": f"Verified: {actual_hash[:8]}..."})
            else:
                results.append({"dep": dep, "ok": False, "msg": f"Mismatch: {actual_hash[:8]}..."})

        # Verify git dependencies
        for dep in deps:
            if dep["source"].startswith("git+"):
                commit = self._extract_git_commit(dep["source"])
                if commit:
                    results.append({"dep": dep, "ok": True, "msg": f"Pinned: {commit[:8]}..."})
                else:
                    results.append({"dep": dep, "ok": False, "msg": "Not pinned to commit"})

        return results

    def get_info(self) -> dict:
        """Get rustc and cargo binary info."""
        rustc_result = subprocess.run(
            ["rustup", "which", "rustc"], capture_output=True, text=True, check=True
        )
        rustc_path = Path(rustc_result.stdout.strip())

        cargo_result = subprocess.run(
            ["rustup", "which", "cargo"], capture_output=True, text=True, check=True
        )
        cargo_path = Path(cargo_result.stdout.strip())

        rustc_ver = subprocess.run(
            ["rustc", "--version"], capture_output=True, text=True, check=True
        )
        cargo_ver = subprocess.run(
            ["cargo", "--version"], capture_output=True, text=True, check=True
        )

        return {
            "rustc_path": rustc_path,
            "rustc_hash": hashlib.sha256(rustc_path.read_bytes()).hexdigest(),
            "rustc_version": rustc_ver.stdout.strip(),
            "cargo_path": cargo_path,
            "cargo_hash": hashlib.sha256(cargo_path.read_bytes()).hexdigest(),
            "cargo_version": cargo_ver.stdout.strip(),
        }

    def build(self, project_dir: Path, **kwargs) -> dict:
        """Execute cargo build."""
        release = kwargs.get("release", True)
        cmd = ["cargo", "build", "--locked"]
        if release:
            cmd.append("--release")

        try:
            result = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, check=True)

            # Find artifacts
            build_type = "release" if release else "debug"
            bin_dir = project_dir / "target" / build_type
            artifacts = []

            if bin_dir.exists():
                for item in bin_dir.iterdir():
                    if item.is_file() and (not item.suffix or item.suffix == ".exe"):
                        if item.stat().st_mode & 0o111:  # Executable
                            artifacts.append({
                                "path": str(item),
                                "hash": hash_file(item),
                                "name": item.name,
                            })

            return {
                "ok": True,
                "artifacts": artifacts,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        except subprocess.CalledProcessError as e:
            return {"ok": False, "artifacts": [], "stdout": e.stdout or "", "stderr": e.stderr or ""}
        except FileNotFoundError:
            return {"ok": False, "artifacts": [], "stdout": "", "stderr": "cargo not found"}

    def dep_to_purl(self, dep: dict) -> dict:
        """Convert dependency to SLSA ResourceDescriptor with PURL."""
        name, version, checksum = dep["name"], dep["version"], dep["checksum"]
        purl = f"pkg:cargo/{quote(name)}@{quote(version)}?checksum=sha256:{checksum}"
        return {
            "uri": purl,
            "digest": {"sha256": checksum},
            "name": name,
        }

    def internal_params(self, info: dict, lock_hash: str) -> dict:
        """Build SLSA internalParameters section."""
        return {
            "toolchain": {
                "rustc": {
                    "version": info["rustc_version"],
                    "digest": {"sha256": info["rustc_hash"]},
                },
                "cargo": {
                    "version": info["cargo_version"],
                    "digest": {"sha256": info["cargo_hash"]},
                },
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

        # Dependencies (sorted for determinism)
        for dep in sorted(lock["deps"], key=lambda x: (x["name"], x["version"])):
            if dep.get("checksum"):
                entry = f"{dep['name']}:{dep['version']}:{dep['checksum']}"
                entries.append(entry.encode())

        # Toolchain info
        entries.append(info["rustc_hash"].encode())
        entries.append(info["rustc_version"].encode())
        entries.append(info["cargo_hash"].encode())
        entries.append(info["cargo_version"].encode())

        return entries

    def merkle_entries_labeled(self, git: dict | None, lock: dict, info: dict) -> list[tuple[str, str, bytes]]:
        """Return labeled entries for inclusion proofs: (label, value_str, value_bytes)."""
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

        for dep in sorted(lock["deps"], key=lambda x: (x["name"], x["version"])):
            if dep.get("checksum"):
                v = f"{dep['name']}:{dep['version']}:{dep['checksum']}"
                entries.append((f"dep:{dep['name']}@{dep['version']}", v, v.encode()))

        entries.append(("rustc_hash", info["rustc_hash"], info["rustc_hash"].encode()))
        entries.append(("rustc_version", info["rustc_version"], info["rustc_version"].encode()))
        entries.append(("cargo_hash", info["cargo_hash"], info["cargo_hash"].encode()))
        entries.append(("cargo_version", info["cargo_version"], info["cargo_version"].encode()))

        return entries

    # Private helpers

    def _get_cargo_home(self) -> Path:
        cargo_home = os.environ.get("CARGO_HOME")
        return Path(cargo_home) if cargo_home else Path.home() / ".cargo"

    def _get_cached_crates(self, cargo_home: Path) -> list[tuple[str, str, Path]]:
        """Get all .crate files from cargo cache."""
        cache_dir = cargo_home / "registry" / "cache"
        if not cache_dir.exists():
            return []

        crates = []
        for index_dir in cache_dir.iterdir():
            if index_dir.is_dir():
                for crate_file in index_dir.glob("*.crate"):
                    parts = crate_file.stem.rsplit("-", 1)
                    if len(parts) == 2:
                        crates.append((parts[0], parts[1], crate_file))
        return crates

    def _extract_git_commit(self, source: str) -> str | None:
        match = re.search(r'#([a-f0-9]+)$', source)
        return match.group(1) if match else None
