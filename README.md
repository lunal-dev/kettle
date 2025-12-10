# Attestable Builds

**SLSA Build Level 3 compliant build attestation with TEE-backed provenance**

A build attestation system that creates verifiable [SLSA v1.2](https://slsa.dev/spec/v1.2/) Build Provenance for software artifacts. Unlike reproducible builds, which require bit-for-bit identical outputs, attestable builds shift the question from "Does this binary have hash X?" to "Was this binary with hash X produced by process Y from sources Z in environment W?"

## Overview

Attestable builds provide cryptographic proof that a binary artifact was built from specific source code, dependencies, and toolchain in a trusted execution environment (TEE). The system generates [SLSA v1.2 provenance](https://slsa.dev/spec/v1.2/provenance) in the standard [in-toto attestation format](https://in-toto.io/), achieving **SLSA Build Level 3** compliance through TEE-based isolation and hardware attestation.

The system first establishes complete provenance of all build inputs (source code, dependencies, toolchain), then generates SLSA provenance linking verified inputs to build outputs, cryptographically signed by TEE hardware.

This approach is more practical than reproducible builds while providing strong security guarantees through cryptographic attestation rather than deterministic compilation.

## Current Implementation

**Rust/Cargo** - Fully implemented with support for git source verification, dependency verification via Cargo.lock, toolchain hashing, and AMD SEV-SNP attestation.

Additional language ecosystems can be supported by implementing the same verification pattern for their respective package managers and toolchains.

## Installation

```bash
# Install uv package manager (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/lunal-dev/attestable-builds.git
cd attestable-builds
uv pip install -e .
```

## Quick Start

**Build with verification:**
```bash
attestable-builds build /path/to/rust/project
```

**Build with TEE attestation:**
```bash
attestable-builds build /path/to/rust/project --attestation
```

**Verify a build:**
```bash
attestable-builds verify /path/to/build/outputs --project-dir /path/to/rust/project
```

See [TESTING.md](TESTING.md) for detailed examples and Docker-based testing.

## Key Features

**Build Verification**
- Verifies all build inputs: source code (git), dependencies, and toolchain
- Generates SLSA v1.2 provenance containing complete build manifest
- Optional TEE attestation for hardware-backed proof

**SLSA v1.2 Provenance**
- Standard in-toto attestation format for interoperability
- Complete build provenance with inputs, outputs, and builder identity
- SLSA Build Level 3 compliant
- Input merkle tree for efficient verification
- Cryptographically bound to attestation reports

**Merkle Inclusion Proofs**
- Generate proofs that specific inputs are included in build
- Efficient verification without revealing full dependency tree
- Useful for supply chain transparency

**Remote TEE Builds**
- Build projects in remote TEE environments via API
- Automatic download of artifacts, passports, and attestations

## Usage

### Building a Project

```bash
# Build with input verification and passport generation
attestable-builds build /path/to/project

# Build with TEE attestation (requires attest-amd)
attestable-builds build /path/to/project --attestation
```

Verifies git source, dependencies, and toolchain, then executes the build and generates a `passport.json` manifest. With `--attestation`, also generates `evidence.b64` signed by TEE hardware.

Options: `--output`, `--verbose`, `--release/--debug`

### Verifying a Build

```bash
# Verify attestation and passport contents
attestable-builds verify /path/to/build/outputs --project-dir /path/to/project
```

Verifies attestation signature (if present), passport binding, git commit, Cargo.lock hash, input merkle root, toolchain hashes, and binary artifact hashes.

Options: `--binary`, `--strict`

### Verifying Attestation Only

```bash
attestable-builds verify-attestation evidence.b64 --passport passport.json
```

Verifies the attestation report signature and passport binding independently.

### Generating Merkle Proofs

```bash
# Generate inclusion proofs for specific hashes
attestable-builds prove-inclusion passport.json abc123def456... --output proofs.json
```

Generates cryptographic proofs that specific inputs are included in the build. Useful for supply chain transparency without revealing all dependencies.

### Remote TEE Builds

```bash
attestable-builds tee-build /path/to/project --api https://builder.example.com
```

Uploads project to remote TEE, executes build, and downloads passport, attestation, and artifacts to `kettle-{build_id}/` directory.

---

Run `attestable-builds <command> --help` for detailed command documentation.

## How It Works

1. **Input Verification** - Before building, the system cryptographically verifies:
   - Source code (git commit + tree hash)
   - Dependency manifests and cached artifacts
   - Build toolchain binaries

2. **Build Execution** - Executes the build and measures output artifacts

3. **Passport Generation** - Creates a JSON passport containing:
   - All verified input hashes
   - Input merkle tree root
   - Build command and timestamp
   - Output artifact hashes

4. **TEE Attestation** (optional) - Generates hardware-backed attestation report:
   - Binds passport hash to attestation
   - Includes nonce for replay protection
   - Signed by TEE hardware (AMD SEV-SNP)

5. **Verification** - Third parties can verify:
   - Attestation signature (proves TEE execution)
   - Passport binding (proves inputs/outputs)
   - Individual inputs via merkle proofs

## Comparison to Reproducible Builds

| Aspect           | Reproducible Builds           | Attestable Builds               |
| ---------------- | ----------------------------- | ------------------------------- |
| **Requirement**  | Bit-for-bit identical outputs | Verifiable build process        |
| **Trust Anchor** | Output hash                   | TEE attestation + process chain |
| **Verification** | Rebuild and compare           | Check cryptographic proofs      |
| **Complexity**   | High (strict env control)     | Medium (TEE setup)              |
| **Speed**        | Slow (full rebuild)           | Fast (proof verification)       |

## License

MIT
