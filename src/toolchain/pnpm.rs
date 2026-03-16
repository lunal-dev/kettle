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
    node_version: String,
    node_hash: String,
    pnpm_version: String,
    pnpm_hash: String,
    kettle_version: String,
    kettle_hash: String,
    lockfile_hash: String,
    deps: Vec<ResolvedDependency>,
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
        let deps = parse_pnpm_lock(lockfile_bytes)?;
        debug!("found deps {:?}", deps);
        Ok(Self {
            node_version: node.version,
            node_hash: node.sha256,
            pnpm_version: pnpm.version,
            pnpm_hash: pnpm.sha256,
            kettle_version: kettle.version,
            kettle_hash: kettle.sha256,
            lockfile_hash: lockfile_hash.to_string(),
            deps,
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
        entries.extend(self.deps.iter().map(|d| d.uri.clone()));
        entries
    }

    fn run_build(path: &Path) -> Result<BuildOutput> {
        let install = Command::new("pnpm")
            .args(["install", "--frozen-lockfile"])
            .current_dir(path)
            .output()
            .context("failed to spawn pnpm install")?;
        if !install.status.success() {
            return Err(anyhow!(
                "pnpm install --frozen-lockfile failed (exit {:?})",
                install.status.code()
            ));
        }

        let build = Command::new("pnpm")
            .arg("build")
            .current_dir(path)
            .output()
            .context("failed to spawn pnpm build")?;
        if !build.status.success() {
            return Err(anyhow!(
                "pnpm build failed (exit {:?})",
                build.status.code()
            ));
        }

        Ok(BuildOutput {
            stdout: build.stdout,
        })
    }

    fn collect_artifacts(
        _output: &BuildOutput,
        path: &Path,
        artifacts_dir: &Path,
    ) -> Result<Vec<Artifact>> {
        let dist_dir = path.join("dist");
        if !dist_dir.exists() {
            return Err(anyhow!(
                "dist/ directory not found in {:?}",
                path
            ));
        }

        let tarball_path = artifacts_dir.join("dist.tar.gz");
        let tar = Command::new("tar")
            .args([
                "-czf",
                tarball_path.to_str().unwrap(),
                "-C",
                path.to_str().unwrap(),
                "dist",
            ])
            .output()
            .context("failed to spawn tar")?;
        if !tar.status.success() {
            return Err(anyhow!(
                "tar failed (exit {:?}): {}",
                tar.status.code(),
                String::from_utf8_lossy(&tar.stderr)
            ));
        }

        let checksum = hex::encode(Sha256::digest(fs_err::read(&tarball_path)?));
        Ok(vec![Artifact {
            name: "dist.tar.gz".into(),
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
                        digest: Digest {
                            sha256: self.node_hash,
                        },
                    },
                    pnpm: ToolchainVersion {
                        version: self.pnpm_version,
                        digest: Digest {
                            sha256: self.pnpm_hash,
                        },
                    },
                    kettle: ToolchainVersion {
                        version: self.kettle_version,
                        digest: Digest {
                            sha256: self.kettle_hash,
                        },
                    },
                },
            },
            resolved_dependencies: self.deps,
        }
    }
}

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
    // Strip peer-dep suffix: trailing (...)
    let key = if let Some(pos) = key.find('(') {
        &key[..pos]
    } else {
        key
    };
    if key.starts_with('@') {
        // scoped: @scope/name@version — find second '@'
        let second_at = key[1..]
            .find('@')
            .ok_or_else(|| anyhow!("pnpm-lock.yaml: cannot parse v9 scoped key {:?}", key))?;
        let split = 1 + second_at; // position of second '@' in original key
        Ok((key[..split].to_string(), key[split + 1..].to_string()))
    } else {
        let at = key
            .find('@')
            .ok_or_else(|| anyhow!("pnpm-lock.yaml: cannot parse v9 key {:?}", key))?;
        Ok((key[..at].to_string(), key[at + 1..].to_string()))
    }
}

fn parse_legacy_key(key: &str) -> Result<(String, String)> {
    let key = key.strip_prefix('/').unwrap_or(key);
    let last_slash = key
        .rfind('/')
        .ok_or_else(|| anyhow!("pnpm-lock.yaml: cannot parse legacy key {:?}", key))?;
    let name = key[..last_slash].to_string();
    let raw_version = &key[last_slash + 1..];
    let version = raw_version.split('_').next().unwrap_or(raw_version).to_string();
    Ok((name, version))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_v9_unscoped() {
        let (name, version) = parse_v9_key("semver@7.6.0").unwrap();
        assert_eq!(name, "semver");
        assert_eq!(version, "7.6.0");
    }

    #[test]
    fn parse_v9_scoped() {
        let (name, version) = parse_v9_key("@types/node@20.11.5").unwrap();
        assert_eq!(name, "@types/node");
        assert_eq!(version, "20.11.5");
    }

    #[test]
    fn parse_v9_peer_dep_stripped() {
        let (name, version) =
            parse_v9_key("@types/react@18.0.0(@types/prop-types@15.7.0)").unwrap();
        assert_eq!(name, "@types/react");
        assert_eq!(version, "18.0.0");
    }

    #[test]
    fn parse_legacy_unscoped() {
        let (name, version) = parse_legacy_key("/semver/7.6.0").unwrap();
        assert_eq!(name, "semver");
        assert_eq!(version, "7.6.0");
    }

    #[test]
    fn parse_legacy_scoped() {
        let (name, version) = parse_legacy_key("/@babel/core/7.24.0").unwrap();
        assert_eq!(name, "@babel/core");
        assert_eq!(version, "7.24.0");
    }

    #[test]
    fn parse_legacy_peer_dep_stripped() {
        let (name, version) =
            parse_legacy_key("/@babel/parser/7.24.0_@types+node@20.11.5").unwrap();
        assert_eq!(name, "@babel/parser");
        assert_eq!(version, "7.24.0");
    }

    #[test]
    fn parse_pnpm_lock_v9_happy_path() {
        let bytes = include_bytes!("../../tests/fixtures/openclaw/pnpm-lock-v9.yaml");
        let deps = parse_pnpm_lock(bytes).unwrap();
        assert_eq!(deps.len(), 4);
        for dep in &deps {
            assert!(
                dep.uri.starts_with("pkg:npm/"),
                "URI should start with pkg:npm/: {}",
                dep.uri
            );
            assert!(
                dep.uri.contains("?checksum="),
                "URI should contain ?checksum=: {}",
                dep.uri
            );
        }
        // Should be sorted by uri
        let uris: Vec<&str> = deps.iter().map(|d| d.uri.as_str()).collect();
        let mut sorted = uris.clone();
        sorted.sort();
        assert_eq!(uris, sorted, "dependencies should be sorted by URI");
    }

    #[test]
    fn parse_pnpm_lock_legacy_happy_path() {
        let bytes = include_bytes!("../../tests/fixtures/openclaw/pnpm-lock-legacy.yaml");
        let deps = parse_pnpm_lock(bytes).unwrap();
        assert_eq!(deps.len(), 3);
        for dep in &deps {
            assert!(
                dep.uri.starts_with("pkg:npm/"),
                "URI should start with pkg:npm/: {}",
                dep.uri
            );
            assert!(
                dep.uri.contains("?checksum="),
                "URI should contain ?checksum=: {}",
                dep.uri
            );
            assert!(!dep.name.is_empty(), "name should not be empty");
        }
    }

    #[test]
    fn parse_pnpm_lock_empty_packages() {
        let yaml = b"lockfileVersion: '9.0'\n\npackages: {}\n";
        let deps = parse_pnpm_lock(yaml).unwrap();
        assert!(deps.is_empty());
    }

    #[test]
    fn parse_pnpm_lock_missing_integrity() {
        let yaml =
            b"lockfileVersion: '9.0'\n\npackages:\n  semver@7.6.0:\n    resolution: {}\n";
        let result = parse_pnpm_lock(yaml);
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("missing resolution.integrity"),
            "error: {err}"
        );
    }

    #[test]
    fn parse_pnpm_lock_malformed_yaml() {
        let result = parse_pnpm_lock(b"not: yaml: content: [unclosed");
        assert!(result.is_err());
    }

    #[test]
    fn parse_pnpm_lock_missing_version() {
        let yaml = b"packages: {}\n";
        let result = parse_pnpm_lock(yaml);
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("missing lockfileVersion"), "error: {err}");
    }

    #[test]
    fn deterministic_ordering() {
        let bytes = include_bytes!("../../tests/fixtures/openclaw/pnpm-lock-v9.yaml");
        let r1 = parse_pnpm_lock(bytes).unwrap();
        let r2 = parse_pnpm_lock(bytes).unwrap();
        assert_eq!(r1.len(), r2.len());
        for (a, b) in r1.iter().zip(r2.iter()) {
            assert_eq!(a.uri, b.uri);
            assert_eq!(a.name, b.name);
            assert_eq!(a.digest.sha256, b.digest.sha256);
        }
    }

    #[test]
    fn uri_format_validation() {
        let bytes = include_bytes!("../../tests/fixtures/openclaw/pnpm-lock-v9.yaml");
        let deps = parse_pnpm_lock(bytes).unwrap();
        for dep in &deps {
            assert!(
                dep.uri.starts_with("pkg:npm/"),
                "URI should start with pkg:npm/: {}",
                dep.uri
            );
            assert!(
                dep.uri.contains("?checksum="),
                "URI should contain ?checksum=: {}",
                dep.uri
            );
        }
    }
}
