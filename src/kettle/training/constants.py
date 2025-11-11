"""
Training constants for attestable ML training.

These constants define default values for deterministic training.
Centralizing these values ensures consistency across the codebase.
"""

# Default master seed for deterministic training
# This seed is used for:
# - RNG initialization in the training loop
# - Dataset shuffling
# - Model weight initialization
DEFAULT_MASTER_SEED = 42

# Other training defaults
DEFAULT_EPOCHS = 10
DEFAULT_BATCH_SIZE = 256
DEFAULT_LEARNING_RATE = 0.01
DEFAULT_LOG_INTERVAL = 100  # Log every N batches (0 = no logging)

# Quick mode settings (for fast testing)
QUICK_MODE_EPOCHS = 1
QUICK_MODE_LOG_INTERVAL = 0  # No logging in quick mode

# Directory defaults
DEFAULT_TRAINING_OUTPUT = "./training-output"
CHECKPOINTS_SUBDIR = "checkpoints"

# Training-specific filenames
FINAL_CHECKPOINT_FILENAME = "final.safetensors"
TRAINING_PASSPORT_FILENAME = "passport.json"
