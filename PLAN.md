# kettle.rs

- [x] `kettle` command
  - [x] set up a rust project
  - [x] set up clap
  - [x] format help output

- [x] `kettle verify` command
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

- [x] `kettle build` command
  - [x] collect provenance data
    - [x] collect git repo data commit_hash, tree_hash, git_binary_hash, repository_url
  - [x] handle cargo build
    - [x] collect lockfile hash
    - [x] collect rustc + cargo binary info (path, hash, version)
    - [x] run `cargo build --locked --release`
    - [x] collect exectutables from target/release/* (path, hash, name)
  - [x] handle nix build
    - [x] collect lockfile hash
    - [x] collect nix binary info (path, hash, version)
    - [x] run `nix build`
    - [x] collect exectutable info (path, hash, name)
  - [x] generate provenance.json file

- [x] `kettle attest` command
  - [x] generate attestation from provenance and build result
    - [x] hash provenance for checksum
    - [x] call attest with custom data of provenance checksum
    - [x] write the results into `evidence.json`

## future work

- [ ] toolchain for python packages
- [ ] toolchain for go binaries
- [ ] `kettle verify-source` BUILD_PATH SOURCE_PATH\
      # verifies that SOURCE_PATH was used to create BUILD_PATH
  - [ ] verify git commit against provenance
  - [ ] verify lockfile against provenance
  - [ ] verify entire merkle tree against provenance
