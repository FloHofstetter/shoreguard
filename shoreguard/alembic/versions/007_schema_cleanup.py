"""Schema cleanup: timestamps to DateTime, gateway integer PK, audit FK.

Revision ID: 007
Revises: 006
Create Date: 2026-04-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Convert timestamps to DateTime, add integer PK to gateways, add audit FK."""
    conn = op.get_bind()
    dialect = conn.dialect.name

    # --- Phase 1: Convert timestamp columns String → DateTime ---
    # SQLite: batch_alter_table recreates the table; ISO strings are natively
    # compatible with SQLAlchemy's DateTime adapter on SQLite.
    # PostgreSQL: ALTER COLUMN with USING cast.

    _timestamp_changes = [
        ("gateways", ["registered_at", "last_seen"]),
        ("users", ["created_at"]),
        ("service_principals", ["created_at", "last_used"]),
        ("audit_log", ["timestamp"]),
    ]

    if dialect == "sqlite":
        for table, columns in _timestamp_changes:
            with op.batch_alter_table(table) as batch_op:
                for col in columns:
                    batch_op.alter_column(col, type_=sa.DateTime(timezone=True))
    else:
        for table, columns in _timestamp_changes:
            for col in columns:
                op.alter_column(
                    table,
                    col,
                    type_=sa.DateTime(timezone=True),
                    existing_type=sa.String(),
                    postgresql_using=f"{col}::timestamptz",
                )

    # --- Phase 2: Rebuild gateways table with integer PK ---

    # 2a. Create new gateways table with integer PK
    op.create_table(
        "_gateways_new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(253), unique=True, nullable=False),
        sa.Column("endpoint", sa.String(260), nullable=False),
        sa.Column("scheme", sa.String(), nullable=False, server_default="https"),
        sa.Column("auth_mode", sa.String(), nullable=True),
        sa.Column("ca_cert", sa.LargeBinary(), nullable=True),
        sa.Column("client_cert", sa.LargeBinary(), nullable=True),
        sa.Column("client_key", sa.LargeBinary(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(), nullable=False, server_default="unknown"),
    )

    # 2b. Copy data (id auto-assigned by autoincrement)
    conn.execute(
        sa.text(
            "INSERT INTO _gateways_new"
            " (name, endpoint, scheme, auth_mode, ca_cert, client_cert,"
            "  client_key, metadata_json, registered_at, last_seen, last_status)"
            " SELECT name, endpoint, scheme, auth_mode, ca_cert, client_cert,"
            "  client_key, metadata_json, registered_at, last_seen, last_status"
            " FROM gateways"
        )
    )

    # 2c. Drop FK constraints on dependent tables before dropping gateways
    if dialect == "sqlite":
        # SQLite: FKs are not enforced during DDL; we'll rebuild these tables below.
        conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    else:
        # PostgreSQL: drop FK constraints explicitly
        op.drop_constraint(
            "user_gateway_roles_gateway_name_fkey",
            "user_gateway_roles",
            type_="foreignkey",
        )
        op.drop_constraint(
            "sp_gateway_roles_gateway_name_fkey",
            "sp_gateway_roles",
            type_="foreignkey",
        )

    # 2d. Drop old gateways, rename new
    op.drop_table("gateways")
    op.rename_table("_gateways_new", "gateways")

    # Recreate indexes on the new gateways table
    op.create_index("ix_gateways_endpoint", "gateways", ["endpoint"])
    op.create_index("ix_gateways_registered_at", "gateways", ["registered_at"])

    # --- Phase 3: Migrate role tables gateway_name → gateway_id ---

    # 3a. Add gateway_id column to dependent tables
    op.add_column("user_gateway_roles", sa.Column("gateway_id", sa.Integer(), nullable=True))
    op.add_column("sp_gateway_roles", sa.Column("gateway_id", sa.Integer(), nullable=True))

    # 3b. Populate gateway_id from name lookup
    conn.execute(
        sa.text(
            "UPDATE user_gateway_roles SET gateway_id ="
            " (SELECT id FROM gateways WHERE gateways.name = user_gateway_roles.gateway_name)"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE sp_gateway_roles SET gateway_id ="
            " (SELECT id FROM gateways WHERE gateways.name = sp_gateway_roles.gateway_name)"
        )
    )

    # 3c. Drop old indexes that reference gateway_name before rebuilding
    op.drop_index("ix_user_gateway_roles_gateway_name", table_name="user_gateway_roles")
    op.drop_index("ix_sp_gateway_roles_gateway_name", table_name="sp_gateway_roles")

    # 3d. Rebuild role tables: drop gateway_name, make gateway_id non-null, add FK
    with op.batch_alter_table("user_gateway_roles") as batch_op:
        batch_op.drop_column("gateway_name")
        batch_op.alter_column("gateway_id", nullable=False, existing_type=sa.Integer())
        batch_op.create_unique_constraint("uq_ugr_user_gw", ["user_id", "gateway_id"])
        batch_op.create_foreign_key(
            "fk_ugr_gateway",
            "gateways",
            ["gateway_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_ugr_user",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("sp_gateway_roles") as batch_op:
        batch_op.drop_column("gateway_name")
        batch_op.alter_column("gateway_id", nullable=False, existing_type=sa.Integer())
        batch_op.create_unique_constraint("uq_sgr_sp_gw", ["sp_id", "gateway_id"])
        batch_op.create_foreign_key(
            "fk_sgr_gateway",
            "gateways",
            ["gateway_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_sgr_sp",
            "service_principals",
            ["sp_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # --- Phase 4: Audit log — rename gateway → gateway_name, add gateway_id FK ---
    op.add_column("audit_log", sa.Column("gateway_id", sa.Integer(), nullable=True))

    # Populate gateway_id from existing gateway name
    conn.execute(
        sa.text(
            "UPDATE audit_log SET gateway_id ="
            " (SELECT id FROM gateways WHERE gateways.name = audit_log.gateway)"
        )
    )

    with op.batch_alter_table("audit_log") as batch_op:
        # Rename gateway → gateway_name
        batch_op.alter_column("gateway", new_column_name="gateway_name")
        # Add FK for gateway_id (SET NULL on gateway deletion)
        batch_op.create_foreign_key(
            "fk_audit_gateway",
            "gateways",
            ["gateway_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # The old ix_audit_log_gateway index (on "gateway" column) was renamed
    # by batch_alter_table. Create the new named index for gateway_name.
    # Other audit indexes are preserved by batch_alter_table.
    if dialect == "sqlite":
        # batch_alter_table on SQLite renames the index target column automatically
        pass
    else:
        op.drop_index("ix_audit_log_gateway", table_name="audit_log")
        op.create_index("ix_audit_log_gateway_name", "audit_log", ["gateway_name"])

    # Re-enable FK enforcement for SQLite
    if dialect == "sqlite":
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))


def downgrade() -> None:
    """Downgrade is not supported — this migration is destructive.

    Raises:
        NotImplementedError: Always raised; restore from backup instead.
    """
    raise NotImplementedError(
        "Migration 007 (schema cleanup) cannot be reversed. Restore from backup if needed."
    )
