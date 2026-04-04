# ShoreGuard development tasks

# Start dev server with local mode and no auth
dev:
    uv run shoreguard --local --no-auth

# Run unit tests (skip integration tests)
test *args:
    uv run pytest -m 'not integration' {{args}}

# Run linter
lint:
    uv run ruff check .

# Auto-format code
format:
    uv run ruff format .

# Run all checks (lint + format check + typecheck + tests)
check:
    uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -m 'not integration'

# Build Docker image
docker-build:
    docker build -t shoreguard:dev .

# Start production-like Docker stack
docker-up:
    docker compose up -d

# Stop Docker stack
docker-down:
    docker compose down

# Serve docs locally
docs:
    uv run mkdocs serve

# Sync dependencies
sync:
    uv sync --group dev
