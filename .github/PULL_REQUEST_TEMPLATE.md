## Summary

<!-- Briefly describe what this PR does and why. -->

## Test plan

<!-- How did you test this? What edge cases did you consider? -->

- [ ] Unit tests pass (`uv run pytest -m 'not integration'`)
- [ ] Linting passes (`uv run ruff check . && uv run ruff format --check .`)

## Database migrations

<!-- If this PR adds or modifies a migration file, complete this checklist. -->

If this PR includes a database migration, the migration must be tested in CI and the rollback
procedure must be documented.

- [ ] This PR does **not** include a database migration *(skip the rest of this section)*
- [ ] Migration tested locally with `./scripts/verify_migrations.sh`
- [ ] `downgrade()` is implemented, or a `NotImplementedError` is raised with a comment
      explaining why the migration is irreversible
- [ ] If irreversible: rollback steps documented in `docs/admin/migration-runbook.md`
- [ ] Pre-migration backup procedure confirmed (see runbook)
