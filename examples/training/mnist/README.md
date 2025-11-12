# MNIST Training Example

**[← Training Examples](../)** | **[Main README](../../../README.md)** | **[Training Documentation](../../../TRAINING.md)**

Demonstrates attestable training with the MNIST handwritten digit dataset.

## Quick Start

```bash
cd examples/training/mnist
kettle train --quick
```

Dataset auto-downloads if missing. See [common usage patterns](../#common-usage-patterns) for more options.

## Dataset Details

**MNIST Dataset:**
- 70,000 grayscale images (28×28 pixels)
- 60,000 training samples + 10,000 test samples
- 10 classes: handwritten digits 0-9
- Pixel values normalized to [0, 1]
- Labels as uint32 (0-9)

**SafeTensors keys:** `"features"` and `"labels"`

## Model Architecture

Simple CNN suitable for MNIST:
- Convolutional layers for spatial feature extraction
- Pooling for dimensionality reduction
- Fully connected layers for classification

See `config.json` for complete architecture specification.

## Further Documentation

- **[Training Examples Overview](../)** - Standard structure, TEE attestation, passport contents
- **[TRAINING.md](../../../TRAINING.md)** - Complete training documentation and troubleshooting
