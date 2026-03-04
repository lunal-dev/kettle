# Kettle, for attested builds

**The security of reproducible builds, but without the pain or hassle.**

Use Kettle to create and verify **attested builds**, binaries with cryptographic signatures showing exactly what source code, dependencies, and toolchains were used to create them.

Kettle uses Trusted Execution Environments to sign builds using hardware attestation. Hardware attestations are verified against certificates published by the hardware manufacturer, cryptographically linking binaries to their exact source code.

## Why Kettle?

Existing attestation systems require you to trust the provider running the build. If you use GitHub's [artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations), you are agreeing to take GitHub's word that their cloud VMs didn't tamper with your build.

In contrast, if you use Kettle to build and attest inside a TEE, not even your cloud provider can read the memory or disk used to run your builds. There's no way for them to tamper, even if they have root on the bare metal where your build is running.

### More about attested builds

For a full tour of Kettle's design, architecture, and security guarantees, read our guide to attested builds.

1. [What are Attestable Builds?](https://github.com/lunal-dev/home/blob/main/docs/attestable-builds/README.md)
2. [How Attestable Builds Work](https://github.com/lunal-dev/home/blob/main/docs/attestable-builds/how-it-works.md)
3. [Provenance and Standards](https://github.com/lunal-dev/home/blob/main/docs/attestable-builds/provenance-standards.md)
4. [Threat Model and Security Boundaries](https://github.com/lunal-dev/home/blob/main/docs/attestable-builds/threat-model.md)

## Installing Kettle

To install Kettle, first [install Rust](https://rustup.rs), and then use Cargo to build and install:

```bash
$ cargo install --git https://github.com/lunal-dev/kettle
```

If you are running inside a TEE, you will need to install OS packages for attestation, and then enable the `attest` feature flag. Here's an example for Ubuntu Linux:

```bash
$ apt-get install -y libtss2-dev
$ cargo install --features attest --git https://github.com/lunal-dev/kettle
```

## Using Kettle
### Attest from a TEE

Run `kettle attest` to generate a SLSA-compliant `provenance.json` file, build the project, checksum the binaries, record measurements from the VM, and apply a hardware signature to everything.

While running inside a TEE, this will check out, build, and attest the `ripgrep` search tool:

```bash
$ git clone https://github.com/burntsushi/ripgrep
$ kettle attest ripgrep
```

After Kettle finishes running , look for the provenance, attestation, and binary inside the `kettle-build` directory.

### Verify anywhere

Run `kettle verify` to cryptographically verify your binaries. Kettle will read the `evidence.json`, verify the signature using hardware vendor public keys, and then validate the signed `provenance.json` and confirm the checksum of your binary.

Verify the attested build created above like this:
```bash
$ kettle verify ripgrep/kettle-build
```

### Build anywhere

Run `kettle build` to do all the steps except the hardware cryptography: generate a SLSA-compliant `provenance.json` file, build the project, and checksum the binaries. Use this command to test your build process even if you aren't inside a TEE.

This will check out the `ripgrep` search tool, build a binary, and generate a provenance file:

```bash
$ git clone https://github.com/burntsushi/ripgrep
$ kettle build ripgrep
```

After Kettle finishes running , look for the provenance and binary inside the `kettle-build` directory.

## Plans

In the future, Kettle will be able to attest builds from any toolchain, including Python, Go, and many others.

## Development

Use `cargo nextest run` to run the tests on any platform.

In a TEE, use `cargo nextest run --ignored all` to run the full integration tests that checkout Rust and Nix projects, build them, attest them, and verify them.
