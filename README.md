# Attestable Builds - Phase 1 POC

**Cryptographic verification of build inputs for Rust projects**

This tool implements Phase 1 of attestable builds:

- **Phase 1**: Establishing verifiable provenance for all build inputs

## Overview

Unlike reproducible builds which require bit-for-bit identical outputs, attestable builds shift the question from:

- ❌ "Does this binary have hash X?"

To:

- ✅ "Was this binary with hash X produced by process Y from sources Z in environment W?"

This approach is more practical and resilient than reproducible builds while providing the same security guarantees through cryptographic attestation.

## Phase 1: Input Locking & Verification

Phase 1 establishes a complete chain of custody for all build inputs:

### What Gets Verified

1. **Source Code** - Complete git verification:
   - Git commit hash (exact source code version)
   - Git tree hash (cryptographic proof of source tree state)
   - Git binary hash (cryptographic proof of git tool itself)
   - Working tree cleanliness (no uncommitted changes)
2. **Cargo.lock** - SHA256 hash of entire lockfile
3. **Dependencies** - Verify actual `.crate` files in cargo cache against Cargo.lock checksums
4. **Toolchain** - Hash `rustc` and `cargo` binaries to prove which compiler was used

### Verification Strategy

The tool verifies that cached dependencies are a **subset** of Cargo.lock:

- ✅ Every `.crate` file in cargo cache must be in Cargo.lock with matching checksum
- ✅ Platform-specific dependencies can be absent (not all Cargo.lock deps are needed)
- ❌ Extra crates in cache that aren't in Cargo.lock are flagged as suspicious

This handles the reality that `Cargo.lock` contains dependencies for all platforms, but builds only download platform-specific ones.

## Phase 2: TEE Build Execution & Attestation

Phase 2 executes the build inside a TEE environment and generates cryptographic attestation:

### What Gets Attested

1. **Passport Binding** - SHA256 hash of the complete passport document
   - Embedded in attestation report's custom data (bytes 0-31)
   - Cryptographically binds all Phase 1 inputs to the attestation
2. **Nonce** - 32-byte freshness token for replay protection
   - Embedded in custom data (bytes 32-63)
   - Timestamp-based for POC (challenge-response for production)
3. **TEE Signature** - Cryptographic signature over attestation data
   - Verified by `attest-amd verify` command
   - Proves the attestation came from genuine TEE hardware

### Attestation Report Structure

The attestation report contains:

- **Report ID**: Unique identifier for this attestation
- **Timestamp**: When the attestation was generated
- **Launch Measurement**: Hash of build runner code (for future use)
- **Custom Data (64 bytes)**: `Hash(passport) || Nonce`
  - Bytes 0-31: SHA256 of passport JSON
  - Bytes 32-63: 32-byte nonce
- **Platform Info**: TEE type, version, status
- **Signature**: Cryptographic signature verified by attest-amd

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

See [TESTING.md](TESTING.md) for detailed testing instructions.

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

### 2. Verify all Phase 1 inputs

```bash
cd ..
python -m attestable_builds.cli verify test-project
```

This verifies:

- Git source (if in a git repo)
- Cargo.lock hash
- All cached `.crate` files match Cargo.lock checksums
- Rust toolchain binaries (rustc + cargo)

### 3. View detailed verification

```bash
python -m attestable_builds.cli verify test-project --verbose
```

Shows for each dependency:

- Cargo.lock checksum
- Local .crate file path
- Computed checksum
- Match status (✓ or ✗)

### 4. Generate passport document

```bash
python -m attestable_builds.cli passport test-project -o passport.json
```

Generates a complete Phase 1 passport with all verified inputs.

### 5. Build with attestation

```bash
# Build and generate attestation (requires attest-amd)
attestable-builds build test-project --attestation
```

This generates:
- `passport.json` - Complete build manifest
- Attestation report (via `attest-amd` command)

### 6. Verify attestation report

```bash
# Verify attestation against passport
attestable-builds verify-attestation attestation.json \
    --passport passport.json \
    --max-age 3600
```

This verifies:
- ✓ Cryptographic signature (via attest-amd verify)
- ✓ Passport binding (hash in attestation matches passport)
- ✓ Nonce freshness (timestamp-based replay protection)

**Note**: Requires `attest-amd` to be installed for cryptographic verification.

## CLI Commands

### `verify [PROJECT_DIR]`

Verify all Phase 1 inputs without generating output.

```bash
# Verify current directory
python -m attestable_builds.cli verify .

# Verify specific project
python -m attestable_builds.cli verify path/to/rust/project

# Show all verified dependencies
python -m attestable_builds.cli verify . --verbose
```

**Output:**

- ✓ Git commit hash, tree hash, version, and working tree status (if git repo)
- ✓ Cargo.lock SHA256
- ✓ Verification status for each cached dependency
- ✓ Toolchain hashes (rustc + cargo)

**Note:** Verification requires a clean git working tree. If you have uncommitted changes, the tool will fail with an error listing the dirty files. Commit or stash your changes before running verification or generating a passport.

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
- `--attestation/-a` - Generate attestation report using attest-amd command

### `verify-attestation [ATTESTATION]`

Verify an attestation report against a passport document.

```bash
# Basic verification
attestable-builds verify-attestation attestation.json \
    --passport passport.json

# Custom nonce age limit
attestable-builds verify-attestation attestation.json \
    --passport passport.json \
    --max-age 7200
```

**Requirements:**
- `attest-amd` must be installed for cryptographic verification

**Options:**
- `--passport/-p PATH` - Path to passport JSON file (required)
- `--max-age SECONDS` - Maximum nonce age in seconds (default: 3600)

**Verifies:**
- ✓ Cryptographic signature (via attest-amd verify)
- ✓ Passport binding (hash in custom data matches passport)
- ✓ Nonce freshness (timestamp-based replay protection)

### `passport [PROJECT_DIR]`

Generate a Phase 1 passport document with all verified inputs.

```bash
# Generate passport
python -m attestable_builds.cli passport . -o passport.json

# Specify output path
python -m attestable_builds.cli passport ./my-project -o evidence/passport.json
```

**Passport Contents:**

```json
{
  "version": "1.0",
  "inputs": {
    "cargo_lock_hash": "23b2e23aa04c93c3...",
    "toolchain": {
      "rustc": {
        "binary_hash": "cb5d96f4c51e916f...",
        "version": "rustc 1.90.0 (1159e78c4 2025-09-14)"
      },
      "cargo": {
        "binary_hash": "2f50d54779378980...",
        "version": "cargo 1.90.0 (840b83a10 2025-07-30)"
      }
    },
    "dependencies": [
      {
        "name": "serde",
        "version": "1.0.228",
        "source": "registry+https://github.com/rust-lang/crates.io-index",
        "checksum": "9a8e94ea7f378bd32cbbd37198a4a91436180c5bb472411e48b5ec2e2124ae9e",
        "verified": true
      }
    ],
    "input_merkle_root": "5a9f5170360ed983...",
    "source": {
      "type": "git",
      "commit_hash": "3ae40f0b47d1e499...",
      "tree_hash": "5f7a8c9d2e4b1a3f...",
      "git_binary_hash": "a1b2c3d4e5f6g7h8...",
      "repository": "https://github.com/user/repo"
    }
  },
  "build_process": {
    "command": "cargo build --locked --release",
    "timestamp": "2025-10-19T15:52:00Z"
  },
  "outputs": {
    "binary": {
      "path": "target/release/my-app",
      "hash": "d7fb5de4e41dbd3a..."
    }
  }
}
```

## Architecture

### Module Structure

```
src/attestable_builds/
├── Phase 1: Input Verification
│   ├── git.py        # Extract git commit hash and repository URL
│   ├── cargo.py      # Parse Cargo.lock, hash lockfile
│   ├── toolchain.py  # Hash rustc/cargo binaries
│   ├── passport.py   # Generate passport document
│   ├── merkle.py     # Merkle tree construction
│   └── build.py      # Execute cargo build
│
├── Phase 2: Attestation Verification
│   └── attestation.py  # Parse and verify attestation reports
│
├── utils.py      # Shared utilities (file hashing)
└── cli.py        # CLI commands and output formatting
```

### Build Flow

```
┌─────────────────────────────────────────────────────────┐
│ Phase 1: Input Verification                             │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. Git Source (optional, but strict if present)        │
│     ├─ Find git binary: which git                       │
│     ├─ Hash git binary: SHA256(git executable)          │
│     ├─ Get commit hash: git rev-parse HEAD              │
│     ├─ Get tree hash: git rev-parse HEAD^{tree}         │
│     ├─ Check working tree: git status --porcelain       │
│     ├─ FAIL if uncommitted changes exist                │
│     └─ Get repo URL: git remote get-url origin          │
│                                                          │
│  2. Cargo.lock Hash                                      │
│     └─ SHA256(Cargo.lock file)                          │
│                                                          │
│  3. Dependencies                                         │
│     ├─ Scan ~/.cargo/registry/cache/ for .crate files   │
│     ├─ For each cached crate:                           │
│     │   ├─ Look up in Cargo.lock                        │
│     │   ├─ SHA256(crate file)                           │
│     │   └─ Compare to Cargo.lock checksum               │
│     └─ Flag any crates NOT in Cargo.lock                │
│                                                          │
│  4. Toolchain                                            │
│     ├─ Find rustc: rustup which rustc                   │
│     ├─ Hash: SHA256(rustc binary)                       │
│     ├─ Find cargo: rustup which cargo                   │
│     └─ Hash: SHA256(cargo binary)                       │
│                                                          │
│  5. Generate Passport                                    │
│     ├─ Include all verified inputs                      │
│     └─ Write passport.json                              │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Security Model

### What Phase 1 Proves

**Input Provenance:**

1. **Exact source code** via:
   - Git commit hash (specific commit)
   - Git tree hash (cryptographic proof of source tree)
   - Git binary hash (cryptographic proof of git tool)
   - Clean working tree verification (no uncommitted changes)
2. **Exact dependency versions** via Cargo.lock hash
3. **Integrity of cached dependencies** via .crate file checksums
4. **Exact build toolchain** via rustc/cargo binary hashes

### Trust Assumptions

**Must Trust:**

- Git repository integrity
- Cargo/crates.io infrastructure (for dependency checksums in Cargo.lock)
- Rustup distribution system (for toolchain binaries)

**Do NOT Need to Trust:**

- Build environment outside verification
- Network infrastructure (after verification)
- Third parties performing verification

### Threat Model

**Defends Against:**

- ✅ Tampered source code (wrong git commit or tree hash)
- ✅ Tampered git binary (wrong git binary hash)
- ✅ Uncommitted local changes (enforced clean working tree)
- ✅ Substituted dependencies (wrong .crate files)
- ✅ Modified toolchain binaries (wrong rustc/cargo)
- ✅ Extra/unexpected crates in cache

**Does NOT Defend Against:**

- ❌ Compromise of source repository
- ❌ Malicious code intentionally committed
- ❌ Vulnerabilities in dependencies (verifies integrity, not security)
- ❌ Bugs in rustc/cargo themselves

## Example Output

```bash
$ python -m attestable_builds.cli verify test-project --verbose

============================================================
Phase 1: Input Verification
============================================================

[1/4] Verifying git source...
  ✓ Commit: 3ae40f0b47d1e499fb93e303fd39710e6963584e
  ✓ Tree hash: 5f7a8c9d2e4b1a3f6c8e7d9b4a2f1e3c5d7a9b8c
  ✓ Git binary: /usr/bin/git
    Hash: a1b2c3d4e5f6g7h8...
  ✓ Working tree: clean
  ✓ Repository: git@github.com:lunal-dev/attestable-builds.git

[2/4] Hashing Cargo.lock...
  ✓ SHA256: 23b2e23aa04c93c350cac09ac73636e4ecedf564acc7f5d1c40a7e3fcf227c10

[3/4] Verifying dependencies...
  Found 11 external dependencies

============================================================
Verification Results: 11/11 passed
============================================================

VERIFIED:
  • serde 1.0.228
    Status: Checksum verified: 9a8e94ea...
    Cargo.lock checksum: 9a8e94ea7f378bd32cbbd37198a4a91436180c5bb472411e48b5ec2e2124ae9e
    Crate path: /root/.cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/serde-1.0.228.crate
    Computed checksum:   9a8e94ea7f378bd32cbbd37198a4a91436180c5bb472411e48b5ec2e2124ae9e
    Match: ✓

[4/4] Verifying Rust toolchain...
  ✓ rustc: rustc 1.90.0 (1159e78c4 2025-09-14)
    Hash: cb5d96f4c51e916f...
  ✓ cargo: cargo 1.90.0 (840b83a10 2025-07-30)
    Hash: 2f50d54779378980...

============================================================
✓ All Phase 1 inputs verified successfully
============================================================
```

## Implementation Status

✅ **Phase 1: Input Locking & Verification** - Complete

- Git source verification with tree hash
- Cargo.lock and dependency verification
- Toolchain binary hashing
- Passport generation

⏳ **Phase 2 & 3: TEE Integration** - Future Work

- Real Azure Confidential Computing VM deployment
- Launch measurement generation
- TEE build orchestration
- Output artifact measurement
- Actual TEE attestation signatures (hardware-backed)
- Challenge-response nonce protocol
- Public verification service
- Golden measurement publication infrastructure
- Integration with CI/CD pipelines

See [CLAUDE.md](CLAUDE.md) for complete design specification.

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
