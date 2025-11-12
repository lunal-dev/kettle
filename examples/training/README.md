# Training Examples

**[← Main README](../../README.md)** | **[📖 Training Documentation](../../TRAINING.md)**

Self-contained examples demonstrating attestable ML training with cryptographic proof.

## Available Examples

- **[MNIST](mnist/)** - Handwritten digit classification (70K images, CNN)
- **[Iris](iris/)** - Iris flower classification (150 samples, MLP)

## Quick Start

```bash
# Run any example with one command
cd examples/training/<example-name>
kettle train --quick
```

**That's it.** Dataset auto-downloads, trains in seconds with `--quick` flag.

## Standard Structure

All examples follow this interface:

```
example-name/
├── config.json    # Model architecture & hyperparameters
├── data/          # Dataset (auto-downloaded)
├── download.py    # Downloads dataset to SafeTensors format
└── README.md      # Dataset & model specifics
```

## Full Documentation

Everything you need is in **[TRAINING.md](../../TRAINING.md)**:

- **[Dataset Format](../../TRAINING.md#dataset-format)** - SafeTensors specification
- **[CLI Commands](../../TRAINING.md#cli-commands)** - All `kettle train` options
- **[Passport Schema](../../TRAINING.md#training-passport-schema)** - Complete JSON structure
- **[TEE Attestation](../../TRAINING.md#cli-commands)** - Using `--attestation` flag
- **[Determinism](../../TRAINING.md#deterministic-training)** - Reproducible training
- **[Troubleshooting](../../TRAINING.md#troubleshooting)** - Common issues

## Adding New Examples

1. Create `examples/training/<name>/`
2. Add `config.json`, `download.py`, `README.md`
3. Follow existing examples as template

See **[TRAINING.md](../../TRAINING.md)** for complete documentation.
