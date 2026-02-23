use std::path::PathBuf;

use sha2::Digest;

use crate::{provenance::{BuildDefiniton, ExternalParameters, Source}, toolchain};

struct BuildInputs {

}

pub(crate) fn build(path: &PathBuf) -> Result<()> {
    // Clean and re-create build directory
    let output_dir = path.join("kettle-build");
    fs_err::remove_dir_all(&output_dir);
    fs_err::create_dir_all(&output_dir);

    /// Record inputs


    /// Run build
    // run `cargo build --locked --release`

    /// Generate provenance
    // list and checksum the files with either no extension or .exe extension in ./target/release
    // write out provenance

    /// Generate attestation
    // attest
    // write out attestation
}
