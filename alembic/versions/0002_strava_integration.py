"""Add non-destructive state for Strava OAuth and incremental sync."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_strava_integration"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("data_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("athlete_external_id", sa.String(length=80), nullable=True),
        sa.Column("athlete_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="disconnected"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "data_source_id", name="uq_integration_user_source"),
    )
    op.create_index("ix_integration_states_user_id", "integration_states", ["user_id"])
    op.create_index("ix_integration_states_data_source_id", "integration_states", ["data_source_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_states_data_source_id", table_name="integration_states")
    op.drop_index("ix_integration_states_user_id", table_name="integration_states")
    op.drop_table("integration_states")
