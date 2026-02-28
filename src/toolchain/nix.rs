use anyhow::Context as _;
use anyhow::Result;
use anyhow::anyhow;
use std::path::PathBuf;
use std::process::Command;

use crate::toolchain::Artifact;
use crate::toolchain::BuildMetadata;

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
        .args(["build", "--no-link", "--print-out-paths"])
        .current_dir(path)
        .output()
        .context("failed to spawn nix")?;
    if !output.status.success() {
        return Err(anyhow!(
            "nix build failed (exit {:?})",
            output.status.code()
        ));
    }

    // List and checksum files with no extension or .exe in target/release
    let release_dirs: String = output.stdout.try_into()?;
    let artifacts = release_dirs
        .lines()
        .flat_map(|line| Artifact::in_dir(&line.into()))
        .collect();

    // Mark the build as completed
    build_metadata.finish();

    // Generate the provenance file from the inputs and outputs
    let provenance = build_provenance(build_inputs, build_metadata, &artifacts);
    let provenance_path = output_dir.join("provenance.json");
    fs_err::write(&provenance_path, serde_json::to_string_pretty(&provenance)?)?;

    // Copy the artifacts
    let artifacts_dir = output_dir.join("artifacts");
    fs_err::create_dir_all(&artifacts_dir)?;
    for artifact in &artifacts {
        fs_err::copy(&artifact.path, artifacts_dir.join(&artifact.name))?;
    }

    println!(
        "Build in {:?} complete, output located in `kettle-build`",
        &path
    );
    Ok(())
}

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
