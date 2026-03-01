use anyhow::{Context as _, Result, anyhow};
use chrono::DateTime;
use sha2::{Digest as _, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::provenance::{InternalParameters, ResolvedDependency};

// --- Moved from toolchain.rs ---

pub(crate) struct BuildMetadata {
    pub(crate) invocation_id: String,
    pub(crate) started_on: String,
    pub(crate) finished_on: Option<String>,
}

impl BuildMetadata {
    pub(crate) fn start() -> Self {
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

    pub(crate) fn finish(&mut self) {
        self.finished_on = Some(Self::ts(chrono::Utc::now()));
    }
}

pub(crate) struct Artifact {
    pub(crate) name: String,
    pub(crate) path: PathBuf,
    pub(crate) checksum: String,
}

impl Artifact {
    pub(crate) fn in_dir(path: &PathBuf) -> Result<Vec<Artifact>> {
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

pub(crate) fn git_cmd(path: &PathBuf, args: &[&str]) -> Result<String> {
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

pub(crate) fn tool_info(cmd: &str) -> Result<(String, String)> {
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

// --- New shared types ---

pub(crate) struct GitContext {
    pub(crate) commit: String,
    pub(crate) tree: String,
    pub(crate) source_uri: String,
}

impl GitContext {
    pub(crate) fn from_dir(path: &PathBuf) -> Result<Self> {
        let commit = git_cmd(path, &["rev-parse", "HEAD"])?;
        let tree = git_cmd(path, &["rev-parse", "HEAD^{tree}"])?;
        let source_uri =
            git_cmd(path, &["remote", "get-url", "origin"]).unwrap_or_default();
        Ok(Self { commit, tree, source_uri })
    }
}

pub(crate) struct BuildOutput {
    pub(crate) stdout: Vec<u8>,
    pub(crate) stderr: Vec<u8>,
}

pub(crate) struct ProvenanceFields {
    pub(crate) build_type: String,
    pub(crate) external_build_command: String,
    pub(crate) internal_parameters: InternalParameters,
    pub(crate) resolved_dependencies: Vec<ResolvedDependency>,
}

/// Locates a tool binary and captures its version + SHA-256 hash.
/// Replaces `tool_info()` (rustup-specific) and `nix_tool_info()` with a unified API.
pub(crate) struct ToolBinaryInfo {
    pub(crate) version: String,
    pub(crate) sha256: String,
}

impl ToolBinaryInfo {
    /// For rustup-managed tools (rustc, cargo): locate via `rustup which`, fall back to `which`.
    pub(crate) fn via_rustup(cmd: &str) -> Result<Self> {
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
        let sha256 = hex::encode(Sha256::digest(fs_err::read(&bin)?));
        Ok(Self { version, sha256 })
    }

    /// For system tools located via `which` (nix, node, pip, etc.).
    pub(crate) fn via_which(cmd: &str) -> Result<Self> {
        let which = Command::new("which")
            .arg(cmd)
            .output()
            .with_context(|| format!("which {cmd} failed"))?;
        let bin = PathBuf::from(String::from_utf8(which.stdout)?.trim().to_string());
        let sha256 = hex::encode(Sha256::digest(fs_err::read(&bin)?));

        let ver = Command::new(cmd)
            .arg("--version")
            .output()
            .with_context(|| format!("{cmd} --version failed"))?;
        let version = String::from_utf8(ver.stdout)?.trim().to_string();
        Ok(Self { version, sha256 })
    }
}

pub(crate) trait ToolchainDriver: Sized {
    /// Lockfile filename relative to the project root (e.g. "Cargo.lock", "flake.lock").
    fn lockfile_filename() -> &'static str;

    /// Human-readable build invocation for printing (e.g. "cargo build --locked --release").
    fn build_command_display() -> &'static str;

    /// Collect toolchain-specific inputs.
    /// Receives pre-computed git context, lockfile hash, and raw lockfile bytes.
    fn collect_inputs(
        path: &PathBuf,
        git: &GitContext,
        lockfile_hash: &str,
        lockfile_bytes: &[u8],
    ) -> Result<Self>;

    /// Ordered strings to be hashed as Merkle leaves.
    ///
    /// IMPORTANT: leaf ordering determines the Merkle root. For existing toolchains
    /// this ordering is a frozen contract — changing it breaks provenance determinism
    /// for any build already attested.
    fn merkle_entries(&self, git: &GitContext, lockfile_hash: &str) -> Vec<String>;

    /// Run the build subprocess.
    fn run_build(path: &PathBuf) -> Result<BuildOutput>;

    /// Collect and stage artifacts into `artifacts_dir`.
    /// Returns Vec<Artifact> with `path` fields pointing into `artifacts_dir`.
    /// Each impl is responsible for the actual file copying (Nix needs special
    /// permission handling for read-only store paths).
    fn collect_artifacts(
        output: &BuildOutput,
        path: &PathBuf,
        artifacts_dir: &Path,
    ) -> Result<Vec<Artifact>>;

    /// Return provenance fields for the shared scaffold to assemble.
    /// Takes `self` by value so impls can move owned fields (e.g. Vec<ResolvedDependency>)
    /// without requiring Clone on provenance types.
    fn provenance_fields(self, git: &GitContext, merkle_root: &str) -> ProvenanceFields;
}
