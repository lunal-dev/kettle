"""Nix flake support for attestable builds.

This module provides Nix-specific functionality for:
- Parsing flake.lock files
- Verifying flake inputs
- Nix toolchain information
- Running nix build and measuring outputs
- Generating Nix build passports
"""

from .parser import parse_flake_lock, hash_flake_lock, extract_direct_inputs
from .verification import verify_flake_input, verify_flake_inputs, verify_nix_inputs
from .toolchain import get_nix_toolchain_info
from .build import run_nix_build
from .provenance import generate_nix_provenance, verify_nix_build_provenance, generate_nix_passport, verify_nix_build_passport

__all__ = [
    "parse_flake_lock",
    "hash_flake_lock",
    "extract_direct_inputs",
    "verify_flake_input",
    "verify_flake_inputs",
    "get_nix_toolchain_info",
    "run_nix_build",
    "generate_nix_provenance",
    "verify_nix_build_provenance",
    "generate_nix_passport",  # Backward compatibility
    "verify_nix_build_passport",  # Backward compatibility
    "verify_nix_inputs",
]
