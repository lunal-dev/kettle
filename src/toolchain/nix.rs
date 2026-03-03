use anyhow::{Context as _, Result, anyhow};
use serde_json::Value;
use sha2::{Digest as _, Sha256};
use std::os::unix::fs::PermissionsExt as _;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::{
    provenance::{
        Annotation, Digest, Evaluation, FlakeInput, InternalParameters, ResolvedDependency,
        Toolchain, ToolchainVersion,
    },
    toolchain::{
        Artifact, BuildOutput, GitContext, ProvenanceFields, ToolBinaryInfo, ToolchainDriver,
    },
};

struct FlakeDep {
    name: String,
    nar_hash: Option<String>,
}

struct FetchEntry {
    name: String,
    drv_path: String,
    output_hash: String,
    output_hash_algo: String,
    output_hash_mode: Option<String>,
    url: Option<String>,
    urls: Option<String>,
}

pub(crate) fn build(path: &PathBuf) -> Result<()> {
    crate::toolchain::runner::run::<NixInputs>(path)
}

struct NixInputs {
    nix_version: String,
    nix_hash: String,
    lockfile_hash: String,
    flake_deps: Vec<FlakeDep>,
    fetches: Vec<FetchEntry>,
    derivation_count: usize,
}

impl ToolchainDriver for NixInputs {
    fn lockfile_filename() -> &'static str {
        "flake.lock"
    }

    fn build_command_display() -> &'static str {
        "nix build --no-link --print-out-paths"
    }

    fn collect_inputs(
        path: &Path,
        _git: &GitContext,
        lockfile_hash: &str,
        lockfile_bytes: &[u8],
    ) -> Result<Self> {
        let nix = ToolBinaryInfo::via_which("nix")?;
        let flake_deps = parse_flake_lock(lockfile_bytes)?;
        let graph = evaluate_derivation_graph(path)?;
        let derivation_count = graph.as_object().map(|o| o.len()).unwrap_or(0);
        let fetches = extract_fixed_output_hashes(&graph);
        Ok(Self {
            nix_version: nix.version,
            nix_hash: nix.sha256,
            lockfile_hash: lockfile_hash.to_string(),
            flake_deps,
            fetches,
            derivation_count,
        })
    }

    fn merkle_entries(&self, git: &GitContext, lockfile_hash: &str) -> Vec<String> {
        // Ordering is a frozen contract — do not change without bumping the build_type URI.
        let mut entries = vec![
            git.commit.clone(),
            git.tree.clone(),
            lockfile_hash.to_string(),
        ];
        for fod in &self.fetches {
            entries.push(format!(
                "fetch:{}:{}:{}",
                fod.name, fod.output_hash_algo, fod.output_hash
            ));
        }
        entries.push(self.nix_hash.clone());
        entries.push(self.nix_version.clone());
        entries
    }

    fn run_build(path: &Path) -> Result<BuildOutput> {
        let output = Command::new("nix")
            .args([
                "build",
                "--no-link",
                "--print-out-paths",
                "--extra-experimental-features",
                "nix-command",
                "--extra-experimental-features",
                "flakes",
            ])
            .current_dir(path)
            .output()
            .context("failed to spawn nix")?;
        if !output.status.success() {
            return Err(anyhow!(
                "nix build failed (exit {:?})",
                output.status.code()
            ));
        }
        Ok(BuildOutput {
            stdout: output.stdout,
        })
    }

    fn collect_artifacts(
        output: &BuildOutput,
        _path: &Path,
        artifacts_dir: &Path,
    ) -> Result<Vec<Artifact>> {
        let store_paths_str = std::str::from_utf8(&output.stdout)?;
        nix_artifacts_from_store_paths(store_paths_str, artifacts_dir)
    }

    fn provenance_fields(self, _git: &GitContext, _merkle_root: &str) -> ProvenanceFields {
        let fetch_count = self.fetches.len();

        let resolved_dependencies: Vec<ResolvedDependency> = self
            .fetches
            .into_iter()
            .map(|fetch| {
                let uri = format!(
                    "pkg:nix-fetch/{}?hash={}:{}",
                    fetch.name, fetch.output_hash_algo, fetch.output_hash
                );
                ResolvedDependency {
                    annotations: Some(Annotation {
                        drv_path: fetch.drv_path,
                        output_hash_mode: fetch.output_hash_mode,
                        url: fetch.url,
                        urls: fetch.urls,
                    }),
                    digest: Digest {
                        sha256: fetch.output_hash,
                    },
                    name: fetch.name,
                    uri,
                }
            })
            .collect();

        // Flake inputs are always included in internalParameters for human context
        let flake_inputs: Vec<FlakeInput> = self
            .flake_deps
            .into_iter()
            .filter_map(|dep| {
                dep.nar_hash.map(|nar_hash| FlakeInput {
                    name: dep.name,
                    nar_hash,
                })
            })
            .collect();

        // Evaluation metadata only present in deep mode
        let evaluation = Evaluation {
            derivation_count: serde_json::Number::from(self.derivation_count),
            fetch_count: serde_json::Number::from(fetch_count),
        };

        ProvenanceFields {
            build_type: "https://lunal.dev/kettle/nix@v1".to_string(),
            external_build_command: "nix build".to_string(),
            internal_parameters: InternalParameters {
                evaluation: Some(evaluation),
                flake_inputs: if flake_inputs.is_empty() {
                    None
                } else {
                    Some(flake_inputs)
                },
                lockfile_hash: Digest {
                    sha256: self.lockfile_hash,
                },
                toolchain: Toolchain::NixToolchain {
                    nix: ToolchainVersion {
                        version: self.nix_version,
                        digest: Digest {
                            sha256: self.nix_hash,
                        },
                    },
                },
            },
            resolved_dependencies,
        }
    }
}

fn parse_flake_lock(bytes: &[u8]) -> Result<Vec<FlakeDep>> {
    let data: Value = serde_json::from_slice(bytes)?;
    let Some(nodes) = data.get("nodes").and_then(|n| n.as_object()) else {
        return Ok(vec![]);
    };
    let Some(root_inputs) = nodes
        .get("root")
        .and_then(|r| r.get("inputs"))
        .and_then(|i| i.as_object())
    else {
        return Ok(vec![]);
    };

    let mut deps = Vec::new();
    for (input_name, input_ref) in root_inputs {
        // input_ref is a string key or an object with an "id" field
        let node_key = if let Some(s) = input_ref.as_str() {
            s.to_string()
        } else if let Some(id) = input_ref.get("id").and_then(|v| v.as_str()) {
            id.to_string()
        } else {
            continue;
        };

        let Some(locked) = nodes.get(&node_key).and_then(|n| n.get("locked")) else {
            continue;
        };

        let nar_hash = locked
            .get("narHash")
            .and_then(|v| v.as_str())
            .map(String::from);

        deps.push(FlakeDep {
            name: input_name.clone(),
            nar_hash,
        });
    }

    deps.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(deps)
}

fn evaluate_derivation_graph(path: &Path) -> Result<Value> {
    let output = Command::new("nix")
        .args([
            "derivation",
            "show",
            ".#default",
            "--recursive",
            "--extra-experimental-features",
            "flakes",
            "--extra-experimental-features",
            "nix-command",
        ])
        .current_dir(path)
        .output()
        .context("nix not found")?;
    if !output.status.success() {
        return Err(anyhow!(
            "nix derivation show failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(serde_json::from_slice(&output.stdout)?)
}

fn extract_fixed_output_hashes(graph: &Value) -> Vec<FetchEntry> {
    // New nix format nests derivations under a "derivations" key; old format uses top-level
    let derivations = if let Some(inner) = graph.get("derivations").and_then(|d| d.as_object()) {
        inner
    } else if let Some(obj) = graph.as_object() {
        obj
    } else {
        return vec![];
    };

    let mut fetches = Vec::new();
    for (drv_path, drv_data) in derivations {
        let Some(drv_obj) = drv_data.as_object() else {
            continue;
        };
        let env = drv_obj.get("env").and_then(|e| e.as_object());
        let outputs = drv_obj.get("outputs").and_then(|o| o.as_object());

        let mut output_hash: Option<String> = None;
        let mut hash_algo = "sha256".to_string();
        let mut hash_mode: Option<String> = None;

        // New format: hash stored in outputs as "sha256-<base64>" under a "hash" key
        if let Some(outputs) = outputs {
            'outer: for (_, out_spec) in outputs {
                if let Some(hash_str) = out_spec.get("hash").and_then(|h| h.as_str()) {
                    if let Some((algo, hash)) = hash_str.split_once('-') {
                        hash_algo = algo.to_string();
                        output_hash = Some(hash.to_string());
                    } else {
                        output_hash = Some(hash_str.to_string());
                    }
                    hash_mode = out_spec
                        .get("method")
                        .and_then(|m| m.as_str())
                        .map(String::from);
                    break 'outer;
                }
            }
        }

        // Old format: hash stored in env as outputHash / outputHashAlgo / outputHashMode
        if output_hash.is_none()
            && let Some(env) = env
            && let Some(hash) = env.get("outputHash").and_then(|h| h.as_str())
        {
            output_hash = Some(hash.to_string());
            hash_algo = env
                .get("outputHashAlgo")
                .and_then(|a| a.as_str())
                .unwrap_or("sha256")
                .to_string();
            hash_mode = env
                .get("outputHashMode")
                .and_then(|m| m.as_str())
                .map(String::from);
        }

        let Some(output_hash) = output_hash else {
            continue;
        };

        let name = drv_obj
            .get("name")
            .or_else(|| env.and_then(|e| e.get("name")))
            .and_then(|n| n.as_str())
            .unwrap_or("unknown")
            .to_string();

        let url = env
            .and_then(|e| e.get("url"))
            .and_then(|u| u.as_str())
            .map(String::from);
        let urls = env.and_then(|e| e.get("urls")).map(|u| {
            if let Some(arr) = u.as_array() {
                arr.iter()
                    .filter_map(|v| v.as_str())
                    .collect::<Vec<_>>()
                    .join(",")
            } else {
                u.as_str().unwrap_or("").to_string()
            }
        });

        fetches.push(FetchEntry {
            name,
            drv_path: drv_path.clone(),
            output_hash,
            output_hash_algo: hash_algo,
            output_hash_mode: hash_mode,
            url,
            urls,
        });
    }

    fetches.sort_by(|a, b| a.name.cmp(&b.name));
    fetches
}

fn nix_artifacts_from_store_paths(
    store_paths_str: &str,
    artifacts_dir: &Path,
) -> Result<Vec<Artifact>> {
    let mut artifacts = Vec::new();

    for line in store_paths_str.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let bin_dir = PathBuf::from(line).join("bin");
        if !bin_dir.is_dir() {
            continue;
        }

        for entry in fs_err::read_dir(&bin_dir)? {
            let entry = entry?;
            let item = entry.path();
            if !item.is_file() {
                continue;
            }
            if fs_err::metadata(&item)?.permissions().mode() & 0o111 == 0 {
                continue;
            }

            let name = item.file_name().unwrap().to_string_lossy().into_owned();
            let dest = artifacts_dir.join(&name);

            // Remove any existing file first — nix store copies may be read-only
            if dest.exists() {
                let mut perms = fs_err::metadata(&dest)?.permissions();
                perms.set_mode(0o644);
                fs_err::set_permissions(&dest, perms)?;
                fs_err::remove_file(&dest)?;
            }

            fs_err::copy(&item, &dest)?;
            let checksum = hex::encode(Sha256::digest(fs_err::read(&dest)?));
            artifacts.push(Artifact {
                name,
                path: dest,
                checksum,
            });
        }
    }

    Ok(artifacts)
}
