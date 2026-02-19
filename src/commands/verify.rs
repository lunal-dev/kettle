use anyhow::Result;
use base64::{Engine, prelude::BASE64_STANDARD};
use colored::Colorize;
use fs_err::DirEntry;
use serde::{Deserialize, Serialize};
use sev::firmware::guest::AttestationReport as SnpReport;
use std::io::Read;
use std::vec::Vec;
use tabled::builder::Builder;
use tabled::settings::object::Columns;
use tabled::settings::themes::BorderCorrection;
use tabled::settings::{Alignment, Panel, Style};

use crate::amd;
use crate::amd::certs::Vcek;
use crate::amd::snp_report::Validateable;
use crate::hcl::HclReport;
use crate::provenance::Provenance;

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
    pub report: serde_json::Value,
    pub certs: serde_json::Value,
    pub report_data: String,
}

pub fn verify_attestation(bytes: Vec<u8>, custom_data: Option<String>) -> Result<Attestation> {
    let evidence = AttestationEvidence::from_base64(&bytes)?;
    let certs = serde_json::to_value(&evidence.certs)?;

    let hcl_report = HclReport::new(evidence.report)?;
    let var_data_hash = hcl_report.var_data_sha256();
    let snp_report: SnpReport = hcl_report.try_into()?;
    let report = serde_json::to_value(snp_report)?;

    // Get and validate certificate chain
    let cert_chain = amd::kds::get_cert_chain(&snp_report)?;
    let vcek = Vcek::from_pem(&evidence.certs.vcek)?;

    // Validate certificates and report
    print_result(
        cert_chain.validate().is_ok(),
        "AMD certificate chain is valid",
        "AMD certificate chain is not valid",
    );
    print_result(
        vcek.validate(&cert_chain).is_ok(),
        "Vcek valid",
        "Vcek invalid",
    );
    print_result(
        snp_report.validate(&vcek).is_ok(),
        "SNP valid",
        "SNP invalid",
    );
    print_result(
        var_data_hash == snp_report.report_data[..32],
        "Report data checksum matches attestation checksum",
        "Report data checksum does not match checksum",
    );

    if let Some(data) = custom_data {
        print_result(
            data == evidence.report_data,
            "Custom data checksum matches attestation checksum",
            "Custom data checksum doesn't match attestation checksum!",
        );
    }

    Ok(Attestation {
        report,
        certs,
        report_data: evidence.report_data,
    })
}

pub(crate) fn print_result(check: bool, success: &str, failure: &str) {
    if check {
        println!("✅ {}", success.green())
    } else {
        println!("⛔️ {}", failure.red())
    }
}

#[derive(thiserror::Error, Debug)]
pub enum ProvenanceError {
    #[error("invalid provenance _type value {}", 0)]
    InvalidType(String),
    #[error("invalid predicateType value {}", 0)]
    InvalidPredicate(String),
}

pub(crate) fn verify_provenance(data: Vec<u8>) -> Result<Provenance> {
    // Parsed successfully, so it has the exact structure we need
    let provenance: Provenance = serde_json::from_slice(&data)?;

    if provenance._type != "https://in-toto.io/Statement/v1" {
        return Err(ProvenanceError::InvalidType(provenance._type).into());
    }

    if provenance.predicate_type != "https://slsa.dev/provenance/v1" {
        return Err(ProvenanceError::InvalidPredicate(provenance.predicate_type).into());
    }

    Ok(provenance)

    // let build_type = &value["predicate"]["buildDefinition"]["buildType"];
    // let run_details = &value["predicate"]["runDetails"];
    // let builder = &value["predicate"]["runDetails"]["builder"]["id"];
    // let build_id = &value["predicate"]["runDetails"]["metadata"]["invocationId"];
    // let timestamp = &value["predicate"]["runDetails"]["metadata"]["startedOn"];
    // let merkle_tree_root = &value["predicate"]["runDetails"]["byproducts"][0]["digest"]["sha256"];
    // println!("{}", serde_json::to_string(&provenance)?);
    // Ok(ProvenanceResult {
    //     checksum: provenance.checksum()?,
    //     format: provenance::Format::SLSAv1dot2,
    //     toolchain: provenance.toolchain().clone(),
    //     merkle_tree_root: [
    //         1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    //         1, 1, 1,
    //     ],
    // })
}

struct Build {
    provenance_bytes: Vec<u8>,
    evidence_bytes: Vec<u8>,
    artifacts: Vec<DirEntry>,
}

impl Build {
    fn from_dir(path: &str) -> Result<Build> {
        let project_dir = fs_err::canonicalize(path)?;
        let evidence_bytes = fs_err::read(project_dir.join("evidence.b64"))?;
        let provenance_bytes = fs_err::read(project_dir.join("provenance.json"))?;
        let artifacts = fs_err::read_dir(project_dir.join("artifacts"))?
            .filter_map(|e| e.ok())
            .collect();

        let build = Build {
            provenance_bytes,
            evidence_bytes,
            artifacts,
        };

        Ok(build)
    }
}

pub(crate) enum Verification {
    Success { message: String },
    Failure { message: String, details: String },
}

impl Verification {
    pub fn success(message: &str) -> Self {
        Self::Success {
            message: message.to_owned(),
        }
    }

    pub fn failure(message: &str, details: &str) -> Self {
        Self::Failure {
            message: message.to_owned(),
            details: details.to_owned(),
        }
    }
}

pub(crate) fn verify(path: String) -> Result<()> {
    let build = Build::from_dir(&path)?;
    let mut results: Vec<Verification> = vec![];

    // Get the provenance and attestation results
    let provenance = verify_provenance(build.provenance_bytes)?;
    let _attestation = verify_attestation(build.evidence_bytes, Some(provenance.checksum()))?;

    results.push(provenance.verify_predicate());
    results.extend(provenance.verify_artifacts(&build.artifacts));

    let mut b = Builder::with_capacity(0, 0);
    for result in &results {
        match result {
            Verification::Success { message } => b.push_record(["✅", message]),
            Verification::Failure {
                message,
                details: _,
            } => b.push_record(["⛔️", message]),
        }
    }

    let result = if results
        .iter()
        .any(|r| matches!(r, Verification::Failure { .. }))
    {
        format!("⛔️ {}", "Verification FAILED".red())
    } else {
        format!("✅ {}", "Verification PASSED".green())
    };

    // Format and print the attestation results
    let header = format!("\n{} {}\n", "Verifying".bold(), &path);
    let build_id = format!("{} {}", "Build ID".bold(), provenance.build_id(),);
    let built_at = format!("{} {}", "Built at".bold(), provenance.timestamp(),);
    let toolchain = format!("{} {}", "Built with".bold(), provenance.toolchain());

    let mut table = b.build();
    table.modify(Columns::first(), Alignment::center());
    table.with(Style::modern());
    table.with(Panel::footer(result));
    table.with(Panel::header(build_id));
    table.with(Panel::header(built_at));
    table.with(Panel::header(toolchain));
    table.with(Panel::header(header));
    table.with(BorderCorrection::span());
    println!("{}\n", table);

    Ok(())
}
