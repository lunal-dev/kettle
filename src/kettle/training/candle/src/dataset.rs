//! Dataset loading for attestable training

use anyhow::{anyhow, bail, Context, Result};
use candle_core::{Device, Tensor};
use std::path::Path;

/// Generic dataset trait for attestable training
pub trait Dataset {
    /// Get number of training samples
    fn train_size(&self) -> usize;

    /// Get a single training sample by index
    fn get_train_sample(&self, index: usize) -> Result<(Tensor, Tensor)>;
}

/// Generic tensor dataset (SafeTensors format)
pub struct TensorDataset {
    train_images: Tensor,
    train_labels: Tensor,
}

impl TensorDataset {
    /// Load dataset from train.safetensors file
    pub fn load(dir: &Path, device: &Device, features_key: &str, labels_key: &str) -> Result<Self> {
        let path = dir.join("train.safetensors");

        // Check file exists with helpful error
        if !path.exists() {
            bail!(
                "Dataset file not found: {}\n\n\
                Expected SafeTensors format with:\n\
                - File: train.safetensors\n\
                - Keys: '{}' (features), '{}' (labels)\n\
                - Shapes: features (num_samples × num_features), labels (num_samples)\n\
                - Types: f32 for features, u32 for labels\n\n\
                See TRAINING.md for dataset format specification.",
                path.display(),
                features_key,
                labels_key
            );
        }

        let tensors = candle_core::safetensors::load(&path, device)
            .context(format!("Failed to load SafeTensors file: {}", path.display()))?;

        // Get available keys for error messages
        let available_keys: Vec<String> = tensors.keys().cloned().collect();

        // Find features tensor
        let train_images = tensors
            .get(features_key)
            .ok_or_else(|| {
                anyhow!(
                    "Missing training features in train.safetensors\n\
                     Expected key: '{}'\n\
                     Available keys: {}",
                    features_key,
                    available_keys.join(", ")
                )
            })?
            .clone();

        // Find labels tensor
        let train_labels = tensors
            .get(labels_key)
            .ok_or_else(|| {
                anyhow!(
                    "Missing training labels in train.safetensors\n\
                     Expected key: '{}'\n\
                     Available keys: {}",
                    labels_key,
                    available_keys.join(", ")
                )
            })?
            .clone();

        // Validate shapes match
        let num_samples = train_images.dims()[0];
        let num_labels = train_labels.dims()[0];

        if num_samples != num_labels {
            bail!(
                "Sample count mismatch in train.safetensors\n\
                 Features shape: {:?} ({} samples)\n\
                 Labels shape: {:?} ({} samples)\n\
                 Features and labels must have same number of samples",
                train_images.dims(),
                num_samples,
                train_labels.dims(),
                num_labels
            );
        }

        Ok(Self {
            train_images,
            train_labels,
        })
    }

    /// Validate dataset against model configuration
    pub fn validate(&self, expected_input_size: usize, expected_output_size: usize) -> Result<()> {
        // Check feature dimensions
        let actual_input_size = self.train_images.dims()[1];
        if actual_input_size != expected_input_size {
            bail!(
                "Feature dimension mismatch\n\
                 Dataset has {} features but model expects {}\n\
                 Check config.json input_size matches your dataset",
                actual_input_size,
                expected_input_size
            );
        }

        // Check label values are in valid range [0, output_size)
        let labels_vec = self.train_labels.to_vec1::<u32>()?;
        if let Some(&max_label) = labels_vec.iter().max() {
            if max_label >= expected_output_size as u32 {
                bail!(
                    "Invalid label value in dataset\n\
                     Found label {} but model only supports classes [0, {})\n\
                     Check config.json output_size matches your number of classes",
                    max_label,
                    expected_output_size
                );
            }
        }

        Ok(())
    }
}

impl Dataset for TensorDataset {
    fn train_size(&self) -> usize {
        self.train_images.dims()[0]
    }

    fn get_train_sample(&self, index: usize) -> Result<(Tensor, Tensor)> {
        let image = self.train_images.get(index)?;
        let label = self.train_labels.get(index)?;
        Ok((image, label))
    }
}
