use std::path::PathBuf;

use crate::Args;

pub(crate) fn build(_args: &Args, path: &PathBuf) -> anyhow::Result<()> {
    println!("Building project in: {:?}", path);

    Ok(())
}
