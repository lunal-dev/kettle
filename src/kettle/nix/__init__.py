"""Nix flake support for attestable builds.

This module provides Nix-specific functionality for:
- Parsing flake.lock files
- Verifying flake inputs
- Nix toolchain information
- Running nix build and measuring outputs
- Generating Nix build passports
"""

from .parser import parse_flake_lock, hash_flake_lock, extract_direct_inputs
from .verifier import verify_flake_input, verify_all
from .toolchain import get_nix_toolchain_info
from .build import run_nix_build
from .passport import generate_nix_passport

__all__ = [
    "parse_flake_lock",
    "hash_flake_lock",
    "extract_direct_inputs",
    "verify_flake_input",
    "verify_all",
    "get_nix_toolchain_info",
    "run_nix_build",
    "generate_nix_passport",
]
