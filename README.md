# Attestable Builds - Phase 1 POC

**Cryptographic verification of build inputs for Rust projects in Trusted Execution Environments (TEE)**

This tool implements Phase 1 of attestable builds: establishing verifiable provenance for all build inputs by cryptographically proving that a binary artifact was built from specific source code, dependencies, and toolchain in a trusted execution environment.

## Overview

Unlike reproducible builds which require bit-for-bit identical outputs, attestable builds shift the question from:
- ❌ "Does this binary have hash X?"

To:
- ✅ "Was this binary with hash X produced by process Y from sources Z in environment W?"

This approach is more practical and resilient than reproducible builds while providing the same security guarantees through cryptographic attestation.

## Phase 1: Input Locking & Verification

Phase 1 establishes a complete chain of custody for all build inputs:

### What Gets Verified

1. **Source Code** - Git commit hash (exact source code version)
2. **Cargo.lock** - SHA256 hash of entire lockfile
3. **Dependencies** - Verify actual `.crate` files in cargo cache against Cargo.lock checksums
4. **Toolchain** - Hash `rustc` and `cargo` binaries to prove which compiler was used

### Verification Strategy

The tool verifies that cached dependencies are a **subset** of Cargo.lock:
- ✅ Every `.crate` file in cargo cache must be in Cargo.lock with matching checksum
- ✅ Platform-specific dependencies can be absent (not all Cargo.lock deps are needed)
- ❌ Extra crates in cache that aren't in Cargo.lock are flagged as suspicious

This handles the reality that `Cargo.lock` contains dependencies for all platforms, but builds only download platform-specific ones.

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

### 1. Create a test Rust project

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
- ✓ Git commit hash (if available)
- ✓ Cargo.lock SHA256
- ✓ Verification status for each cached dependency
- ✓ Toolchain hashes (rustc + cargo)

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
    "source": {
      "type": "git",
      "commit_hash": "3ae40f0b47d1e499...",
      "repository": "https://github.com/user/repo"
    },
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
    ]
  },
  "build_process": {
    "command": "cargo build --release",
    "timestamp": "2025-10-19T15:52:00Z"
  }
}
```

## Architecture

### Module Structure

```
src/attestable_builds/
├── git.py        # Extract git commit hash and repository URL
├── cargo.py      # Parse Cargo.lock, hash lockfile
├── verify.py     # Verify .crate files from cargo cache
├── toolchain.py  # Hash rustc/cargo binaries
├── passport.py   # Generate passport document
└── cli.py        # CLI commands and output formatting
```

### Verification Flow

```
┌─────────────────────────────────────────────────────────┐
│ Phase 1: Input Verification                             │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. Git Source (optional)                               │
│     ├─ Get commit hash: git rev-parse HEAD              │
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
│     └─ JSON document with all verified inputs           │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Security Model

### What Phase 1 Proves

1. **Exact source code** via git commit hash
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
- ✅ Tampered source code (wrong git commit)
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

## Future Phases

**Phase 2: Build Execution**
- Execute builds inside Azure Confidential Computing TEE
- TEE generates launch measurements
- Isolated build environment

**Phase 3: Attestation Chain**
- Generate attestation report signed by TEE
- Bind passport to attestation via custom data
- Enable third-party verification

See [claude.md](claude.md) for complete design specification.

## Comparison to Reproducible Builds

| Aspect | Reproducible Builds | Attestable Builds (This POC) |
|--------|---------------------|------------------------------|
| **Core Requirement** | Bit-for-bit identical outputs | Verifiable build process |
| **Trust Anchor** | Output hash | TEE attestation + process chain |
| **Toolchain** | Must be deterministic | Can use standard toolchains |
| **Verification** | Rebuild and compare | Check cryptographic proofs |
| **Complexity** | High (env control) | Medium (TEE setup) |
| **Maintenance** | Brittle | More resilient |
| **Speed** | Requires rebuild | Fast verification |

## Contributing

This is a proof-of-concept implementation of Phase 1. Contributions welcome!

## License

MIT
