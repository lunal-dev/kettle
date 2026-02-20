use std::path::PathBuf;

pub(crate) fn build(path: &PathBuf) -> anyhow::Result<()> {
    println!("Building project in: {:?}", path);

    Ok(())
}
