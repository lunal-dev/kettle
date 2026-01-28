"""Toolchain implementations.

Auto-registers all toolchains on import.
"""

from kettle import core
from .cargo import CargoToolchain
from .nix import NixToolchain

# Register toolchains (order matters for detection priority)
# Nix first since flake.nix is more specific than Cargo.toml
core.register(NixToolchain())
core.register(CargoToolchain())

__all__ = ["CargoToolchain", "NixToolchain"]
