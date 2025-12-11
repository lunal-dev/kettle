# Kettle

Cryptographic build attestation system for verifying software provenance.

## What It Does

Kettle proves that a binary was built from specific source code, dependencies, and toolchain in a verifiable environment. Instead of requiring bit-for-bit reproducible builds, it generates cryptographic proof of the build process itself.

It verifies all build inputs (git source, dependency lockfiles, toolchain binaries), executes the build, and produces SLSA v1.2 provenance statements. For hardware-backed trust, it can generate AMD SEV-SNP attestations that cryptographically bind the build proof to Trusted Execution Environment (TEE) hardware.

**The Core Principle**: Shift from asking "Does this binary have hash X?" to "Was this binary with hash X produced by process Y from sources Z in environment W?"

## Why This Matters

Traditional reproducible builds require deterministic compilation—the same source must produce identical binaries bit-for-bit. This is brittle and difficult to maintain across toolchain updates, platforms, and timestamps.

Kettle takes a different approach: instead of proving output identity, it proves process integrity. By cryptographically verifying every input (source, dependencies, toolchain) and binding them to outputs through TEE attestation, you get verifiable provenance without the fragility of deterministic builds.

This enables:
- **Supply chain transparency** - Prove what went into your binaries
- **Third-party verification** - Anyone can verify build claims without rebuilding
- **Merkle inclusion proofs** - Selectively prove dependencies without revealing your full tree
- **Workload execution on attested builds** - Run security audits or tests with cryptographic proof they operated on the claimed inputs

## Installation

```bash
# Clone repository
git clone https://github.com/lunal-dev/attestable-builds.git
cd attestable-builds

# Install with uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

Requires Python 3.10+

## Quick Start

```bash
# Build a project with verification
kettle build /path/to/project

# This creates:
# - manifest.json (build manifest with inputs/outputs)
# - build/provenance.json (SLSA v1.2 provenance statement)

# Verify the build
kettle verify ./build --project-dir /path/to/project

# Generate with TEE attestation (requires AMD SEV-SNP hardware)
kettle build /path/to/project --attestation
# Also creates: build/evidence.b64 (hardware-signed attestation)
```

## How It Works

Kettle follows a three-phase process:

### 1. Input Verification
Before building, cryptographically verify and hash all inputs:
- **Source code**: Git commit hash, tree hash, and git binary hash
- **Dependencies**: Lockfile hash (Cargo.lock/flake.lock) and cached artifact verification
- **Toolchain**: Binary hashes of build tools (rustc, cargo, nix)

All input hashes are combined into a Merkle tree to produce a single input root hash.

### 2. Build Execution
Execute the build with verified inputs:
- For Cargo: `cargo build --locked` (prevents lockfile modification)
- For Nix: `nix build` with flake.lock verification
- Measure all output artifacts (binaries, libraries)

### 3. Provenance Generation
Create cryptographic proof binding inputs to outputs:
- **manifest.json**: Complete build manifest with all input/output hashes
- **provenance.json**: SLSA v1.2 provenance statement in standard in-toto format
- **evidence.b64** (optional): TEE attestation report signed by hardware

The attestation binds the manifest hash to AMD SEV-SNP hardware, proving the build occurred in a trusted execution environment.

## Commands

### `kettle build`
Build with full input verification and provenance generation.

```bash
kettle build /path/to/project [OPTIONS]

Options:
  -o, --output PATH         Output directory (default: project directory)
  --release/--debug         Build mode (default: release, Cargo only)
  -a, --attestation         Generate TEE attestation with attest-amd
  -v, --verbose            Show all verification checks
```

Auto-detects build system (Cargo or Nix) and verifies inputs, executes build, measures outputs, and generates manifest + SLSA provenance.

### `kettle verify`
Verify complete build attestation and provenance.

```bash
kettle verify BUILD_DIR [OPTIONS]

Options:
  -p, --project-dir PATH    Project directory with verification manifest
  -b, --binary PATH         Binary artifact to verify
  --strict                  Fail if optional checks unavailable
```

Verifies both TEE attestation (evidence.b64) and provenance content (provenance.json). Checks attestation signature, provenance binding, git commit, lockfile hash, input merkle root, toolchain hashes, and binary artifact hashes.

### `kettle verify-passport`
Verify SLSA provenance document only (no attestation).

```bash
kettle verify-passport PROVENANCE_JSON [OPTIONS]

Options:
  -m, --manifest PATH       Verification manifest with expected values
  -p, --project-dir PATH    Project directory for git/lockfile checks
  -b, --binary PATH         Binary artifact to verify hash
  --strict                  Fail if optional checks unavailable
```

Verifies provenance format, git commit/tree hash, lockfile hash, input merkle root, toolchain hashes, and binary hashes against known values.

### `kettle verify-attestation`
Verify TEE attestation report only.

```bash
kettle verify-attestation EVIDENCE_FILE --passport PROVENANCE_JSON

Options:
  -p, --passport PATH       Path to provenance JSON file (required)
```

Verifies cryptographic signature via `attest-amd`, checks provenance hash binding in attestation data, and validates nonce freshness for replay protection.

### `kettle prove-inclusion`
Generate Merkle inclusion proofs for specific input hashes.

```bash
kettle prove-inclusion PROVENANCE_JSON HASH [HASH...] [OPTIONS]

Options:
  -o, --output PATH         Save proofs to JSON file (default: stdout)
```

Generates and verifies cryptographic proofs that specific hashes are included in the input merkle tree. Supports partial hash matching (e.g., "abc123" or "serde:1.0") for convenience. Useful for proving specific dependencies without revealing the full tree.

### `kettle tee-build`
Execute build on remote TEE server.

```bash
kettle tee-build PROJECT_DIR [OPTIONS]

Options:
  --api URL                 TEE build service URL (default: localhost:8000)
```

Creates source archive, uploads to remote TEE build API, and downloads manifest, provenance, attestation, and artifacts to `kettle-{build_id}/` directory. Enables building in trusted hardware without local TEE access.

### `kettle run-workload`
Execute workload on local attested build.

```bash
kettle run-workload WORKLOAD_DIR BUILD_ID
```

Executes a workload (defined in workload.yaml) in a sandboxed environment using build at `/tmp/kettle-{build_id}`. Generates workload provenance with execution results. Useful for security audits, testing, or analysis with cryptographic proof of execution context.

### `kettle tee-run-workload`
Execute workload on remote TEE build.

```bash
kettle tee-run-workload WORKLOAD_DIR BUILD_ID EXPECTED_INPUT_ROOT [OPTIONS]

Options:
  --api URL                 TEE service URL (default: localhost:8000)
```

Uploads workload to remote TEE, executes on specified build with input root verification, and returns workload provenance and results.

### `kettle tee-get-results`
Download workload execution results from TEE.

```bash
kettle tee-get-results BUILD_ID WORKLOAD_ID [OPTIONS]

Options:
  --api URL                 TEE service URL (default: localhost:8000)
  -o, --output PATH         Output directory (default: workload-results-{id})
```

Downloads complete workload execution results including provenance, outputs, and attestation from remote TEE service.

## Build System Support

Kettle is designed as a generic build attestation system. Current implementations:

- **Cargo (Rust)** - Fully supported with Cargo.lock verification, crate checksum validation, and rustup toolchain hashing
- **Nix (flakes)** - In progress with flake.lock verification and narHash validation

The architecture supports any build system by implementing input verification patterns for its package manager and toolchain.

## Generated Artifacts

### manifest.json
Complete build manifest containing all input and output hashes. Human-readable JSON with git info, lockfile hash, dependency list with checksums, toolchain binary hashes, input merkle root, and output artifact hashes.

### provenance.json
SLSA v1.2 provenance statement in standard in-toto attestation format. Contains builder identity, build invocation, resolved dependencies with PURLs (Package URLs), and cryptographic binding to inputs via merkle root.

### evidence.b64 (optional)
TEE attestation report signed by AMD SEV-SNP hardware. Contains attestation signature, manifest hash binding (first 32 bytes of custom data), nonce for replay protection (second 32 bytes), and platform measurements proving execution in trusted hardware.

## Requirements

- Python 3.10 or later
- Git
- Cargo (for Rust projects) or Nix (for Nix projects)
- For TEE attestation: AMD SEV-SNP hardware + `attest-amd` tool
- For remote builds: Access to a Kettle TEE service endpoint

## Security Model

**What Kettle Proves:**
- All build inputs (source, dependencies, toolchain) are cryptographically verified
- The build process executed with these exact inputs
- Output artifacts match the measured build results
- (With TEE) Execution occurred in hardware-attested isolated environment
- (With TEE) Attestation is fresh and not replayed (nonce validation)

**What You Must Trust:**
- TEE hardware and firmware (AMD SEV-SNP)
- TEE attestation signing keys
- Git repository integrity for source code
- Package registry infrastructure (crates.io, nixpkgs) for dependency checksums
- Distribution system for toolchain binaries (rustup, nix)

**What You Don't Need to Trust:**
- Build environment outside the TEE
- Network infrastructure (after input verification)
- Third-party verifiers (they can verify independently)

## Comparison to Reproducible Builds

| Aspect | Reproducible Builds | Kettle (Attestable Builds) |
|--------|---------------------|----------------------------|
| **Core requirement** | Bit-for-bit identical outputs | Verifiable process integrity |
| **Trust anchor** | Output hash matching | TEE attestation + input/output binding |
| **Toolchain constraints** | Must be deterministic | Standard toolchains work |
| **Verification method** | Rebuild and compare hashes | Check cryptographic proofs |
| **Environment control** | Strict (timestamp, locale, etc.) | Flexible (inputs verified, not environment) |
| **Verification speed** | Slow (full rebuild required) | Fast (cryptographic proof check) |
| **Maintenance burden** | High (breaks on toolchain updates) | Low (adapt to new toolchains easily) |

Kettle trades reproducibility for verifiable provenance—you don't need identical outputs, just cryptographic proof of the build process.
