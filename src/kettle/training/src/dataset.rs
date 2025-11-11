//! Dataset loading for attestable training

use anyhow::{Context, Result};
use candle_core::{DType, Device, Tensor};
use std::path::Path;

/// Generic dataset trait for attestable training
pub trait Dataset {
    /// Get number of training samples
    fn train_size(&self) -> usize;

    /// Get a single training sample by index
    fn get_train_sample(&self, index: usize) -> Result<(Tensor, Tensor)>;
}

/// MNIST dataset loader
pub struct MnistDataset {
    train_images: Tensor,
    train_labels: Tensor,
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
}

impl Dataset for MnistDataset {
    fn train_size(&self) -> usize {
        self.train_images.dims()[0]
    }

    fn get_train_sample(&self, index: usize) -> Result<(Tensor, Tensor)> {
        let image = self.train_images.get(index)?;
        let label = self.train_labels.get(index)?;
        Ok((image, label))
    }
}
