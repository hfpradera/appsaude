"""Add non-destructive links between a primary activity and external sources."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_activity_source_links"
down_revision: str | None = "0002_strava_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_source_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("data_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_data_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="linked"),
        sa.Column("reconciliation_method", sa.String(length=80), nullable=True),
        sa.Column("reconciliation_confidence", sa.String(length=40), nullable=True),
        sa.Column("reconciliation_score", sa.Float(), nullable=True),
        sa.Column("reconciliation_evidence_json", sa.Text(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("data_source_id", "external_id", name="uq_source_link_external"),
    )


def downgrade() -> None:
    op.drop_table("activity_source_links")
