use anyhow::{Context, Result, anyhow};
use chrono::DateTime;
use rs_merkle::MerkleTree;
use sha2::{Digest as _, Sha256};
use std::path::PathBuf;
use std::process::Command;

use crate::provenance::{
    BuildDefiniton, Builder, Byproduct, Digest, ExternalParameters, InternalParameters, Metadata,
    Predicate, Provenance, ResolvedDependency, RunDetails, Source, SourceDigest, Subject,
    Toolchain, ToolchainVersion,
};

struct BuildInputs {
    git_commit: String,
    git_tree: String,
    source_uri: String,
    rustc_version: String,
    rustc_hash: String,
    cargo_version: String,
    cargo_hash: String,
    lockfile_hash: String,
    resolved_deps: Vec<ResolvedDependency>,
    merkle_root: String,
}

impl BuildInputs {
    fn from_dir(path: &PathBuf) -> Result<Self> {
        let git_commit = git_cmd(path, &["rev-parse", "HEAD"])?;
        let git_tree = git_cmd(path, &["rev-parse", "HEAD^{tree}"])?;
        let source_uri = git_cmd(path, &["remote", "get-url", "origin"]).unwrap_or_default();

        let (rustc_version, rustc_hash) = tool_info("rustc")?;
        let (cargo_version, cargo_hash) = tool_info("cargo")?;

        let lockfile_path = path.join("Cargo.lock");
        let lockfile_bytes = fs_err::read(&lockfile_path).context("reading Cargo.lock")?;
        let lockfile_hash = hex::encode(Sha256::digest(&lockfile_bytes));
        let resolved_deps = parse_cargo_lock(&lockfile_bytes)?;

        let mut merkle_entries = vec![
            &git_commit,
            &git_tree,
            &rustc_hash,
            &rustc_version,
            &cargo_hash,
            &cargo_version,
            &lockfile_hash,
        ];

        let merkle_deps = resolved_deps
            .iter()
            .map(|dep| dep.uri.clone())
            .collect::<Vec<String>>();

        merkle_entries.extend(&merkle_deps);
        let leaves: Vec<[u8; 32]> = merkle_entries
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
            rustc_version,
            rustc_hash,
            cargo_version,
            cargo_hash,
            lockfile_hash,
            resolved_deps,
            merkle_root,
        })
    }
}

struct BuildMetadata {
    invocation_id: String,
    started_on: String,
    finished_on: Option<String>,
}

impl BuildMetadata {
    fn start() -> Self {
        let now = chrono::Utc::now();
        let id_suffix = &hex::encode(uuid::Uuid::new_v4())[..8];
        let invocation_id = format!("build-{}-{}", now.format("%Y%m%d-%H%M%S"), id_suffix);

        Self {
            invocation_id,
            started_on: Self::ts(now),
            finished_on: None,
        }
    }

    fn ts(now: DateTime<chrono::Utc>) -> String {
        now.to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
    }

    fn finish(&mut self) {
        self.finished_on = Some(Self::ts(chrono::Utc::now()));
    }
}

pub(crate) fn build(path: &PathBuf) -> Result<()> {
    // Clean and re-create build directory
    let output_dir = path.join("kettle-build");
    fs_err::remove_dir_all(&output_dir)?;
    fs_err::create_dir_all(&output_dir)?;

    let build_inputs = BuildInputs::from_dir(path)?;
    let mut build_metadata = BuildMetadata::start();

    // Run build
    println!("Running `cargo build --locked --release`");
    let status = Command::new("cargo")
        .args(["build", "--locked", "--release"])
        .current_dir(path)
        .status()
        .context("failed to spawn cargo")?;
    if !status.success() {
        return Err(anyhow!("cargo build failed (exit {:?})", status.code()));
    }

    // List and checksum files with no extension or .exe in target/release
    let release_dir = path.join("target").join("release");
    let artifacts = collect_artifacts(&release_dir)?;

    // Mark the build as completed at this time
    build_metadata.finish();

    let provenance = build_provenance(build_inputs, build_metadata, artifacts);

    // Write provenance
    let provenance_path = output_dir.join("provenance.json");
    fs_err::write(&provenance_path, serde_json::to_string_pretty(&provenance)?)?;
    println!("Provenance: {}", provenance_path.display());

    // Generate attestation
    #[cfg(feature = "attest")]
    attest();

    #[cfg(not(feature = "attest"))]
    eprintln!("Cannot attest build due to missing hardware security module.");

    println!(
        "Build in {:?} complete, output located in `kettle-build`",
        &path
    );
    Ok(())
}

fn build_provenance(
    inputs: BuildInputs,
    metadata: BuildMetadata,
    artifacts: Vec<(String, String)>,
) -> Provenance {
    Provenance {
        _type: "https://in-toto.io/Statement/v1".to_string(),
        predicate_type: "https://slsa.dev/provenance/v1".to_string(),
        subject: artifacts
            .iter()
            .map(|(name, hash)| Subject {
                name: name.clone(),
                digest: Digest {
                    sha256: hash.clone(),
                },
            })
            .collect(),
        predicate: Predicate {
            build_definition: BuildDefiniton {
                build_type: "https://attestable-builds.dev/kettle/cargo@v1".to_string(),
                external_parameters: ExternalParameters {
                    build_command: "cargo build".to_string(),
                    source: Source {
                        digest: SourceDigest {
                            git_commit: inputs.git_commit,
                            git_tree: inputs.git_tree,
                        },
                        uri: inputs.source_uri,
                    },
                },
                internal_parameters: InternalParameters {
                    evaluation: None,
                    flake_inputs: None,
                    lockfile_hash: Digest {
                        sha256: inputs.lockfile_hash,
                    },
                    toolchain: Toolchain::RustToolchain {
                        rustc: ToolchainVersion {
                            version: inputs.rustc_version,
                            digest: Digest {
                                sha256: inputs.rustc_hash,
                            },
                        },
                        cargo: ToolchainVersion {
                            version: inputs.cargo_version,
                            digest: Digest {
                                sha256: inputs.cargo_hash,
                            },
                        },
                    },
                },
                resolved_dependencies: inputs.resolved_deps,
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

fn git_cmd(path: &PathBuf, args: &[&str]) -> Result<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(path)
        .args(args)
        .output()
        .context("git not found")?;
    if !out.status.success() {
        return Err(anyhow!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8(out.stdout)?.trim().to_string())
}

fn tool_info(cmd: &str) -> Result<(String, String)> {
    let ver = Command::new(cmd)
        .arg("--version")
        .output()
        .with_context(|| format!("{cmd} not found"))?;
    let version = String::from_utf8(ver.stdout)?.trim().to_string();

    let mut which = Command::new("rustup")
        .args(["which", cmd])
        .output()
        .with_context(|| format!("rustup which {cmd} failed"))?;
    if which.stdout.is_empty() {
        which = Command::new("which")
            .arg(cmd)
            .output()
            .with_context(|| format!("which {cmd} failed"))?;
    }
    let bin = PathBuf::from(String::from_utf8(which.stdout)?.trim().to_string());

    let hash = hex::encode(Sha256::digest(fs_err::read(&bin)?));
    Ok((version, hash))
}

fn collect_artifacts(release_dir: &PathBuf) -> Result<Vec<(String, String)>> {
    let mut artifacts = Vec::new();
    for entry in fs_err::read_dir(release_dir)? {
        let entry = entry?;
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        if ext.is_empty() || ext == "exe" {
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            let hash = hex::encode(Sha256::digest(fs_err::read(&path)?));
            artifacts.push((name, hash));
        }
    }
    Ok(artifacts)
}

fn parse_cargo_lock(bytes: &[u8]) -> Result<Vec<ResolvedDependency>> {
    let content = std::str::from_utf8(bytes)?;
    let lock: toml::Value = toml::from_str(content)?;
    let Some(packages) = lock.get("package").and_then(|v| v.as_array()) else {
        return Ok(vec![]);
    };

    let mut deps = Vec::new();
    for pkg in packages {
        let name = pkg.get("name").and_then(|v| v.as_str()).unwrap_or_default();
        let version = pkg
            .get("version")
            .and_then(|v| v.as_str())
            .unwrap_or_default();
        if let Some(checksum) = pkg.get("checksum").and_then(|v| v.as_str()) {
            deps.push(ResolvedDependency {
                annotations: None,
                digest: Digest {
                    sha256: checksum.to_string(),
                },
                name: name.to_string(),
                uri: format!("pkg:cargo/{name}@{version}?checksum=sha256:{checksum}"),
            });
        }
    }

    deps.sort_by_cached_key(|e| e.uri.clone());

    Ok(deps)
}
