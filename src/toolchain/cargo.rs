use std::path::PathBuf;

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

pub(crate) fn build(path: &PathBuf) -> Result<()> {
    // Clean and re-create build directory
    let output_dir = path.join("kettle-build");
    fs_err::remove_dir_all(&output_dir)?;
    fs_err::create_dir_all(&output_dir)?;

    // Record inputs
    let git_commit = git_cmd(path, &["rev-parse", "HEAD"])?;
    let git_tree = git_cmd(path, &["rev-parse", "HEAD^{tree}"])?;
    let source_uri = git_cmd(path, &["remote", "get-url", "origin"]).unwrap_or_default();

    let (_, git_hash) = tool_info("git")?;
    let (rustc_version, rustc_hash) = tool_info("rustc")?;
    let (cargo_version, cargo_hash) = tool_info("cargo")?;

    let lockfile_path = path.join("Cargo.lock");
    let lockfile_bytes = fs_err::read(&lockfile_path).context("reading Cargo.lock")?;
    let lockfile_hash = hex::encode(Sha256::digest(&lockfile_bytes));
    let resolved_deps = parse_cargo_lock(&lockfile_bytes)?;

    let now = chrono::Utc::now();
    let id_suffix = &hex::encode(uuid::Uuid::new_v4())[..8];
    let invocation_id = format!("build-{}-{}", now.format("%Y%m%d-%H%M%S"), id_suffix);
    let started_on = now.to_rfc3339_opts(chrono::SecondsFormat::Micros, false);

    Ok(())
}
