use std::path::PathBuf;

pub(crate) enum ProjectToolchain {
    Cargo,
    Nix,
}
impl ProjectToolchain {
    fn from_dir(path: &PathBuf) -> Self {
        todo!()
    }
}

pub(crate) fn build(path: &PathBuf) -> anyhow::Result<()> {
    println!("Building project in: {:?}", path);

    let toolchain = ProjectToolchain::from_dir(path);
    match toolchain {
        ProjectToolchain::Cargo => crate::toolchain::cargo::build(&toolchain),
        ProjectToolchain::Nix => crate::toolchain::nix::build(&toolchain),
    }

    Ok(())
}
