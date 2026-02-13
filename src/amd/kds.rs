use crate::amd::certs::{AmdChain, Vcek};
use pem::parse_many;
use sev::Generation;
use sev::firmware::guest::AttestationReport;
use thiserror::Error;
use x509_cert::Certificate;
use x509_cert::der::Decode;

const KDS_CERT_SITE: &str = "https://kdsintf.amd.com";
const KDS_VCEK: &str = "/vcek/v1";
const KDS_CERT_CHAIN: &str = "cert_chain";

/// Derive the SEV product name from an AttestationReport.
/// Uses CPUID family/model from v3+ reports, or falls back to "Genoa" for v2 reports.
fn get_product_name(report: &AttestationReport) -> &'static str {
    match (report.cpuid_fam_id, report.cpuid_mod_id) {
        (Some(family), Some(model)) => {
            match Generation::identify_cpu(family, model) {
                Ok(Generation::Milan) => "Milan",
                Ok(Generation::Genoa) => "Genoa",
                Ok(Generation::Turin) => "Turin",
                // Naples/Rome don't support SNP, but handle for completeness
                Ok(_) | Err(_) => "Genoa",
            }
        }
        // v2 reports don't have CPUID fields; fall back to Genoa
        _ => "Genoa",
    }
}

fn get(url: &str) -> Result<Vec<u8>, HttpError> {
    let response = reqwest::blocking::get(url)?;
    Ok(response.bytes()?.to_vec())
}

#[derive(Error, Debug)]
pub enum HttpError {
    #[error("HTTP error")]
    Http(#[from] reqwest::Error),
    #[error("failed to read HTTP response")]
    Io(#[from] std::io::Error),
}

#[derive(Error, Debug)]
pub enum AmdKdsError {
    #[error("X.509 certificate error: {0}")]
    X509(#[from] x509_cert::der::Error),
    #[error("PEM parsing error: {0}")]
    Pem(#[from] pem::PemError),
    #[error("Http error")]
    Http(#[from] HttpError),
    #[error("Certificate chain parsing error: expected 2 certificates, found {0}")]
    InvalidChainLength(usize),
}

/// Retrieve the AMD chain of trust (ASK & ARK) from AMD's KDS
pub fn get_cert_chain(report: &AttestationReport) -> Result<AmdChain, AmdKdsError> {
    let product_name = get_product_name(report);
    let url = format!("{KDS_CERT_SITE}{KDS_VCEK}/{product_name}/{KDS_CERT_CHAIN}");
    let bytes = get(&url)?;

    // Parse PEM certificates
    let pem_objects = parse_many(&bytes)?;

    if pem_objects.len() != 2 {
        return Err(AmdKdsError::InvalidChainLength(pem_objects.len()));
    }

    // Convert PEM to Certificate objects
    let ask = Certificate::from_der(pem_objects[0].contents())?;
    let ark = Certificate::from_der(pem_objects[1].contents())?;

    let chain = AmdChain { ask, ark };

    Ok(chain)
}

fn hexify(bytes: &[u8]) -> String {
    let mut hex_string = String::new();
    for byte in bytes {
        hex_string.push_str(&format!("{byte:02x}"));
    }
    hex_string
}

/// Retrieve a VCEK cert from AMD's KDS, based on an AttestationReport's platform information
pub fn get_vcek(report: &AttestationReport) -> Result<Vcek, AmdKdsError> {
    let product_name = get_product_name(report);
    let hw_id = hexify(&report.chip_id);
    let url = format!(
        "{KDS_CERT_SITE}{KDS_VCEK}/{product_name}/{hw_id}?blSPL={:02}&teeSPL={:02}&snpSPL={:02}&ucodeSPL={:02}",
        report.reported_tcb.bootloader,
        report.reported_tcb.tee,
        report.reported_tcb.snp,
        report.reported_tcb.microcode
    );

    println!("🔍 Fetching VCEK from URL: {}", url);
    println!("🔍 Chip ID: {}", hw_id);
    println!(
        "🔍 TCB levels: bl={:02}, tee={:02}, snp={:02}, ucode={:02}",
        report.reported_tcb.bootloader,
        report.reported_tcb.tee,
        report.reported_tcb.snp,
        report.reported_tcb.microcode
    );

    let bytes = get(&url)?;
    println!("🔍 Received {} bytes from KDS", bytes.len());

    // Add some bSNP_REPORT_SIZEic validation of the DER data
    println!(
        "🔍 First 32 bytes: {:02x?}",
        &bytes[..std::cmp::min(32, bytes.len())]
    );
    println!(
        "🔍 Last 32 bytes: {:02x?}",
        &bytes[bytes.len().saturating_sub(32)..]
    );

    let cert = Certificate::from_der(&bytes)?;
    println!("🔍 Successfully parsed VCEK certificate");

    let vcek = Vcek(cert);
    Ok(vcek)
}
