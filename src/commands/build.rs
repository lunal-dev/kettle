use anyhow::{Result, anyhow};
use fs_err::exists;
use std::path::PathBuf;

#[derive(Debug)]
pub(crate) enum ProjectToolchain {
    Cargo,
    Nix,
}
impl ProjectToolchain {
    fn from_dir(path: &PathBuf) -> Result<Self> {
        if exists(path.join("flake.nix"))? {
            Ok(Self::Nix)
        } else if exists(path.join("Cargo.lock"))? {
            Ok(Self::Cargo)
        } else {
            Err(anyhow!(
                "Could not determine toolchain. Is {:?} a rust or nix project?",
                path
            ))
        }
    }
}

pub(crate) fn build(path: &PathBuf) -> Result<()> {
    println!("Building project in: {:?}", path);

    let toolchain = ProjectToolchain::from_dir(path)?;
    println!("Found {:?} project", toolchain);
    match toolchain {
        ProjectToolchain::Cargo => crate::toolchain::cargo::build(&path),
        ProjectToolchain::Nix => crate::toolchain::nix::build(&path),
    }

    Ok(())
}
