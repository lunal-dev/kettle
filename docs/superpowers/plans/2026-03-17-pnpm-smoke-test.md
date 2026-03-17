# pnpm Smoke Test Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `openclaw/openclaw` to the CI `build-projects` matrix so the pnpm toolchain gets real-world smoke test coverage.

**Architecture:** Two changes in one commit: (1) make `bin/kettle-build` idempotent by guarding `git clone`, (2) add pre-clone, version detection, pnpm setup, Node.js setup steps and the new matrix entry to `.github/workflows/test.yml`.

**Tech Stack:** Bash, GitHub Actions YAML

**Spec:** `docs/superpowers/specs/2026-03-17-pnpm-smoke-test-design.md`

---

### Task 1: Make `bin/kettle-build` idempotent

**Files:**
- Modify: `bin/kettle-build:13`

- [ ] **Step 1: Add clone guard**

Wrap the `git clone` on line 13 in a directory-existence check:

```bash
if [[ ! -d "$DIR" ]]; then
  git clone "https://github.com/$REPO" "$DIR"
fi
```

This replaces the bare `git clone` line. The rest of the script is unchanged.

- [ ] **Step 2: Verify the script still works locally**

Run:
```bash
bin/kettle-build not-a-real/repo 2>&1 || true
```
Expected: fails on `git clone` (repo doesn't exist), NOT on the `-e` guard. This confirms the guard logic is correct.

---

### Task 2: Add pnpm smoke test to CI workflow

**Files:**
- Modify: `.github/workflows/test.yml:74-83`

- [ ] **Step 1: Add `openclaw/openclaw` to the matrix**

Add a third entry to the `project` matrix list (line 76, after `eza-community/eza`):

```yaml
        project:
          - burntsushi/ripgrep
          - eza-community/eza
          - openclaw/openclaw
```

- [ ] **Step 2: Add pre-clone and version detection steps**

Insert four new steps between the `cachix/install-nix-action` step and the `bin/kettle-build` step. The final `steps:` block should be:

```yaml
    steps:
      - uses: actions/checkout@v6
      - uses: actions-rust-lang/setup-rust-toolchain@v1
      - uses: cachix/install-nix-action@v31
        with:
          github_access_token: ${{ secrets.GITHUB_TOKEN }}
      - name: Clone target project
        run: git clone "https://github.com/${{ matrix.project }}" "/tmp/$(basename "${{ matrix.project }}")"
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
      - name: Setup pnpm
        if: steps.detect-versions.outputs.pnpm-version != ''
        uses: pnpm/action-setup@v4
        with:
          version: ${{ steps.detect-versions.outputs.pnpm-version }}
      - name: Setup Node.js
        if: steps.detect-versions.outputs.pnpm-version != ''
        uses: actions/setup-node@v4
        with:
          node-version: ${{ steps.detect-versions.outputs.node-version }}
      - run: bin/kettle-build ${{ matrix.project }}
```

- [ ] **Step 3: Validate YAML syntax**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))"
```
Expected: no output (valid YAML).

---

### Task 3: Commit

- [ ] **Step 1: Commit both changes together**

```bash
git add bin/kettle-build .github/workflows/test.yml
git commit -m "ci: add openclaw/openclaw pnpm smoke test to build-projects matrix"
```

Both changes must land together — the workflow pre-clones repos, and `kettle-build` needs the idempotency guard to handle the already-cloned directory.
