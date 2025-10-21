.PHONY: help test test-docker build-docker run-docker clean extract-outputs interactive install

help:
	@echo "Attestable Builds - Phase 2 Testing"
	@echo ""
	@echo "Available targets:"
	@echo "  make test          - Run all tests locally with uv"
	@echo "  make test-docker   - Build Docker image and run Phase 2 build"
	@echo "  make build-docker  - Build Docker test image"
	@echo "  make run-docker    - Run Phase 2 build in Docker"
	@echo "  make extract       - Extract passport and attestation from Docker"
	@echo "  make interactive   - Run interactive shell in Docker"
	@echo "  make measure       - Generate golden measurement"
	@echo "  make clean         - Clean up outputs and Docker images"
	@echo "  make install       - Install attestable-builds locally"

# Run tests locally
test:
	uv run pytest tests/ -v

# Build and run Docker test
test-docker: build-docker run-docker

# Build Docker test image
build-docker:
	@echo "Building Docker test image..."
	docker build -f Dockerfile.test -t attestable-builds-test .

# Run Phase 2 build in Docker
run-docker:
	@echo "Running Phase 2 attestable build in Docker..."
	docker run --rm attestable-builds-test

# Extract outputs from Docker
extract:
	@echo "Extracting passport and attestation..."
	@mkdir -p outputs
	docker run --rm -v $$(pwd)/outputs:/outputs attestable-builds-test sh -c \
		'python3 -m attestable_builds.cli build . > /dev/null 2>&1 && cp passport.json attestation.json /outputs/'
	@echo "✓ Outputs saved to outputs/"
	@echo ""
	@echo "Passport (first 20 lines):"
	@python3 -m json.tool outputs/passport.json | head -20
	@echo ""
	@echo "Attestation (first 20 lines):"
	@python3 -m json.tool outputs/attestation.json | head -20

# Run interactive shell in Docker
interactive:
	docker run --rm -it attestable-builds-test bash

# Generate golden measurement locally
measure:
	uv run python -m attestable_builds.cli measure -o golden-measurements.json
	@echo ""
	@cat golden-measurements.json | python3 -m json.tool

# Clean up
clean:
	rm -rf outputs/
	rm -f golden-measurements.json passport.json attestation.json
	docker rmi attestable-builds-test 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

# Install locally
install:
	uv pip install -e .
