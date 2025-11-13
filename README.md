# Attestable Builds

**Cryptographic verification of build inputs and TEE-attested build execution**

Attestable builds provide cryptographic proof that a binary was built from specific sources using specific dependencies and toolchain, executed in a trusted environment. Unlike reproducible builds, which require bit-for-bit identical outputs, attestable builds shift the question from:

- ❌ "Does this binary have hash X?"

To:

- ✅ "Was this binary with hash X produced by process Y from sources Z in environment W?"

This approach is more practical and resilient while providing strong security guarantees through cryptographic attestation.

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System architecture and implementation details
- **[SECURITY.md](SECURITY.md)** - Security model, threat model, and trust assumptions
- **[service/README.md](service/README.md)** - REST API service documentation
- **[TRAINING.md](TRAINING.md)** - ML training attestation documentation
- **[PIPELINES.md](PIPELINES.md)** - Pipeline orchestration system (GitHub Actions-like workflows)

## How It Works

### Phase 1: Input Verification

Verifies and records all build inputs:

- **Source Code** - Git commit/tree hash, clean working tree, git binary hash
- **Cargo.lock** - SHA256 hash of entire lockfile
- **Dependencies** - Verify .crate files in cargo cache match Cargo.lock checksums
- **Toolchain** - Hash rustc and cargo binaries

Outputs a **passport.json** containing cryptographic hashes of all inputs and build outputs.

### Phase 2: TEE Attestation (Optional)

Generates cryptographic attestation that the build executed in a TEE:

- **Passport Binding** - SHA256(passport) embedded in attestation custom data
- **Nonce** - Freshness token for replay protection
- **TEE Signature** - Cryptographic proof from genuine TEE hardware (AMD SEV-SNP)

Requires `attest-amd` command for attestation generation and verification.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for detailed build flow and **[SECURITY.md](SECURITY.md)** for security guarantees.

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/):

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/lunal-dev/attestable-builds.git
cd attestable-builds
uv pip install -e .
```

## Quick Start

### Docker Test (Easiest)

```bash
# Build Docker image and run Phase 2 attestable build
make test-docker

# Extract outputs for inspection
make extract

# See all available commands
make help
```

### Manual Test

#### 1. Create a test Rust project

```bash
# Create project
mkdir test-project && cd test-project
cargo init --name simple-app

# Add some dependencies
cat >> Cargo.toml << EOF
[dependencies]
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
EOF

# Generate Cargo.lock and download dependencies
cargo generate-lockfile
cargo fetch
```

### 2. Build with verification and passport generation

```bash
cd ..
attestable-builds build test-project
```

This command:

- Verifies all Phase 1 inputs (git, Cargo.lock, dependencies, toolchain)
- Executes cargo build
- Measures output artifacts
- Generates `passport.json` with complete build manifest

Use `--verbose` to see detailed dependency verification.

### 3. Build with attestation

```bash
# Build and generate attestation (requires attest-amd)
attestable-builds build test-project --attestation
```

This generates:

- `passport.json` - Complete build manifest
- `evidence.b64` - TEE attestation report (base64-encoded, via `attest-amd attest` command)
- `custom_data.hex` - Custom data used in attestation (passport hash + nonce)

### 4. Verify attestation report

```bash
# Verify attestation against passport
attestable-builds verify-attestation evidence.b64 custom_data.hex \
    --passport passport.json \
    --max-age 3600
```

This verifies:

- ✓ Cryptographic signature (via attest-amd verify)
- ✓ Passport binding (hash in attestation matches passport)
- ✓ Nonce freshness (timestamp-based replay protection)

**Note**: Requires `attest-amd` to be installed for cryptographic verification.

## CLI Commands

### `build [PROJECT_DIR]`

Build project with full input verification and output measurement.

```bash
# Build with Phase 1 verification
attestable-builds build . --release

# Build with attestation generation
attestable-builds build . --attestation

# Build in debug mode
attestable-builds build . --debug --verbose
```

**Options:**

- `--output/-o PATH` - Output path for passport JSON (default: passport.json)
- `--release/--debug` - Build in release or debug mode (default: release)
- `--verbose/-v` - Show all verification results
- `--attestation/-a` - Generate attestation report using attest-amd command (saves to evidence.b64 and custom_data.hex)
- `--allow-dirty` - Allow uncommitted changes in git (for testing only)

**Outputs:**

- `passport.json` - Build manifest with all verified inputs and measured outputs (always generated)
- `evidence.b64` - TEE attestation report (only with `--attestation` flag)
- `custom_data.hex` - Custom data for attestation verification (only with `--attestation` flag)

### `verify-attestation [ATTESTATION] [CUSTOM_DATA]`

Verify an attestation report against a passport document.

```bash
# Basic verification
attestable-builds verify-attestation evidence.b64 custom_data.hex \
    --passport passport.json

# Custom nonce age limit
attestable-builds verify-attestation evidence.b64 custom_data.hex \
    --passport passport.json \
    --max-age 7200
```

**Requirements:**

- `attest-amd` must be installed for cryptographic verification

**Arguments:**

- `ATTESTATION` - Path to evidence.b64 file
- `CUSTOM_DATA` - Path to custom_data.hex file

**Options:**

- `--passport/-p PATH` - Path to passport JSON file (required)
- `--max-age SECONDS` - Maximum nonce age in seconds (default: 3600)

**Verifies:**

- ✓ Cryptographic signature (via attest-amd verify)
- ✓ Passport binding (hash in custom data matches passport)
- ✓ Nonce freshness (timestamp-based replay protection)

### `verify [PASSPORT]`

Verify a passport document against known values.

```bash
# Verify passport with manifest file (expected hashes)
attestable-builds verify passport.json --manifest manifest.json

# Verify passport against project directory
attestable-builds verify passport.json --project-dir ./test-project

# Verify specific binary artifact
attestable-builds verify passport.json --binary target/release/my-app
```

**Options:**

- `--manifest/-m PATH` - Verification manifest JSON with expected hashes
- `--project-dir/-p PATH` - Project directory (for git/Cargo.lock checks)
- `--binary/-b PATH` - Binary artifact to verify
- `--strict` - Fail if optional checks cannot be performed

### `pipeline [PIPELINE_FILE]`

Execute multi-step attestable workflows using GitHub Actions-like YAML pipelines.

```bash
# Run a pipeline
kettle pipeline build-train.yml

# Verbose output
kettle pipeline build-train.yml --verbose
```

**Features:**

- GitHub Actions-like YAML syntax
- Dependency management and automatic job ordering
- Variable interpolation (`${{ env.VAR }}`, `${{ jobs.X.outputs.Y }}`)
- Built-in actions: build, train, verify, train-verify
- Attestation support at any pipeline stage

**Example Pipeline:**

```yaml
name: Build and Train Pipeline
version: "1.0"

env:
  ATTESTATION_ENABLED: false

jobs:
  build-binary:
    action: build
    inputs:
      project_dir: ./training-binary
      attestation: ${{ env.ATTESTATION_ENABLED }}

  train-model:
    action: train
    depends_on: [build-binary]
    inputs:
      config: ./config.json
      dataset: ./data
```

See **[PIPELINES.md](PIPELINES.md)** for complete documentation.

## Example Output

```bash
$ attestable-builds build test-project

[1/4] Verifying git source...
  ✓ Commit: 3ae40f0b47d1e499fb93e303fd39710e6963584e
  ✓ Tree hash: 5f7a8c9d2e4b1a3f6c8e7d9b4a2f1e3c5d7a9b8c
  ✓ Working tree: clean

[2/4] Hashing Cargo.lock...
  ✓ SHA256: 23b2e23aa04c93c3...

[3/4] Verifying dependencies...
  ✓ Verified 11/11 dependencies

[4/4] Verifying Rust toolchain...
  ✓ rustc 1.90.0 (cb5d96f4...)
  ✓ cargo 1.90.0 (2f50d547...)

✓ All inputs verified successfully
✓ Passport written to passport.json
```

Use `--verbose` flag for detailed verification output.

## Implementation Status

✅ **Phase 1: Input Locking & Verification** - Complete

- Git source verification with tree hash
- Cargo.lock and dependency verification
- Toolchain binary hashing
- Passport generation
- Build command with integrated verification and passport generation

✅ **Phase 2: Attestation Verification** - Complete

- Attestation report generation via attest-amd
- Cryptographic verification (delegated to attest-amd verify)
- Passport binding verification
- Nonce freshness verification
- Passport validation against known values

⏳ **Phase 3: Production TEE Integration** - Future Work

- Real Azure Confidential Computing VM deployment
- TEE build orchestration and automation
- Challenge-response nonce protocol (replace timestamp-based)
- Launch measurement and golden measurement verification
- Public verification service
- Integration with CI/CD pipelines

## Comparison to Reproducible Builds

| Aspect               | Reproducible Builds           | Attestable Builds (This POC)    |
| -------------------- | ----------------------------- | ------------------------------- |
| **Core Requirement** | Bit-for-bit identical outputs | Verifiable build process        |
| **Trust Anchor**     | Output hash                   | TEE attestation + process chain |
| **Toolchain**        | Must be deterministic         | Can use standard toolchains     |
| **Verification**     | Rebuild and compare           | Check cryptographic proofs      |
| **Complexity**       | High (env control)            | Medium (TEE setup)              |
| **Maintenance**      | Brittle                       | More resilient                  |
| **Speed**            | Requires rebuild              | Fast verification               |

## Contributing

This is a proof-of-concept implementation of Phase 1. Contributions welcome!

## License

MIT
