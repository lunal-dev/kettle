# pnpm Smoke Test: Build openclaw/openclaw in CI

## Summary

Add `openclaw/openclaw` to the `build-projects` CI matrix so the pnpm toolchain gets the same real-world end-to-end coverage as the Cargo and Nix toolchains. Add Node.js and pnpm setup steps that read versions from the target project's declarations.

## Changes

Both changes must land in the same PR. The `bin/kettle-build` guard is required for the workflow change to work, since the workflow pre-clones the repo and `kettle-build` would otherwise fail trying to clone into an existing directory.

### 1. `bin/kettle-build`

Add a guard around `git clone` so it skips cloning when the target directory already exists. This makes the script idempotent and allows CI to pre-clone the repo for version detection.

Before:
```bash
git clone "https://github.com/$REPO" "$DIR"
```

After:
```bash
if [[ ! -d "$DIR" ]]; then
  git clone "https://github.com/$REPO" "$DIR"
fi
```

### 2. `.github/workflows/test.yml`

Add `openclaw/openclaw` to the `build-projects` matrix and insert four new steps before the existing `bin/kettle-build` step:

#### a. Pre-clone

```yaml
- name: Clone target project
  run: git clone "https://github.com/${{ matrix.project }}" "/tmp/$(basename "${{ matrix.project }}")"
```

Runs unconditionally for all matrix entries. Clones the target repo to the same `/tmp/<name>` path that `kettle-build` uses. Since `kettle-build` now skips clone when the dir exists, there is no double-clone.

#### b. Detect project toolchain versions

```yaml
- name: Detect pnpm and node versions
  id: detect-versions
  run: |
    PKG="/tmp/$(basename "${{ matrix.project }}")/package.json"
    if [[ -f "$PKG" ]]; then
      PNPM_VERSION=$(jq -r '.packageManager // empty' "$PKG" | sed -n 's/^pnpm@//p')
      NODE_VERSION=$(jq -r '.engines.node // empty' "$PKG")
      echo "pnpm-version=$PNPM_VERSION" >> "$GITHUB_OUTPUT"
      echo "node-version=$NODE_VERSION" >> "$GITHUB_OUTPUT"
    fi
```

Reads `packageManager` and `engines.node` from the target project's `package.json`. If the file is absent or the fields don't exist, the outputs are empty. The `sed` strips the `pnpm@` prefix to extract the bare version number.

For `openclaw/openclaw` this currently produces `pnpm-version=10.23.0` and `node-version=>=22.16.0`.

`jq` is pre-installed on `ubuntu-latest` GitHub Actions runners.

#### c. Setup pnpm

```yaml
- name: Setup pnpm
  if: steps.detect-versions.outputs.pnpm-version != ''
  uses: pnpm/action-setup@v4
  with:
    version: ${{ steps.detect-versions.outputs.pnpm-version }}
```

Conditional on a non-empty pnpm version. Skipped for Cargo/Nix projects.

#### d. Setup Node.js

```yaml
- name: Setup Node.js
  if: steps.detect-versions.outputs.pnpm-version != ''
  uses: actions/setup-node@v4
  with:
    node-version: ${{ steps.detect-versions.outputs.node-version }}
```

Uses the `node-version` input directly with the value extracted from `engines.node` (e.g. `>=22.16.0`). `actions/setup-node@v4` supports semver ranges in `node-version`, so this resolves to the latest matching release. Also conditional on pnpm version being detected.

### Matrix after change

```yaml
matrix:
  project:
    - burntsushi/ripgrep
    - eza-community/eza
    - openclaw/openclaw
```

The existing `fail-fast: false` setting ensures a failure in the openclaw build does not cancel the Cargo/Nix matrix entries.

## Error handling

- Missing `package.json` or `packageManager` field: version outputs are empty, pnpm/node setup steps are skipped. Build proceeds with whatever is on PATH (correct for Cargo/Nix projects).
- Malformed `packageManager` field: `pnpm/action-setup` fails the job. Correct behavior since the project's declarations are broken.
- `pnpm install` or `pnpm build` failure: `kettle-build` exits non-zero, job fails. Correct behavior.
- Missing `engines.node` but `packageManager` present: `actions/setup-node@v4` receives an empty `node-version` and falls back to the runner's default Node.js version.
- Pre-clone `git clone` failure (e.g. target repo renamed/unavailable): job fails for that matrix entry only (`fail-fast: false`). This is an inherent risk of smoke-testing external repos and is acceptable.

## Testing

The smoke test is self-testing: CI runs `bin/kettle-build openclaw/openclaw` and the job passes or fails based on whether the pnpm toolchain produces valid provenance for a real project.
