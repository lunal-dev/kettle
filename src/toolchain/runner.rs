use anyhow::{Result, anyhow};
use rs_merkle::MerkleTree;
use sha2::{Digest as _, Sha256};
use std::path::PathBuf;

use crate::provenance::{
    BuildDefiniton, Builder, Byproduct, Digest, ExternalParameters, Metadata, Predicate,
    Provenance, RunDetails, Source, SourceDigest, Subject,
};

use super::driver::{Artifact, BuildMetadata, GitContext, ProvenanceFields, ToolchainDriver};

pub(crate) fn run<T: ToolchainDriver>(path: &PathBuf) -> Result<()> {
    // 1. Clean / create output dir
    let output_dir = path.join("kettle-build");
    if fs_err::exists(&output_dir)? {
        fs_err::remove_dir_all(&output_dir)?;
    }
    fs_err::create_dir_all(&output_dir)?;

    // 2. Git context (shared)
    let git = GitContext::from_dir(path)?;

    // 3. Read and hash lockfile (shared)
    let lockfile_bytes = fs_err::read(path.join(T::lockfile_filename()))?;
    let lockfile_hash = hex::encode(Sha256::digest(&lockfile_bytes));

    // 4. Toolchain-specific inputs
    let inputs = T::collect_inputs(path, &git, &lockfile_hash, &lockfile_bytes)?;

    // 5. Start metadata
    let mut build_metadata = BuildMetadata::start();

    // 6. Run build
    println!("Running `{}`", T::build_command_display());
    let build_output = T::run_build(path)?;

    // 7. Collect artifacts (each impl stages into artifacts_dir itself)
    let artifacts_dir = output_dir.join("artifacts");
    fs_err::create_dir_all(&artifacts_dir)?;
    let artifacts = T::collect_artifacts(&build_output, path, &artifacts_dir)?;

    // 8. Finish metadata
    build_metadata.finish();

    // 9. Merkle root (shared)
    let merkle_root = compute_merkle_root(inputs.merkle_entries(&git, &lockfile_hash))?;

    // 10. Assemble and write provenance (shared)
    let pf = inputs.provenance_fields(&git, &merkle_root);
    let provenance = assemble_provenance(&git, &build_metadata, &artifacts, &merkle_root, pf);
    fs_err::write(
        output_dir.join("provenance.json"),
        serde_json::to_string_pretty(&provenance)?,
    )?;

    println!(
        "Build in {:?} complete, output located in `kettle-build`",
        path
    );
    Ok(())
}

fn compute_merkle_root(entries: Vec<String>) -> Result<String> {
    let leaves: Vec<[u8; 32]> = entries
        .iter()
        .map(|e| Sha256::digest(e.as_bytes()).into())
        .collect();
    let tree = MerkleTree::<rs_merkle::algorithms::Sha256>::from_leaves(&leaves);
    let root = tree
        .root()
        .ok_or(anyhow!("Merkle tree root calculation failed!"))?;
    Ok(hex::encode(root))
}

fn assemble_provenance(
    git: &GitContext,
    metadata: &BuildMetadata,
    artifacts: &[Artifact],
    merkle_root: &str,
    pf: ProvenanceFields,
) -> Provenance {
    Provenance {
        _type: "https://in-toto.io/Statement/v1".to_string(),
        predicate_type: "https://slsa.dev/provenance/v1".to_string(),
        subject: artifacts
            .iter()
            .map(|a| Subject {
                name: a.name.clone(),
                digest: Digest {
                    sha256: a.checksum.clone(),
                },
            })
            .collect(),
        predicate: Predicate {
            build_definition: BuildDefiniton {
                build_type: pf.build_type,
                external_parameters: ExternalParameters {
                    build_command: pf.external_build_command,
                    source: Source {
                        digest: SourceDigest {
                            git_commit: git.commit.clone(),
                            git_tree: git.tree.clone(),
                        },
                        uri: git.source_uri.clone(),
                    },
                },
                internal_parameters: pf.internal_parameters,
                resolved_dependencies: pf.resolved_dependencies,
            },
            run_details: RunDetails {
                builder: Builder {
                    id: "https://lunal.dev/kettle-tee/v1".to_string(),
                },
                metadata: Metadata {
                    invocation_id: metadata.invocation_id.clone(),
                    started_on: metadata.started_on.clone(),
                    finished_on: metadata.finished_on.clone(),
                },
                byproducts: vec![Byproduct {
                    name: "input_merkle_root".to_string(),
                    digest: Digest {
                        sha256: merkle_root.to_string(),
                    },
                }],
            },
        },
    }
}
