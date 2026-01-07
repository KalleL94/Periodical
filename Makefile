.PHONY: dev test coverage lint format clean help

# Default target
.DEFAULT_GOAL := help

# Development server with auto-reload (avoids file watch limit issues)
dev:
	@./scripts/dev.sh

# Run development server on custom port
dev-port:
	@./scripts/dev.sh --port $(PORT)

# Run tests
test:
	@pytest

# Run tests with coverage report
coverage:
	@pytest --cov=app --cov-report=html --cov-report=term

# Lint code with ruff
lint:
	@ruff check .

# Format code with ruff
format:
	@ruff format .

# Lint and format
check: lint format

# Clean up cache and build files
clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name ".coverage" -delete 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Cleaned up cache and build files"

# Show help
help:
	@echo "Available commands:"
	@echo "  make dev        - Start development server (default: 127.0.0.1:8001)"
	@echo "  make dev-port   - Start dev server on custom port (e.g., make dev-port PORT=8002)"
	@echo "  make test       - Run tests"
	@echo "  make coverage   - Run tests with coverage report"
	@echo "  make lint       - Lint code with ruff"
	@echo "  make format     - Format code with ruff"
	@echo "  make check      - Lint and format code"
	@echo "  make clean      - Clean up cache and build files"
	@echo "  make help       - Show this help message"
