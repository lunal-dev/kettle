use anyhow::Result;
use base64::{Engine, prelude::BASE64_STANDARD};
use ecdsa::{Signature, VerifyingKey};
use p384::PublicKey;
use serde::{Deserialize, Serialize};
use sev::parser::Encoder;
use sev::{firmware::guest::AttestationReport as SnpReport, parser::ByteParser};
use sha2::{Digest, Sha384};
use signature::DigestVerifier;
use std::io::Read;
use std::vec::Vec;

use crate::amd::certs::Vcek;
use crate::amd::snp_report::Validateable;
use crate::hcl::{HclReport, MAX_REPORT_SIZE};
use crate::{amd, hcl::AttestationReport};

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
    pub report_data: Vec<u8>,
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

pub fn verify(evidence_b64: String) -> Result<VerificationResult> {
    let evidence_gz = BASE64_STANDARD.decode(evidence_b64)?;
    let mut evidence_bytes = Vec::new();
    flate2::read::MultiGzDecoder::new(&evidence_gz[..]).read_to_end(&mut evidence_bytes)?;
    let evidence = AttestationEvidence::from_bytes(&evidence_bytes[..])?;

    let certs_json = serde_json::to_value(&evidence.certs)?;
    let report_data = String::from_utf8_lossy(&evidence.report_data).to_string();

    let hcl_report = HclReport::new(evidence.report)?;
    let var_data_hash = hcl_report.var_data_sha256();
    let snp_report: SnpReport = hcl_report.try_into()?;
    let report_json = serde_json::to_value(snp_report)?;

    // Get and validate certificate chain
    let cert_chain = amd::kds::get_cert_chain(&snp_report)?;
    let vcek = Vcek::from_pem(&evidence.certs.vcek)?;

    // Validate certificates and report
    cert_chain.validate()?;
    vcek.validate(&cert_chain)?;
    snp_report.validate(&vcek)?;

    // // Verify var_data_hash matches report_data
    // if var_data_hash != snp_report.report_data[..32] {
    //     return Err("Variable data hash mismatch".into());
    // }

    // if check_custom_data.unwrap_or(false) {
    //     let data_vec: Vec<u8> = custom_data.into();
    //     if data_vec != report_data {
    //         return Err("Variable data hash mismatch".into());
    //     }
    // }

    let result = VerificationResult {
        report: report_json,
        certs: certs_json,
        report_data,
    };

    Ok(result)
}
