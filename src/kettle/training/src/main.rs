//! Kettle Train - Deterministic ML training for attestable builds
//!
//! This binary provides attestable training using the Candle ML framework.
//! All training is deterministic and produces cryptographically verifiable outputs.

mod dataset;
mod model;
mod train;

use anyhow::{Context, Result};
use clap::Parser;
use std::path::PathBuf;

use dataset::MnistDataset;
use model::MLPConfig;
use train::{TrainConfig, Trainer};

#[derive(Parser)]
#[command(name = "kettle-train")]
#[command(author, version, about = "Deterministic ML training for attestable builds")]
struct Cli {
    /// Path to model configuration JSON
    #[arg(short, long)]
    config: PathBuf,

    /// Path to dataset directory
    #[arg(short, long)]
    dataset: PathBuf,

    /// Output directory for checkpoints and results
    #[arg(short, long, default_value = "./training-output")]
    output: PathBuf,

    /// Master seed for deterministic training (required)
    #[arg(long)]
    seed: u64,

    /// Number of training epochs (required)
    #[arg(long)]
    epochs: usize,

    /// Batch size for training (required)
    #[arg(long)]
    batch_size: usize,

    /// Learning rate (required)
    #[arg(long)]
    learning_rate: f64,

    /// Log interval in batches, 0 = no logging (required)
    #[arg(long)]
    log_interval: usize,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    // Load model configuration
    let model_config =
        MLPConfig::from_file(&cli.config).context("Failed to load model configuration")?;

    // Load dataset
    let device = candle_core::Device::Cpu;
    let dataset = MnistDataset::load(&cli.dataset, &device)
        .context("Failed to load dataset - ensure dataset files exist in dataset directory")?;

    // Create trainer and train with all parameters from CLI
    let mut trainer = Trainer::new(TrainConfig {
        epochs: cli.epochs,
        batch_size: cli.batch_size,
        learning_rate: cli.learning_rate,
        master_seed: cli.seed,
        log_interval: cli.log_interval,
    })?;
    trainer.train(&model_config, &dataset as &dyn dataset::Dataset, &cli.output)?;

    Ok(())
}
