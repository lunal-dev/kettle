# Attestable Builds POC - Design Document

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This document specifies the design for a Proof of Concept (POC) implementation of attestable builds for Rust projects. The POC demonstrates how to cryptographically prove that a binary artifact was built from specific source code and dependencies in a trusted execution environment (TEE).

## Goals

1. **Establish verifiable provenance** for Rust build artifacts
2. **Prove the integrity** of the build process without requiring bit-for-bit reproducibility
3. **Create a practical, deployable** system using Azure Confidential Computing
4. **Enable third-party verification** of build claims without exposing proprietary code

## Core Principle

Shift from asking "does this binary have hash X?" to asking "was this binary with hash X produced by process Y from sources Z in environment W?"

---

## Architecture Overview

The attestable build system consists of three main phases:

### Phase 1: Input Locking & Verification
Lock down and cryptographically verify all build inputs before compilation begins.

### Phase 2: Build Execution
Execute the build process inside an Azure Confidential Computing TEE with full isolation and measurement.

### Phase 3: Attestation Chain & Signing
Generate cryptographic proofs linking inputs to outputs, signed by the TEE.

---

## Phase 1: Input Locking & Verification

### Inputs to be Measured

1. **Source Code**
   - **Method**: Git commit hash
   - **Verification**: Cryptographic hash of the exact commit being built
   - **Purpose**: Proves the precise source code version

2. **Dependency Lock File**
   - **File**: `Cargo.lock`
   - **Method**: SHA256 hash of the entire file
   - **Purpose**: Proves exact dependency versions and their checksums

3. **Dependencies (Crates)**
   - **Location**: `~/.cargo/registry/cache/index.crates.io-{hash}/{crate}-{version}.crate`
   - **Method**: Verify each `.crate` tarball against checksums in `Cargo.lock`
   - **Purpose**: Ensures downloaded dependencies match declared versions
   - **Process**:
     - Parse `Cargo.lock` to extract package names, versions, and checksums
     - Locate corresponding `.crate` files in cargo cache
     - Hash each `.crate` file (SHA256)
     - Verify against `Cargo.lock` checksum
     - Fail build if any mismatch detected

4. **Build Toolchain**
   - **Components**: `rustc` and `cargo` binaries
   - **Installation**: Via rustup with pinned version
   - **Specification**: `{version}-{target-triple}` (e.g., `1.75.0-x86_64-unknown-linux-gnu`)
   - **Method**: Hash the actual binary files
   - **Metadata**: Record version strings for human readability
   - **Reproducibility**: Rustup-distributed binaries are bit-identical across installations
   - **Purpose**: Proves which compiler and build tool produced the artifact

### Input Merkle Tree Structure

All inputs are combined into a Merkle tree to produce a single root hash:

```
Input Root Hash
├─── Source Code Subtree
│    ├─── Git commit hash
│    ├─── Git tree hash
│    └─── Git binary hash
├─── Cargo.lock Hash
├─── Dependencies Subtree
│    ├─── Dependency 1 (verified checksum)
│    ├─── Dependency 2 (verified checksum)
│    ├─── Dependency 3 (verified checksum)
│    └─── ...
└─── Toolchain Subtree
     ├─── rustc binary hash
     ├─── rustc version string
     ├─── cargo binary hash
     └─── cargo version string
```

**Output**: Input Merkle Root Hash representing all verified inputs

**Implementation Note**: The git binary hash is included because git is the verification tool for the source tree. This ensures complete provenance of all tools (git, rustc, cargo) used in the build process.

---

## Phase 2: Build Execution

### TEE Environment

- **Platform**: Azure Confidential Computing
- **Isolation**: Build runs entirely within TEE
- **Networking**: Not disabled for POC (simplified)
- **Measurement**: TEE measures its own launch configuration

### Build Process

1. **Environment Setup**
   - Boot TEE with attestable build runner code
   - TEE generates launch measurements (code/config hash)
   - Load verified toolchain (rustc/cargo from Phase 1)

2. **Build Execution**
   - Command: `cargo build --release`
   - Uses verified toolchain and dependencies
   - All compilation happens inside TEE

3. **Output Measurement**
   - Locate output binary: `target/release/{binary_name}`
   - Hash the output binary (SHA256)
   - Record binary hash as build output

### Launch Measurements

The TEE's launch measurement proves which code is running inside the TEE:
- Hash of the attestable build runner code/image
- This measurement is included in the attestation report
- Verifiers check this against a published "golden measurement"
- Proves: "This specific trusted build code created the passport"

---

## Phase 3: Attestation Chain & Signing

### The Passport Document

A structured manifest containing complete build information:

```json
{
  "version": "1.0",
  "inputs": {
    "cargo_lock_hash": "def456...",
    "toolchain": {
      "rustc": {
        "binary_hash": "...",
        "version": "1.75.0-x86_64-unknown-linux-gnu"
      },
      "cargo": {
        "binary_hash": "...",
        "version": "1.75.0"
      }
    },
    "dependencies": [
      {
        "name": "serde",
        "version": "1.0.228",
        "source": "registry+https://github.com/rust-lang/crates.io-index",
        "checksum": "9a8e94ea...",
        "verified": true
      }
    ],
    "input_merkle_root": "5a9f5170360ed983...",
    "source": {
      "type": "git",
      "commit_hash": "abc123...",
      "tree_hash": "def456...",
      "git_binary_hash": "789xyz...",
      "repository": "https://github.com/org/repo"
    }
  },
  "build_process": {
    "command": "cargo build --release",
    "timestamp": "2025-10-17T12:34:56Z"
  },
  "outputs": {
    "binary": {
      "path": "target/release/my-app",
      "hash": "xyz789..."
    }
  }
}
```

**Properties**:
- Self-contained build manifest
- Independent of timing/nonce data
- Can be verified independently
- Human-readable (JSON)

### Attestation Report Data Structure

Azure attestation reports support 64 bytes of custom data:

```
Bytes 0-31:  Hash(passport)     // SHA256 of complete passport JSON
Bytes 32-63: Nonce              // 32 bytes for replay protection
```

**Purpose**:
- First 32 bytes: Cryptographically bind passport to attestation
- Second 32 bytes: Prevent replay attacks

### Complete Attestation Report

The Azure TEE generates an attestation report containing:

1. **Launch Measurement**: Hash of TEE code/configuration
2. **Custom Data (64 bytes)**: `Hash(passport) || Nonce`
3. **TEE Signature**: Cryptographic signature over all above data
4. **Additional metadata**: Timestamp, platform info, etc.

**Note**: The passport itself is NOT included in the attestation report; it's transmitted separately.

---

## Verification Process

### Inputs Required by Verifier

1. Attestation Report (from Azure TEE)
2. Passport Document (JSON manifest)
3. Golden Launch Measurement (published reference hash)
4. Binary Artifact (the actual build output to verify)

### Verification Steps

#### Step 1: Verify Attestation Integrity
```
✓ Verify attestation signature using Azure's public keys
✓ Confirm signature is valid and report is authentic
✓ Result: Attestation came from genuine Azure TEE
```

#### Step 2: Verify Trusted Code Execution
```
✓ Extract launch measurement from attestation report
✓ Compare against golden launch measurement reference
✓ Result: The trusted "attestable build runner" code created this
```

#### Step 3: Verify Nonce Freshness
```
✓ Extract nonce from attestation data (bytes 32-63)
✓ Check nonce is fresh/valid (time-based or challenge-response)
✓ Result: Attestation is recent, not replayed
```

#### Step 4: Verify Passport Binding
```
✓ Hash the received passport document: SHA256(passport_json)
✓ Extract Hash(passport) from attestation data (bytes 0-31)
✓ Compare: Hash(passport) == attestation_data[0:31]
✓ Result: This exact passport was signed by the TEE
```

#### Step 5: Verify Passport Contents

**Source Code Verification:**
```
✓ Check git commit exists in repository
✓ Verify commit hash in passport matches expected source
```

**Dependency Verification:**
```
✓ Verify Cargo.lock hash in passport
✓ Optionally fetch and verify Cargo.lock independently
```

**Toolchain Verification:**
```
✓ Verify rustc/cargo versions are acceptable
✓ Optionally verify binary hashes match known rustup distributions
```

**Build Output Verification:**
```
✓ Hash the received binary artifact
✓ Compare against output hash in passport
✓ Result: Binary matches claimed build output
```

#### Step 6: Verify Input Merkle Root (Optional)
```
✓ Reconstruct Merkle tree from passport inputs
✓ Compare computed root against passport's input_merkle_root
✓ Result: All inputs properly accounted for
```

### Verification Chain Summary

```
Azure Signature ──> Attestation is authentic
       │
       ├──> Launch Measurement ──> Trusted code ran
       │
       └──> Attestation Data
              │
              ├──> Hash(passport) ──> Passport is authentic
              │         │
              │         └──> Passport Contents ──> Inputs/Outputs verified
              │
              └──> Nonce ──> Recent execution
```

---

## Security Model

### What This System Proves

1. **Authentic TEE Execution**
   - Azure's cryptographic signature proves attestation came from genuine TEE hardware

2. **Trusted Build Code**
   - Launch measurement proves the specific "attestable build runner" code executed
   - Verifier checks against published golden measurement

3. **Input Integrity**
   - All build inputs (source, dependencies, toolchain) are cryptographically verified
   - Merkle tree ensures complete accounting of inputs

4. **Process Integrity**
   - Build executed inside isolated TEE
   - No external tampering during compilation

5. **Output Provenance**
   - Cryptographic chain from verified inputs → TEE build → signed outputs
   - Binary hash proves it's the exact artifact produced

6. **Temporal Integrity**
   - Nonce prevents replay attacks
   - Proves attestation is fresh

### Trust Assumptions

**Must Trust**:
1. Azure TEE hardware and firmware
2. Azure's attestation signing keys
3. The attestable build runner code (verified via launch measurement)
4. Git repository integrity (for source code)
5. Cargo/crates.io infrastructure (for dependency checksums)
6. Rustup distribution system (for toolchain binaries)

**Do NOT Need to Trust**:
1. Build environment outside the TEE
2. Network infrastructure (after inputs are verified)
3. Third parties performing verification

### Threat Model

**Defends Against**:
- ✅ Tampered source code
- ✅ Substituted dependencies
- ✅ Modified toolchain binaries
- ✅ Build environment compromise (outside TEE)
- ✅ Man-in-the-middle attacks on verification
- ✅ Replay attacks (via nonce)

**Does NOT Defend Against**:
- ❌ Compromise of Azure TEE hardware/firmware
- ❌ Compromise of source repository
- ❌ Malicious code intentionally committed to source
- ❌ Vulnerabilities in dependencies (we verify integrity, not security)
- ❌ Bugs in rustc/cargo themselves

---

## Implementation Considerations

### Golden Launch Measurement Publication

The "golden measurement" of the attestable build runner must be:
- Published in a trusted location (e.g., signed git tag, public registry)
- Updated when build runner code changes
- Version-controlled alongside the code

### Nonce Handling Options

**Option A: Challenge-Response**
- Verifier provides nonce before build
- Builder includes it in attestation
- Verifier checks nonce matches

**Option B: Timestamp-Based**
- Builder generates nonce + timestamp
- Verifier checks timestamp freshness
- Simpler but requires clock synchronization

**Recommendation for POC**: Option B (timestamp-based) for simplicity

### Passport Storage & Distribution

**Options**:
1. Embedded with binary (e.g., in ELF section)
2. Sidecar file (e.g., `binary.passport.json`)
3. Separate registry/storage system

**Recommendation for POC**: Sidecar file for simplicity

### Performance Considerations

**Input Verification**:
- Hashing `.crate` files: ~seconds for typical projects
- Git commit verification: Negligible
- Toolchain hashing: One-time per toolchain version

**Build Time**:
- TEE overhead: Minimal for CPU-bound compilation
- No significant performance impact expected

**Verification Time**:
- Attestation signature check: Milliseconds
- Passport hash: Milliseconds
- Total: Sub-second verification possible

---

## POC Scope & Limitations

### In Scope for POC

✅ Rust projects with Cargo
✅ Azure Confidential Computing TEE
✅ Single binary output
✅ Rustup-based toolchain
✅ JSON passport format
✅ Basic verification tooling

### Out of Scope for POC

❌ Multiple build artifacts
❌ Network isolation during build
❌ Custom build scripts (`build.rs`) verification
❌ Workspace/multi-crate projects
❌ Cross-compilation scenarios
❌ Toolchain built from source
❌ Production-grade key management
❌ Automated verification pipeline

### Future Enhancements

- Support for complex build scripts
- Multi-artifact builds
- Network isolation and allowlisting
- Integration with CI/CD systems
- Public verification service
- Support for other languages/build systems

---

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

---

## Deliverables

### Code Components

1. **Input Verification Tool**
   - Parses Cargo.lock
   - Verifies .crate checksums
   - Hashes toolchain binaries
   - Builds input Merkle tree

2. **TEE Build Runner**
   - Runs inside Azure CC
   - Executes cargo build
   - Generates passport document
   - Creates attestation with custom data

3. **Verification Tool**
   - Validates attestation signature
   - Checks launch measurement
   - Verifies passport binding
   - Validates passport contents

### Documentation

1. Setup guide for Azure CC
2. Build runner deployment instructions
3. Verification tool usage guide
4. Golden measurement publication process

### Test Cases

1. Successful build with valid inputs
2. Tampered source code detection
3. Modified dependency detection
4. Invalid toolchain detection
5. Replay attack prevention

---

## Success Criteria

The POC is successful if it demonstrates:

1. ✅ Complete input provenance tracking
2. ✅ Verifiable build execution in TEE
3. ✅ Cryptographic chain from inputs to outputs
4. ✅ Independent third-party verification
5. ✅ Detection of tampered inputs
6. ✅ Practical performance (build + verify under reasonable time)

---

## Next Steps

1. Set up Azure Confidential Computing environment
2. Implement input verification tool
3. Develop TEE build runner
4. Create passport generation logic
5. Build verification tool
6. Test with sample Rust project
7. Document findings and limitations

---

## Appendix: Data Structures

### Passport JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["version", "inputs", "build_process", "outputs"],
  "properties": {
    "version": {
      "type": "string",
      "description": "Passport format version"
    },
    "inputs": {
      "type": "object",
      "required": ["cargo_lock_hash", "toolchain", "dependencies", "input_merkle_root"],
      "properties": {
        "cargo_lock_hash": { "type": "string" },
        "toolchain": {
          "type": "object",
          "required": ["rustc", "cargo"],
          "properties": {
            "rustc": {
              "type": "object",
              "required": ["binary_hash", "version"],
              "properties": {
                "binary_hash": { "type": "string" },
                "version": { "type": "string" }
              }
            },
            "cargo": {
              "type": "object",
              "required": ["binary_hash", "version"],
              "properties": {
                "binary_hash": { "type": "string" },
                "version": { "type": "string" }
              }
            }
          }
        },
        "dependencies": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "version", "source", "checksum", "verified"],
            "properties": {
              "name": { "type": "string" },
              "version": { "type": "string" },
              "source": { "type": "string" },
              "checksum": { "type": "string" },
              "verified": { "type": "boolean" }
            }
          }
        },
        "input_merkle_root": { "type": "string" },
        "source": {
          "type": "object",
          "required": ["type", "commit_hash", "tree_hash", "git_binary_hash"],
          "properties": {
            "type": { "type": "string", "enum": ["git"] },
            "commit_hash": { "type": "string" },
            "tree_hash": { "type": "string" },
            "git_binary_hash": { "type": "string" },
            "repository": { "type": "string" }
          }
        }
      }
    },
    "build_process": {
      "type": "object",
      "required": ["command", "timestamp"],
      "properties": {
        "command": { "type": "string" },
        "timestamp": { "type": "string", "format": "date-time" }
      }
    },
    "outputs": {
      "type": "object",
      "required": ["binary"],
      "properties": {
        "binary": {
          "type": "object",
          "required": ["path", "hash"],
          "properties": {
            "path": { "type": "string" },
            "hash": { "type": "string" }
          }
        }
      }
    }
  }
}