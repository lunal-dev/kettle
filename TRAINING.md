# Attestable Training with Candle

This document describes how to use the attestable training feature, which extends the attestable builds system to machine learning model training using [Candle ML framework](https://github.com/huggingface/candle).

## Overview

Attestable training provides:

- **Deterministic training**: Same inputs + same seed = same model weights (bit-exact reproducibility)
- **Cryptographic verification**: All training inputs and outputs are hashed and verified via merkle trees
- **Self-attestation chain**: Training binary itself is built and verified by the attestable builds system
- **Complete provenance**: Training passports link the entire chain: Source Code → Binary → Model

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ SELF-ATTESTATION CHAIN                                  │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. Build Training Binary (Rust + Candle)               │
│     Source Code → kettle build → Binary + Build Passport│
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

**See [examples/training/mnist/](examples/training/mnist/) for a complete working example.**

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
kettle train-verify training-output/training-passport.json
```

This verifies:

- All input hashes match
- Final model weights hash is correct
- Merkle tree verification passes
- Complete chain of trust is intact

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
- `--tee`: Execute training in TEE with attestation (future)

**Examples:**

```bash
# Train with dataset
kettle train config.json --dataset /path/to/data

# Quick test
kettle train config.json --dataset /path/to/data --quick

# Custom output
kettle train config.json --dataset /path/to/data --output ./my-model
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
kettle train-verify training-output/training-passport.json
```

## Training Passport Schema

The training passport follows the same structure as build passports (version 1.0):

```json
{
  "version": "1.0",
  "inputs": {
    "binary": {
      "build_passport_hash": "sha256:...",
      "commit_hash": "abc123...",
      "candle_version": "0.9.1"
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
      "final_train_loss": 0.023
    }
  },
  "outputs": {
    "final_weights": {
      "path": "final.safetensors",
      "hash": "sha256:..."
    }
  },
  "merkle_verification": {
    "root": "sha256:...",
    "tree_size": 5
  }
}
```

**Structure aligned with build passports:**

- `inputs` - All training inputs (binary, dataset, config, weights, seed)
- `process` - Deterministic proof and training metrics
- `outputs` - Final model weights
- `merkle_verification` - Cryptographic verification data

## Deterministic Training

Training is fully deterministic when:

1. **Same seed**: Use the `--seed` flag with identical value
2. **Same inputs**: Identical dataset, model config, and binary
3. **CPU execution**: Training always runs on CPU (no GPU non-determinism)
4. **Same environment**: Same hardware and OS (for perfect bit-exact reproducibility)

### Verifying Determinism

Train twice with the same seed and compare checkpoint hashes:

```bash
# First run
kettle train config.json --dataset ./data --output ./run1

# Second run
kettle train config.json --dataset ./data --output ./run2

# Compare checkpoints
kettle verify-determinism \
  run1/checkpoints/final.safetensors \
  run2/checkpoints/final.safetensors
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
│           └── dataset.rs    # Dataset trait + MNIST impl
├── examples/
│   └── training/
│       └── mnist/            # MNIST example
│           ├── config.json
│           ├── download_mnist.py
│           ├── train_mnist.py
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

### Training Fails

If training fails, check that all dependencies are installed and try rebuilding the binary with `kettle train --rebuild-binary`.

### Determinism Verification Fails

Ensure you're using:

- Identical seed values
- Identical dataset files
- Identical model configuration
- Same version of the training binary

### Memory Issues

Training uses batch_size=256 by default. For custom batch sizes, modify `training_constants.py` or the Rust source in `src/kettle/training/` and rebuild with `kettle train --rebuild-binary`.

## Future Work

- TEE training with attestation (`--tee` flag)
- Multi-GPU support (requires non-deterministic mode)
- Additional model architectures (CNNs, Transformers)
- Distributed training across multiple nodes
- Integration with model hubs (Hugging Face)

## See Also

- [Attestable Builds README](README.md) - Main attestable builds documentation
- [Candle Framework](https://github.com/huggingface/candle) - Minimal ML framework in Rust
