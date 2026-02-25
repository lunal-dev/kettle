use anyhow::Result;
use std::path::PathBuf;

#[cfg(feature = "attest")]
pub(crate) async fn attest(path: &PathBuf) -> Result<()> {
    crate::commands::build::build(&path)?;

    let platform = attestation::detect().expect("no TEE platform detected");
    eprintln!("Attesting build on platform: {}", platform);

    let provenance_bytes = fs_err::read("provenance.json");
    let provenance_value = serde_json::from_slice(provenance_bytes);
    let provenance_checksum_bytes = serde_json::to_string(provenance_value);
    let provenance_checksum = sha2::Sha256::digest(provenance_checksum_bytes);

    let evidence_json = attestation::attest(platform, provenance_checksum)
        .await
        .expect("attestation failed");
    fs_err::write("evidence.json", String::from_utf8_lossy(&evidence_json));
    println!("Attestation complete! Evidence written to file `evidence.json`");

    Ok(())
}

#[cfg(not(feature = "attest"))]
use anyhow::anyhow;
pub(crate) async fn attest(_path: &PathBuf) -> Result<()> {
    Err(anyhow!(
        "Attestation disabled. Rebuild Kettle with `--features attest` to enable this command."
    ))
}
