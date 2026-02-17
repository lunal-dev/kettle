use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

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

    pub fn verify_predicate(&self) -> bool {
        &self.predicate_type == "https://slsa.dev/provenance/v1"
    }
}

#[derive(Serialize, Deserialize)]
pub(crate) struct Subject {
    digest: Digest,
    name: String,
}

#[derive(Clone, Serialize, Deserialize)]
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

#[derive(Clone, Serialize, Deserialize)]
pub(crate) struct Toolchain {
    cargo: ToolchainVersion,
    rustc: ToolchainVersion,
}

#[derive(Clone, Serialize, Deserialize)]
pub(crate) struct ToolchainVersion {
    digest: Digest,
    version: String,
}
