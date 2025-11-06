//! Dataset loading for attestable training

use anyhow::{Context, Result};
use candle_core::{DType, Device, Tensor};
use std::path::Path;

/// MNIST dataset loader
pub struct MnistDataset {
    pub train_images: Tensor,
    pub train_labels: Tensor,
}

impl MnistDataset {
    /// Load MNIST dataset from directory
    pub fn load(dir: &Path, device: &Device) -> Result<Self> {
        let m = candle_datasets::vision::mnist::load_dir(dir)
            .context("Failed to load MNIST dataset")?;

        // Normalize images to [0, 1] and flatten
        let train_images = (m.train_images.to_dtype(DType::F32)? / 255.0)?
            .to_device(device)?;
        let train_labels = m.train_labels.to_dtype(DType::U32)?.to_device(device)?;

        Ok(Self {
            train_images,
            train_labels,
        })
    }

    /// Get number of training samples
    pub fn train_size(&self) -> usize {
        self.train_images.dims()[0]
    }
}
