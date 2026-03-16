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
| `src/commands/build.rs` | Add `Pnpm` variant to `ProjectToolchain`, detect `pnpm-lock.yaml` |
| `src/provenance.rs` | Add `PnpmToolchain { node, pnpm, kettle }` variant to `Toolchain` enum and `Display` impl |
| `Cargo.toml` | Add `serde_yaml` dependency for pnpm lockfile parsing |
| `tests/fixtures/openclaw/` | New fixture directory with sample `pnpm-lock.yaml` and expected `provenance.json` |

---

## Toolchain Detection

`ProjectToolchain::from_dir` in `commands/build.rs` gains a `Pnpm` variant. Detection checks file presence in this priority order:

1. `flake.nix` → Nix
2. `Cargo.lock` → Cargo
3. `pnpm-lock.yaml` → Pnpm
4. None matched → error

---

## `ToolchainDriver` Implementation (`pnpm.rs`)

### `lockfile_filename()`
Returns `"pnpm-lock.yaml"`.

### `build_command_display()`
Returns `"pnpm install --frozen-lockfile && pnpm build"`.

### `collect_inputs()`
1. `ToolBinaryInfo::via_which("node")` — records node version and binary hash
2. `ToolBinaryInfo::via_which("pnpm")` — records pnpm version and binary hash
3. `ToolBinaryInfo::kettle_info()`
4. `parse_pnpm_lock(lockfile_bytes)` — extracts resolved dependencies (see below)

### `merkle_entries()`
Ordered leaf strings (frozen contract — do not reorder without bumping build_type URI):
1. `git.commit`
2. `git.tree`
3. `node_hash`
4. `node_version`
5. `pnpm_hash`
6. `pnpm_version`
7. `lockfile_hash`

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
- `external_build_command`: `"pnpm build"`
- `toolchain`: `Toolchain::PnpmToolchain { node, pnpm, kettle }`
- `resolved_dependencies`: parsed from lockfile

---

## Lockfile Parsing

Parses `pnpm-lock.yaml` to extract one `ResolvedDependency` per package.

pnpm lockfile versions differ in structure:
- **v6/v7/v8**: packages keyed as `/name/version` under `packages:`
- **v9**: packages keyed as `name@version` under `packages:`, with `resolution.integrity`

For each package entry:
- Extract package name and version from the key
- Read `resolution.integrity` — **hard error if absent**
- URI format: `pkg:npm/name@version?integrity=<integrity>`
- `digest.sha256` is left as the integrity string (which may be sha512; the field name is a legacy artifact of the schema)

**Error policy:** malformed YAML or any package missing `resolution.integrity` is a hard error. All input files must be recorded.

Results are sorted by URI for determinism.

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

`Display` impl for `PnpmToolchain` renders the `node` version string.

---

## Error Handling Summary

| Condition | Behaviour |
|---|---|
| `pnpm install` exits non-zero | Hard error with exit code |
| `pnpm build` exits non-zero | Hard error with exit code |
| `dist/` absent after build | Hard error |
| `tar` subprocess fails | Hard error |
| Malformed `pnpm-lock.yaml` | Hard error |
| Package missing `resolution.integrity` | Hard error |
| `node` or `pnpm` not on PATH | Hard error (via `via_which`) |

---

## Testing

### Unit tests in `pnpm.rs`
- Lockfile parser happy path (v9 format): correct dep count, URI format, sorted by URI
- Lockfile parser happy path (v6/v7/v8 format)
- Empty `packages` block → empty deps vec
- Package missing `resolution.integrity` → error
- Malformed YAML → error
- Deterministic ordering across two calls on same input

### Unit tests in `build.rs`
- `pnpm-lock.yaml` present → `Pnpm` detected
- `flake.nix` + `pnpm-lock.yaml` → `Nix` wins
- `Cargo.lock` + `pnpm-lock.yaml` → `Cargo` wins

### Fixture (`tests/fixtures/openclaw/`)
- `pnpm-lock.yaml`: representative real-world snippet with multiple packages
- `provenance.json`: expected output, used for round-trip and key-ordering tests

---

## New Dependency

`serde_yaml` for parsing `pnpm-lock.yaml`. No other new dependencies.
