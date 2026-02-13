use anyhow::{Result, anyhow};
use base64::{Engine, prelude::BASE64_STANDARD};
use serde::{Deserialize, Serialize};
use sev::firmware::guest::AttestationReport as SnpReport;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::vec::Vec;

use crate::amd;
use crate::amd::certs::Vcek;
use crate::amd::snp_report::Validateable;
use crate::hcl::HclReport;

/// PEM encoded VCEK certificate and AMD certificate chain.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Certificates {
    #[serde(rename = "vcekCert")]
    pub vcek: String,
    #[serde(rename = "certificateChain")]
    pub amd_chain: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttestationEvidence {
    pub report: Vec<u8>,
    pub certs: Certificates,
    pub report_data: String,
}

impl AttestationEvidence {
    /// Serialize to bytes using bincode
    pub fn _to_bytes(&self) -> Result<Vec<u8>> {
        Ok(bincode::serialize(self)?)
    }

    /// Deserialize from bytes using bincode
    pub fn from_bytes(data: &[u8]) -> Result<Self> {
        Ok(bincode::deserialize(data)?)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationResult {
    pub report: serde_json::Value,
    pub certs: serde_json::Value,
    pub report_data: String,
}

pub fn verify_attestation(
    evidence: Vec<u8>,
    custom_data: Option<String>,
) -> Result<VerificationResult> {
    let evidence_gz = BASE64_STANDARD.decode(evidence)?;
    let mut evidence_bytes = Vec::new();
    flate2::read::MultiGzDecoder::new(&evidence_gz[..]).read_to_end(&mut evidence_bytes)?;
    let evidence = AttestationEvidence::from_bytes(&evidence_bytes[..])?;
    let certs = serde_json::to_value(&evidence.certs)?;

    let hcl_report = HclReport::new(evidence.report)?;
    let var_data_hash = hcl_report.var_data_sha256();
    let snp_report: SnpReport = hcl_report.try_into()?;
    let report = serde_json::to_value(snp_report)?;

    // Get and validate certificate chain
    let cert_chain = amd::kds::get_cert_chain(&snp_report)?;
    let vcek = Vcek::from_pem(&evidence.certs.vcek)?;

    // Validate certificates and report
    cert_chain.validate()?;
    vcek.validate(&cert_chain)?;
    snp_report.validate(&vcek)?;

    // Verify var_data_hash matches report_data
    if var_data_hash != snp_report.report_data[..32] {
        return Err(anyhow!("Report data hash doesn't match!"));
    }

    if let Some(custom_data) = custom_data
        && custom_data != evidence.report_data
    {
        return Err(anyhow!("Custom data hash doesn't match!"));
    }

    Ok(VerificationResult {
        report,
        certs,
        report_data: evidence.report_data,
    })
}

pub(crate) struct ProvenanceVerification {
    pub(crate) checksum: String,
}

pub(crate) fn verify_provenance(provenance: Vec<u8>) -> Result<ProvenanceVerification> {
    let provenance_value: serde_json::Value = serde_json::from_slice(&provenance)?;
    let checksum = hex::encode(Sha256::digest(provenance_value.to_string()));
    let verification = ProvenanceVerification { checksum };
    Ok(verification)
}

struct Build {
    provenance: Vec<u8>,
    evidence: Vec<u8>,
}

impl Build {
    fn from_dir(path: String) -> Result<Build> {
        let project_dir = fs_err::canonicalize(&path)?;
        let evidence = fs_err::read(project_dir.join("evidence.b64"))?;
        let provenance = fs_err::read(project_dir.join("provenance.json"))?;
        let build = Build {
            provenance,
            evidence,
        };

        Ok(build)
    }
}

pub(crate) fn verify(path: String) -> Result<()> {
    let build = Build::from_dir(path)?;

    let provenance_verification =
        verify_provenance(build.provenance).expect("Provenance was not valid");

    match verify_attestation(build.evidence, Some(provenance_verification.checksum)) {
        Ok(_) => Ok(()),
        Err(e) => Err(e),
    }
}
