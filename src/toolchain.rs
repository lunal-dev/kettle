use anyhow::Context as _;
use anyhow::Result;
use anyhow::anyhow;
use chrono::DateTime;
use sha2::{Digest as _, Sha256};
use std::{path::PathBuf, process::Command};

pub(crate) mod cargo;
pub(crate) mod nix;

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

struct Artifact {
    name: String,
    path: PathBuf,
    checksum: String,
}

impl Artifact {
    fn in_dir(path: &PathBuf) -> Result<Vec<Artifact>> {
        let mut artifacts = Vec::new();
        for entry in fs_err::read_dir(path)? {
            let entry = entry?;
            let path = entry.path();
            let is_dotfile = path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .starts_with(".");

            if !path.is_file() || is_dotfile {
                continue;
            }
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext.is_empty() || ext == "exe" {
                let name = path.file_name().unwrap().to_string_lossy().into_owned();
                let checksum = hex::encode(Sha256::digest(fs_err::read(&path)?));
                artifacts.push(Artifact {
                    name,
                    path,
                    checksum,
                });
            }
        }
        Ok(artifacts)
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
