# Attestable Builds for Confidential Computing

This doc explains how attestable builds solve the measurement verification problem in confidential computing. After reading, you'll understand why runtime attestation alone leaves a gap, how build attestation creates verifiable provenance for measurements, and how Kettle implements this.

We assume familiarity with TEE basics: memory encryption, attestation reports, launch measurements. No prior supply chain security knowledge required.

## The Problem: Measurements Without Provenance

Runtime attestation proves what code is running. It doesn't prove where that code came from.

A client wants to verify your service before sending sensitive data. They request an attestation report, validate the TEE signature, and extract the launch measurement: a hash representing the code and initial state loaded into the enclave. The measurement is valid. The hardware signature checks out.

Now what?

That measurement is a hash. The client has no way to know what source code, dependencies, or build process produced it. You could publish a list of expected measurements, but that's just another claim for them to trust. You're asking them to believe that measurement `a3f7b2c9...` corresponds to a legitimate build of your service, without evidence.

The client could theoretically rebuild your binary themselves. If they get the same hash, they've verified the measurement independently. But this requires your exact source tree, dependency versions, toolchain, build flags, and environment. In practice, nobody does this.

This creates a gap in the trust chain. Runtime attestation cryptographically proves "this code is running in a hardware-protected environment." But the link between that code and auditable source is based on trust, not proof. Clients either believe your published measurements or they don't. There's no independent verification.

The problem gets sharper when you consider the attack surface. A compromised build server could inject malicious code. A tampered dependency could introduce a backdoor. A modified toolchain could insert vulnerabilities. Any of these would produce a different measurement, but how would a client know the measurement they're checking against wasn't itself the product of a compromised build?

You've cryptographically verified the last mile while taking the first mile on faith.

## Reproducible Builds Are Not Practical

The standard answer to build verification is reproducible builds. If compilation is deterministic, anyone can rebuild from source and verify they get the same binary hash.

The problem is that most toolchains are non-deterministic. Compilers embed timestamps, parallelize in non-deterministic order, and make optimization decisions that vary between runs. Achieving bit-for-bit identical output requires controlling all of this across your entire dependency tree. It's years of engineering effort, and any single non-reproducible component breaks the whole chain.

Even if you achieve reproducibility, clients would need to actually rebuild to verify. They won't. So you've invested heavily in determinism while clients still trust your published hashes on faith.

## The Core Idea: Attest the Process

TEEs provide a different approach. Instead of proving that anyone can produce the same output, prove that the output was produced by a verified process in a trusted environment.

The key insight: shift from asking "does this binary have hash X?" to asking "was this binary with hash X produced by process Y from sources Z in environment W?"

This reframes the problem. You're not trying to achieve deterministic compilation. You're trying to create a cryptographic chain of evidence that binds your output to auditable inputs through a process that can be independently verified.

Attestable builds work by verifying every input before building, executing the build inside a TEE, and producing a signed attestation that binds the inputs to the output. The attestation proves:

- Supply chain verification: the artifact was built from a specific source commit, with specific dependency versions, using a specific toolchain. Each input is cryptographically identified, not just named.
- Build process integrity: the build executed in a hardware-isolated environment that can be remotely verified. The TEE attestation proves the build environment wasn't tampered with and that only the specified process ran.
- Verifiable chain of custody: every link from source commit to running code is cryptographically bound. There's no gap where tampering could hide undetected.

This gives you the security properties that reproducible builds aim for (verifiable provenance, tamper detection, independent verification) without requiring deterministic compilation and without requiring clients to rebuild.

The verification is also fast. Instead of rebuilding (hours, significant compute), a client checks cryptographic signatures and attestation reports (milliseconds, trivial compute). Verification becomes practical at scale.

## How Attestable Builds Work

The attestable build process follows a chain: signed inputs → attested TEE → signed artifacts. Each link is cryptographically bound to the next, creating an unbroken chain of evidence from source to measurement.

### Input Preparation

Before the build begins, every input is cryptographically identified:

- Source code: pinned to a specific git commit, with commit hash, tree hash, and repository signature recorded
- Dependencies: pinned via lockfiles (Cargo.lock, flake.lock) with each dependency identified by cryptographic hash
- Toolchain: compiler, linker, and build tool binaries are hashed
- Configuration: build configuration files are hashed

All input hashes are combined into a Merkle tree, producing a single root hash that uniquely identifies the complete set of inputs. If any input changes, the root hash changes.

### TEE Environment Setup

The build environment boots inside a TEE with measured boot. As the TEE initializes, it loads all build components: source code, dependencies, toolchain. Each component is measured during loading, creating a cryptographic record of exactly what entered the build environment.

Networking is disabled after inputs are loaded. The build environment is isolated: nothing can be injected or exfiltrated during compilation.

The TEE generates an attestation report proving the integrity of the loaded environment. This attestation is signed by hardware, rooted in keys that only the CPU can access. It proves that this environment contains exactly these measured components, and nothing else.

### Build Execution

The build runs inside the TEE using the pre-loaded, attested components. Standard toolchains work: there's no requirement for deterministic compilation. The build process executes normally (compilation, linking, packaging).

The key difference is context: the build is happening inside a hardware-isolated environment where the inputs have been measured and attested. The TEE ensures no external tampering during execution.

When the build completes, output artifacts are measured and their hashes recorded.

### Artifact Signing

The TEE generates a signing key derived from its hardware root of trust. This key is bound to the specific TEE instance and its measured state: it can only be produced by a TEE with exactly this configuration.

The TEE signs a provenance.json containing all input hashes (including the Merkle root), the TEE attestation, and all output hashes. This signature cryptographically binds the outputs to the attested build environment and verified inputs.

The signed provenance.json includes the output measurement: the same hash that will appear in runtime attestation when this artifact runs in a confidential VM. The build attestation and runtime attestation now reference the same measurement, closing the loop.

### The Complete Chain

The result is a cryptographic chain:

1. Source commit (signed by developers)
2. Dependencies (pinned by hash)
3. Toolchain (hashed)
4. Merkle root of all inputs
5. TEE attestation (hardware-signed proof of build environment)
6. Output artifacts (signed by TEE-derived key)
7. Runtime measurement (referenced in both build and runtime attestation)

Every link is verifiable. The chain connects auditable source to running code with no gaps.

## Client Verification Flow

A client verifying your service checks the complete chain from source to runtime. This is the payoff: verification that's independent, fast, and doesn't require rebuilding anything.

| Step                         | What the client does                                                                                                      |
| :--------------------------- | :------------------------------------------------------------------------------------------------------------------------ |
| Verify input signatures      | Check source signature against known public keys. Check dependency hashes against package registries.                     |
| Verify TEE build attestation | Check attestation signature against hardware vendor root of trust. Confirm attestation is fresh.                          |
| Verify artifact binding      | Confirm output artifacts were signed by a key derived from the attested TEE.                                              |
| Verify runtime attestation   | Request runtime attestation from live service. Confirm launch measurement matches the measurement from build attestation. |

If the runtime measurement matches the build attestation measurement, the chain is complete. The client has cryptographic proof that:

- The running code has measurement X
- Measurement X was produced by a build inside an attested TEE
- That build used verified inputs: specific source commit, specific dependencies, specific toolchain
- Every link in the chain is independently verifiable

Verification takes milliseconds. No rebuilding. No trusting published hash lists. Each step is a cryptographic check against hardware-rooted signatures or known public keys.

## Kettle

Kettle is our implementation of attestable builds. It handles input verification, build execution, and provenance generation.

### What Kettle Produces

A Kettle build generates three artifacts:

- provenance.json: SLSA v1.2 provenance statement in standard in-toto format, enabling interoperability with other supply chain security tools
- evidence.b64: TEE attestation report signed by AMD SEV-SNP hardware, binding the provenance.json hash to the hardware root of trust

### Additional Features

- Merkle inclusion proofs: clients can request proof that specific dependencies are in the input tree without revealing the full dependency graph, useful for targeted security audits
- Remote TEE builds: for environments without local SEV-SNP hardware, Kettle supports remote builds via a TEE service API

## Trust Assumptions

Attestable builds shrink the trust surface, but they don't eliminate it.

### What you're trusting

- TEE hardware and firmware: the security model depends on AMD SEV-SNP functioning correctly
- Attestation root of trust: TEE attestations are verified against hardware vendor signing keys
- Source repository integrity: if your repository is compromised, the attested build faithfully reproduces a compromised artifact
- Package registry infrastructure: dependency hashes are checked against registries (crates.io, nixpkgs), which must maintain integrity
- Toolchain distribution: compiler and build tool binaries are hashed against binaries from distribution channels (rustup, nix)

### What you're not trusting

- Build infrastructure outside the TEE: the host machine can be compromised without breaking guarantees
- Network infrastructure: after inputs are loaded, the build is isolated
- Service operators: clients verify the cryptographic chain themselves
- Published hash lists: measurements are traced to source, not accepted on faith

The tradeoff: you're trusting hardware vendors and source infrastructure rather than build operators and their claims. This is a smaller, more auditable trust surface, but not a trustless system.
