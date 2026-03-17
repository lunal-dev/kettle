# Plans

Future work items tracked here.

---

## Smoke test: build openclaw/openclaw in CI

Add `openclaw/openclaw` to the `build-projects` matrix in the tests, and make sure that `bin/kettle-build openclaw/openclaw` succeeds. This will validate the pnpm toolchain in a real-world test.

---

## Generalise `Digest` struct field name

Update the `ResolvedDependency.digest` struct to an enum that can also handle sha512 values, for toolchains that provide sha512 instead of sha256 (like pnpm).

---

## Update Cargo lockfile parser to track checksums for git and path packages

Cargo.lock doesn't innately provide checksums for packages that are provided from git repos or from paths on disk. We want to skip workspace members (since those files are part of the current git repo), but fetch git commit shas from git repositories or paths on disk. We also want to fail if there is a dependency with a path on disk but the git repository at that path is dirty, since that would mean inputs that aren't tracked by the git sha.

After we track checksums for git repository dependencies and path dependencies outside the project's git repository, we should error on any cargo dependencies that don't have a checksum or git commit sha.
