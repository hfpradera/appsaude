"""Add generic external records for WHOOP payloads."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_whoop_external_records"
down_revision: str | None = "0003_activity_source_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "external_records" in inspector.get_table_names():
        return
    op.create_table(
        "external_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("data_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("data_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "data_source_id",
            "kind",
            "external_id",
            name="uq_external_record_source_kind_external",
        ),
    )
    op.create_index("ix_external_records_user_id", "external_records", ["user_id"])
    op.create_index("ix_external_records_data_source_id", "external_records", ["data_source_id"])
    op.create_index("ix_external_records_kind", "external_records", ["kind"])
    op.create_index("ix_external_records_day", "external_records", ["day"])


def downgrade() -> None:
    op.drop_index("ix_external_records_day", table_name="external_records")
    op.drop_index("ix_external_records_kind", table_name="external_records")
    op.drop_index("ix_external_records_data_source_id", table_name="external_records")
    op.drop_index("ix_external_records_user_id", table_name="external_records")
    op.drop_table("external_records")
