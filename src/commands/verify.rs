use anyhow::Result;
use attestation::VerificationResult;
use colored::Colorize;
use fs_err::DirEntry;
use std::path::PathBuf;
use std::vec::Vec;
use tabled::builder::Builder;
use tabled::settings::object::Columns;
use tabled::settings::themes::BorderCorrection;
use tabled::settings::{Alignment, Panel, Style};

use crate::provenance::Provenance;

pub async fn verify(path: &PathBuf, verbose: bool) -> Result<()> {
    let build = Build::from_dir(path)?;

    // Get the provenance and attestation
    let provenance = Provenance::from_json(&build.provenance_bytes)?;
    let verification = attestation::verify(&build.evidence_bytes, &Default::default()).await?;

    let mut results: Vec<Verification> = vec![];
    results.push(verify_signature(&verification));
    results.push(provenance.verify_predicate());
    results.push(verify_report_data(&verification, &provenance));
    results.extend(provenance.verify_artifacts(&build.artifacts)?);

    // Print build information
    print_table(
        vec![format!(
            "\n{} {}\n",
            "Verifying build dir".bold(),
            &path.file_name().unwrap().to_string_lossy()
        )],
        vec![
            vec!["Build ID".bold().to_string(), provenance.build_id().clone()],
            vec![
                "Built at".bold().to_string(),
                provenance.timestamp().clone(),
            ],
            vec![
                "Built with".bold().to_string(),
                format!("{}", provenance.toolchain()),
            ],
            vec![
                "Git commit".bold().to_string(),
                format!("{}", provenance.git_commit()),
            ],
        ],
        vec![],
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
    let rows = results
        .iter()
        .map(|r| match r {
            Verification::Success { message } => vec!["✅".to_string(), message.clone()],
            Verification::Failure {
                message,
                details: _,
            } => vec!["⛔️".to_string(), message.clone()],
        })
        .collect();
    let headers = vec![format!("{}", "Verification Results".bold())];
    let footers = vec![summary];
    print_table(headers, rows, footers);

    // Print detailed information about failures (if any)
    for r in results {
        match r {
            Verification::Success { .. } => (),
            Verification::Failure { message, details } => {
                eprintln!("{}\n{}\n", message.red().bold(), details);
            }
        }
    }

    if verbose {
        println!("{}\n{:?}", "Attestation claims".bold(), &verification);
        println!();
    }

    Ok(())
}

fn verify_signature(verification_result: &VerificationResult) -> Verification {
    match verification_result.signature_valid {
        true => Verification::success("Attestation hardware signature valid"),
        false => Verification::failure(
            "Attestation hardware signature invalid",
            "signature verification failed",
        ),
    }
}

fn verify_report_data(
    verification_result: &VerificationResult,
    provenance: &Provenance,
) -> Verification {
    let data_value = verification_result
        .claims
        .platform_data
        .pointer("/tpm/nonce");
    let checksum = hex::encode(provenance.checksum());

    if let Some(report_data) = data_value {
        match *report_data == checksum {
            true => Verification::success("Provenance checksum match"),
            false => Verification::failure(
                "Provenance checksum mismatch",
                &format!(
                    "Expected provenance.json checksum {:?}\nActual provenance.json checksum   {:?}",
                    report_data, checksum
                ),
            ),
        }
    } else {
        Verification::failure(
            "Provenance checksum missing",
            "Expected to validate provenance.json checksum, but no checksum was present in the attestation.",
        )
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
        let evidence_bytes = fs_err::read(project_dir.join("evidence.json"))?;
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

pub enum Verification {
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

fn print_table(headers: Vec<String>, rows: Vec<Vec<String>>, footers: Vec<String>) {
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
