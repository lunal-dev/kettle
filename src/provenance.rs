use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

#[derive(Serialize, Deserialize)]
pub struct Provenance {
    pub _type: String,
    pub subject: Vec<Subject>,
    #[serde(rename = "predicateType")]
    pub predicate_type: String,
    pub predicate: Predicate,
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
}

#[derive(Serialize, Deserialize)]
pub(crate) struct Subject {
    name: String,
    digest: Digest,
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
    uri: String,
    digest: Digest,
    name: String,
}

#[derive(Serialize, Deserialize)]
struct RunDetails {
    builder: Builder,
    metadata: Metadata,
    byproducts: Vec<Byproduct>,
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
    pub(crate) toolchain: Toolchain,
    lockfile_hash: Digest,
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
    name: String,
    digest: Digest,
}

pub(crate) enum Format {
    SLSAv1dot2,
}

#[derive(Clone, Serialize, Deserialize)]
pub(crate) struct Toolchain {
    rustc: ToolchainVersion,
    cargo: ToolchainVersion,
}

#[derive(Clone, Serialize, Deserialize)]
pub(crate) struct ToolchainVersion {
    version: String,
    digest: Digest,
}
