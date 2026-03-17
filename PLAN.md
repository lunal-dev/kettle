# Plans

Future work items tracked here.

---

## Smoke test: build openclaw/openclaw in CI

**Context:** The `build-projects` job in `.github/workflows/test.yml` runs `bin/kettle-build` against a matrix of real projects (currently `burntsushi/ripgrep` and `eza-community/eza`) to verify the Cargo and Nix toolchains work end-to-end. There is no equivalent coverage for the pnpm toolchain.

**Work:** Add `openclaw/openclaw` to the `build-projects` matrix and add Node.js and pnpm setup steps to the job (alongside the existing Rust and Nix setup steps). The pnpm setup steps are harmless for the existing Cargo/Nix matrix entries, so no job split is needed. This gives the pnpm toolchain the same real-world CI coverage as the other toolchains.

---

## Tech Debt

### Generalise `Digest` struct field name

**Context:** `ResolvedDependency.digest.sha256` stores sha512 SRI values (e.g. `sha512-<base64>`) for pnpm packages. The field name `sha256` is a misnomer.

**Work:** Rename `digest.sha256` to `digest.integrity` (or generalise the `Digest` struct to carry an algorithm-tagged value), and provide a versioned migration for existing serialised provenance JSON. The change affects the provenance JSON schema so it requires bumping the `build_type` URI for affected toolchains.

---

### Harden Cargo lockfile parser to reject packages without checksums

**Context:** `src/toolchain/cargo.rs` silently skips `Cargo.lock` packages that have no `checksum` field (workspace members fall into this category). The pnpm toolchain treats a missing integrity field as a hard error, so the two toolchains have inconsistent policies.

**Work:** Audit which packages legitimately lack checksums in `Cargo.lock` (path dependencies, workspace members, git dependencies), decide the correct policy (skip with a logged warning, or error), and update the parser and its tests to match the strict policy applied in the pnpm toolchain.
