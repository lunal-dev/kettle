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
