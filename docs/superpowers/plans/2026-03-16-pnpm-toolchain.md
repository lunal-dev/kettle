# pnpm Toolchain Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pnpm `ToolchainDriver` so that `kettle build` and `kettle attest` work on pnpm-based TypeScript projects like openclaw/openclaw.

**Architecture:** A new `src/toolchain/pnpm.rs` implements the `ToolchainDriver` trait. Detection adds `pnpm-lock.yaml` as a third probe in `ProjectToolchain::from_dir`. Artifacts are collected by tarballing `dist/` into a single `dist.tar.gz`. A new `PnpmToolchain` variant is added to the `Toolchain` enum in `provenance.rs`.

**Tech Stack:** Rust, `serde_yaml 0.9` (new dep), system `tar` binary, `cargo nextest` for tests.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `Cargo.toml` | Modify | Add `serde_yaml = "0.9"` |
| `src/provenance.rs` | Modify | Add `PnpmToolchain` variant + `Display` |
| `src/commands/build.rs` | Modify | Add `Pnpm` to `ProjectToolchain`, detection, dispatch, update error message |
| `src/toolchain.rs` | Modify | Add `pub(crate) mod pnpm` |
| `src/toolchain/pnpm.rs` | Create | Full `ToolchainDriver` implementation + lockfile parser |
| `tests/fixtures/openclaw/pnpm-lock-v9.yaml` | Create | v9 lockfile fixture for parser tests |
| `tests/fixtures/openclaw/pnpm-lock-legacy.yaml` | Create | Legacy (v5) lockfile fixture for parser tests |
| `tests/fixtures/openclaw/provenance.json` | Create | Expected provenance output (generated in Task 8) |

---

## Chunk 1: Types and Detection

### Task 1: Add serde_yaml dependency

**Files:**
- Modify: `Cargo.toml`

- [ ] **Step 1: Add the dependency**

Open `Cargo.toml` and add to `[dependencies]`:
```toml
serde_yaml = "0.9"
```

- [ ] **Step 2: Verify it compiles**

```bash
cargo build
```
Expected: compiles without errors.

- [ ] **Step 3: Commit**

```bash
git add Cargo.toml Cargo.lock
git commit -m "feat: add serde_yaml dependency for pnpm lockfile parsing"
```

---

### Task 2: Add `PnpmToolchain` to the `Toolchain` enum

**Files:**
- Modify: `src/provenance.rs` (around lines 207–232)

- [ ] **Step 1: Write failing tests**

Add to the `#[cfg(test)]` block at the bottom of `src/provenance.rs`:

```rust
#[test]
fn toolchain_display_pnpm() {
    let t = Toolchain::PnpmToolchain {
        node: ToolchainVersion {
            version: "node v22.0.0".to_string(),
            digest: Digest { sha256: String::new() },
        },
        pnpm: ToolchainVersion {
            version: "pnpm/9.0.0".to_string(),
            digest: Digest { sha256: String::new() },
        },
        kettle: ToolchainVersion {
            version: "kettle 0.1.0".to_string(),
            digest: Digest { sha256: String::new() },
        },
    };
    assert_eq!(format!("{t}"), "node v22.0.0");
}

#[test]
fn pnpm_toolchain_serde_roundtrip() {
    let t = Toolchain::PnpmToolchain {
        node: ToolchainVersion {
            version: "node v22.0.0".to_string(),
            digest: Digest { sha256: "a".repeat(64) },
        },
        pnpm: ToolchainVersion {
            version: "pnpm/9.0.0".to_string(),
            digest: Digest { sha256: "b".repeat(64) },
        },
        kettle: ToolchainVersion {
            version: "kettle 0.1.0".to_string(),
            digest: Digest { sha256: "c".repeat(64) },
        },
    };
    let json = serde_json::to_string(&t).unwrap();
    let t2: Toolchain = serde_json::from_str(&json).unwrap();
    match t2 {
        Toolchain::PnpmToolchain { node, .. } => assert_eq!(node.version, "node v22.0.0"),
        _ => panic!("expected PnpmToolchain"),
    }
}
```

- [ ] **Step 2: Run to verify failure**

```bash
cargo nextest run toolchain_display_pnpm pnpm_toolchain_serde_roundtrip
```
Expected: compile error — `PnpmToolchain` does not exist yet.

- [ ] **Step 3: Add the variant and update `Display`**

In `src/provenance.rs`, add `PnpmToolchain` to the `Toolchain` enum (after `RustToolchain`):

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Toolchain {
    NixToolchain {
        nix: ToolchainVersion,
        kettle: ToolchainVersion,
    },
    RustToolchain {
        cargo: ToolchainVersion,
        rustc: ToolchainVersion,
        kettle: ToolchainVersion,
    },
    PnpmToolchain {
        node: ToolchainVersion,
        pnpm: ToolchainVersion,
        kettle: ToolchainVersion,
    },
}
```

Update the `Display` impl to handle the new variant:

```rust
impl Display for Toolchain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Toolchain::NixToolchain { kettle: _, nix } => write!(f, "{}", nix.version),
            Toolchain::RustToolchain {
                kettle: _,
                cargo: _,
                rustc,
            } => write!(f, "{}", rustc.version),
            Toolchain::PnpmToolchain {
                kettle: _,
                pnpm: _,
                node,
            } => write!(f, "{}", node.version),
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cargo nextest run toolchain_display_pnpm pnpm_toolchain_serde_roundtrip
```
Expected: both PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
cargo nextest run
```
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/provenance.rs
git commit -m "feat: add PnpmToolchain variant to Toolchain enum"
```

---

### Task 3: Add `Pnpm` variant to toolchain detection

**Files:**
- Modify: `src/commands/build.rs`

- [ ] **Step 1: Write failing tests**

Add to the `#[cfg(test)]` block in `src/commands/build.rs`:

```rust
#[test]
fn from_dir_pnpm_lock_yaml() {
    let tmp = TempDir::new().unwrap();
    fs_err::write(tmp.path().join("pnpm-lock.yaml"), b"lockfileVersion: '9.0'").unwrap();
    match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
        ProjectToolchain::Pnpm => {}
        other => panic!("expected Pnpm, got {:?}", other),
    }
}

#[test]
fn from_dir_nix_wins_over_pnpm() {
    let tmp = TempDir::new().unwrap();
    fs_err::write(tmp.path().join("flake.nix"), b"{}").unwrap();
    fs_err::write(tmp.path().join("pnpm-lock.yaml"), b"lockfileVersion: '9.0'").unwrap();
    match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
        ProjectToolchain::Nix => {}
        other => panic!("expected Nix when both present, got {:?}", other),
    }
}

#[test]
fn from_dir_cargo_wins_over_pnpm() {
    let tmp = TempDir::new().unwrap();
    fs_err::write(tmp.path().join("Cargo.lock"), b"version = 4").unwrap();
    fs_err::write(tmp.path().join("pnpm-lock.yaml"), b"lockfileVersion: '9.0'").unwrap();
    match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
        ProjectToolchain::Cargo => {}
        other => panic!("expected Cargo when both present, got {:?}", other),
    }
}

#[test]
fn from_dir_error_mentions_pnpm() {
    let tmp = TempDir::new().unwrap();
    let err = ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap_err();
    assert!(
        err.to_string().contains("pnpm"),
        "error message should mention pnpm: {err}"
    );
}
```

- [ ] **Step 2: Run to verify failure**

```bash
cargo nextest run from_dir_pnpm_lock_yaml from_dir_nix_wins_over_pnpm from_dir_cargo_wins_over_pnpm from_dir_error_mentions_pnpm
```
Expected: compile errors — `Pnpm` variant and dispatch not yet defined.

- [ ] **Step 3: Update `ProjectToolchain` and `build()`**

Replace the contents of `src/commands/build.rs` with:

```rust
use anyhow::{Result, anyhow};
use fs_err::exists;
use std::path::PathBuf;

#[derive(Debug)]
pub(crate) enum ProjectToolchain {
    Cargo,
    Nix,
    Pnpm,
}
impl ProjectToolchain {
    fn from_dir(path: &PathBuf) -> Result<Self> {
        if exists(path.join("flake.nix"))? {
            Ok(Self::Nix)
        } else if exists(path.join("Cargo.lock"))? {
            Ok(Self::Cargo)
        } else if exists(path.join("pnpm-lock.yaml"))? {
            Ok(Self::Pnpm)
        } else {
            Err(anyhow!(
                "Could not determine toolchain. Is {:?} a rust, nix, or pnpm project?",
                path
            ))
        }
    }
}

pub fn build(path: &PathBuf) -> Result<()> {
    println!("Building project in: {:?}", path);

    let toolchain = ProjectToolchain::from_dir(path)?;
    println!("Found {:?} project", toolchain);
    match toolchain {
        ProjectToolchain::Cargo => crate::toolchain::cargo::build(path)?,
        ProjectToolchain::Nix => crate::toolchain::nix::build(path)?,
        ProjectToolchain::Pnpm => crate::toolchain::pnpm::build(path)?,
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn from_dir_flake_nix() {
        let tmp = TempDir::new().unwrap();
        fs_err::write(tmp.path().join("flake.nix"), b"{}").unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Nix => {}
            other => panic!("expected Nix, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_cargo_lock() {
        let tmp = TempDir::new().unwrap();
        fs_err::write(tmp.path().join("Cargo.lock"), b"version = 4").unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Cargo => {}
            other => panic!("expected Cargo, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_pnpm_lock_yaml() {
        let tmp = TempDir::new().unwrap();
        fs_err::write(tmp.path().join("pnpm-lock.yaml"), b"lockfileVersion: '9.0'").unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Pnpm => {}
            other => panic!("expected Pnpm, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_both_flake_wins() {
        let tmp = TempDir::new().unwrap();
        fs_err::write(tmp.path().join("flake.nix"), b"{}").unwrap();
        fs_err::write(tmp.path().join("Cargo.lock"), b"version = 4").unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Nix => {}
            other => panic!("expected Nix when both present, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_nix_wins_over_pnpm() {
        let tmp = TempDir::new().unwrap();
        fs_err::write(tmp.path().join("flake.nix"), b"{}").unwrap();
        fs_err::write(tmp.path().join("pnpm-lock.yaml"), b"lockfileVersion: '9.0'").unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Nix => {}
            other => panic!("expected Nix when both present, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_cargo_wins_over_pnpm() {
        let tmp = TempDir::new().unwrap();
        fs_err::write(tmp.path().join("Cargo.lock"), b"version = 4").unwrap();
        fs_err::write(tmp.path().join("pnpm-lock.yaml"), b"lockfileVersion: '9.0'").unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Cargo => {}
            other => panic!("expected Cargo when both present, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_neither_present() {
        let tmp = TempDir::new().unwrap();
        let result = ProjectToolchain::from_dir(&tmp.path().to_path_buf());
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("Could not determine toolchain"),
            "error: {err}"
        );
    }

    #[test]
    fn from_dir_error_mentions_pnpm() {
        let tmp = TempDir::new().unwrap();
        let err = ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap_err();
        assert!(
            err.to_string().contains("pnpm"),
            "error message should mention pnpm: {err}"
        );
    }

    #[test]
    fn from_dir_symlink_flake_nix() {
        let tmp = TempDir::new().unwrap();
        let real = tmp.path().join("real_flake.nix");
        fs_err::write(&real, b"{}").unwrap();
        std::os::unix::fs::symlink(&real, tmp.path().join("flake.nix")).unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Nix => {}
            other => panic!("expected Nix via symlink, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_symlink_cargo_lock() {
        let tmp = TempDir::new().unwrap();
        let real = tmp.path().join("real_cargo.lock");
        fs_err::write(&real, b"version = 4").unwrap();
        std::os::unix::fs::symlink(&real, tmp.path().join("Cargo.lock")).unwrap();
        match ProjectToolchain::from_dir(&tmp.path().to_path_buf()).unwrap() {
            ProjectToolchain::Cargo => {}
            other => panic!("expected Cargo via symlink, got {:?}", other),
        }
    }

    #[test]
    fn from_dir_broken_symlink() {
        let tmp = TempDir::new().unwrap();
        std::os::unix::fs::symlink("/nonexistent/flake.nix", tmp.path().join("flake.nix"))
            .unwrap();
        let result = ProjectToolchain::from_dir(&tmp.path().to_path_buf());
        assert!(
            result.is_err(),
            "broken symlink should not satisfy exists()"
        );
    }
}
```

**Important:** Complete Steps 4 and 5 before running any `cargo` commands — the `pnpm::build` dispatch in Step 3 will cause a compile error until the module and stub exist.

- [ ] **Step 4: Add the `pnpm` module declaration to `src/toolchain.rs`**

```rust
pub(crate) mod driver;
pub(crate) use driver::*;

pub(crate) mod runner;

pub(crate) mod cargo;
pub(crate) mod nix;
pub(crate) mod pnpm;
```

- [ ] **Step 5: Create a minimal stub `src/toolchain/pnpm.rs`** so it compiles:

```rust
use anyhow::Result;
use std::path::PathBuf;

pub(crate) fn build(_path: &PathBuf) -> Result<()> {
    unimplemented!("pnpm toolchain not yet implemented")
}
```

- [ ] **Step 6: Run the new tests**

```bash
cargo nextest run from_dir_pnpm_lock_yaml from_dir_nix_wins_over_pnpm from_dir_cargo_wins_over_pnpm from_dir_error_mentions_pnpm
```
Expected: all 4 PASS.

- [ ] **Step 7: Run full suite to check regressions**

```bash
cargo nextest run
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/commands/build.rs src/toolchain.rs src/toolchain/pnpm.rs
git commit -m "feat: add Pnpm variant to ProjectToolchain detection"
```

---

## Chunk 2: Lockfile Parser

### Task 4: Create fixture YAML files

**Files:**
- Create: `tests/fixtures/openclaw/pnpm-lock-v9.yaml`
- Create: `tests/fixtures/openclaw/pnpm-lock-legacy.yaml`

- [ ] **Step 1: Create the v9 fixture**

Create `tests/fixtures/openclaw/pnpm-lock-v9.yaml`:

```yaml
lockfileVersion: '9.0'

packages:

  '@types/node@20.11.5':
    resolution: {integrity: sha512-g557pLM3WHa03E5VQVMPxJlLiRvFGKNJFGSijzIZEZFAAAAGTvZe1BXKL6nS2Y2TpL3yc+gHHIBYJ0AWXB4gA==}

  semver@7.6.0:
    resolution: {integrity: sha512-FlbbvFBkXde/wSwMo2H5oBufd/kLDxiotbnWBFAY/mNpAJeH2LIt/xnANPGkdkixjTarg7zbImKDqblo9/A==}

  typescript@5.4.3:
    resolution: {integrity: sha512-goMHfm00nWPa8UvR/Z8Kp2oTMnY0ThLLPMDVKT/7/6UNJvqmOY1bvqUv93BqiL9nQ5bLwJ6GWA4Y+Ld5z+g==}

  '@types/react@18.0.0(@types/prop-types@15.7.0)':
    resolution: {integrity: sha512-abcDEFghijKLMNOPqrstUVWXYZ1234567890abcdefghijklmnopqrstuvwxyz12==}

snapshots:

  '@types/node@20.11.5': {}
  '@types/react@18.0.0(@types/prop-types@15.7.0)': {}
  semver@7.6.0: {}
  typescript@5.4.3: {}
```

- [ ] **Step 2: Create the legacy fixture**

Create `tests/fixtures/openclaw/pnpm-lock-legacy.yaml`:

```yaml
lockfileVersion: 5

packages:

  /@types/node/20.11.5:
    resolution: {integrity: sha512-g557pLM3WHa03E5VQVMPxJlLiRvFGKNJFGSijzIZEZFAAAAGTvZe1BXKL6nS2Y2TpL3yc+gHHIBYJ0AWXB4gA==}
    dev: true

  /semver/7.6.0:
    resolution: {integrity: sha512-FlbbvFBkXde/wSwMo2H5oBufd/kLDxiotbnWBFAY/mNpAJeH2LIt/xnANPGkdkixjTarg7zbImKDqblo9/A==}
    dev: false

  /typescript/5.4.3_@types+node@20.11.5:
    resolution: {integrity: sha512-goMHfm00nWPa8UvR/Z8Kp2oTMnY0ThLLPMDVKT/7/6UNJvqmOY1bvqUv93BqiL9nQ5bLwJ6GWA4Y+Ld5z+g==}
    dev: true
```

Note: the `typescript` entry in the legacy fixture deliberately includes a peer-dep suffix (`_@types+node@20.11.5`) to exercise that stripping path.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/openclaw/
git commit -m "test: add pnpm lockfile fixtures"
```

---

### Task 5: Implement key-parsing helpers and lockfile parser

**Files:**
- Modify: `src/toolchain/pnpm.rs`

- [ ] **Step 1: Write failing tests**

Replace `src/toolchain/pnpm.rs` with the full file below. The tests will compile but the functions they call don't exist yet.

```rust
use anyhow::{Context, Result, anyhow};
use sha2::{Digest as _, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;
use tracing::debug;

use crate::{
    provenance::{Digest, InternalParameters, ResolvedDependency, Toolchain, ToolchainVersion},
    toolchain::{
        Artifact, BuildOutput, GitContext, ProvenanceFields, ToolBinaryInfo, ToolchainDriver,
    },
};

pub(crate) fn build(path: &PathBuf) -> Result<()> {
    crate::toolchain::runner::run::<PnpmInputs>(path)
}

#[derive(Debug)]
struct PnpmInputs {
    kettle_version: String,
    kettle_hash: String,
    node_version: String,
    node_hash: String,
    pnpm_version: String,
    pnpm_hash: String,
    lockfile_hash: String,
    resolved_deps: Vec<ResolvedDependency>,
}

impl ToolchainDriver for PnpmInputs {
    fn lockfile_filename() -> &'static str {
        "pnpm-lock.yaml"
    }

    fn build_command_display() -> &'static str {
        "pnpm install --frozen-lockfile && pnpm build"
    }

    fn collect_inputs(
        _path: &Path,
        _git: &GitContext,
        lockfile_hash: &str,
        lockfile_bytes: &[u8],
    ) -> Result<Self> {
        let node = ToolBinaryInfo::via_which("node")?;
        debug!("node info {:?}", node);
        let pnpm = ToolBinaryInfo::via_which("pnpm")?;
        debug!("pnpm info {:?}", pnpm);
        let kettle = ToolBinaryInfo::kettle_info()?;
        debug!("kettle info {:?}", kettle);
        let resolved_deps = parse_pnpm_lock(lockfile_bytes)?;
        debug!("found {} pnpm deps", resolved_deps.len());
        Ok(Self {
            kettle_version: kettle.version,
            kettle_hash: kettle.sha256,
            node_version: node.version,
            node_hash: node.sha256,
            pnpm_version: pnpm.version,
            pnpm_hash: pnpm.sha256,
            lockfile_hash: lockfile_hash.to_string(),
            resolved_deps,
        })
    }

    fn merkle_entries(&self, git: &GitContext, lockfile_hash: &str) -> Vec<String> {
        // Ordering is a frozen contract — do not change without bumping the build_type URI.
        let mut entries = vec![
            git.commit.clone(),
            git.tree.clone(),
            self.node_hash.clone(),
            self.node_version.clone(),
            self.pnpm_hash.clone(),
            self.pnpm_version.clone(),
            lockfile_hash.to_string(),
        ];
        entries.extend(self.resolved_deps.iter().map(|d| d.uri.clone()));
        entries
    }

    fn run_build(path: &Path) -> Result<BuildOutput> {
        let install = Command::new("pnpm")
            .args(["install", "--frozen-lockfile"])
            .current_dir(path)
            .output()
            .context("failed to spawn pnpm")?;
        if !install.status.success() {
            return Err(anyhow!(
                "pnpm install failed (exit {:?})",
                install.status.code()
            ));
        }
        let build = Command::new("pnpm")
            .args(["build"])
            .current_dir(path)
            .output()
            .context("failed to spawn pnpm build")?;
        if !build.status.success() {
            return Err(anyhow!(
                "pnpm build failed (exit {:?})",
                build.status.code()
            ));
        }
        Ok(BuildOutput { stdout: build.stdout })
    }

    fn collect_artifacts(
        _output: &BuildOutput,
        path: &Path,
        artifacts_dir: &Path,
    ) -> Result<Vec<Artifact>> {
        let dist_dir = path.join("dist");
        if !dist_dir.is_dir() {
            return Err(anyhow!(
                "dist/ directory not found after build in {:?}",
                path
            ));
        }
        let tarball_path = artifacts_dir.join("dist.tar.gz");
        let output = Command::new("tar")
            .args([
                "-czf",
                tarball_path.to_str().unwrap(),
                "-C",
                path.to_str().unwrap(),
                "dist",
            ])
            .output()
            .context("failed to spawn tar")?;
        if !output.status.success() {
            return Err(anyhow!(
                "tar failed (exit {:?}): {}",
                output.status.code(),
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        let checksum = hex::encode(Sha256::digest(fs_err::read(&tarball_path)?));
        Ok(vec![Artifact {
            name: "dist.tar.gz".to_string(),
            path: tarball_path,
            checksum,
        }])
    }

    fn provenance_fields(self, _git: &GitContext, _merkle_root: &str) -> ProvenanceFields {
        ProvenanceFields {
            build_type: "https://lunal.dev/kettle/pnpm@v1".to_string(),
            external_build_command: "pnpm build".to_string(),
            internal_parameters: InternalParameters {
                evaluation: None,
                flake_inputs: None,
                lockfile_hash: Digest {
                    sha256: self.lockfile_hash,
                },
                toolchain: Toolchain::PnpmToolchain {
                    node: ToolchainVersion {
                        version: self.node_version,
                        digest: Digest { sha256: self.node_hash },
                    },
                    pnpm: ToolchainVersion {
                        version: self.pnpm_version,
                        digest: Digest { sha256: self.pnpm_hash },
                    },
                    kettle: ToolchainVersion {
                        version: self.kettle_version,
                        digest: Digest { sha256: self.kettle_hash },
                    },
                },
            },
            resolved_dependencies: self.resolved_deps,
        }
    }
}

fn parse_pnpm_lock(bytes: &[u8]) -> Result<Vec<ResolvedDependency>> {
    todo!()
}

fn parse_v9_key(key: &str) -> Result<(String, String)> {
    todo!()
}

fn parse_legacy_key(key: &str) -> Result<(String, String)> {
    todo!()
}

#[cfg(test)]
mod tests {
    use super::*;

    const PNPM_LOCK_V9: &[u8] =
        include_bytes!("../../tests/fixtures/openclaw/pnpm-lock-v9.yaml");
    const PNPM_LOCK_LEGACY: &[u8] =
        include_bytes!("../../tests/fixtures/openclaw/pnpm-lock-legacy.yaml");

    // --- parse_v9_key ---

    #[test]
    fn v9_key_unscoped() {
        let (name, version) = parse_v9_key("semver@7.6.0").unwrap();
        assert_eq!(name, "semver");
        assert_eq!(version, "7.6.0");
    }

    #[test]
    fn v9_key_scoped() {
        let (name, version) = parse_v9_key("@babel/core@7.24.0").unwrap();
        assert_eq!(name, "@babel/core");
        assert_eq!(version, "7.24.0");
    }

    #[test]
    fn v9_key_peer_dep_suffix_stripped() {
        let (name, version) = parse_v9_key("@babel/core@7.24.0(@types/node@20.0.0)").unwrap();
        assert_eq!(name, "@babel/core");
        assert_eq!(version, "7.24.0");
    }

    #[test]
    fn v9_key_unscoped_no_at_error() {
        assert!(parse_v9_key("semver-no-at").is_err());
    }

    #[test]
    fn v9_key_scoped_no_version_error() {
        assert!(parse_v9_key("@babel/core").is_err());
    }

    // --- parse_legacy_key ---

    #[test]
    fn legacy_key_unscoped() {
        let (name, version) = parse_legacy_key("/semver/7.6.0").unwrap();
        assert_eq!(name, "semver");
        assert_eq!(version, "7.6.0");
    }

    #[test]
    fn legacy_key_scoped() {
        let (name, version) = parse_legacy_key("/@babel/core/7.24.0").unwrap();
        assert_eq!(name, "@babel/core");
        assert_eq!(version, "7.24.0");
    }

    #[test]
    fn legacy_key_peer_dep_suffix_stripped() {
        let (name, version) =
            parse_legacy_key("/@babel/core/7.24.0_@types+node@20.0.0").unwrap();
        assert_eq!(name, "@babel/core");
        assert_eq!(version, "7.24.0");
    }

    #[test]
    fn legacy_key_no_leading_slash_error() {
        assert!(parse_legacy_key("semver/7.6.0").is_err());
    }

    #[test]
    fn legacy_key_no_version_separator_error() {
        assert!(parse_legacy_key("/semver").is_err());
    }

    // --- parse_pnpm_lock ---

    #[test]
    fn parse_v9_happy_path() {
        let deps = parse_pnpm_lock(PNPM_LOCK_V9).unwrap();
        assert_eq!(deps.len(), 4);
        for dep in &deps {
            assert!(
                dep.uri.starts_with("pkg:npm/"),
                "URI should start with pkg:npm/: {}",
                dep.uri
            );
            assert!(
                dep.uri.contains("?checksum="),
                "URI should contain checksum: {}",
                dep.uri
            );
        }
        // Sorted by URI
        let uris: Vec<&str> = deps.iter().map(|d| d.uri.as_str()).collect();
        let mut sorted = uris.clone();
        sorted.sort();
        assert_eq!(uris, sorted, "deps should be sorted by URI");
        // Peer-dep suffix stripped: @types/react entry should have clean version
        let react = deps.iter().find(|d| d.name == "@types/react").unwrap();
        assert!(
            react.uri.contains("@types/react@18.0.0?checksum="),
            "peer-dep suffix should be stripped: {}",
            react.uri
        );
    }

    #[test]
    fn parse_v9_scoped_package() {
        let deps = parse_pnpm_lock(PNPM_LOCK_V9).unwrap();
        let scoped = deps.iter().find(|d| d.name.starts_with('@')).unwrap();
        assert_eq!(scoped.name, "@types/node");
        assert!(scoped.uri.starts_with("pkg:npm/@types/node@"));
    }

    #[test]
    fn parse_legacy_happy_path() {
        let deps = parse_pnpm_lock(PNPM_LOCK_LEGACY).unwrap();
        assert_eq!(deps.len(), 3);
        for dep in &deps {
            assert!(dep.uri.starts_with("pkg:npm/"), "URI: {}", dep.uri);
        }
    }

    #[test]
    fn parse_legacy_scoped_package() {
        let deps = parse_pnpm_lock(PNPM_LOCK_LEGACY).unwrap();
        let scoped = deps.iter().find(|d| d.name.starts_with('@')).unwrap();
        assert_eq!(scoped.name, "@types/node");
    }

    #[test]
    fn parse_legacy_peer_dep_suffix_stripped() {
        let deps = parse_pnpm_lock(PNPM_LOCK_LEGACY).unwrap();
        // typescript entry has peer dep suffix in key — version should be clean
        let ts = deps.iter().find(|d| d.name == "typescript").unwrap();
        assert!(
            ts.uri.contains("typescript@5.4.3"),
            "version should be clean: {}",
            ts.uri
        );
    }

    #[test]
    fn parse_no_packages_section() {
        let yaml = b"lockfileVersion: '9.0'\n";
        let deps = parse_pnpm_lock(yaml).unwrap();
        assert!(deps.is_empty());
    }

    #[test]
    fn parse_missing_integrity_error() {
        let yaml =
            b"lockfileVersion: '9.0'\npackages:\n  semver@7.6.0:\n    resolution: {}\n";
        let result = parse_pnpm_lock(yaml);
        assert!(result.is_err());
        assert!(
            result.unwrap_err().to_string().contains("resolution.integrity"),
            "error should mention resolution.integrity"
        );
    }

    #[test]
    fn parse_empty_integrity_error() {
        let yaml =
            b"lockfileVersion: '9.0'\npackages:\n  semver@7.6.0:\n    resolution: {integrity: \"\"}\n";
        let result = parse_pnpm_lock(yaml);
        assert!(result.is_err(), "empty integrity string should be an error");
    }

    #[test]
    fn parse_invalid_yaml_error() {
        let result = parse_pnpm_lock(b"{{{{not valid yaml}}}}");
        assert!(result.is_err());
    }

    #[test]
    fn parse_missing_lockfile_version_error() {
        let yaml =
            b"packages:\n  semver@7.6.0:\n    resolution: {integrity: sha512-abc}\n";
        let result = parse_pnpm_lock(yaml);
        assert!(result.is_err());
        assert!(
            result.unwrap_err().to_string().contains("lockfileVersion"),
            "error should mention lockfileVersion"
        );
    }

    #[test]
    fn parse_deterministic() {
        let r1 = parse_pnpm_lock(PNPM_LOCK_V9).unwrap();
        let r2 = parse_pnpm_lock(PNPM_LOCK_V9).unwrap();
        assert_eq!(r1.len(), r2.len());
        for (a, b) in r1.iter().zip(r2.iter()) {
            assert_eq!(a.uri, b.uri);
        }
    }
}
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cargo nextest run -p kettle v9_key_unscoped legacy_key_unscoped parse_v9_happy_path
```
Expected: compile succeeds — the `todo!()` stubs are valid Rust. Tests panic at runtime with "not yet implemented".

- [ ] **Step 3: Implement the three parser functions**

Replace the `todo!()` stubs with real implementations:

```rust
fn parse_pnpm_lock(bytes: &[u8]) -> Result<Vec<ResolvedDependency>> {
    let data: serde_yaml::Value =
        serde_yaml::from_slice(bytes).context("malformed pnpm-lock.yaml")?;

    let lockfile_version = data
        .get("lockfileVersion")
        .ok_or_else(|| anyhow!("pnpm-lock.yaml: missing lockfileVersion"))?;
    let version_str = match lockfile_version {
        serde_yaml::Value::String(s) => s.clone(),
        serde_yaml::Value::Number(n) => n.to_string(),
        _ => return Err(anyhow!("pnpm-lock.yaml: lockfileVersion has unexpected type")),
    };
    let is_v9 = version_str.starts_with('9');

    let Some(packages) = data.get("packages").and_then(|p| p.as_mapping()) else {
        return Ok(vec![]);
    };

    let mut deps = Vec::new();
    for (key, value) in packages {
        let key_str = key
            .as_str()
            .ok_or_else(|| anyhow!("pnpm-lock.yaml: package key is not a string"))?;

        let integrity = value
            .get("resolution")
            .and_then(|r| r.get("integrity"))
            .and_then(|i| i.as_str())
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                anyhow!(
                    "pnpm-lock.yaml: package {:?} missing resolution.integrity",
                    key_str
                )
            })?;

        let (name, version) = if is_v9 {
            parse_v9_key(key_str)?
        } else {
            parse_legacy_key(key_str)?
        };

        let uri = format!("pkg:npm/{}@{}?checksum={}", name, version, integrity);
        deps.push(ResolvedDependency {
            annotations: None,
            digest: Digest {
                sha256: integrity.to_string(),
            },
            name,
            uri,
        });
    }

    deps.sort_by_cached_key(|d| d.uri.clone());
    Ok(deps)
}

fn parse_v9_key(key: &str) -> Result<(String, String)> {
    // Strip trailing (...) peer dep suffix
    let key = if let Some(paren) = key.find('(') {
        &key[..paren]
    } else {
        key
    };

    if key.starts_with('@') {
        // Scoped package: @scope/name@version — split at the second '@'
        let second_at = key[1..]
            .find('@')
            .ok_or_else(|| anyhow!("pnpm-lock.yaml: cannot parse scoped v9 key {:?}", key))?;
        let split_pos = 1 + second_at;
        Ok((key[..split_pos].to_string(), key[split_pos + 1..].to_string()))
    } else {
        // Unscoped: name@version
        let at = key
            .find('@')
            .ok_or_else(|| anyhow!("pnpm-lock.yaml: cannot parse unscoped v9 key {:?}", key))?;
        Ok((key[..at].to_string(), key[at + 1..].to_string()))
    }
}

fn parse_legacy_key(key: &str) -> Result<(String, String)> {
    // Strip leading '/'
    let key = key.strip_prefix('/').ok_or_else(|| {
        anyhow!(
            "pnpm-lock.yaml: legacy key does not start with '/': {:?}",
            key
        )
    })?;
    // Split on last '/' — version is the final segment, name is everything before
    let last_slash = key.rfind('/').ok_or_else(|| {
        anyhow!(
            "pnpm-lock.yaml: legacy key has no version separator: {:?}",
            key
        )
    })?;
    let name = &key[..last_slash];
    let raw_version = &key[last_slash + 1..];
    // Strip peer dep suffix (underscore form: '1.2.3_@types+node@20.0.0')
    let version = if let Some(underscore) = raw_version.find('_') {
        &raw_version[..underscore]
    } else {
        raw_version
    };
    Ok((name.to_string(), version.to_string()))
}
```

- [ ] **Step 4: Run all parser tests**

```bash
cargo nextest run -p kettle v9_key legacy_key parse_
```
Expected: all parser tests PASS.

- [ ] **Step 5: Run full suite**

```bash
cargo nextest run
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/toolchain/pnpm.rs
git commit -m "feat: implement pnpm lockfile parser"
```

---

## Chunk 3: Wiring and Fixture Tests

### Task 6: Generate provenance fixture and add key-ordering test

The `ToolchainDriver` implementation is already in place from the file written in Task 5. This task generates the `provenance.json` fixture and adds the round-trip test.

**Files:**
- Create: `tests/fixtures/openclaw/provenance.json`
- Modify: `src/provenance.rs`

- [ ] **Step 1: Add a fixture-generation helper test to `src/provenance.rs`**

Add this test temporarily to the `#[cfg(test)]` block in `src/provenance.rs`. It will be removed after generating the fixture:

```rust
#[test]
fn dump_pnpm_provenance_fixture() {
    use std::path::PathBuf;
    let p = Provenance {
        _type: "https://in-toto.io/Statement/v1".to_string(),
        predicate_type: "https://slsa.dev/provenance/v1".to_string(),
        subject: vec![Subject {
            name: "dist.tar.gz".to_string(),
            digest: Digest { sha256: "1".repeat(64) },
        }],
        predicate: Predicate {
            build_definition: BuildDefiniton {
                build_type: "https://lunal.dev/kettle/pnpm@v1".to_string(),
                external_parameters: ExternalParameters {
                    build_command: "pnpm build".to_string(),
                    source: Source {
                        digest: SourceDigest {
                            git_commit: "a".repeat(40),
                            git_tree: "b".repeat(40),
                        },
                        uri: "https://github.com/openclaw/openclaw".to_string(),
                    },
                },
                internal_parameters: InternalParameters {
                    evaluation: None,
                    flake_inputs: None,
                    lockfile_hash: Digest { sha256: "c".repeat(64) },
                    toolchain: Toolchain::PnpmToolchain {
                        node: ToolchainVersion {
                            version: "node v22.0.0".to_string(),
                            digest: Digest { sha256: "d".repeat(64) },
                        },
                        pnpm: ToolchainVersion {
                            version: "pnpm/9.0.0".to_string(),
                            digest: Digest { sha256: "e".repeat(64) },
                        },
                        kettle: ToolchainVersion {
                            version: "kettle 0.1.0".to_string(),
                            digest: Digest { sha256: "f".repeat(64) },
                        },
                    },
                },
                resolved_dependencies: vec![],
            },
            run_details: RunDetails {
                builder: Builder {
                    id: "https://lunal.dev/kettle-tee/v1".to_string(),
                },
                byproducts: vec![Byproduct {
                    name: "input_merkle_root".to_string(),
                    digest: Digest { sha256: "0".repeat(64) },
                }],
                metadata: Metadata {
                    invocation_id: "build-20260316-000000-00000000".to_string(),
                    started_on: "2026-03-16T00:00:00.000000+00:00".to_string(),
                    finished_on: Some("2026-03-16T00:00:01.000000+00:00".to_string()),
                },
            },
        },
    };
    let json = serde_json::to_string_pretty(&p).unwrap();
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/openclaw/provenance.json");
    std::fs::write(&path, &json).unwrap();
    println!("Wrote fixture to {}", path.display());
}
```

- [ ] **Step 2: Run the generation test**

```bash
cargo nextest run dump_pnpm_provenance_fixture --no-capture
```
Expected: PASS; `tests/fixtures/openclaw/provenance.json` is created.

**Important:** `serde_json::to_string_pretty` does not emit a trailing newline. Do not let an editor or git hook add one — the `key_ordering_matches_when_regenerated_pnpm` test will fail if the file has a trailing newline that the serializer did not produce.

- [ ] **Step 3: Remove the generation test**

Delete the `dump_pnpm_provenance_fixture` function from `src/provenance.rs`.

- [ ] **Step 4: Add the key-ordering test and `from_json` test to `src/provenance.rs`**

Add to the `#[cfg(test)]` block, alongside the other fixture-based tests at the top of the block:

```rust
const PNPM_FIXTURE: &[u8] = include_bytes!("../tests/fixtures/openclaw/provenance.json");
```

Then add the tests:

```rust
#[test]
fn from_json_happy_path_pnpm() {
    let p = Provenance::from_json(PNPM_FIXTURE).unwrap();
    assert_eq!(p._type, "https://in-toto.io/Statement/v1");
    assert_eq!(p.predicate_type, "https://slsa.dev/provenance/v1");
    assert_eq!(
        p.predicate.build_definition.build_type,
        "https://lunal.dev/kettle/pnpm@v1"
    );
    assert_eq!(
        p.predicate
            .build_definition
            .external_parameters
            .build_command,
        "pnpm build"
    );
    assert_eq!(p.subject.len(), 1);
    assert_eq!(p.subject[0].name, "dist.tar.gz");
    match &p.predicate.build_definition.internal_parameters.toolchain {
        Toolchain::PnpmToolchain { node, pnpm, kettle: _ } => {
            assert_eq!(node.version, "node v22.0.0");
            assert_eq!(pnpm.version, "pnpm/9.0.0");
        }
        _ => panic!("expected PnpmToolchain"),
    }
    assert!(
        p.predicate
            .build_definition
            .internal_parameters
            .evaluation
            .is_none()
    );
    assert!(
        p.predicate
            .build_definition
            .internal_parameters
            .flake_inputs
            .is_none()
    );
}

#[test]
fn key_ordering_matches_when_regenerated_pnpm() {
    let p = Provenance::from_json(PNPM_FIXTURE).unwrap();
    let regenerated = serde_json::to_string_pretty(&p).unwrap();
    assert_eq!(
        PNPM_FIXTURE,
        regenerated.as_bytes(),
        "regenerated provenance changed!"
    );
}
```

- [ ] **Step 5: Run the new tests**

```bash
cargo nextest run from_json_happy_path_pnpm key_ordering_matches_when_regenerated_pnpm toolchain_display_pnpm pnpm_toolchain_serde_roundtrip
```
Expected: all PASS.

- [ ] **Step 6: Run full suite**

```bash
cargo nextest run
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/provenance.rs tests/fixtures/openclaw/provenance.json
git commit -m "test: add pnpm provenance fixture and key-ordering test"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run linters**

```bash
./bin/lint
```
Expected: no warnings or errors.

- [ ] **Step 2: Run full test suite one final time**

```bash
cargo nextest run
```
Expected: all pass.

- [ ] **Step 3: Verify `kettle build` help still works**

```bash
cargo run -- --help
```
Expected: help text printed without errors.
