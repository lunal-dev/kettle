# Iris Training Example

**[← Training Examples](../)** | **[Main README](../../../README.md)** | **[Training Documentation](../../../TRAINING.md)**

Demonstrates attestable training with the Iris dataset (tabular classification).

This example shows how to train on non-image data using SafeTensors format.

## Quick Start

```bash
cd examples/training/iris
kettle train --quick
```

Dataset auto-downloads if missing. See [common usage patterns](../#common-usage-patterns) for more options.

## Dataset Details

**Iris Dataset:**
- 150 samples (tabular data)
- 4 features: sepal length/width, petal length/width
- 3 classes: setosa, versicolor, virginica
- Features normalized to [0, 1]
- Labels as uint32 (0, 1, 2)

**SafeTensors keys:** `"features"` and `"labels"`

## Model Architecture

Simple MLP: 4 inputs → 10 hidden → 3 outputs

This shallow network is sufficient for the linearly-separable Iris dataset.

See `config.json` for complete architecture specification.

## Further Documentation

- **[Training Examples Overview](../)** - Standard structure, TEE attestation, passport contents
- **[TRAINING.md](../../../TRAINING.md)** - Complete training documentation and troubleshooting
