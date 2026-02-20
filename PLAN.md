# kettle.rs

- [x] `kettle` command
  - [x] set up a rust project
  - [x] set up clap
  - [x] format help output

- [ ] `kettle verify` command
  - [x] import verify code from attestation-rs
  - [x] fetch AMD cert chain, check signature
  - [x] parse provenance.json files for cargo and nix
  - [x] validate attestation checksum matches provenance.json checksum
  - [x] print tables of build info and verification results
  - [x] print AMD cert chain verify result
  - [x] print VCEK verify result
  - [x] print sev-snp report verify result
  - [x] print report data checksum verify result
  - [x] print provenance checksum verify result
  - [x] verify artifacts against provenance.json checksums
  - [x] print launch measurements, guest_svn, policy, version, and vmpl
  - [x] print git commit sha
  - [x] print detailed error message after table with expected and actual checksums

- [ ] `kettle build` command
  - [ ] collect provenance data
    - [ ] collect git repo data commit_hash, tree_hash, git_binary_hash, repository_url
  - [ ] handle cargo build
    - [ ] collect lockfile hash
    - [ ] collect rustc + cargo binary info (path, hash, version)
    - [ ] run `cargo build --locked --release`
    - [ ] collect exectutables from target/release/* (path, hash, name)
  - [ ] handle nix build
    - [ ] ???
  - [ ] generate provenance.json file
  - [ ] generate attestation from provenance and build result
    - [ ] hash provenance for checksum
    - [ ] call attest with custom data of provenance checksum
    - [ ] write the results into `evidence.b64`

## future work

- [ ] `kettle verify-source` BUILD_PATH SOURCE_PATH # verify SOURCE_PATH was used to create BUILD_PATH
  - [ ] verify git commit against provenance
  - [ ] verify lockfile against provenance
  - [ ] verify entire merkle tree against provenance
