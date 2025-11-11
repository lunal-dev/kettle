# Attestable Builds Service

REST API for TEE-attested Rust builds and ML training. Upload source code or training data, get back cryptographically verified artifacts.

## What It Does

### Builds
Upload a Rust project zip → Verifies inputs → Builds → Generates attestation → Returns build ID

**Verification includes:** Git source, Cargo.lock, all dependencies, toolchain

### Training
Upload config + dataset → Verifies inputs → Trains model → Generates training passport → Returns training ID

**Verification includes:** Training binary (with full dependency verification), dataset, config, deterministic seed

See [main README](../README.md#phase-1-input-locking--verification) and [TRAINING.md](../TRAINING.md) for detailed documentation.

## Installation

### Local Development

```bash
cd attestable-builds
uv pip install -e ".[service]"
uvicorn service.app:app --host 127.0.0.1 --port 8000
```

### Production Deployment

```bash
cd deployment-scripts
uv run ansible-playbook playbooks/deploy.yml -e provision=true --limit attestable-build-service
```

Deploys on Azure CVM with TEE attestation.

## API

### Build Endpoints

#### POST /build

Upload zip, build project.

```bash
curl -F "source=@project.zip" http://localhost:8000/build
```

Returns:
```json
{"build_id": "550e8400-...", "success": true}
```

Or on error:
```json
{"build_id": "550e8400-...", "success": false, "error": "message"}
```

#### GET /builds/{build_id}/artifacts/{name}

Download build artifacts.

```bash
curl -O http://localhost:8000/builds/{id}/artifacts/myapp
```

### Training Endpoints

#### POST /train

Submit training job (async).

```bash
curl -F "config=@config.json" \
     -F "dataset=@dataset.zip" \
     -F "quick=false" \
     -F "attestation=true" \
     http://localhost:8000/train
```

Returns (202 Accepted):
```json
{
  "training_id": "a3f5b2c1",
  "status": "queued",
  "message": "Training job queued. Check status at /trainings/a3f5b2c1"
}
```

#### GET /trainings/{training_id}

Get training job status.

```bash
curl http://localhost:8000/trainings/a3f5b2c1
```

Returns (while running):
```json
{
  "training_id": "a3f5b2c1",
  "status": "running",
  "started_at": "2025-01-15T10:30:00Z"
}
```

Returns (when complete):
```json
{
  "training_id": "a3f5b2c1",
  "status": "success",
  "passport": { /* full training passport */ },
  "attestation": "base64_encoded...",
  "artifacts": ["final.safetensors"],
  "metrics": {
    "total_epochs": 10,
    "final_train_loss": 0.089
  },
  "completed_at": "2025-01-15T10:45:12Z"
}
```

#### GET /trainings/{training_id}/artifacts/{name}

Download model weights.

```bash
curl -O http://localhost:8000/trainings/{id}/artifacts/final.safetensors
```

## Examples

### Build Example

```bash
# Create test project
mkdir test && cd test
cargo init --name myapp
echo 'serde = "1.0"' >> Cargo.toml
cargo generate-lockfile && cargo fetch
git init && git add . && git commit -m "init"

# Build
cd ..
zip -r test.zip test/
BUILD_ID=$(curl -F "source=@test.zip" http://localhost:8000/build | jq -r '.build_id')

# Download
curl -O http://localhost:8000/builds/$BUILD_ID/artifacts/myapp
```

### Training Example

```bash
# Prepare training data
cd examples/training/mnist
python download.py  # Download MNIST dataset
zip -r dataset.zip data/

# Submit training job
TRAINING_ID=$(curl -F "config=@config.json" \
                    -F "dataset=@dataset.zip" \
                    -F "quick=true" \
                    -F "attestation=true" \
                    http://localhost:8000/train | jq -r '.training_id')

# Poll status
while true; do
  STATUS=$(curl -s http://localhost:8000/trainings/$TRAINING_ID | jq -r '.status')
  echo "Status: $STATUS"
  [[ "$STATUS" == "success" ]] && break
  [[ "$STATUS" == "failed" ]] && exit 1
  sleep 10
done

# Download results
curl -s http://localhost:8000/trainings/$TRAINING_ID | jq '.passport' > passport.json
curl -O http://localhost:8000/trainings/$TRAINING_ID/artifacts/final.safetensors

# Verify
kettle train-verify passport.json
```

## Storage

Storage root: `$KETTLE_STORAGE_DIR` (default: `/tmp/kettle`)

### Builds

```
builds/{build_id}/
├── source.zip
├── source/           (extracted)
├── passport.json
├── evidence.b64
└── artifacts/
```

### Training

```
trainings/{training_id}/
├── config.json       (model config)
├── dataset.zip       (uploaded dataset)
├── dataset/          (extracted)
├── output/
│   ├── checkpoints/
│   │   └── final.safetensors
│   └── passport.json
├── evidence.b64      (optional)
└── status.json       (job status)
```

## Requirements

- Rust toolchain (for building projects)
- attestable-builds CLI
- FastAPI + uvicorn

## Implementation

The service calls existing CLI functions:

```python
verify_inputs()      # Verify git, Cargo.lock, dependencies, toolchain
execute_build()      # Run cargo build
generate_passport()  # Create passport.json
generate_attestation()  # Create evidence.b64
```

All logic lives in the CLI. Service is just HTTP wrapper.

## Supported Projects

Rust projects with:
- `Cargo.toml`
- `Cargo.lock`
- Git repository (optional but recommended)

## License

MIT
