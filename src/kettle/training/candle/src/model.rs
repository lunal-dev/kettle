//! Model architecture definitions for attestable training

use anyhow::Result;
use candle_core::Tensor;
use candle_nn::{linear, Linear, Module, VarBuilder};
use serde::{Deserialize, Serialize};

/// Dataset key configuration for SafeTensors files
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatasetKeys {
    #[serde(default = "DatasetKeys::default_features")]
    pub features: String,
    #[serde(default = "DatasetKeys::default_labels")]
    pub labels: String,
}

impl DatasetKeys {
    fn default_features() -> String {
        "features".to_string()
    }

    fn default_labels() -> String {
        "labels".to_string()
    }
}

impl Default for DatasetKeys {
    fn default() -> Self {
        Self {
            features: Self::default_features(),
            labels: Self::default_labels(),
        }
    }
}

/// Configuration for a Multi-Layer Perceptron (MLP)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MLPConfig {
    pub input_size: usize,
    pub hidden_sizes: Vec<usize>,
    pub output_size: usize,
    #[serde(default)]
    pub dataset_keys: DatasetKeys,
}

impl MLPConfig {
    /// Load configuration from JSON file
    pub fn from_file(path: &std::path::Path) -> Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config = serde_json::from_str(&content)?;
        Ok(config)
    }
}

/// Multi-Layer Perceptron model
pub struct Mlp {
    layers: Vec<Linear>,
}

impl Mlp {
    /// Create a new MLP from configuration
    pub fn new(config: &MLPConfig, vb: VarBuilder) -> Result<Self> {
        let mut layers = Vec::new();
        let mut prev_size = config.input_size;

        // Hidden layers
        for (i, &hidden_size) in config.hidden_sizes.iter().enumerate() {
            let layer = linear(prev_size, hidden_size, vb.pp(format!("hidden_{}", i)))?;
            layers.push(layer);
            prev_size = hidden_size;
        }

        // Output layer
        let output_layer = linear(prev_size, config.output_size, vb.pp("output"))?;
        layers.push(output_layer);

        Ok(Self { layers })
    }

    /// Forward pass through the network
    pub fn forward(&self, xs: &Tensor, train: bool) -> Result<Tensor> {
        let mut x = xs.clone();

        // Apply all layers except the last with ReLU activation
        for layer in &self.layers[..self.layers.len() - 1] {
            x = layer.forward(&x)?.relu()?;
        }

        // Final layer (no activation, raw logits)
        Ok(self.layers.last().unwrap().forward(&x)?)
    }
}
