# Security Model

**[← Back to Main README](README.md)**

Security guarantees, trust assumptions, and threat model.

## What's Proven

**Phase 1:** Cryptographic proof of build inputs
- Exact source code (git commit + tree hash)
- Exact dependencies (Cargo.lock + .crate checksums)
- Exact toolchain (rustc/cargo binary hashes)

**Phase 2:** TEE attestation of execution environment
- Build executed in genuine TEE hardware (AMD SEV-SNP)
- Passport binding (SHA256(passport) in attestation)
- Freshness (nonce mechanism)

## Verification Strategies

**Dependencies (Subset Verification):**
- Cached .crate files must be subset of Cargo.lock with matching checksums
- Platform-specific deps can be absent (Cargo.lock has all platforms, build only downloads some)
- Extra crates not in Cargo.lock are flagged

**Git (Optional but Strict):**
- If git repo detected, ALL checks must pass or build fails
- Enforces clean working tree (no uncommitted changes)
- If no git repo, build proceeds without git verification

## Trust Assumptions

### Must Trust ✓

1. **Git infrastructure** - Repository integrity, commit authenticity
2. **Cargo/crates.io** - Dependency checksums are correct
3. **Rustup** - Toolchain binaries are authentic
4. **TEE hardware** (Phase 2) - AMD SEV-SNP is genuine, firmware uncompromised

### Don't Need to Trust ✗

1. **Build environment** - Verification works despite compromised host
2. **Network** - Offline verification possible after initial download
3. **Verifiers** - Anyone can verify without trust

## Threat Model

### Defends Against ✅

| Attack | Defense |
|--------|---------|
| Tampered source code | Git commit/tree hash verification |
| Uncommitted changes | Clean working tree enforced |
| Substituted dependencies | .crate checksum verification |
| Modified toolchain | rustc/cargo binary hashes |
| Extra/unexpected crates | Flagged if not in Cargo.lock |
| Build environment tampering | TEE isolation (Phase 2) |
| MITM on verification | Cryptographic attestation |
| Replay attacks | Nonce mechanism |

### Does NOT Defend Against ❌

| Attack | Why | Mitigation |
|--------|-----|------------|
| Compromised source repo | System proves integrity, not security | Code review, signed commits |
| Intentional malicious code | Verification ≠ security audit | Code review, scanning |
| Vulnerable dependencies | Proves versions, not absence of bugs | cargo audit, version pinning |
| Compiler bugs | Rustc itself can have bugs | Use stable, tested versions |
| Trust anchor compromise | If crates.io/rustup compromised | Multiple verification sources |
| TEE side-channels | Spectre/Meltdown attacks | Updated firmware, microcode |

## Attack Scenario Examples

**Dependency Substitution:** Attacker replaces .crate file → SHA256 mismatch → Build fails ✅

**Source Tampering:** Attacker modifies files → git status detects changes → Build fails ✅

**Toolchain Backdoor:** Attacker replaces rustc → Hash mismatch in passport → Detectable ✅

**Build Environment Compromise:** Attacker tampers during build → TEE isolation prevents → Protected ✅ (Phase 2)

**Repository Compromise:** Attacker commits malicious code → Verification proves malicious commit was used but doesn't flag it → Not defended ❌ (requires code review)

## Best Practices

### For Build Authors

- Use signed commits for additional authenticity
- Pin toolchain versions in rust-toolchain.toml
- Run `cargo audit` to check for vulnerabilities
- Review Cargo.lock changes in code review
- Archive passports for audit trail

### For Verifiers

- Verify passport completeness (all expected fields present)
- Cross-reference git commit with repository
- Validate toolchain versions meet requirements
- Check build timestamps are reasonable
- Always verify TEE signature with `attest-amd verify` (Phase 2)
- Enforce reasonable nonce max-age limits

## Comparison to Other Approaches

| Aspect | Reproducible Builds | Signed Binaries | Attestable Builds |
|--------|-------------------|-----------------|------------------|
| **Proves** | Output hash match | Author identity | Full build process |
| **Verification** | Slow (rebuild) | Fast (sig check) | Fast (crypto) |
| **Build visible** | No | No | Yes (passport) |
| **Input verification** | No | No | Yes (all inputs) |
| **Environment proof** | No | No | Yes (TEE) |
| **Maintenance** | High (brittle) | Low | Medium |

## Future Enhancements

**Planned:**
- Challenge-response nonce (vs timestamp)
- Launch measurement verification
- Certificate pinning for attestation
- Multi-party verification
- Public transparency log

**Under Consideration:**
- Require GPG-signed commits
- SBOM generation
- Custom verification policies
- VEX integration

## Responsible Disclosure

Security vulnerabilities: security@lunal.dev

Include: description, reproduction steps, impact, suggested mitigation.
Response within 48 hours.
