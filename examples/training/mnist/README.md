# MNIST Training Example

Demonstrates attestable training with the MNIST dataset.

## Quick Start

```bash
cd examples/training/mnist
kettle train --quick
```

Dataset auto-downloads if missing.

## Directory Structure

```
examples/training/mnist/
├── config.json    # Model configuration
├── data/          # Dataset (auto-downloaded, gitignored)
├── download.py    # Dataset downloader (standard interface)
└── README.md
```

## Interface

Each training example follows this standard structure:
- `config.json` - Model configuration
- `data/` - Dataset directory (gitignored)
- `download.py` - Downloads dataset to `./data/`

`kettle train` uses these defaults and auto-downloads dataset via `download.py` if missing.

## Usage

```bash
# From example directory
cd examples/training/mnist
kettle train --quick

# Or from repository root
kettle train examples/training/mnist --quick

# Manual download
cd examples/training/mnist
python download.py
kettle train
```

## TEE Attestation

Generate cryptographic attestation proof (requires AMD SEV-SNP and `attest-amd`):

```bash
kettle train --attestation
```

Creates:
- `output/passport.json` - Training passport
- `evidence.b64` - AMD SEV-SNP attestation report (sidecar)

Verify:
```bash
kettle verify-attestation evidence.b64 --passport output/passport.json
```

## Passport Contents

The training passport includes:
- Complete build passport of the training binary (with all verified dependencies including Candle)
- Dataset and configuration hashes
- Training metrics embedded in checkpoint metadata
- Final model weights hash

See [TRAINING.md](../../../TRAINING.md#training-passport-schema) for the complete passport structure.
