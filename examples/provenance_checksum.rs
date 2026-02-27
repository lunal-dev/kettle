use std::{env::args, path::PathBuf};

use anyhow::{Result, anyhow};
use kettle::provenance::Provenance;
use sha2::{Digest as _, Sha256};

pub fn main() -> Result<()> {
    let mut args = args();
    args.next(); // This is the executable
    let path: PathBuf = args
        .next()
        .unwrap_or_else(|| "test/files/ripgrep/provenance.json".to_string())
        .into();
    println!("Checking file {:?}", path);
    let provenance_bytes = fs_err::read(&path)?;
    let bytes_checksum = Sha256::digest(&provenance_bytes);

    // Compact the JSON for comparison
    let provenance_value: serde_json::Value = serde_json::from_slice(&provenance_bytes)?;
    let compact_provenance_bytes = serde_json::to_string(&provenance_value)?;
    let compact_path = path
        .parent()
        .ok_or(anyhow!("no parent"))?
        .join("provenance-serde.json");
    fs_err::write(&compact_path, &compact_provenance_bytes)?;
    let compact_checksum = Sha256::digest(&compact_provenance_bytes);

    let provenance = Provenance::from_json(&provenance_bytes)?;
    let provenance_checksum = provenance.checksum();
    let provenance_path = path
        .parent()
        .ok_or(anyhow!("no parent"))?
        .join("provenance-output.json");
    fs_err::write(&provenance_path, serde_json::to_string(&provenance)?)?;

    println!("Checksum of file on disk: {}", hex::encode(bytes_checksum));
    println!(
        "Checksum compacted bytes: {}",
        hex::encode(compact_checksum)
    );
    println!(
        "Provenance.checksum():    {}",
        hex::encode(provenance_checksum)
    );

    println!(
        "Wrote compacted to  {:?}\nWrote provenance to {:?}",
        compact_path, provenance_path
    );

    Ok(())
}
