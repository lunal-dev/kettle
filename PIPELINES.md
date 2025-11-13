# Pipeline System

**[← Main README](README.md)** | **[Training Documentation](TRAINING.md)**

Orchestrate multi-step attestable build and training workflows using GitHub Actions-like YAML pipelines.

## Overview

The pipeline system provides:

- **GitHub Actions-like syntax** - Familiar YAML format for defining workflows
- **Dependency management** - Automatic job ordering based on `depends_on`
- **Variable interpolation** - Pass outputs between jobs with `${{ ... }}` syntax
- **Built-in actions** - Secure, curated actions for build, train, and verify operations
- **Synchronous execution** - Simple, debuggable blocking execution model
- **Attestation-aware** - Support TEE attestation at any pipeline stage

## Quick Start

### 1. Create a Pipeline YAML File

```yaml
name: Build and Train Pipeline
version: "1.0"

env:
  ATTESTATION_ENABLED: false
  QUICK_MODE: true

jobs:
  build-binary:
    name: Build Training Binary
    action: build
    inputs:
      project_dir: ./src/kettle/training/candle
      release: true
      attestation: ${{ env.ATTESTATION_ENABLED }}
    outputs:
      - passport.json

  train-model:
    name: Train Model
    action: train
    depends_on: [build-binary]
    inputs:
      config: ./examples/training/mnist/config.json
      dataset: ./examples/training/mnist/data
      quick: ${{ env.QUICK_MODE }}
      attestation: ${{ env.ATTESTATION_ENABLED }}
    outputs:
      - passport.json
      - final.safetensors

  verify:
    name: Verify Training
    action: train-verify
    depends_on: [train-model]
    inputs:
      passport: ${{ jobs.train-model.outputs.passport.json }}
```

### 2. Run the Pipeline

```bash
kettle pipeline build-train.yml
```

## Pipeline YAML Schema

### Top-Level Fields

```yaml
name: string # Required: Pipeline name
version: string # Required: Schema version (currently "1.0")
env: # Optional: Environment variables
  KEY: value
jobs: # Required: Dictionary of jobs
  job-id:
    # Job definition
```

### Job Definition

```yaml
jobs:
  job-id: # Unique job identifier
    name: string # Optional: Human-readable name (defaults to job-id)
    action: string # Required: Action type (build, train, verify, train-verify)
    inputs: # Required: Action-specific inputs
      key: value
    outputs: # Optional: List of output filenames that will be produced
      - filename.ext
    depends_on: # Optional: List of job IDs this job depends on
      - other-job-id
```

## Variable Interpolation

Use `${{ ... }}` syntax to reference variables and job outputs:

### Environment Variables

```yaml
env:
  RELEASE_MODE: true
  OUTPUT_DIR: ./my-output

jobs:
  my-job:
    inputs:
      release: ${{ env.RELEASE_MODE }}
      output: ${{ env.OUTPUT_DIR }}
```

### Job Outputs

Reference outputs from previous jobs:

```yaml
jobs:
  job1:
    outputs:
      - passport.json

  job2:
    depends_on: [job1]
    inputs:
      passport: ${{ jobs.job1.outputs.passport.json }}
```

**Output Names:**
- Outputs are declared as a list of filenames (not paths)
- Filenames must match what the action actually produces
- Include file extensions (e.g., `passport.json`, `final.safetensors`, `evidence.b64`)
- Reference outputs using `${{ jobs.JOB_ID.outputs.FILENAME }}`

**Important:** Jobs can only reference outputs from jobs they depend on (directly or transitively).

## Built-in Actions

### `build` - Build Rust Project

Builds a Rust project with full attestable builds verification.

**Required Inputs:**

- `project_dir` (Path) - Path to Cargo project directory

**Optional Inputs:**

- `release` (bool) - Build in release mode (default: true)
- `attestation` (bool) - Generate TEE attestation (default: false)
- `verbose` (bool) - Show verbose output (default: false)
- `allow_dirty` (bool) - Allow uncommitted changes in git, for testing (default: false)
- `output` (Path) - Output directory (default: job output dir)

**Outputs:**

- `passport.json` - Build passport
- `evidence.b64` - TEE attestation (only if attestation enabled)

**Example:**

```yaml
build-app:
  action: build
  inputs:
    project_dir: ./my-rust-app
    release: true
    attestation: false
  outputs:
    - passport.json
```

### `train` - Train ML Model

Trains a machine learning model using Candle framework.

**Required Inputs:**

- `config` (Path) - Path to model configuration JSON
- `dataset` (Path) - Path to dataset directory

**Optional Inputs:**

- `quick` (bool) - Quick mode, 1 epoch (default: false)
- `rebuild_binary` (bool) - Force rebuild training binary (default: false)
- `attestation` (bool) - Generate TEE attestation (default: false)

**Outputs:**

- `passport.json` - Training passport
- `final.safetensors` - Trained model weights
- `evidence.b64` - TEE attestation (only if attestation enabled)

**Example:**

```yaml
train-mnist:
  action: train
  inputs:
    config: ./config.json
    dataset: ./data
    quick: false
    attestation: true
  outputs:
    - passport.json
    - final.safetensors
    - evidence.b64
```

### `verify` - Verify Build Passport

Verifies a build passport against known values.

**Required Inputs:**

- `passport` (Path) - Path to passport JSON file

**Outputs:** None (validation action)

**Example:**

```yaml
verify-build:
  action: verify
  depends_on: [build-app]
  inputs:
    passport: ${{ jobs.build-app.outputs.passport.json }}
```

### `train-verify` - Verify Training Passport

Verifies a training passport including merkle tree validation.

**Required Inputs:**

- `passport` (Path) - Path to training passport JSON file

**Outputs:** None (validation action)

**Example:**

```yaml
verify-training:
  action: train-verify
  depends_on: [train-model]
  inputs:
    passport: ${{ jobs.train-model.outputs.passport.json }}
```

## Job Dependencies

Jobs are executed in dependency order using topological sort:

```yaml
jobs:
  job-a:
    action: build

  job-b:
    depends_on: [job-a] # Runs after job-a
    action: train

  job-c:
    depends_on: [job-a] # Also runs after job-a (parallel with job-b)
    action: verify

  job-d:
    depends_on: [job-b, job-c] # Runs after both job-b and job-c
    action: train-verify
```

**Execution order:** `job-a` → `job-b` + `job-c` (parallel) → `job-d`

**Note:** Circular dependencies are detected and rejected during pipeline validation.

## Storage and Artifacts

Pipeline runs are stored in `~/.cache/kettle/pipelines/{run-id}/`:

```
~/.cache/kettle/pipelines/
└── a1b2c3d4/                    # Run ID
    ├── metadata.json             # Pipeline metadata and status
    └── jobs/
        ├── build-binary/
        │   ├── status.json       # Job status
        │   └── build-output/     # Job outputs
        │       └── passport.json
        └── train-model/
            ├── status.json
            └── training-output/
                ├── passport.json
                └── checkpoints/
                    └── final.safetensors
```

## CLI Usage

### Run a Pipeline

```bash
# Basic usage
kettle pipeline pipeline.yml

# Verbose output
kettle pipeline pipeline.yml --verbose

# Override environment variables (future enhancement)
kettle pipeline pipeline.yml --env ATTESTATION_ENABLED=true
```

### Pipeline Output

```
╭───────────── Pipeline Execution ─────────────╮
│ Build and Train Pipeline                     │
│ Run ID: a1b2c3d4                            │
│ Output: ~/.cache/kettle/pipelines/a1b2c3d4  │
╰──────────────────────────────────────────────╯

→ Build Training Binary (build)
  ✓ passport: build-output/passport.json

→ Train Model (train)
  ✓ passport: training-output/passport.json
  ✓ model: training-output/checkpoints/final.safetensors

→ Verify Training (train-verify)
  ✓ Completed

✓ Pipeline completed successfully
Run ID: a1b2c3d4
Output: ~/.cache/kettle/pipelines/a1b2c3d4
```

## CI/CD Integration

### GitHub Actions

```yaml
name: Attestable Build Pipeline
on: [push]

jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.10"

      - name: Install kettle
        run: pip install -e .

      - name: Run pipeline
        run: kettle pipeline .github/pipelines/build-train.yml

      - name: Upload artifacts
        uses: actions/upload-artifact@v3
        with:
          name: passports
          path: ~/.cache/kettle/pipelines/*/jobs/*/
```

### GitLab CI

```yaml
stages:
  - pipeline

attestable-pipeline:
  stage: pipeline
  image: python:3.10
  script:
    - pip install -e .
    - kettle pipeline pipeline.yml
  artifacts:
    paths:
      - ~/.cache/kettle/pipelines/
```

## Best Practices

### 1. Use Descriptive Job Names

```yaml
jobs:
  build-training-binary:
    name: Build ML Training Binary
    action: build
```

### 2. Enable Attestation in Production

```yaml
env:
  # Set via CI/CD environment variable
  IS_PRODUCTION: false

jobs:
  train:
    inputs:
      attestation: ${{ env.IS_PRODUCTION }}
```

### 3. Chain Verification Steps

Always verify training outputs:

```yaml
jobs:
  train:
    action: train

  verify:
    depends_on: [train]
    action: train-verify
    inputs:
      passport: ${{ jobs.train.outputs.passport.json }}
```

### 4. Use Quick Mode for Testing

```yaml
env:
  QUICK_MODE: true # Override via CLI in production

jobs:
  train:
    inputs:
      quick: ${{ env.QUICK_MODE }}
```

## Examples

See `examples/pipelines/` for complete examples:

- `build-train.yml` - Full build and train workflow with verification

## Troubleshooting

### Pipeline Validation Errors

**Error:** "Circular dependency detected"

- Check `depends_on` relationships
- Draw dependency graph to identify cycles

**Error:** "Job depends on unknown job"

- Verify job IDs in `depends_on` match actual job definitions
- Check for typos in job references

### Variable Interpolation Errors

**Error:** "Environment variable not found"

- Ensure variable is defined in `env` section
- Check variable name spelling

**Error:** "Job outputs not available"

- Verify job has `depends_on` for the referenced job
- Check output name matches the job's `outputs` declaration

### Execution Errors

**Error:** "Action failed"

- Run with `--verbose` for detailed error messages
- Check job inputs are valid paths
- Verify all required inputs are provided

## See Also

- [Main README](README.md) - Attestable builds overview
- [Training Documentation](TRAINING.md) - ML training details
- [Architecture](ARCHITECTURE.md) - System architecture
