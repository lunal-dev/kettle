# Kettle

Kettle generates cryptographic proof that software was built from specific source code, dependencies, and toolchain. By the end of this document, you'll understand why proving "this binary came from this source" is a hard problem, how Kettle solves it without requiring bit-for-bit reproducible builds, and how TEE attestation makes that proof hardware-backed. We assume familiarity with git, package managers, and the concept of cryptographic hashes.

## The Problem

You receive a binary from your CI system. You want to verify it was built from commit `a1b2c3d4` with dependencies locked to specific versions. Today, you cannot do this. You trust that your CI server wasn't compromised. You trust that your package registry served the correct packages. You trust that nothing modified the binary between build and deployment. Each of these is an assumption, not a verification.

This matters now because supply chain attacks are increasingly common and increasingly sophisticated. SolarWinds, Codecov, and ua-parser-js demonstrated that attackers target build infrastructure precisely because the resulting binaries are implicitly trusted. Compliance frameworks like SLSA and Executive Order 14028 now require provenance for software artifacts. Customers and security teams are asking: "Prove this binary came from this source."

The standard answer is reproducible builds: rebuild from source and compare hashes. If you get an identical binary, you've verified the build. The problem is that most toolchains produce non-deterministic output. Compilers embed timestamps, parallelize in varying order, and make optimization decisions that differ between runs. Achieving bit-for-bit identical output requires controlling all of this across your entire dependency tree. It's years of engineering work, and a single non-reproducible component breaks the whole chain. Even if you achieve it, verifiers must actually rebuild to verify. They won't.

## The Approach

Kettle inverts the verification model. Instead of proving "the same inputs always produce identical outputs," it proves "these specific inputs were used to produce this output in a verified process."

The key insight: you don't need deterministic compilation if you can cryptographically verify what went into the build and bind that to what came out.

```
Reproducible Builds:
  Source A ─[build]──> Binary X
  Source A ─[rebuild]─> Binary Y
  Verify: X == Y ?   (must be bit-for-bit identical)
  Problem: Fails if timestamps, file ordering, or optimizations differ

Attestable Builds (Kettle):
  Source A ──┐
  Deps B    ─┼─[Kettle build in TEE]──> Binary X + Provenance + Attestation
  Toolchain C┘

  Verify: Attestation proves build ran in trusted hardware
          Provenance proves inputs A, B, C were used
          Provenance binds to output hash X
```

A verifier checks cryptographic signatures and attestation reports in milliseconds rather than rebuilding for hours. The verification is independent: anyone can verify without trusting the builder's claims.

## How It Works

### The Build Process

When you run `kettle build`, five phases execute in sequence.

**Phase 1: Input Verification.** Before compilation begins, Kettle verifies and hashes every build input. For source code, it records the git commit hash, tree hash (a content-addressed hash of the entire source tree), and the hash of the git binary itself. For dependencies, it parses the lockfile (Cargo.lock or flake.lock), extracts the list of pinned packages with their checksums, and verifies that cached artifacts match those checksums. For the toolchain, it hashes the actual compiler binaries (rustc, cargo, or nix).

```
━━━ Verifying Build Inputs ━━━

[1/4] Verifying git source...
✓ Commit: a1b2c3d4e5f67890abcdef1234567890abcdef12
✓ Tree hash: 7890abcdef1234567890abcdef1234567890abcde
  Git binary hash: 5a39a790...

[2/4] Hashing lockfile...
✓ SHA256: 23b2e23aa04c93c350cac09ac73636e4ecedf564...
  127 dependencies

[3/4] Verifying dependencies...
✓ All 127 dependencies verified against lockfile checksums

[4/4] Verifying cargo toolchain...
✓ rustc: 1.75.0
  Hash: e6abf55ab1859e7c990be77fd593f5166...
✓ cargo: 1.75.0
  Hash: 51de284e8bb0d03dcee595a0fb1cb3a952...
```

**Phase 2: Merkle Tree Construction.** All input hashes become leaves in a Merkle tree. The tree is constructed in deterministic order: git information first, then lockfile hash, then dependencies sorted alphabetically, then toolchain hashes. The root of this tree, the `input_merkle_root`, is a single 32-byte hash that uniquely identifies the complete set of build inputs. If any input changes by a single byte, the root changes.

```
                      [input_merkle_root]
                      ────────┬────────
                    ┌─────────┴─────────┐
               [subtree]            [subtree]
               ────┬────            ────┬────
            ┌──────┴──────┐      ┌──────┴──────┐
       [git_commit]  [tree_hash] [lockfile]  [deps_root]
                                              ────┬────
                                         ┌────────┼────────┐
                                     [serde]   [rand]   [tokio]
                                       ...      ...      ...
```

The Merkle structure enables selective disclosure: you can prove a specific dependency is in the tree without revealing the full list. This matters for audits where you need to prove you used a specific version of a library but don't want to disclose your entire dependency graph.

**Phase 3: Build Execution.** Kettle executes the actual build command. For Cargo projects, this is `cargo build --locked --release`. For Nix projects, `nix build`. The `--locked` flag ensures the lockfile cannot be modified during build. When compilation completes, Kettle hashes each output artifact.

**Phase 4: Provenance Generation.** Kettle creates two documents. The manifest (`manifest.json`) is a human-readable summary of everything that went into and came out of the build: git hashes, lockfile hash, dependency list, toolchain info, artifact hashes, and the input merkle root. The provenance (`provenance.json`) is a SLSA v1.2 statement in the standard in-toto format. It contains the same information structured for interoperability with other supply chain security tools.

**Phase 5: TEE Attestation (optional).** If you're building on AMD SEV-SNP hardware, Kettle can generate a hardware-signed attestation. It hashes the provenance document to 32 bytes and requests an attestation report from the CPU. The CPU signs a statement that includes this hash, proving "this provenance hash was presented to me, running in this measured environment, at this time." The attestation is saved as `evidence.b64`.

### What Kettle Produces

A complete Kettle build generates three files in the output directory:

**manifest.json** contains a readable summary of the build:

```json
{
  "git_commit": "a1b2c3d4e5f67890abcdef1234567890abcdef12",
  "git_tree": "7890abcdef1234567890abcdef1234567890abcde",
  "lockfile_hash": "23b2e23aa04c93c350cac09ac73636e4ecedf564...",
  "input_merkle_root": "72a97c73d0c59905c89dc7da145a5ecc3d809be5...",
  "toolchain": {
    "rustc_hash": "e6abf55ab1859e7c990be77fd593f5166...",
    "cargo_hash": "51de284e8bb0d03dcee595a0fb1cb3a952..."
  },
  "artifacts": [
    {"name": "my-app", "hash": "1d1ea25c371d4f6de8d6e3c26fdad2238..."}
  ]
}
```

**provenance.json** contains the SLSA v1.2 provenance statement. The structure follows the in-toto attestation specification: a subject (the output artifacts with their hashes), a predicate type identifying this as SLSA provenance, and a predicate containing the build definition (what was built, with what inputs) and run details (who built it, when, with what results). Dependencies are recorded as Package URLs (PURLs) like `pkg:cargo/serde@1.0.228?checksum=sha256:9a8e94ea...`.

**evidence.b64** contains the TEE attestation report, if generated. This is a base64-encoded structure signed by the AMD CPU's attestation key. The first 32 bytes of the custom data field contain the SHA256 hash of the provenance document, cryptographically binding the attestation to the provenance content.

### Verification

Verification reconstructs the chain of trust from attestation through provenance to the actual artifact.

**Without TEE attestation** (provenance-only verification): Kettle validates that the provenance document is well-formed SLSA v1.2. If you provide the project directory, it verifies the current git commit and tree hash match the provenance. If you provide the lockfile, it verifies the hash matches. If you provide the binary, it verifies the artifact hash matches. It can also recompute the input merkle root from current state and verify it matches the provenance.

**With TEE attestation** (full verification): Kettle first verifies the attestation cryptographic signature using `attest-amd`. It checks that the provenance hash in the attestation matches the actual provenance document. Then it runs all the provenance checks. This gives you hardware-rooted proof that the provenance was generated inside a trusted execution environment, not fabricated after the fact.

```
                        evidence.b64
                             │
                             ▼
                   [attest-amd verify]
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
   Signature valid?   Provenance hash    Platform state
          │            matches?           verified?
          ▼                  │                  │
         Yes                 ▼                  ▼
                      provenance.json          Yes
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
    Git commit          Lockfile hash      Binary hash
    matches?            matches?           matches?
```

### Inclusion Proofs

Sometimes you need to prove that a specific input was part of the build without revealing everything else. The `prove-inclusion` command generates Merkle inclusion proofs for specified hashes.

For example, a security auditor might ask: "Prove you built against serde 1.0.228." You can generate a proof that includes only the path from the serde leaf to the merkle root. The auditor verifies the proof against the published merkle root without seeing your other 126 dependencies.

This works because Merkle trees have a useful property: you can prove membership with a logarithmic-sized proof (the sibling hashes along the path to the root) without revealing other leaves.

### Toolchain Support

**Cargo (Rust)**: Kettle parses `Cargo.lock` to extract package names, versions, and SHA256 checksums. It verifies each dependency against your local cargo cache (`~/.cargo/registry/cache/`). The checksums come directly from crates.io during resolution and are stored in the lockfile. Kettle also hashes your rustc and cargo binaries to record the exact compiler version.

**Nix (Flakes)**: Kettle parses `flake.lock` to extract flake inputs with their `narHash` values (Nix's content-addressed hash format). It verifies inputs exist in your Nix store at the expected paths. Nix offers two verification modes:

- **Shallow mode** (default): Only verifies direct flake inputs listed in flake.lock. Fast, typically 5-15 inputs.
- **Deep mode**: Evaluates the full derivation graph and extracts all fixed-output derivations (FODs), the content-addressed network fetches that Nix makes during build. Comprehensive but slow (can take minutes for large projects).

Use shallow mode for development iteration. Use deep mode for release builds when you need complete dependency coverage.

### Workloads

Kettle can execute arbitrary scripts on attested builds in a sandboxed environment. This is useful for security audits, compliance checks, or any analysis that needs cryptographic proof it ran on the claimed build.

A workload is defined in `workload.yaml`:

```yaml
name: security-audit
steps:
  - name: run-analysis
    run: ./analyze.sh $BUILD_ARTIFACTS
inputs:
  expected_input_root: "sha256:72a97c73d0c59905c89dc7da..."
```

The `expected_input_root` ensures the workload only runs if the build's input merkle root matches. Kettle generates workload provenance that chains to the original build provenance, creating a complete audit trail: this analysis ran on this build which came from this source.

## Common Issues

**"Verification failed but I haven't changed anything."** Git verification requires a clean working tree. The tree hash changes if any tracked file changes, even if you haven't committed. Run `git status` and ensure there are no uncommitted changes before building.

**"Nix deep evaluation takes forever."** Deep evaluation runs `nix derivation show --recursive`, which evaluates your entire dependency graph. For large projects with hundreds of derivations, this can take several minutes. If you're iterating quickly, use `--shallow` mode. Save deep evaluation for release builds.

**"I don't have AMD SEV-SNP hardware."** Kettle works without TEE attestation. The provenance-only mode still gives you verified build inputs and SLSA-compliant provenance. For hardware-backed attestation without local SEV-SNP, use `kettle tee-build` to build on a remote TEE service.

**"The merkle root doesn't match between builds."** Input ordering is deterministic but toolchain-specific. Common causes: different toolchain version (different binary hash), modified lockfile (different dependency set or order), or running on different machines with different cached artifacts. Compare `manifest.json` files to identify which input differs.

**"Dependency verification failed: Store path not found."** For Nix, flake inputs must be in your local store before verification. Kettle runs `nix flake prefetch` automatically, but if your network is restricted or the prefetch fails, verification will fail. Run `nix flake prefetch` manually in the project directory to ensure inputs are cached.

## Security Model

**What Kettle proves:**
- All build inputs (source, dependencies, toolchain) match specific cryptographic hashes
- The build process executed with exactly these inputs
- Output artifacts have specific hashes bound to those inputs
- (With TEE) The build environment was hardware-isolated and unmodified
- (With TEE) The attestation is fresh, not replayed from a previous build

**What you must trust:**
- AMD CPU and firmware, if using TEE attestation
- Git repository integrity for source code (Kettle verifies hashes, not that the repository is trustworthy)
- Package registry infrastructure (crates.io, nixpkgs) for dependency checksums
- The Kettle codebase itself (open source, auditable)

**What Kettle does not prove:**
- Source code is free of vulnerabilities (that's your responsibility)
- Dependencies are safe to use (Kettle verifies they match checksums, not that they're secure)
- The compiler is bug-free (Kettle hashes the compiler, doesn't verify its correctness)

The trust model shifts from trusting build infrastructure to trusting hardware vendors and source infrastructure. This is a smaller, more auditable surface, but not a trustless system.

## Installation

```bash
git clone https://github.com/lunal-dev/attestable-builds.git
cd attestable-builds

# Install with uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

Requirements:
- Python 3.10 or later
- Git
- Cargo (for Rust projects) or Nix (for Nix projects)
- For TEE attestation: AMD SEV-SNP hardware and `attest-amd` tool
- For remote builds: Access to a Kettle TEE service endpoint

## Commands

### Building

**kettle build** builds a project with full input verification and provenance generation.

```bash
kettle build /path/to/project [OPTIONS]

Options:
  -o, --output PATH    Output directory for build artifacts (default: ./kettle-build)
  --release/--debug    Build mode (default: release)
  -a, --attestation    Generate TEE attestation (requires SEV-SNP hardware)
  --shallow            Use shallow verification (Nix only: skip deep evaluation)
  -v, --verbose        Show detailed verification output
```

**kettle tee-build** builds on a remote TEE service.

```bash
kettle tee-build /path/to/project [OPTIONS]

Options:
  --api URL            TEE service URL (default: http://localhost:8000)
```

Creates a source archive, uploads to the remote TEE, and downloads the resulting artifacts to `kettle-{build_id}/`.

### Verification

**kettle verify** performs full verification (attestation and provenance).

```bash
kettle verify BUILD_DIR [OPTIONS]

Options:
  -p, --project-dir PATH    Project directory for git/lockfile checks
  -b, --binary PATH         Binary artifact to verify hash
  --strict                  Fail if optional checks cannot be performed
```

**kettle verify-provenance** verifies only the provenance document.

```bash
kettle verify-provenance PROVENANCE_JSON [OPTIONS]

Options:
  -p, --project-dir PATH    Project directory for verification
  -b, --binary PATH         Binary to verify
  --strict                  Fail on optional check failures
```

**kettle verify-attestation** verifies only the TEE attestation.

```bash
kettle verify-attestation EVIDENCE_FILE --passport PROVENANCE_JSON
```

**kettle prove-inclusion** generates Merkle inclusion proofs.

```bash
kettle prove-inclusion PROVENANCE_JSON HASH [HASH...] [OPTIONS]

Options:
  -o, --output PATH    Save proofs to file (default: stdout)
```

Supports partial hash matching. For example, `serde:1.0` matches any serde dependency starting with version 1.0.

### Workloads

**kettle run-workload** executes a workload on a local build.

```bash
kettle run-workload WORKLOAD_DIR BUILD_ID
```

**kettle tee-run-workload** executes on a remote TEE build.

```bash
kettle tee-run-workload WORKLOAD_DIR BUILD_ID EXPECTED_INPUT_ROOT [OPTIONS]

Options:
  --api URL            TEE service URL (default: http://localhost:8000)
```

**kettle tee-get-results** downloads workload results.

```bash
kettle tee-get-results BUILD_ID WORKLOAD_ID [OPTIONS]

Options:
  --api URL            TEE service URL
  -o, --output PATH    Output directory (default: workload-results-{id})
```
