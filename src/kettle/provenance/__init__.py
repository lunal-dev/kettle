"""Provenance generation and verification for attestable builds."""

from .generate import generate, verify
from .slsa import (
    generate_slsa_statement,
    hash_slsa_statement,
    build_subject,
    build_source_descriptor,
    build_byproduct,
)
from .verification import (
    verify_inputs,
    verify_git_source,
    run_verify_passport_workflow,
    run_verify_attestation_workflow,
    run_combined_verify_workflow,
)

__all__ = [
    # Generation
    "generate",
    "verify",
    "generate_slsa_statement",
    "hash_slsa_statement",
    "build_subject",
    "build_source_descriptor",
    "build_byproduct",
    # Verification workflows
    "verify_inputs",
    "verify_git_source",
    "run_verify_passport_workflow",
    "run_verify_attestation_workflow",
    "run_combined_verify_workflow",
]
