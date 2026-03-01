use anyhow::{Context as _, Result, anyhow};
use rs_merkle::MerkleTree;
use serde_json::Value;
use sha2::{Digest as _, Sha256};
use std::path::PathBuf;
use std::process::Command;
use std::{os::unix::fs::PermissionsExt as _, path::Path};

use crate::{
    provenance::{
        Annotation, BuildDefiniton, Builder, Byproduct, Digest, Evaluation, ExternalParameters,
        FlakeInput, InternalParameters, Metadata, Predicate, Provenance, ResolvedDependency,
        RunDetails, Source, SourceDigest, Subject, Toolchain, ToolchainVersion,
    },
    toolchain::{Artifact, BuildMetadata, git_cmd},
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

struct BuildInputs {
    git_commit: String,
    git_tree: String,
    source_uri: String,
    nix_version: String,
    nix_hash: String,
    lockfile_hash: String,
    flake_deps: Vec<FlakeDep>,
    fetches: Vec<FetchEntry>,
    derivation_count: usize,
    merkle_root: String,
}

impl BuildInputs {
    fn from_dir(path: &PathBuf) -> Result<Self> {
        let git_commit = git_cmd(path, &["rev-parse", "HEAD"])?;
        let git_tree = git_cmd(path, &["rev-parse", "HEAD^{tree}"])?;
        let source_uri = git_cmd(path, &["remote", "get-url", "origin"]).unwrap_or_default();

        let (nix_version, nix_hash) = nix_tool_info()?;

        let lockfile_path = path.join("flake.lock");
        let lockfile_bytes = fs_err::read(&lockfile_path).context("reading flake.lock")?;
        let lockfile_hash = hex::encode(Sha256::digest(&lockfile_bytes));
        let flake_deps = parse_flake_lock(&lockfile_bytes)?;

        let graph = evaluate_derivation_graph(path)?;
        let derivation_count = graph.as_object().map(|o| o.len()).unwrap_or(0);
        let fetches = extract_fixed_output_hashes(&graph);

        // Merkle entries: git_commit, git_tree, lockfile_hash, then fetches (sorted by name),
        // then nix_hash, nix_version
        let mut merkle_strings: Vec<String> =
            vec![git_commit.clone(), git_tree.clone(), lockfile_hash.clone()];

        for fod in &fetches {
            merkle_strings.push(format!(
                "fetch:{}:{}:{}",
                fod.name, fod.output_hash_algo, fod.output_hash
            ));
        }

        merkle_strings.push(nix_hash.clone());
        merkle_strings.push(nix_version.clone());

        let leaves: Vec<[u8; 32]> = merkle_strings
            .iter()
            .map(|e| Sha256::digest(e.as_bytes()).into())
            .collect();

        let merkle_tree = MerkleTree::<rs_merkle::algorithms::Sha256>::from_leaves(&leaves);
        let merkle_root_bytes = merkle_tree
            .root()
            .ok_or(anyhow!("Merkle tree root calculation failed!"))?;
        let merkle_root = hex::encode(merkle_root_bytes);

        Ok(Self {
            git_commit,
            git_tree,
            source_uri,
            nix_version,
            nix_hash,
            lockfile_hash,
            flake_deps,
            fetches,
            derivation_count,
            merkle_root,
        })
    }
}

fn nix_tool_info() -> Result<(String, String)> {
    let which = Command::new("which")
        .arg("nix")
        .output()
        .context("which nix failed")?;
    let nix_path = PathBuf::from(String::from_utf8(which.stdout)?.trim().to_string());
    let nix_hash = hex::encode(Sha256::digest(fs_err::read(&nix_path)?));

    let ver = Command::new("nix")
        .arg("--version")
        .output()
        .context("nix --version failed")?;
    let nix_version = String::from_utf8(ver.stdout)?.trim().to_string();

    Ok((nix_version, nix_hash))
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

fn evaluate_derivation_graph(path: &PathBuf) -> Result<Value> {
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

pub(crate) fn build(path: &PathBuf) -> Result<()> {
    // Clean and re-create build directory
    let output_dir = path.join("kettle-build");
    if fs_err::exists(&output_dir)? {
        fs_err::remove_dir_all(&output_dir)?;
    }
    fs_err::create_dir_all(&output_dir)?;

    let build_inputs = BuildInputs::from_dir(path)?;
    let mut build_metadata = BuildMetadata::start();

    // Run build
    println!("Running `nix build --no-link --print-out-paths`");
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

    // Mark the build as completed
    build_metadata.finish();

    // Copy executables from bin/ in each store path into artifacts/
    let store_paths_str = String::from_utf8(output.stdout)?;
    let artifacts_dir = output_dir.join("artifacts");
    fs_err::create_dir_all(&artifacts_dir)?;
    let artifacts = nix_artifacts_from_store_paths(&store_paths_str, &artifacts_dir)?;

    // Generate the provenance file from the inputs and outputs
    let provenance = build_provenance(build_inputs, build_metadata, &artifacts);
    let provenance_path = output_dir.join("provenance.json");
    fs_err::write(&provenance_path, serde_json::to_string_pretty(&provenance)?)?;

    println!(
        "Build in {:?} complete, output located in `kettle-build`",
        &path
    );
    Ok(())
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

fn build_provenance(
    inputs: BuildInputs,
    metadata: BuildMetadata,
    artifacts: &[Artifact],
) -> Provenance {
    let resolved_deps: Vec<ResolvedDependency> = inputs
        .fetches
        .iter()
        .map(|fetch| {
            let uri = format!(
                "pkg:nix-fetch/{}?hash={}:{}",
                fetch.name, fetch.output_hash_algo, fetch.output_hash
            );
            ResolvedDependency {
                annotations: Some(Annotation {
                    drv_path: fetch.drv_path.clone(),
                    output_hash_mode: fetch.output_hash_mode.clone(),
                    url: fetch.url.clone(),
                    urls: fetch.urls.clone(),
                }),
                digest: Digest {
                    sha256: fetch.output_hash.clone(),
                },
                name: fetch.name.clone(),
                uri,
            }
        })
        .collect();

    // Flake inputs are always included in internalParameters for human context
    let flake_inputs: Vec<FlakeInput> = inputs
        .flake_deps
        .iter()
        .filter_map(|dep| {
            dep.nar_hash.as_ref().map(|nar_hash| FlakeInput {
                name: dep.name.clone(),
                nar_hash: nar_hash.clone(),
            })
        })
        .collect();

    // Evaluation metadata only present in deep mode
    let evaluation = Evaluation {
        derivation_count: serde_json::Number::from(inputs.derivation_count),
        fetch_count: serde_json::Number::from(inputs.fetches.len()),
    };

    Provenance {
        _type: "https://in-toto.io/Statement/v1".to_string(),
        predicate_type: "https://slsa.dev/provenance/v1".to_string(),
        subject: artifacts
            .iter()
            .map(|artifact| Subject {
                name: artifact.name.clone(),
                digest: Digest {
                    sha256: artifact.checksum.clone(),
                },
            })
            .collect(),
        predicate: Predicate {
            build_definition: BuildDefiniton {
                build_type: "https://attestable-builds.dev/kettle/nix@v1".to_string(),
                external_parameters: ExternalParameters {
                    build_command: "nix build".to_string(),
                    source: Source {
                        digest: SourceDigest {
                            git_commit: inputs.git_commit,
                            git_tree: inputs.git_tree,
                        },
                        uri: inputs.source_uri,
                    },
                },
                internal_parameters: InternalParameters {
                    evaluation: Some(evaluation),
                    flake_inputs: if flake_inputs.is_empty() {
                        None
                    } else {
                        Some(flake_inputs)
                    },
                    lockfile_hash: Digest {
                        sha256: inputs.lockfile_hash,
                    },
                    toolchain: Toolchain::NixToolchain {
                        nix: ToolchainVersion {
                            version: inputs.nix_version,
                            digest: Digest {
                                sha256: inputs.nix_hash,
                            },
                        },
                    },
                },
                resolved_dependencies: resolved_deps,
            },
            run_details: RunDetails {
                builder: Builder {
                    id: "https://attestable-builds.dev/kettle-tee/v1".to_string(),
                },
                metadata: Metadata {
                    invocation_id: metadata.invocation_id,
                    started_on: metadata.started_on,
                    finished_on: metadata.finished_on,
                },
                byproducts: vec![Byproduct {
                    name: "input_merkle_root".to_string(),
                    digest: Digest {
                        sha256: inputs.merkle_root,
                    },
                }],
            },
        },
    }
}
