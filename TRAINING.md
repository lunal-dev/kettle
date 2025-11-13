# Attestable Training with Candle

**[← Main README](README.md)** | **[Examples](examples/training/)**

This document describes how to use the attestable training feature, which extends the attestable builds system to machine learning model training using [Candle ML framework](https://github.com/huggingface/candle).

## Overview

Attestable training provides:

- **Deterministic training**: Same inputs + same seed = same model weights (bit-exact reproducibility)
- **Cryptographic verification**: All training inputs and outputs are hashed and verified via merkle trees
- **Self-attestation chain**: Training binary itself is built and verified by the attestable builds system
- **Complete provenance**: Training passports link the entire chain: Source Code → Binary → Model

## What Gets Verified

### Binary Build (Phase 1)

Before training begins, the `kettle-train` binary is built and verified:

1. **Source Code** - Git commit, tree hash, and binary hash
2. **Cargo.lock** - SHA256 hash of entire lockfile
3. **Dependencies** - All crates verified against Cargo.lock checksums
   - Candle ML framework and all its dependencies (`candle-core`, `candle-nn`, `candle-datasets`)
   - All transitive dependencies (serde, safetensors, rand, etc.)
   - Platform-specific crates are verified as a subset
4. **Toolchain** - Rust compiler and Cargo binary hashes

All verification results are captured in the build passport at `inputs.binary.build_passport.inputs.dependencies[]`.

### Training Inputs (Phase 2)

During training, additional inputs are verified:

1. **Binary** - Complete build passport embedded in training passport
2. **Dataset** - Directory hash of all training data
3. **Model Config** - Configuration file hash
4. **Master Seed** - Deterministic training seed

All inputs are combined into a merkle tree for cryptographic verification.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ SELF-ATTESTATION CHAIN                                  │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. Build Training Binary (Rust + Candle)               │
│     a. Parse Cargo.lock and verify all dependencies     │
│     b. Verify cached .crate files against checksums     │
│     c. Build binary with verified toolchain             │
│     → Binary + Build Passport (with dependencies)       │
│                                                          │
│  2. Train Model                                          │
│     Binary + Dataset + Config → kettle train             │
│     → Model + Training Passport                          │
│                                                          │
│  3. Verify Complete Chain                                │
│     kettle train-verify → Verify all hashes and links    │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

**See [examples/training/](examples/training/) for ready-to-run examples** (MNIST, Iris).

### 1. Train a Model

```bash
# Train with your dataset and config
kettle train config.json --dataset /path/to/data --output ./training-output

# Quick test (1 epoch)
kettle train config.json --dataset /path/to/data --quick
```

This will:

1. Auto-build training binary if needed (first run only)
2. Hash all training inputs (dataset, config, binary)
3. Build a merkle tree of inputs
4. Execute deterministic training
5. Generate a training passport

### 2. Verify the Training Passport

```bash
kettle train-verify training-output/passport.json
```

This verifies:

- All input hashes match
- Final model weights hash is correct
- Merkle tree verification passes
- Complete chain of trust is intact

## Dataset Format

Training requires datasets in **SafeTensors format** for deterministic, verifiable loading.

### Format Specification

**File:** `train.safetensors` in your dataset directory

**Tensor Requirements:**
- Features: `(num_samples, num_features)` as float32
- Labels: `(num_samples,)` as uint32
- Sample counts must match

**Key Configuration:**

Keys are configured in `config.json` (defaults: `"features"` and `"labels"`):

```json
{
  "type": "mlp",
  "input_size": 784,
  "hidden_sizes": [128],
  "output_size": 10,
  "dataset_keys": {
    "features": "images",
    "labels": "labels"
  }
}
```

If omitted, defaults to `"features"` and `"labels"`.

### Creating Custom Datasets

Example using Python:

```python
import numpy as np
from safetensors.numpy import save_file

# Your data
features = np.random.rand(1000, 20).astype(np.float32)  # 1000 samples, 20 features
labels = np.random.randint(0, 5, size=1000).astype(np.uint32)  # 5 classes

# Save with custom key names
save_file(
    {"my_features": features, "my_labels": labels},
    "data/train.safetensors"
)

# Update config.json to match:
# "dataset_keys": {"features": "my_features", "labels": "my_labels"}
```

**Examples:** [examples/training/](examples/training/) directory has MNIST and Iris examples - both use default keys.

### Why SafeTensors?

SafeTensors was chosen for dataset format because it provides:

- **Determinism**: Binary format with no parsing ambiguity (unlike CSV/JSON)
- **Speed**: Fast zero-copy loading, no deserialization overhead
- **Safety**: Cannot execute code (unlike pickle), resistant to attacks
- **Verification**: Easy to hash entire file for cryptographic verification
- **Simplicity**: Single file per dataset, standard in ML ecosystem

This makes SafeTensors ideal for attestable builds where determinism and verifiability are critical.

### Normalization

**Features should be normalized** before saving to SafeTensors. Common patterns:

- **Images**: Divide by 255 to get [0, 1] range
- **Tabular**: Min-max scaling `(x - min) / (max - min)` or standardization `(x - mean) / std`

Examples show normalization in download scripts. The training binary expects normalized f32 features.

### Determinism Verification

Train twice and compare passport hashes to verify determinism:

```bash
kettle train config.json --dataset ./data --output ./run1
kettle train config.json --dataset ./data --output ./run2

# Compare hashes from passports
jq '.outputs.artifacts[0].hash' run1/passport.json
jq '.outputs.artifacts[0].hash' run2/passport.json
# Should be identical
```

For perfect bit-exact reproducibility, use same seed, dataset, config, and training binary version.

## CLI Commands

### `kettle train`

Train a model with attestable training.

```bash
kettle train CONFIG --dataset DATASET [OPTIONS]
```

**Arguments:**

- `config`: Path to model configuration JSON
- `--dataset`, `-d`: Path to dataset directory (required)

**Options:**

- `--output`, `-o`: Output directory (default: `./training-output`)
- `--quick`: Quick test mode (1 epoch)
- `--rebuild-binary`: Force rebuild of training binary
- `--attestation`, `-a`: Generate TEE attestation report (requires AMD SEV-SNP and `attest-amd`)

**Output Structure:**

```
<output-dir>/
├── passport.json              # Training passport (always generated)
├── evidence.b64              # TEE attestation (only with --attestation)
└── checkpoints/
    ├── final.safetensors     # Final trained model weights
    └── training-results.json  # Training metadata
```

**Examples:**

```bash
# Train with dataset
kettle train config.json --dataset /path/to/data

# Train with attestation
kettle train config.json --dataset /path/to/data --attestation

# Quick test
kettle train config.json --dataset /path/to/data --quick

# Custom output with attestation
kettle train config.json --dataset /path/to/data --output ./my-model --attestation
```

### `kettle train-verify`

Verify a training passport.

```bash
kettle train-verify <passport>
```

**Arguments:**

- `passport`: Path to training passport JSON

**Example:**

```bash
kettle train-verify training-output/passport.json
```

## Training Passport Schema

The training passport embeds the complete Phase 1 build passport to provide full provenance:

```json
{
  "version": "1.0",
  "inputs": {
    "binary": {
      "build_passport": {
        "version": "1.0",
        "inputs": {
          "git_source": {
            "commit_hash": "232a80c...",
            "tree_hash": "sha256:..."
          },
          "cargo_lock_hash": "sha256:...",
          "dependencies": [
            {
              "name": "candle-core",
              "version": "0.9.1",
              "source": "registry+https://github.com/rust-lang/crates.io-index",
              "checksum": "a9f51e2ecf6efe9737af8f993433c839f956d2b6ed4fd2dd4a7c6d8b0fa667ff",
              "verified": true
            },
            {
              "name": "candle-nn",
              "version": "0.9.1",
              "source": "registry+https://github.com/rust-lang/crates.io-index",
              "checksum": "c1980d53280c8f9e2c6cbe1785855d7ff8010208b46e21252b978badf13ad69d",
              "verified": true
            },
            {
              "name": "safetensors",
              "version": "0.4.5",
              "source": "registry+https://github.com/rust-lang/crates.io-index",
              "checksum": "sha256:...",
              "verified": true
            }
            // ... 418+ more dependencies ...
          ],
          "toolchain": {
            "rustc": {
              "binary_hash": "sha256:...",
              "version": "1.90.0"
            },
            "cargo": {
              "binary_hash": "sha256:...",
              "version": "1.90.0"
            }
          }
        },
        "outputs": {
          "artifacts": [
            {
              "path": "/path/to/kettle-train",
              "hash": "sha256:...",
              "type": "binary"
            }
          ]
        }
      }
    },
    "dataset": {
      "path": "/path/to/dataset",
      "hash": "sha256:..."
    },
    "model_config": {
      "path": "config.json",
      "hash": "sha256:..."
    },
    "master_seed": 42
  },
  "process": {
    "deterministic_proof": {
      "backend": "cpu",
      "single_threaded": true,
      "seed": 42
    },
    "metrics": {
      "total_epochs": 10,
      "final_train_loss": 0.1899
    }
  },
  "outputs": {
    "artifacts": [
      {
        "path": "/path/to/final.safetensors",
        "hash": "sha256:...",
        "type": "model_weights"
      }
    ]
  },
  "merkle_verification": {
    "root": "sha256:...",
    "tree_size": 4
  }
}
```

**Key Structure:**

- `inputs.binary.build_passport` - **Complete Phase 1 build passport** (not just a hash)
  - All Candle dependencies at `inputs.binary.build_passport.inputs.dependencies[]`
  - Each dependency includes name, version, checksum, and verification status
- `inputs.dataset` - Dataset hash
- `inputs.model_config` - Configuration hash
- `inputs.master_seed` - Deterministic seed
- `process.deterministic_proof` - CPU-only execution proof
- `process.metrics` - Training metrics embedded in checkpoint
- `outputs.artifacts` - Final model weights
- `merkle_verification` - Merkle tree of all inputs

This provides **complete provenance** from source code through dependencies to trained model.

## Deterministic Training

Training is fully deterministic when:

1. **Same seed**: Use the `--seed` flag with identical value
2. **Same inputs**: Identical dataset, model config, and binary
3. **CPU execution**: Training always runs on CPU (no GPU non-determinism)
4. **Same environment**: Same hardware and OS (for perfect bit-exact reproducibility)

### Verifying Determinism

Train twice and compare passport hashes:

```bash
# First run
kettle train config.json --dataset ./data --output ./run1

# Second run
kettle train config.json --dataset ./data --output ./run2

# Compare model hashes from passports
jq '.outputs.artifacts[0].hash' run1/passport.json
jq '.outputs.artifacts[0].hash' run2/passport.json
# Should be identical
```

### Rebuilding the Training Binary

Force a rebuild of the training binary (e.g., after modifying the Rust source):

```bash
kettle train --rebuild-binary
```

## Project Structure

```
attestable-builds/
├── src/kettle/
│   ├── training.py           # Main orchestration
│   ├── training_tool.py      # Binary build/cache management
│   ├── training_inputs.py    # Input verification and hashing
│   ├── training_passport.py  # Passport schema
│   └── training/             # Rust training binary
│       ├── Cargo.toml
│       └── src/
│           ├── main.rs       # CLI entry point
│           ├── train.rs      # Training loop
│           ├── model.rs      # MLP neural network
│           └── dataset.rs    # Dataset trait + SafeTensors loader
├── examples/
│   └── training/
│       └── mnist/            # MNIST example
│           ├── config.json
│           ├── download.py  # Downloads and converts to SafeTensors
│           └── README.md
└── TRAINING.md               # This file

~/.cache/kettle/training/     # Cached training binary
├── bin/kettle-train          # Built binary
└── build-passport.json       # Binary's build passport
```

## Troubleshooting

### Binary Build Fails

If the training binary fails to build on first run:

1. Check you have Rust installed: `rustc --version`
2. Check you have git installed: `git --version`
3. Try rebuilding: `kettle train --rebuild-binary`

### Common Issues

**Dataset not found:** Ensure `train.safetensors` exists in dataset directory (not just empty `data/` folder)

**Key mismatch:** Add `dataset_keys` to config.json if your SafeTensors uses custom key names

**Dimension mismatch:** Verify `input_size` and `output_size` in config match your dataset

**Training fails:** Run `kettle train --rebuild-binary` to force rebuild of training binary

### Determinism Verification Fails

Ensure you're using:

- Identical seed values
- Identical dataset files
- Identical model configuration
- Same version of the training binary

### Memory Issues

Training uses batch_size=256 by default. For custom batch sizes, modify `training_constants.py` or the Rust source in `src/kettle/training/` and rebuild with `kettle train --rebuild-binary`.

## See Also

- [Attestable Builds README](README.md) - Main attestable builds documentation
- [Candle Framework](https://github.com/huggingface/candle) - Minimal ML framework in Rust
