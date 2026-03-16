# Plans

Future work items tracked here.

---

## Tech Debt

### Generalise `Digest` struct field name

**Context:** `ResolvedDependency.digest.sha256` stores sha512 SRI values (e.g. `sha512-<base64>`) for pnpm packages. The field name `sha256` is a misnomer.

**Work:** Rename `digest.sha256` to `digest.integrity` (or generalise the `Digest` struct to carry an algorithm-tagged value), and provide a versioned migration for existing serialised provenance JSON. The change affects the provenance JSON schema so it requires bumping the `build_type` URI for affected toolchains.

---

### Harden Cargo lockfile parser to reject packages without checksums

**Context:** `src/toolchain/cargo.rs` silently skips `Cargo.lock` packages that have no `checksum` field (workspace members fall into this category). The pnpm toolchain treats a missing integrity field as a hard error, so the two toolchains have inconsistent policies.

**Work:** Audit which packages legitimately lack checksums in `Cargo.lock` (path dependencies, workspace members, git dependencies), decide the correct policy (skip with a logged warning, or error), and update the parser and its tests to match the strict policy applied in the pnpm toolchain.
