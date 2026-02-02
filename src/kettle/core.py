"""Core toolchain abstraction and registry."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal, overload

_toolchains: list["Toolchain"] = []


class UnsupportedToolchainError(Exception):
    """Raised when no supported toolchain is detected for a project."""

    def __init__(self, project_dir: Path, found_files: list[str] | None = None):
        self.project_dir = project_dir
        self.found_files = found_files or []
        self.supported_toolchains = get_supported_toolchains()

        msg = (
            f"No supported build system detected. "
            f"Supported toolchains: {', '.join(self.supported_toolchains)}. "
            f"Expected one of: Cargo.toml (Rust/Cargo), flake.nix (Nix)."
        )
        super().__init__(msg)


def register(tc: "Toolchain") -> None:
    """Register a toolchain. Order matters for detection priority."""
    _toolchains.append(tc)


def get_supported_toolchains() -> list[str]:
    """Get list of registered toolchain names."""
    return [tc.name for tc in _toolchains]


@overload
def detect(project_dir: Path, raise_on_none: Literal[True]) -> "Toolchain": ...


@overload
def detect(project_dir: Path, raise_on_none: Literal[False] = ...) -> "Toolchain | None": ...


def detect(project_dir: Path, raise_on_none: bool = False) -> "Toolchain | None":
    """Auto-detect toolchain for project.

    Args:
        project_dir: Path to the project directory
        raise_on_none: If True, raise UnsupportedToolchainError instead of returning None

    Returns:
        Matching Toolchain or None

    Raises:
        UnsupportedToolchainError: If raise_on_none=True and no toolchain matches
    """
    for tc in _toolchains:
        if tc.detect(project_dir):
            return tc

    if raise_on_none:
        found_files = [
            f.name for f in project_dir.iterdir()
            if f.is_file() and not f.name.startswith(".")
        ][:10]
        raise UnsupportedToolchainError(project_dir, found_files)

    return None


def from_build_type(uri: str) -> "Toolchain | None":
    """Get toolchain from SLSA buildType URI."""
    for tc in _toolchains:
        if tc.build_type_uri in uri:
            return tc
    return None


class Toolchain(ABC):
    """Abstract base for toolchain implementations."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Toolchain identifier: 'cargo', 'nix', etc."""
        ...

    @property
    @abstractmethod
    def build_type_uri(self) -> str:
        """SLSA build type URI for provenance."""
        ...

    @property
    @abstractmethod
    def lockfile_name(self) -> str:
        """Lockfile filename: 'Cargo.lock', 'flake.lock', etc."""
        ...

    @abstractmethod
    def detect(self, project_dir: Path) -> bool:
        """Return True if this toolchain handles the project."""
        ...

    @abstractmethod
    def parse_lockfile(self, project_dir: Path) -> dict:
        """Parse lockfile and return {"path": Path, "hash": str, "deps": list[dict]}."""
        ...

    @abstractmethod
    def verify_deps(self, deps: list[dict]) -> list[dict]:
        """Verify dependencies. Return [{"dep": dict, "ok": bool, "msg": str}, ...]."""
        ...

    @abstractmethod
    def get_info(self) -> dict:
        """Return toolchain binary info as dict."""
        ...

    @abstractmethod
    def build(self, project_dir: Path, **kwargs) -> dict:
        """Execute build. Return {"ok": bool, "artifacts": list, "stdout": str, "stderr": str}."""
        ...

    @abstractmethod
    def get_build_artifacts(self, project_dir: Path) -> list[Path]:
        """Return paths to built executable artifacts."""
        ...

    @abstractmethod
    def dep_to_purl(self, dep: dict) -> dict:
        """Convert dependency to SLSA ResourceDescriptor with PURL."""
        ...

    @abstractmethod
    def internal_params(self, info: dict, lock_hash: str, lock: dict | None = None) -> dict:
        """Build SLSA internalParameters section."""
        ...

    @abstractmethod
    def merkle_entries(self, git: dict | None, lock: dict, info: dict) -> list[bytes]:
        """Return ordered list of bytes for merkle tree calculation."""
        ...
