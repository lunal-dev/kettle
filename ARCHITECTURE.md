# Architecture

**[← Back to Main README](README.md)**

System architecture for attestable builds - how inputs are verified, builds executed, and outputs attested.

## Build Flow

```
┌─────────────────────────────────────────────────────────┐
│ Phase 1: Input Verification                             │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. Git Source (optional, strict if present)            │
│     ├─ Hash git binary: SHA256(git executable)          │
│     ├─ Get commit hash: git rev-parse HEAD              │
│     ├─ Get tree hash: git rev-parse HEAD^{tree}         │
│     ├─ Check working tree: git status --porcelain       │
│     └─ FAIL if uncommitted changes exist                │
│                                                          │
│  2. Cargo.lock: SHA256(entire file)                     │
│                                                          │
│  3. Dependencies                                         │
│     ├─ Scan ~/.cargo/registry/cache/ for .crate files   │
│     ├─ For each cached crate:                           │
│     │   ├─ SHA256(crate file)                           │
│     │   └─ Compare to Cargo.lock checksum               │
│     └─ Flag crates NOT in Cargo.lock                    │
│                                                          │
│  4. Toolchain: SHA256(rustc) + SHA256(cargo)            │
│                                                          │
│  5. Build: cargo build                                   │
│                                                          │
│  6. Generate Passport                                    │
│     ├─ Merkle tree of all inputs                        │
│     ├─ Include output hashes                            │
│     └─ Write passport.json                              │
│                                                          │
├─────────────────────────────────────────────────────────┤
│ Phase 2: Attestation (Optional)                         │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. Hash passport: SHA256(passport.json)                │
│  2. Generate nonce: timestamp-based (POC)               │
│  3. Create custom data: passport_hash || nonce          │
│  4. Generate TEE attestation:                           │
│     └─ attest-amd attest --custom-data <data>           │
│  5. Save attestation: evidence.b64                      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Data Flow

### Build Command

```
User: kettle build <project> --attestation
  ↓
verify_inputs()  →  {git_info, cargo_lock_hash, deps, toolchain}
  ↓
execute_build()  →  {artifacts: [{path, hash}]}
  ↓
generate_passport()  →  passport.json
  ↓
generate_attestation()  →  evidence.b64 (if --attestation)
```

### Verification Command

```
User: kettle verify-attestation <evidence> --passport <passport>
  ↓
attest-amd verify  →  signature_valid: bool
  ↓
extract custom_data  →  {passport_hash, nonce}
  ↓
hash passport  →  computed_hash
  ↓
compare hashes + check nonce freshness  →  verification result
```

## Key Algorithms

### Dependency Verification (Subset Strategy)

```python
cargo_lock_deps = parse("Cargo.lock")  # All platforms
cached_crates = scan("~/.cargo/registry/cache/")

for crate in cached_crates:
    if crate not in cargo_lock_deps:
        flag_suspicious(crate)  # Extra crate
    elif hash(crate) != cargo_lock_deps[crate].checksum:
        fail_verification()  # Tampered crate

# Allow missing crates (platform-specific)
```

**Rationale:** Cargo.lock contains all platforms; builds only cache needed ones.

### Merkle Tree Construction

```python
inputs = [
    hash(git_commit),
    hash(cargo_lock),
    hash(deps),
    hash(toolchain)
]

merkle_root = build_merkle_tree(inputs)
passport.merkle_verification.root = merkle_root
```

## Module Organization

```
src/kettle/
├── git.py          # Git verification
├── cargo.py        # Cargo.lock + dependency verification
├── toolchain.py    # Rustc/cargo hashing
├── build.py        # Build execution
├── passport.py     # Passport generation
├── attestation.py  # TEE attestation
├── merkle.py       # Merkle trees
└── cli.py          # CLI orchestration
```

See source code for implementation details.

## Extension Points

Add new verification inputs by:
1. Create module (e.g., `npm.py` for Node.js)
2. Return `{type, hash, metadata}` dict
3. Add to `passport.py` aggregation
4. Update merkle tree in `generate_passport()`

Example:
```python
# src/kettle/npm.py
def verify_npm_inputs(project_dir):
    package_lock_hash = hash_file("package-lock.json")
    return {
        "type": "npm",
        "hash": package_lock_hash,
        "metadata": {"node_version": get_node_version()}
    }
```
