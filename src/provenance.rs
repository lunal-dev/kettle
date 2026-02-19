use std::fmt::Display;

use fs_err::DirEntry;
use serde::{Deserialize, Serialize};
use serde_json::Number;
use sha2::{Digest as _, Sha256};

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

    pub fn verify_predicate(&self) -> Verification {
        let expected = "https://slsa.dev/provenance/v1";
        if self.predicate_type == expected {
            Verification::success("Provenance predicateType is SLSA v1")
        } else {
            Verification::failure(
                "Provenance predicateType not SLSA v1",
                &format!(
                    "Expected predicateType {:?}, but instead found {:?}",
                    expected, &self.predicate_type
                ),
            )
        }
    }

    pub fn verify_artifacts(&self, artifacts: &[DirEntry]) -> Vec<Verification> {
        artifacts
            .iter()
            .map(|entry| Verification::Success {
                message: entry.file_name().to_string_lossy().to_string(),
            })
            .collect()
    }
}

#[derive(Serialize, Deserialize)]
pub(crate) struct Subject {
    digest: Digest,
    name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Digest {
    sha256: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Predicate {
    pub(crate) build_definition: BuildDefiniton,
    run_details: RunDetails,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BuildDefiniton {
    pub(crate) build_type: String,
    external_parameters: ExternalParameters,
    internal_parameters: InternalParameters,
    resolved_dependencies: Vec<ResolvedDependency>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ResolvedDependency {
    #[serde(skip_serializing_if = "Option::is_none")]
    annotations: Option<Annotation>,
    digest: Digest,
    name: String,
    uri: String,
}

#[derive(Serialize, Deserialize)]
struct RunDetails {
    builder: Builder,
    byproducts: Vec<Byproduct>,
    metadata: Metadata,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ExternalParameters {
    build_command: String,
    source: Source,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Source {
    digest: SourceDigest,
    uri: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SourceDigest {
    git_commit: String,
    git_tree: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct InternalParameters {
    #[serde(skip_serializing_if = "Option::is_none")]
    evaluation: Option<Evaluation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    flake_inputs: Option<Vec<FlakeInput>>,
    lockfile_hash: Digest,
    pub(crate) toolchain: Toolchain,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Builder {
    id: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Metadata {
    invocation_id: String,
    started_on: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Byproduct {
    digest: Digest,
    name: String,
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
    digest: Digest,
    version: String,
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
