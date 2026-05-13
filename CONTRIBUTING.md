# Contributing to ShoreGuard

Thank you for your interest in ShoreGuard — the open-source control
plane for NVIDIA OpenShell. Contributions of all sizes are welcome.

## Before you start

By contributing, you agree that your contribution will be licensed
under the same terms as the project (Apache License, Version 2.0).
A bot will ask you to sign the Individual CLA on your first pull
request — this is a one-time step that lets the project relicense
in the future if needed.

## Reporting issues

- **Bug reports** and **feature requests** go through GitHub
  Issues — pick the right template from the *New Issue* picker.
- **Security vulnerabilities** must NOT be filed as public issues.
  See [`SECURITY.md`](SECURITY.md) for the responsible-disclosure
  path.

## Development environment

ShoreGuard is a Python 3.14 project managed with
[`uv`](https://docs.astral.sh/uv/). Postgres 16 is required for the
full integration suite (SQLite covers the unit tests).

```bash
git clone git@github.com:FloHofstetter/shoreguard.git
cd shoreguard
uv sync
uv run shoreguard           # serves http://127.0.0.1:8000
```

## Local gates

```bash
uv run pytest -m 'not integration'         # unit tests
uv run pytest -m integration               # integration (needs PG + OpenShell)
uv run ruff check . && uv run ruff format --check .
uv run pyright                             # type-check
uv run pydoclint shoreguard                # docstring lint
bash scripts/verify_migrations.sh          # alembic round-trip
```

`uv run pre-commit install` arms the hook so most of these run on
every commit.

## Branch and commit conventions

- **Branch**: any descriptive name.
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/)
  format — `feat(scope): …`, `fix(scope): …`, etc. Scope is the
  subsystem (`api`, `ui`, `policy`, `sandbox`, `tf`, `alembic`,
  `docs`, `ci`, …).

## Pull request process

1. Open a draft PR early.
2. Fill in the PR template. Tick every gate that applies.
3. If the change adds a database migration, follow the
   migration-runbook section of the PR template (rollback path
   documented or `NotImplementedError` raised with a comment).
4. One reviewer signs off. CI must be green.
5. Update `CHANGELOG.md` and `ROADMAP.md` in the same PR.

## Fixing bugs at the source

ShoreGuard, the `terraform-provider-shoreguard` Go provider, and
the OpenClaw / Paperclip plugins are first-party repositories.
Fix bugs at the source rather than working around them downstream.

## Code of conduct

Be kind. Disagree with code, not with people. The project follows
the spirit of the Contributor Covenant; the formal text will be
added before the public visibility flip.
