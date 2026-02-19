use anyhow::Result;
use base64::{Engine, prelude::BASE64_STANDARD};
use colored::Colorize;
use fs_err::DirEntry;
use serde::{Deserialize, Serialize};
use sev::firmware::guest::AttestationReport as SnpReport;
use std::io::Read;
use std::path::PathBuf;
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

struct Build {
    provenance_bytes: Vec<u8>,
    evidence_bytes: Vec<u8>,
    artifacts: Vec<DirEntry>,
}

impl Build {
    fn from_dir(path: &PathBuf) -> Result<Build> {
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

pub(crate) fn verify(path: PathBuf) -> Result<()> {
    let build = Build::from_dir(&path)?;

    // Get the provenance and attestation
    let provenance: Provenance = serde_json::from_slice(&build.provenance_bytes)?;
    let _attestation = verify_attestation(build.evidence_bytes, Some(provenance.checksum()))?;

    let mut results: Vec<Verification> = vec![];
    results.push(provenance.verify_type());
    results.push(provenance.verify_predicate());
    results.extend(provenance.verify_artifacts(&build.artifacts));

    // Print build information
    let header = format!(
        "\n{} {}\n",
        "Verifying build dir".bold(),
        &path.file_name().unwrap().to_string_lossy()
    );
    let build_id = "Build ID".bold().to_string();
    let built_at = "Built at".bold().to_string();
    let toolchain = "Built with".bold().to_string();
    print_table(
        &vec![header],
        &vec![
            vec![&build_id, provenance.build_id()],
            vec![&built_at, provenance.timestamp()],
            vec![&toolchain, &format!("{}", provenance.toolchain())],
        ],
        &vec![],
    );

    // Print verification results
    let summary = if results
        .iter()
        .any(|r| matches!(r, Verification::Failure { .. }))
    {
        format!("⛔️ {}", "Verification FAILED".red())
    } else {
        format!("✅ {}", "Verification PASSED".green())
    };
    let rows: &Vec<Vec<&str>> = &results
        .iter()
        .map(|r| match r {
            Verification::Success { message } => vec!["✅", &message],
            Verification::Failure {
                message,
                details: _,
            } => vec!["⛔️", &message],
        })
        .collect();

    let headers = vec![format!("{}", "Verification Results".bold())];
    let footers = vec![summary];
    print_table(&headers, rows, &footers);

    // Print detailed information about failures (if any)
    for r in results {
        match r {
            Verification::Success { .. } => (),
            Verification::Failure { message, details } => {
                eprintln!("{}\n{}\n", message.red().bold(), details);
            }
        }
    }
    Ok(())
}

fn print_table(headers: &Vec<String>, rows: &Vec<Vec<&str>>, footers: &Vec<String>) {
    let mut b = Builder::with_capacity(rows.len(), 2);
    for row in rows {
        b.push_record(row.clone());
    }

    let mut table = b.build();
    table.modify(Columns::first(), Alignment::center());
    table.with(Style::modern());
    for footer in footers {
        table.with(Panel::footer(footer));
    }
    for header in headers {
        table.with(Panel::header(header));
    }
    table.with(BorderCorrection::span());
    println!("{}\n", table);
}
