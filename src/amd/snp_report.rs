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
use crate::hcl::{HclReport, MAX_REPORT_SIZE};
use crate::{amd, hcl::AttestationReport};

#[derive(thiserror::Error, Debug)]
pub enum ValidateError {
    #[error("TCB data is not valid")]
    Tcb,
    #[error("Measurement signature is not valid")]
    MeasurementSignature,
    #[error("Unsupported public key algorithm")]
    UnsupportedPublicKeyAlgorithm,
    #[error("IO error")]
    Io(#[from] std::io::Error),
    #[error("Elliptic curve error: {0}")]
    EllipticCurve(#[from] p384::elliptic_curve::Error),
    #[error("bincode error")]
    Bincode(#[from] Box<bincode::ErrorKind>),
}

pub trait Validateable {
    fn validate(&self, vcek: &Vcek) -> Result<(), ValidateError>;
}

impl Validateable for SnpReport {
    fn validate(&self, vcek: &Vcek) -> Result<(), ValidateError> {
        if self.reported_tcb != self.committed_tcb {
            return Err(ValidateError::Tcb);
        }

        let signature = Signature::try_from(&self.signature)?;
        let public_key_info = &vcek.0.tbs_certificate.subject_public_key_info;

        // Ensure this is an EC public key
        const EC_PUBLIC_KEY_OID: x509_cert::der::oid::ObjectIdentifier =
            x509_cert::der::oid::ObjectIdentifier::new_unwrap("1.2.840.10045.2.1");

        if public_key_info.algorithm.oid != EC_PUBLIC_KEY_OID {
            return Err(ValidateError::UnsupportedPublicKeyAlgorithm);
        }

        // Parse the EC public key using P-384
        let public_key_bytes = public_key_info.subject_public_key.raw_bytes();
        let public_key = PublicKey::from_sec1_bytes(public_key_bytes)?;
        let verifying_key = VerifyingKey::from(&public_key);

        // Get the measurable bytes (first 0x2A0 bytes of serialized report)
        let base_message = get_report_base(self)?;

        let digest = Sha384::new_with_prefix(&base_message);
        if verifying_key.verify_digest(digest, &signature).is_err() {
            return Err(ValidateError::MeasurementSignature);
        }

        Ok(())
    }
}

fn get_report_base(report: &SnpReport) -> Result<Vec<u8>, Box<bincode::ErrorKind>> {
    // Use sev's write_bytes method (since SEV-6) for serializing SNP reports to ensure full compatibility
    // Original bincode::serialize + size_of calculation is inaccurate on SEV 6.x
    let mut raw_bytes = Vec::with_capacity(MAX_REPORT_SIZE);
    report
        .encode(&mut raw_bytes, ())
        .map_err(|e| Box::new(bincode::ErrorKind::Io(e)))?;
    let report_bytes_without_sig = &raw_bytes[0..0x2a0];
    Ok(report_bytes_without_sig.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;
    use hcl::HclReport;

    #[test]
    fn test_report_data_hash() {
        let bytes: &[u8] = include_bytes!("../../vtpm-attestation/test/hcl-report-snp.bin");
        let hcl_report = HclReport::new(bytes.to_vec()).unwrap();
        let var_data_hash = hcl_report.var_data_sha256();
        let snp_report: AttestationReport = hcl_report.try_into().unwrap();
        assert!(var_data_hash == snp_report.report_data[..32]);
    }
}
