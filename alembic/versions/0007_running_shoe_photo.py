"""Add photo_path to running_shoes."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_running_shoe_photo"
down_revision: str | None = "0006_ai_responses_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "running_shoes" in tables:
        columns = {column["name"] for column in inspector.get_columns("running_shoes")}
        if "photo_path" not in columns:
            op.add_column("running_shoes", sa.Column("photo_path", sa.String(length=500), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "running_shoes" in tables:
        columns = {column["name"] for column in inspector.get_columns("running_shoes")}
        if "photo_path" in columns:
            op.drop_column("running_shoes", "photo_path")
