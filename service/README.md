# Attestable Builds Service

REST API for TEE-attested Rust builds. Upload source code, get back cryptographically verified artifacts.

## What It Does

Upload a Rust project zip → Verifies inputs → Builds → Generates attestation → Returns build ID

Download: passport.json, evidence.b64, custom_data.hex, artifacts

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

### POST /build

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

### GET /build/{build_id}/{file}

Download files: `passport.json`, `evidence.b64`, `custom_data.hex`

```bash
curl -O http://localhost:8000/build/{id}/passport.json
curl -O http://localhost:8000/build/{id}/evidence.b64
curl -O http://localhost:8000/build/{id}/custom_data.hex
```

## Example

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
curl -O http://localhost:8000/build/$BUILD_ID/passport.json
curl -O http://localhost:8000/build/$BUILD_ID/evidence.b64
```

## Storage

Builds stored at `/var/lib/attestable-builds/{build_id}/`:

```
{build_id}/
├── source.zip
├── source/           (extracted)
├── passport.json
├── evidence.b64
└── custom_data.hex
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
