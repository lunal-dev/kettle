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

See [TRAINING.md](../../../TRAINING.md) for general documentation.
