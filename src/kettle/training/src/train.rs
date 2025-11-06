//! Deterministic training loop for attestable training

use anyhow::Result;
use candle_core::Tensor;
use candle_nn::{loss, ops, Optimizer, VarMap, VarBuilder};
use rand::SeedableRng;
use rand_chacha::ChaCha8Rng;
use std::path::Path;

use crate::dataset::MnistDataset;
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

    /// Train a model on MNIST dataset
    pub fn train(
        &mut self,
        model_config: &MLPConfig,
        dataset: &MnistDataset,
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

        // Save final checkpoint
        let final_checkpoint_path = output_dir.join("final.safetensors");
        self.varmap.save(&final_checkpoint_path)?;

        // Save training results
        let results_path = output_dir.join("training-results.json");
        let results_json = format!(
            r#"{{
  "total_epochs": {},
  "final_train_loss": {}
}}"#,
            self.config.epochs, final_loss
        );
        std::fs::write(&results_path, results_json)?;

        Ok(())
    }

    /// Train for one epoch
    fn train_epoch(
        &mut self,
        model: &Mlp,
        optimizer: &mut candle_nn::AdamW,
        dataset: &MnistDataset,
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
            let batch_images = Tensor::stack(
                &batch_indices
                    .iter()
                    .map(|&i| dataset.train_images.get(i))
                    .collect::<Result<Vec<_>, _>>()?,
                0,
            )?;
            let batch_labels = Tensor::stack(
                &batch_indices
                    .iter()
                    .map(|&i| dataset.train_labels.get(i))
                    .collect::<Result<Vec<_>, _>>()?,
                0,
            )?;

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
