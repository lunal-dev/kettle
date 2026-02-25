use anyhow::Result;
use std::path::PathBuf;

#[cfg(feature = "attest")]
pub(crate) async fn attest(path: &PathBuf) -> Result<()> {
    let platform = attestation::detect().expect("no TEE platform detected");
    eprintln!("Detected platform: {}", platform);

    let nonce = b"hello-attestation";
    let evidence_json = attestation::attest(platform, nonce)
        .await
        .expect("attestation failed");
    println!("{}", String::from_utf8_lossy(&evidence_json));

    Ok(())
}

#[cfg(not(feature = "attest"))]
use anyhow::anyhow;
pub(crate) async fn attest(_path: &PathBuf) -> Result<()> {
    Err(anyhow!(
        "Attestation disabled. Rebuild Kettle with `--features attest` to enable this command."
    ))
}
