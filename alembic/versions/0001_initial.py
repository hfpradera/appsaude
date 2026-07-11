# Initial schema.
#
# Revision ID: 0001_initial
# Revises:
# Create Date: 2026-07-11

from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The application uses SQLAlchemy metadata creation in MVP startup.
    # Alembic is wired for future controlled migrations.
    pass


def downgrade() -> None:
    pass
