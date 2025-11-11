//! Deterministic training loop for attestable training

use anyhow::Result;
use candle_core::Tensor;
use candle_nn::{loss, ops, Optimizer, VarMap, VarBuilder};
use rand::SeedableRng;
use rand_chacha::ChaCha8Rng;
use std::path::Path;

use crate::dataset::Dataset;
use crate::model::{Mlp, MLPConfig};

/// Training configuration
pub struct TrainConfig {
    pub epochs: usize,
    pub batch_size: usize,
    pub learning_rate: f64,
    pub master_seed: u64,
    pub log_interval: usize,
}

/// Deterministic trainer
pub struct Trainer {
    varmap: VarMap,
    rng: ChaCha8Rng,
    config: TrainConfig,
}

impl Trainer {
    /// Create a new trainer with deterministic seeding
    pub fn new(config: TrainConfig) -> Result<Self> {
        Ok(Self {
            varmap: VarMap::new(),
            rng: ChaCha8Rng::seed_from_u64(config.master_seed),
            config,
        })
    }

    /// Train a model on dataset
    pub fn train(
        &mut self,
        model_config: &MLPConfig,
        dataset: &dyn Dataset,
        output_dir: &Path,
    ) -> Result<()> {
        // Create output directory
        std::fs::create_dir_all(output_dir)?;

        // Build model
        let vb = VarBuilder::from_varmap(&self.varmap, candle_core::DType::F32, &candle_core::Device::Cpu);
        let model = Mlp::new(model_config, vb)?;

        // Create optimizer
        let mut optimizer = candle_nn::AdamW::new_lr(self.varmap.all_vars(), self.config.learning_rate)?;

        // Train for all epochs and track final loss
        let mut final_loss = 0.0;
        for _epoch in 1..=self.config.epochs {
            final_loss = self.train_epoch(&model, &mut optimizer, dataset)?;
        }

        // Save final checkpoint with embedded metadata
        let final_checkpoint_path = output_dir.join("final.safetensors");

        // Save checkpoint (candle handles safetensors format)
        self.varmap.save(&final_checkpoint_path)?;

        // Add metadata to the saved safetensors file by rewriting it
        use std::collections::HashMap;

        // Read and keep file bytes in scope for the lifetime of the views
        let file_bytes = std::fs::read(&final_checkpoint_path)?;
        let safetensors_view = safetensors::SafeTensors::deserialize(&file_bytes)?;

        // Create metadata
        let mut metadata = HashMap::new();
        metadata.insert("total_epochs".to_string(), self.config.epochs.to_string());
        metadata.insert("final_train_loss".to_string(), final_loss.to_string());

        // Collect tensor views (keeping them borrowed from file_bytes)
        let tensors_data: Vec<_> = safetensors_view
            .names()
            .iter()
            .map(|name| {
                let view = safetensors_view.tensor(name).unwrap();
                (name.clone(), view)
            })
            .collect();

        // Serialize with metadata
        safetensors::serialize_to_file(
            tensors_data,
            &Some(metadata),
            &final_checkpoint_path,
        )?;

        Ok(())
    }

    /// Train for one epoch
    fn train_epoch(
        &mut self,
        model: &Mlp,
        optimizer: &mut candle_nn::AdamW,
        dataset: &dyn Dataset,
    ) -> Result<f32> {
        let mut total_loss = 0.0;
        let mut num_batches = 0;

        // Generate shuffled indices deterministically
        use rand::seq::SliceRandom;
        let mut indices: Vec<usize> = (0..dataset.train_size()).collect();
        indices.shuffle(&mut self.rng);

        for batch_start in (0..indices.len()).step_by(self.config.batch_size) {
            let batch_end = (batch_start + self.config.batch_size).min(indices.len());
            let batch_indices = &indices[batch_start..batch_end];

            // Get batch
            let batch_samples: Vec<(Tensor, Tensor)> = batch_indices
                .iter()
                .map(|&i| dataset.get_train_sample(i))
                .collect::<Result<Vec<_>>>()?;

            let (images, labels): (Vec<Tensor>, Vec<Tensor>) =
                batch_samples.into_iter().unzip();

            let batch_images = Tensor::stack(&images, 0)?;
            let batch_labels = Tensor::stack(&labels, 0)?;

            // Forward pass
            let logits = model.forward(&batch_images, true)?;

            // Compute loss
            let log_sm = ops::log_softmax(&logits, candle_core::D::Minus1)?;
            let loss = loss::nll(&log_sm, &batch_labels)?;

            // Backward pass + optimizer step
            optimizer.backward_step(&loss)?;

            total_loss += loss.to_scalar::<f32>()?;
            num_batches += 1;

            if self.config.log_interval > 0 && num_batches % self.config.log_interval == 0 {
                let avg_loss = total_loss / num_batches as f32;
                println!("  Batch {:4} | Avg Loss: {:.4}", num_batches, avg_loss);
            }
        }

        Ok(total_loss / num_batches as f32)
    }
}
