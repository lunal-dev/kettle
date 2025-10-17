# Attestable Builds for TEE

Build-time verification and attestation pipeline for **TEE (Trusted Execution Environment)** deployments.

## Overview

Prepares verified build evidence that links to TEE runtime attestation (Intel SGX, AMD SEV-SNP).

**Build-time pipeline:**

1. **Verify inputs** - Parse `Cargo.lock` and verify all dependencies against registries
2. **Execute build** - Run `cargo build --locked`
3. **Generate evidence** - Capture SHA-256 hashes of inputs and outputs for TEE attestation

**TEE runtime linkage:**
The build evidence contains hashes that the TEE can verify match the code actually running, creating a cryptographic chain from source dependencies → build outputs → TEE runtime measurement.

## Features

- **Build-time verification**: Parse and verify all dependencies from `Cargo.lock`
- **Registry verification**: Check crates.io checksums for all registry dependencies
- **Git pinning**: Ensure git dependencies use pinned commit hashes
- **Build evidence generation**: SHA-256 hashes of inputs and outputs for TEE attestation
- **TEE-ready output**: Build evidence designed to link with runtime TEE attestations

## Why TEE Attestation?

TEE attestation proves what code is running inside a trusted execution environment (hardware-protected enclave). This tool provides the **build-time half** of the attestation:

```
Build Time (this tool)          TEE Runtime
┌──────────────────────┐       ┌────────────────────────┐
│ Verify dependencies  │       │ TEE measures code      │
│ Build with --locked  │       │ loaded into enclave    │
│ Hash output binary   │──────▶│ Compares measurement   │
│ Generate evidence    │       │ to build evidence      │
└──────────────────────┘       └────────────────────────┘
         SHA-256: abc123...  =  SHA-256: abc123... ✓
```

If hashes match, the TEE can cryptographically prove it's running the exact binary built from verified dependencies.

## Installation

Requires [uv](https://docs.astral.sh/uv/) (fast Python package manager):

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync --dev
```

## Usage

### Full build pipeline (verify → build → generate evidence)

```bash
uv run attestable-builds build /path/to/rust/project -o build-evidence.json
```

This executes the complete pipeline:

1. Verifies all input dependencies in `Cargo.lock`
2. Runs `cargo build --release --locked`
3. Generates build evidence with hashes of inputs AND outputs

### Verify inputs only

```bash
uv run attestable-builds verify Cargo.lock
```

Checks all dependencies without building:

- Registry dependencies: verifies checksums against crates.io
- Git dependencies: ensures commits are pinned

### Generate build evidence (inputs only)

```bash
uv run attestable-builds evidence Cargo.lock -o build-evidence.json
```

Generate build evidence for verified inputs (no build execution).

## Architecture

### Three-phase pipeline

```
Phase 1: Input Verification    Phase 2: Build         Phase 3: Build Evidence
┌────────────────────┐         ┌──────────────┐       ┌──────────────────────┐
│ cargo.py           │         │ build.py     │       │ evidence.py          │
│ - Parse Cargo.lock │────────▶│ - Run cargo  │──────▶│ - Hash artifacts     │
│                    │         │ - Collect    │       │ - Generate JSON      │
│ verify.py          │         │   artifacts  │       │ - Include inputs +   │
│ - Hash Cargo.lock  │         │              │       │   outputs            │
│ - Verify checksums │         └──────────────┘       └──────────────────────┘
└────────────────────┘
```

### Modules

```
src/attestable_builds/
├── cargo.py     # Parse Cargo.lock, extract dependencies
├── verify.py    # Verify inputs, hash Cargo.lock
├── build.py     # Execute cargo build
├── evidence.py  # Generate build evidence with output hashes
└── cli.py       # CLI orchestration
```

Core principles:

- **Clear separation**: Input verification ≠ Build execution ≠ Build evidence generation
- **DRY**: Shared logic reused across commands
- **KISS**: Simple data structures (NamedTuples)

## Testing

```bash
uv run pytest tests/ -v
```

8 tests covering:

- Cargo.lock parsing
- Checksum verification
- Build evidence generation (inputs + outputs)
- Full pipeline integration

## TEE Attestation Format Options

Currently generates JSON build evidence with SHA-256 hashes. Future integration with [attestation-rs](https://github.com/lunal-dev/attestation-rs) could add signing and TEE-specific attestation formats:

| Format | Best For | Pros | Cons |
|--------|----------|------|------|
| **EAT (RFC 9711)** | Cross-platform TEE | IETF standard, hardware-agnostic, composable | New standard, requires CBOR |
| **Platform-specific** | Single TEE type | Native, well-documented, optimized | Not portable, vendor-specific |
| **Custom** | Unique needs | Full control, minimal deps | No interoperability |

**Recommendation:** For cross-platform deployments, consider [EAT (RFC 9711)](https://www.rfc-editor.org/rfc/rfc9711). For single-platform, use vendor formats (SGX quotes, SEV reports, Nitro documents).

**TEE platforms:** Intel SGX, AMD SEV-SNP, AWS Nitro, ARM TrustZone
