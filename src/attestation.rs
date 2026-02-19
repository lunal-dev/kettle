use std::io::Read;

use anyhow::Result;
use base64::Engine;
use base64::prelude::BASE64_STANDARD;
use serde::{Deserialize, Serialize};
use sev::firmware::guest::AttestationReport as SnpReport;

use crate::amd;
use crate::amd::certs::Vcek;
use crate::amd::snp_report::Validateable;
use crate::commands::verify::Verification;
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
    pub fn from_base64(bytes_b64: &[u8]) -> Result<Self> {
        let bytes_gz = BASE64_STANDARD.decode(bytes_b64)?;
        let mut evidence_bytes = Vec::new();
        flate2::read::MultiGzDecoder::new(&bytes_gz[..]).read_to_end(&mut evidence_bytes)?;
        Ok(bincode::deserialize(&evidence_bytes[..])?)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Attestation {
    pub evidence: AttestationEvidence,
    pub data_hash: [u8; 32],
    pub report: SnpReport,
    pub certs: serde_json::Value,
}

impl Attestation {
    pub fn from_base64(bytes: &[u8]) -> Result<Self> {
        let evidence = AttestationEvidence::from_base64(bytes)?;
        let certs = serde_json::to_value(&evidence.certs)?;

        let hcl_report = HclReport::new(evidence.report.clone())?;
        let data_hash = hcl_report.var_data_sha256();
        let report: SnpReport = hcl_report.try_into()?;

        Ok(Attestation {
            evidence,
            data_hash,
            report,
            certs,
        })
    }

    pub fn verify(&self) -> Result<Vec<Verification>> {
        let mut results: Vec<Verification> = vec![];
        let cert_chain = amd::kds::get_cert_chain(&self.report)?;
        let vcek = Vcek::from_pem(&self.evidence.certs.vcek)?;

        // Validate the certificate chain
        let subject = "AMD certificate chain";
        results.push(match cert_chain.validate() {
            Ok(_) => Verification::success(&format!("{subject} is valid")),
            Err(e) => Verification::failure(&format!("{subject} is not valid"), &format!("{}", e)),
        });

        // Validate the vcek cert against the cert chain
        let subject = "VCEK certificate";
        results.push(match vcek.validate(&cert_chain) {
            Ok(_) => Verification::success(&format!("{subject} signed by AMD cert")),
            Err(e) => Verification::failure(
                &format!("{subject} not signed by AMD cert"),
                &format!("{}", e),
            ),
        });

        let subject = "SEV-SNP report";
        results.push(match self.report.validate(&vcek) {
            Ok(_) => Verification::success(&format!("{subject} valid and signed by VCEK")),
            Err(e) => {
                Verification::failure(&format!("{subject} failed verification"), &format!("{}", e))
            }
        });

        // Validate the report data checksum
        let subject = "SEV-SNP embedded data";
        results.push(match self.data_hash == self.report.report_data[..32] {
            true => Verification::success(&format!("{subject} checksum match")),
            false => Verification::failure(
                &format!("{subject} checksum mismatch"),
                &format!(
                    "Expected checksum {}\nActual checskum   {}",
                    hex::encode(&self.report.report_data[..32]),
                    hex::encode(self.data_hash)
                ),
            ),
        });

        Ok(results)
    }

    pub fn verify_provenance(&self, checksum: &str) -> Verification {
        match checksum == self.evidence.report_data {
            true => Verification::success("Provenance checksum match"),
            false => Verification::failure(
                "Provenance checksum mismatch",
                &format!(
                    "Expected provenance.json checksum {:?}\nActual provenance.json checksum   {:?}",
                    self.evidence.report_data, checksum
                ),
            ),
        }
    }
}
