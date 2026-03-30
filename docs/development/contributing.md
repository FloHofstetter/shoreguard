# Contributing

Thank you for your interest in contributing to ShoreGuard! This guide covers the
workflow, setup, and standards we follow.

## Issue-first workflow

Before submitting a pull request, **open an issue** to discuss the change you have
in mind. This helps avoid duplicated effort and ensures alignment on scope and
approach. Once the issue is triaged, you can reference it in your PR.

## Setup

```bash
git clone https://github.com/your-org/shoreguard.git
cd shoreguard
uv sync --group dev
```

This installs all runtime and development dependencies in an isolated virtual
environment managed by [uv](https://docs.astral.sh/uv/).

## Running the server

```bash
uv run shoreguard
```

The server starts on `http://localhost:8000` by default.

## Check suite

Run the full check suite **before pushing** to make sure CI will pass:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -m 'not integration'
```

This covers linting, formatting, type checking, and unit tests in a single
command.

## CI requirements

All of the following checks must pass on every pull request:

- **Lint** -- ruff check
- **Format** -- ruff format
- **Typecheck** -- pyright
- **Tests** -- pytest on Python 3.12 and 3.13

## Code style

| Area         | Standard                                        |
| ------------ | ----------------------------------------------- |
| Docstrings   | Google-style                                    |
| Linting      | [ruff](https://docs.astral.sh/ruff/)            |
| Formatting   | ruff format                                     |
| Type checking| [pyright](https://github.com/microsoft/pyright) |
| Line length  | **100 characters**                              |
