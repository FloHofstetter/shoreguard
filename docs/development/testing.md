# Testing

ShoreGuard maintains a comprehensive test suite to catch regressions early and
keep confidence high during refactors.

## Test categories

| Category    | Count   | Scope                                              |
| ----------- | ------- | -------------------------------------------------- |
| Unit        | ~1 100  | Client, services, API routes, DB, registry, OIDC   |
| Integration | 35      | Live gRPC calls against a real OpenShell gateway    |
| Mutation    | 72% kill rate | Fault injection via mutmut                    |

## Running tests

**All unit tests:**

```bash
uv run pytest
```

**With coverage report:**

```bash
uv run pytest --cov=shoreguard --cov-report=term-missing
```

**Integration tests only:**

```bash
uv run pytest tests/integration/ -m integration
```

**Skip integration tests:**

```bash
uv run pytest -m 'not integration'
```

**Mutation testing:**

```bash
uv run mutmut run
```

## Test framework

- **pytest** with **pytest-asyncio** in auto mode -- async test functions are
  detected automatically without explicit markers.

## Fixtures

Shared fixtures are defined in `conftest.py` and provide:

- `mock_client` -- a mocked gRPC client for unit-level service tests
- `api_client` -- an HTTPX `AsyncClient` wired to the FastAPI test app
- In-memory **SQLite** database -- each test session starts with a clean schema,
  keeping tests fast and isolated
