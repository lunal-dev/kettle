use der::Encode;
use p256::{PublicKey as P256PublicKey, ecdsa::VerifyingKey as P256VerifyingKey};
use p384::{PublicKey as P384PublicKey, ecdsa::VerifyingKey as P384VerifyingKey}; // Add P-384 support
use pem::{parse, parse_many};
use rsa::pkcs1::DecodeRsaPublicKey;
use rsa::{RsaPublicKey, pkcs1v15::VerifyingKey, pss::VerifyingKey as PssVerifyingKey};
use sha2::{Digest, Sha256, Sha384};
use signature::Verifier;
use thiserror::Error;
pub use x509_cert::Certificate;
use x509_cert::der::Decode;
use x509_cert::der::oid::ObjectIdentifier;
// Common signature algorithm OIDs
const RSA_WITH_SHA256: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.113549.1.1.11");
const RSA_WITH_SHA384: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.113549.1.1.12");
const RSA_PSS: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.113549.1.1.10");
const ECDSA_WITH_SHA256: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.10045.4.3.2");
const ECDSA_WITH_SHA384: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.10045.4.3.3"); // Add SHA-384

// Public key algorithm OIDs
const RSA_ENCRYPTION: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.113549.1.1.1");
const EC_PUBLIC_KEY: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.10045.2.1");

pub struct AmdChain {
    pub ask: Certificate,
    pub ark: Certificate,
}

#[derive(Error, Debug)]
pub enum ValidateError {
    #[error("X.509 certificate error: {0}")]
    X509(#[from] x509_cert::der::Error),
    #[error("RSA key error: {0}")]
    Rsa(#[from] rsa::Error),
    #[error("RSA PKCS#1 error: {0}")]
    RsaPkcs1(#[from] rsa::pkcs1::Error),
    #[error("ECDSA key error: {0}")]
    Ecdsa(#[from] p256::elliptic_curve::Error),
    #[error("Signature verification failed")]
    SignatureVerificationFailed,
    #[error("Unsupported signature algorithm: {0}")]
    UnsupportedAlgorithm(ObjectIdentifier),
    #[error("Unsupported public key algorithm: {0}")]
    UnsupportedPublicKeyAlgorithm(ObjectIdentifier),
    #[error("Invalid public key format")]
    InvalidPublicKeyFormat,
    #[error("ARK is not self-signed")]
    ArkNotSelfSigned,
    #[error("ASK is not signed by ARK")]
    AskNotSignedByArk,
    #[error("VCEK is not signed by ASK")]
    VcekNotSignedByAsk,
}

impl AmdChain {
    pub fn validate(&self) -> Result<(), ValidateError> {
        // Verify ARK is self-signed
        if !self.verify_signature(&self.ark, &self.ark)? {
            return Err(ValidateError::ArkNotSelfSigned);
        }

        // Verify ASK is signed by ARK
        if !self.verify_signature(&self.ask, &self.ark)? {
            return Err(ValidateError::AskNotSignedByArk);
        }

        Ok(())
    }

    fn verify_signature(
        &self,
        cert_to_verify: &Certificate,
        signing_cert: &Certificate,
    ) -> Result<bool, ValidateError> {
        let public_key_info = &signing_cert.tbs_certificate.subject_public_key_info;
        let signature_algorithm = &cert_to_verify.signature_algorithm;
        let signature = cert_to_verify.signature.raw_bytes();

        // Get the TBS (To Be Signed) certificate data
        let tbs_cert_der = cert_to_verify.tbs_certificate.to_der()?;

        // Handle different signature algorithms
        match signature_algorithm.oid {
            RSA_WITH_SHA256 => {
                // Extract RSA public key
                if public_key_info.algorithm.oid != RSA_ENCRYPTION {
                    return Err(ValidateError::UnsupportedPublicKeyAlgorithm(
                        public_key_info.algorithm.oid,
                    ));
                }
                let rsa_key =
                    RsaPublicKey::from_pkcs1_der(public_key_info.subject_public_key.raw_bytes())?;
                let verifying_key = VerifyingKey::<Sha256>::new(rsa_key);
                let signature = rsa::pkcs1v15::Signature::try_from(signature)
                    .map_err(|_| ValidateError::SignatureVerificationFailed)?;
                Ok(verifying_key.verify(&tbs_cert_der, &signature).is_ok())
            }
            RSA_WITH_SHA384 => {
                // Extract RSA public key
                if public_key_info.algorithm.oid != RSA_ENCRYPTION {
                    return Err(ValidateError::UnsupportedPublicKeyAlgorithm(
                        public_key_info.algorithm.oid,
                    ));
                }
                let rsa_key =
                    RsaPublicKey::from_pkcs1_der(public_key_info.subject_public_key.raw_bytes())?;
                let verifying_key = VerifyingKey::<Sha384>::new(rsa_key);
                let signature = rsa::pkcs1v15::Signature::try_from(signature)
                    .map_err(|_| ValidateError::SignatureVerificationFailed)?;
                Ok(verifying_key.verify(&tbs_cert_der, &signature).is_ok())
            }
            RSA_PSS => {
                // Extract RSA public key for PSS
                if public_key_info.algorithm.oid != RSA_ENCRYPTION {
                    return Err(ValidateError::UnsupportedPublicKeyAlgorithm(
                        public_key_info.algorithm.oid,
                    ));
                }

                let rsa_key =
                    RsaPublicKey::from_pkcs1_der(public_key_info.subject_public_key.raw_bytes())?;
                let signature_pss = rsa::pss::Signature::try_from(signature)
                    .map_err(|_| ValidateError::SignatureVerificationFailed)?;

                // Try SHA-256 first
                let verifying_key_256 = PssVerifyingKey::<Sha256>::new(rsa_key.clone());
                if verifying_key_256
                    .verify(&tbs_cert_der, &signature_pss)
                    .is_ok()
                {
                    return Ok(true);
                }

                // Try SHA-384
                let verifying_key_384 = PssVerifyingKey::<Sha384>::new(rsa_key);
                Ok(verifying_key_384
                    .verify(&tbs_cert_der, &signature_pss)
                    .is_ok())
            }
            ECDSA_WITH_SHA256 => {
                self.verify_ecdsa_signature(cert_to_verify, signing_cert, signature, false)
            }
            ECDSA_WITH_SHA384 => {
                self.verify_ecdsa_signature(cert_to_verify, signing_cert, signature, true)
            }
            oid => Err(ValidateError::UnsupportedAlgorithm(oid)),
        }
    }

    fn verify_ecdsa_signature(
        &self,
        cert_to_verify: &Certificate,
        signing_cert: &Certificate,
        signature: &[u8],
        use_sha384: bool,
    ) -> Result<bool, ValidateError> {
        let public_key_info = &signing_cert.tbs_certificate.subject_public_key_info;

        if public_key_info.algorithm.oid != EC_PUBLIC_KEY {
            return Err(ValidateError::UnsupportedPublicKeyAlgorithm(
                public_key_info.algorithm.oid,
            ));
        }

        let public_key_bytes = public_key_info.subject_public_key.raw_bytes();
        let tbs_cert_der = cert_to_verify.tbs_certificate.to_der()?;

        if use_sha384 {
            // Use P-384 for SHA-384 (like your working implementation)
            let p384_key = P384PublicKey::from_sec1_bytes(public_key_bytes)?;
            let verifying_key = P384VerifyingKey::from(&p384_key);
            let signature = p384::ecdsa::Signature::try_from(signature)
                .map_err(|_| ValidateError::SignatureVerificationFailed)?;

            // Create digest with prefix (like your working implementation)
            let digest = Sha384::new_with_prefix(&tbs_cert_der);

            // Use DigestVerifier instead of regular Verifier
            use p384::ecdsa::signature::DigestVerifier;
            Ok(verifying_key.verify_digest(digest, &signature).is_ok())
        } else {
            // Use P-256 for SHA-256
            let p256_key = P256PublicKey::from_sec1_bytes(public_key_bytes)?;
            let verifying_key = P256VerifyingKey::from(&p256_key);
            let signature = p256::ecdsa::Signature::try_from(signature)
                .map_err(|_| ValidateError::SignatureVerificationFailed)?;

            // Create digest with prefix
            let digest = Sha256::new_with_prefix(&tbs_cert_der);

            // Use DigestVerifier
            use p256::ecdsa::signature::DigestVerifier;
            Ok(verifying_key.verify_digest(digest, &signature).is_ok())
        }
    }
}

pub struct Vcek(pub Certificate);

impl Vcek {
    pub fn from_pem(pem: &str) -> Result<Self, ParseError> {
        let pem_obj = parse(pem.as_bytes())?;
        let cert = Certificate::from_der(&pem_obj.contents())?;
        Ok(Self(cert))
    }

    pub fn validate(&self, amd_chain: &AmdChain) -> Result<(), ValidateError> {
        if !amd_chain.verify_signature(&self.0, &amd_chain.ask)? {
            return Err(ValidateError::VcekNotSignedByAsk);
        }
        Ok(())
    }
}

#[derive(Error, Debug)]
pub enum ParseError {
    #[error("X.509 certificate error: {0}")]
    X509(#[from] x509_cert::der::Error),
    #[error("PEM parsing error: {0}")]
    Pem(#[from] pem::PemError),
    #[error("wrong amount of certificates (expected {0:?}, found {1:?})")]
    WrongAmount(usize, usize),
}

/// build ASK + ARK certificate chain from a multi-pem string
pub fn build_cert_chain(pem: &str) -> Result<AmdChain, ParseError> {
    let pem_objects = parse_many(pem.as_bytes())?;

    if pem_objects.len() != 2 {
        return Err(ParseError::WrongAmount(2, pem_objects.len()));
    }

    let ask = Certificate::from_der(&pem_objects[0].contents())?;
    let ark = Certificate::from_der(&pem_objects[1].contents())?;

    let chain = AmdChain { ask, ark };

    Ok(chain)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_certificates() {
        let bytes = include_bytes!("../../test/files/certs.pem");
        let pem_str = std::str::from_utf8(bytes).unwrap();
        let pem_objects = parse_many(pem_str.as_bytes()).unwrap();

        let vcek = Certificate::from_der(&pem_objects[0].contents()).unwrap();
        let ask = Certificate::from_der(&pem_objects[1].contents()).unwrap();
        let ark = Certificate::from_der(&pem_objects[2].contents()).unwrap();

        let vcek = Vcek(vcek);
        let cert_chain = AmdChain { ask, ark };
        cert_chain.validate().unwrap();
        vcek.validate(&cert_chain).unwrap();
    }
}
