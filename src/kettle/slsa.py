"""Generate SLSA v1.2 Build Provenance in in-toto attestation format.

This module provides generic SLSA statement generation. Build-system-specific
logic should be handled in the respective build system modules (provenance.py files).
"""

import json
from pathlib import Path


def generate_slsa_statement(
    subject: list[dict],
    build_type: str,
    external_parameters: dict,
    internal_parameters: dict,
    resolved_dependencies: list[dict],
    builder_id: str,
    metadata: dict,
    byproducts: list[dict],
) -> dict:
    """Generate SLSA v1.2 provenance statement (generic, build-system agnostic).

    Args:
        subject: List of artifact ResourceDescriptors (name, digest)
        build_type: URI identifying the build type (e.g., https://example.com/build-type@v1)
        external_parameters: User-controlled parameters (source, buildCommand, etc.)
        internal_parameters: Platform-controlled parameters (toolchain, lockfileHash, etc.)
        resolved_dependencies: List of dependency ResourceDescriptors (PURL format recommended)
        builder_id: URI identifying the builder (e.g., https://example.com/builder/v1)
        metadata: Build metadata (invocationId, startedOn, finishedOn)
        byproducts: Additional artifacts generated during build

    Returns:
        SLSA v1.2 provenance statement (in-toto format)
    """
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subject,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": build_type,
                "externalParameters": external_parameters,
                "internalParameters": internal_parameters,
                "resolvedDependencies": resolved_dependencies,
            },
            "runDetails": {
                "builder": {"id": builder_id},
                "metadata": metadata,
                "byproducts": byproducts,
            },
        },
    }

    return statement


def hash_slsa_statement(statement: dict) -> bytes:
    """Hash SLSA statement for TEE binding.

    Args:
        statement: SLSA provenance statement

    Returns:
        32-byte SHA256 hash of canonicalized JSON
    """
    import hashlib

    # Canonicalize JSON (sorted keys, no whitespace)
    canonical_json = json.dumps(statement, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).digest()


# Helper functions for building SLSA components
def build_subject(output_artifacts: list[tuple]) -> list[dict]:
    """Build subject array from output artifacts.

    Args:
        output_artifacts: List of (path, hash_value) tuples

    Returns:
        List of subject ResourceDescriptors
    """
    from pathlib import Path

    subject = []
    for path, hash_value in output_artifacts:
        subject.append(
            {"name": Path(path).name, "digest": {"sha256": hash_value}}
        )
    return subject


def build_source_descriptor(git_source: dict) -> dict:
    """Build source ResourceDescriptor from git info.

    Args:
        git_source: Dict with commit_hash, tree_hash, optional repository_url

    Returns:
        Source ResourceDescriptor
    """
    descriptor = {
        "digest": {
            "gitCommit": git_source["commit_hash"],
            "gitTree": git_source["tree_hash"],
        }
    }
    if git_source.get("repository_url"):
        descriptor["uri"] = git_source["repository_url"]
    return descriptor


def build_byproduct(name: str, digest_value: str, digest_alg: str = "sha256") -> dict:
    """Build a byproduct ResourceDescriptor.

    Args:
        name: Byproduct name
        digest_value: Hash value
        digest_alg: Hash algorithm (default: sha256)

    Returns:
        Byproduct ResourceDescriptor
    """
    return {
        "name": name,
        "digest": {digest_alg: digest_value}
    }
