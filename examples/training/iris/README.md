# Iris Training Example

Demonstrates attestable training with the Iris dataset (tabular classification).

This example shows how to train on non-image data using SafeTensors format.

## Quick Start

```bash
cd examples/training/iris
kettle train --quick
```

Dataset auto-downloads if missing.

## Directory Structure

```
examples/training/iris/
├── config.json    # Model configuration (4→10→3)
├── data/          # Dataset (auto-downloaded, gitignored)
├── download.py    # Dataset downloader (outputs SafeTensors)
└── README.md
```

## Dataset Details

**Iris Dataset:**
- 150 samples
- 4 features: sepal length/width, petal length/width
- 3 classes: setosa, versicolor, virginica
- Features normalized to [0, 1]
- Labels as uint32 (0, 1, 2)

**SafeTensors keys:** `"features"` and `"labels"` (defaults)

## Usage

```bash
# From example directory
cd examples/training/iris
kettle train --quick

# Or from repository root
kettle train examples/training/iris --quick

# Manual download
cd examples/training/iris
python download.py
kettle train
```

## Model Architecture

Simple MLP: 4 inputs → 10 hidden → 3 outputs

This shallow network is sufficient for the linearly-separable Iris dataset.
