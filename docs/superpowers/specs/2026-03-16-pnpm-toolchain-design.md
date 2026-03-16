# pnpm Toolchain for Kettle

**Date:** 2026-03-16
**Scope:** Add support for building and attesting pnpm-based TypeScript projects (initially targeting openclaw/openclaw).

---

## Goal

Kettle can currently build and attest Rust (Cargo) and Nix projects. This spec adds a third toolchain: pnpm. The first project to use it is openclaw/openclaw, a TypeScript CLI tool built with `pnpm build` that outputs to `dist/`.

---

## Files Changed

| File | Change |
|---|---|
| `src/toolchain/pnpm.rs` | New — implements `ToolchainDriver` for pnpm projects |
| `src/toolchain.rs` | Add `pub(crate) mod pnpm` |
| `src/commands/build.rs` | Add `Pnpm` variant to `ProjectToolchain`, detect `pnpm-lock.yaml`, update error message |
| `src/provenance.rs` | Add `PnpmToolchain { node, pnpm, kettle }` variant to `Toolchain` enum and `Display` impl |
| `Cargo.toml` | Add `serde_yaml` dependency for pnpm lockfile parsing |
| `tests/fixtures/openclaw/` | New fixture directory with sample `pnpm-lock.yaml` files and expected `provenance.json` |

---

## Toolchain Detection

`ProjectToolchain::from_dir` in `commands/build.rs` gains a `Pnpm` variant. Detection checks file presence in this priority order:

1. `flake.nix` → Nix
2. `Cargo.lock` → Cargo
3. `pnpm-lock.yaml` → Pnpm
4. None matched → error: `"Could not determine toolchain. Is {:?} a rust, nix, or pnpm project?"`

---

## `pnpm.rs` Entry Point

Following the pattern of `cargo.rs` and `nix.rs`, the module exposes:

```rust
pub(crate) fn build(path: &PathBuf) -> Result<()> {
    crate::toolchain::runner::run::<PnpmInputs>(path)
}
```

`commands/build.rs` calls `crate::toolchain::pnpm::build(path)` for the `Pnpm` variant.

---

## `ToolchainDriver` Implementation (`pnpm.rs`)

### `lockfile_filename()`
Returns `"pnpm-lock.yaml"`.

### `build_command_display()`
Returns `"pnpm install --frozen-lockfile && pnpm build"`. This is the human-readable string printed to the user. It is distinct from `provenance_fields().external_build_command` (see below).

### `collect_inputs()`
1. `ToolBinaryInfo::via_which("node")` — records node version and binary hash
2. `ToolBinaryInfo::via_which("pnpm")` — records pnpm version and binary hash
3. `ToolBinaryInfo::kettle_info()`
4. `parse_pnpm_lock(lockfile_bytes)` — extracts resolved dependencies (see Lockfile Parsing)

### `merkle_entries()`
Ordered leaf strings (frozen contract — do not reorder without bumping build_type URI):
1. `git.commit`
2. `git.tree`
3. `node_hash`
4. `node_version`
5. `pnpm_hash`
6. `pnpm_version`
7. `lockfile_hash`
8. All resolved dependency URIs (one entry per dep, in sorted URI order, matching the Cargo driver pattern)

### `run_build()`
Two sequential subprocess calls in `path`:
1. `pnpm install --frozen-lockfile`
2. `pnpm build`

Both must exit 0. On failure, return an error with the exit code.

### `collect_artifacts()`
1. Check that `dist/` exists inside `path` — hard error if absent
2. Create `dist.tar.gz` in `artifacts_dir` using the system `tar` command:
   `tar -czf <artifacts_dir>/dist.tar.gz -C <path> dist`
3. Compute SHA-256 of the resulting tarball
4. Return a single `Artifact { name: "dist.tar.gz", ... }`

### `provenance_fields()`
- `build_type`: `"https://lunal.dev/kettle/pnpm@v1"`
- `external_build_command`: `"pnpm build"` (the canonical provenance field, distinct from `build_command_display()`)
- `toolchain`: `Toolchain::PnpmToolchain { node, pnpm, kettle }`
- `internal_parameters.lockfile_hash`: `Digest { sha256: self.lockfile_hash }` (the hash received by `collect_inputs`, stored on the inputs struct)
- `internal_parameters.evaluation`: `None`
- `internal_parameters.flake_inputs`: `None`
- `resolved_dependencies`: parsed from lockfile

---

## Lockfile Parsing

Parses `pnpm-lock.yaml` to extract one `ResolvedDependency` per package.

### Lockfile version detection

Read the `lockfileVersion` field from the YAML root. Treat it as a string (it may be a number or quoted string depending on pnpm version). If the string representation starts with `"9"`, use v9 key format; otherwise use legacy (v5/v6) key format.

Hard error if `lockfileVersion` is absent or cannot be parsed.

### Key formats

**v9** (`lockfileVersion` starts with `"9"`):
- Unscoped: `semver@7.6.0`
- Scoped: `@babel/core@7.24.0`
- With peer dep suffix (strip before parsing): `@babel/core@7.24.0(@types/node@20.0.0)`

Parsing algorithm for v9:
1. Strip trailing `(...)` suffix if present
2. If key starts with `@` (scoped package): split on the **second** `@` — name is everything before it, version is everything after
3. Otherwise: split on the **first** `@` — name is before, version is after

**Legacy (v5/v6)** (`lockfileVersion` < 9):
- Unscoped: `/semver/7.6.0`
- Scoped: `/@babel/core/7.24.0`

Parsing algorithm for legacy:
1. Strip leading `/`
2. Split on the **last** `/` — version is the final segment (raw), name is everything before it
3. Strip any peer-dep suffix from the raw version: if the version segment contains `_`, truncate at the first `_` (e.g. `7.24.0_@types+node@20.0.0` → `7.24.0`)

### Integrity and URI

For each package:
- Read `resolution.integrity` — **hard error if absent or empty**
- The integrity value is an npm Subresource Integrity string, e.g. `sha512-<base64>`
- URI format: `pkg:npm/name@version?checksum=<full-integrity-string>`
- `digest.sha256` stores the full integrity string verbatim (the field name is a legacy artifact of the shared `Digest` struct; the value is sha512, not sha256 — this is tracked as tech debt)

Results are sorted by URI for determinism.

### Error policy

- Malformed YAML → hard error
- `lockfileVersion` absent → hard error
- Any package missing `resolution.integrity` → hard error
- Key cannot be parsed into name+version → hard error

All input files must be recorded; partial lockfile parsing is not permitted.

---

## Provenance Types (`provenance.rs`)

New variant added to the `Toolchain` enum:

```rust
PnpmToolchain {
    node: ToolchainVersion,
    pnpm: ToolchainVersion,
    kettle: ToolchainVersion,
}
```

### Serde untagged variant ordering

The `Toolchain` enum uses `#[serde(untagged)]`. Serde disambiguates by trying variants in declaration order; the first variant whose fields all deserialize successfully wins.

Field sets per variant:
- `NixToolchain`: `{ nix, kettle }`
- `RustToolchain`: `{ cargo, rustc, kettle }`
- `PnpmToolchain`: `{ node, pnpm, kettle }`

All three sets are disjoint (no variant's unique fields appear in another), so order does not affect correctness. `PnpmToolchain` may be declared in any position. There is no ambiguity risk.

### `Display` impl
`PnpmToolchain` renders the `node` version string (matching the pattern of `NixToolchain` rendering `nix` and `RustToolchain` rendering `rustc`).

---

## Error Handling Summary

| Condition | Behaviour |
|---|---|
| `pnpm install` exits non-zero | Hard error with exit code |
| `pnpm build` exits non-zero | Hard error with exit code |
| `dist/` absent after build | Hard error |
| `tar` subprocess fails | Hard error |
| Malformed `pnpm-lock.yaml` | Hard error |
| `lockfileVersion` absent | Hard error |
| Package missing `resolution.integrity` | Hard error |
| Package key cannot be parsed | Hard error |
| `node` or `pnpm` not on PATH | Hard error (via `via_which`) |

---

## Testing

### Unit tests in `pnpm.rs`

- Lockfile parser happy path — v9 format, unscoped packages: correct dep count, URI format (`pkg:npm/...`), sorted by URI
- Lockfile parser happy path — v9 format, scoped packages (`@scope/name@version`): correct name extraction
- Lockfile parser happy path — v9 format, peer dep suffix stripped: key `name@version(peer@version)` parsed correctly
- Lockfile parser happy path — legacy (v5/v6) format, unscoped packages
- Lockfile parser happy path — legacy format, scoped packages (`/@scope/name/version`)
- Lockfile parser happy path — legacy format, peer dep suffix stripped (`/@scope/name/version_peer+name@ver` → version=`version`)
- Empty `packages` block → empty deps vec
- Package missing `resolution.integrity` → error
- Malformed YAML → error
- `lockfileVersion` absent → error
- Deterministic ordering: two calls on same input produce identical results
- URI format validation: URIs start with `pkg:npm/` and contain `?checksum=`

### Unit tests in `build.rs`

- `pnpm-lock.yaml` present → `Pnpm` detected
- `flake.nix` + `pnpm-lock.yaml` → `Nix` wins
- `Cargo.lock` + `pnpm-lock.yaml` → `Cargo` wins
- None present → error message includes "pnpm"

### Unit tests in `provenance.rs`

- `key_ordering_matches_when_regenerated_pnpm`: serialise a `PnpmToolchain` provenance fixture and verify the output is byte-identical to the fixture file (matching the pattern of the existing `key_ordering_matches_when_regenerated_cargo` test)
- `toolchain_display_pnpm`: construct a `PnpmToolchain` and assert `format!("{t}")` equals the `node` version string (matching the existing `toolchain_display_nix` and `toolchain_display_rust` tests)

### Fixture (`tests/fixtures/openclaw/`)

- `pnpm-lock-v9.yaml`: v9 format snippet with at least 3 packages including one scoped package
- `pnpm-lock-legacy.yaml`: v5/v6 format snippet with at least 3 packages including one scoped package
- `provenance.json`: expected output for the v9 fixture, used for round-trip and key-ordering tests

---

## New Dependency

`serde_yaml` for parsing `pnpm-lock.yaml`. No other new dependencies.

---

## Tech Debt (out of scope)

- `digest.sha256` in `ResolvedDependency` stores sha512 SRI values for pnpm packages. The field should eventually be renamed to `digest.integrity` or the `Digest` struct generalised. This affects serialised provenance JSON so it requires a versioned migration.
- Cargo's lockfile parser silently skips packages without a checksum (workspace members). This should be audited and hardened to match the strict policy applied here.
