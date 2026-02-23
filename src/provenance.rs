use anyhow::Result;
use fs_err::DirEntry;
use serde::{Deserialize, Serialize};
use serde_json::Number;
use sha2::{Digest as _, Sha256};
use std::fmt::Display;

use crate::commands::verify::Verification;

#[derive(Serialize, Deserialize)]
pub struct Provenance {
    pub _type: String,
    pub predicate: Predicate,
    #[serde(rename = "predicateType")]
    pub predicate_type: String,
    pub subject: Vec<Subject>,
}

impl Provenance {
    pub fn from_json(bytes: &[u8]) -> Result<Self> {
        Ok(serde_json::from_slice(bytes)?)
    }

    pub fn from_dir(path: PathBuf) -> Result<Self> {
        Ok(Provenance {
            _type: "".to_string(),
            predicate: Predicate {
                build_definition: todo!(),
                run_details: todo!(),
            },
            predicate_type: "".to_string(),
            subject: vec![Subject {
                digest: todo!(),
                name: todo!(),
            }],
        })
    }

    pub fn checksum(&self) -> String {
        let json = serde_json::to_string(&self).expect("could not generate JSON");
        hex::encode(Sha256::digest(json))
    }

    pub fn toolchain(&self) -> &Toolchain {
        &self
            .predicate
            .build_definition
            .internal_parameters
            .toolchain
    }

    pub fn build_id(&self) -> &String {
        &self.predicate.run_details.metadata.invocation_id
    }

    pub fn timestamp(&self) -> &String {
        &self.predicate.run_details.metadata.started_on
    }

    pub fn git_commit(&self) -> &String {
        &self
            .predicate
            .build_definition
            .external_parameters
            .source
            .digest
            .git_commit
    }

    pub fn verify_type(&self) -> Verification {
        let expected = "https://in-toto.io/Statement/v1";
        if self._type == expected {
            Verification::success("Provenance type is in-toto v1")
        } else {
            Verification::failure(
                "Provenance type not in-toto v1",
                &format!(
                    "Expected _type {:?}\nActual _type   {:?}",
                    expected, &self._type
                ),
            )
        }
    }
    pub fn verify_predicate(&self) -> Verification {
        let expected = "https://slsa.dev/provenance/v1";
        if self.predicate_type == expected {
            Verification::success("Provenance predicateType is SLSA v1")
        } else {
            Verification::failure(
                "Provenance predicateType not SLSA v1",
                &format!(
                    "Expected predicateType {:?}\nActual predicateType   {:?}",
                    expected, &self.predicate_type
                ),
            )
        }
    }

    pub fn verify_artifacts(&self, artifacts: &[DirEntry]) -> Result<Vec<Verification>> {
        artifacts
            .iter()
            .map(|entry| {
                let name = entry.file_name().to_string_lossy().into_owned();
                let checksum = Sha256::digest(fs_err::read(entry.path())?);
                let subject = self.subject.iter().find(|s| s.name == name);
                if let Some(subject) = subject {
                    if hex::encode(checksum) == subject.digest.sha256 {
                        Ok(Verification::success(&format!(
                            "Checksum match for binary `{}`",
                            name
                        )))
                    } else {
                        Ok(Verification::failure(
                            "Checksum mismatch for `{}`!",
                            &format!(
                                "Provenance did not contain checksum for binary named {:?}",
                                name
                            ),
                        ))
                    }
                } else {
                    Ok(Verification::failure(
                        &format!("Checksum missing for `{}`!", name),
                        &format!(
                            "Provenance did not contain checksum for binary named {:?}",
                            name
                        ),
                    ))
                }
            })
            .collect()
    }
}

#[derive(Serialize, Deserialize)]
pub(crate) struct Subject {
    pub(crate) digest: Digest,
    pub(crate) name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct Digest {
    pub(crate) sha256: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Predicate {
    pub(crate) build_definition: BuildDefiniton,
    pub(crate) run_details: RunDetails,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BuildDefiniton {
    pub(crate) build_type: String,
    pub(crate) external_parameters: ExternalParameters,
    pub(crate) internal_parameters: InternalParameters,
    pub(crate) resolved_dependencies: Vec<ResolvedDependency>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ResolvedDependency {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) annotations: Option<Annotation>,
    pub(crate) digest: Digest,
    pub(crate) name: String,
    #[serde(skip)]
    pub(crate) version: String,
    pub(crate) uri: String,
}

#[derive(Serialize, Deserialize)]
pub(crate) struct RunDetails {
    pub(crate) builder: Builder,
    pub(crate) byproducts: Vec<Byproduct>,
    pub(crate) metadata: Metadata,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ExternalParameters {
    pub(crate) build_command: String,
    pub(crate) source: Source,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Source {
    pub(crate) digest: SourceDigest,
    pub(crate) uri: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SourceDigest {
    pub(crate) git_commit: String,
    pub(crate) git_tree: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct InternalParameters {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) evaluation: Option<Evaluation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) flake_inputs: Option<Vec<FlakeInput>>,
    pub(crate) lockfile_hash: Digest,
    pub(crate) toolchain: Toolchain,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Builder {
    pub(crate) id: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Metadata {
    pub(crate) invocation_id: String,
    pub(crate) started_on: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Byproduct {
    pub(crate) digest: Digest,
    pub(crate) name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub(crate) enum Toolchain {
    NixToolchain {
        nix: ToolchainVersion,
    },
    RustToolchain {
        cargo: ToolchainVersion,
        rustc: ToolchainVersion,
    },
}

impl Display for Toolchain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Toolchain::NixToolchain { nix } => write!(f, "{}", nix.version),
            Toolchain::RustToolchain { cargo: _, rustc } => write!(f, "{}", rustc.version),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct ToolchainVersion {
    pub(crate) digest: Digest,
    pub(crate) version: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Annotation {
    drv_path: String,
    output_hash_mode: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    urls: Option<String>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Evaluation {
    derivation_count: Number,
    fetch_count: Number,
    mode: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct FlakeInput {
    name: String,
    nar_hash: String,
}
