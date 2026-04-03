#!/usr/bin/env bash
# verify_migrations.sh — create a fresh test database and run all Alembic migrations.
#
# Usage:
#   ./scripts/verify_migrations.sh                   # SQLite (default)
#   DATABASE_URL=postgresql://... ./scripts/verify_migrations.sh
#
# Exit codes:
#   0  all migrations applied successfully
#   1  migration failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

if [[ -n "${DATABASE_URL:-}" ]]; then
    echo "Using provided DATABASE_URL."
    DB_URL="$DATABASE_URL"
else
    TMPDIR_PATH="$(mktemp -d)"
    DB_FILE="$TMPDIR_PATH/verify_migrations_test.db"
    DB_URL="sqlite:///$DB_FILE"
    echo "No DATABASE_URL set — using temporary SQLite database: $DB_FILE"
    # shellcheck disable=SC2064
    trap "rm -rf '$TMPDIR_PATH'" EXIT
fi

echo ""
echo "Running migrations against: $DB_URL"
echo "---"

export DB_URL

uv run python - <<'EOF'
import os
import sys
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pathlib import Path
from sqlalchemy import create_engine, inspect

url = os.environ["DB_URL"]

alembic_dir = Path("shoreguard/alembic")
cfg = AlembicConfig()
cfg.set_main_option("script_location", str(alembic_dir))
cfg.set_main_option("sqlalchemy.url", url)

print("Upgrading to head...")
command.upgrade(cfg, "head")

engine = create_engine(url)
with engine.connect() as conn:
    ctx = MigrationContext.configure(conn)
    current = ctx.get_current_revision()

script = ScriptDirectory.from_config(cfg)
expected = script.get_current_head()

if current != expected:
    print(f"ERROR: current revision '{current}' != expected head '{expected}'", file=sys.stderr)
    sys.exit(1)

tables = inspect(engine).get_table_names()
print(f"Tables created: {sorted(tables)}")
print(f"Current revision: {current}")
print("")
print("All migrations applied successfully.")
engine.dispose()
EOF
