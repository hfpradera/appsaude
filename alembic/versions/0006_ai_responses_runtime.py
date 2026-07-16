"""Add OpenAI runtime tracking and pending actions."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_ai_responses_runtime"
down_revision: str | None = "0005_ai_assistant_meals_shoes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "ai_runs" in tables:
        columns = {column["name"] for column in inspector.get_columns("ai_runs")}
        for name, column in [
            ("total_tokens", sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0")),
            ("cached_tokens", sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0")),
            ("tool_call_count", sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0")),
            ("currency", sa.Column("currency", sa.String(length=12), nullable=True)),
            ("provider_response_id", sa.Column("provider_response_id", sa.String(length=255), nullable=True)),
        ]:
            if name not in columns:
                op.add_column("ai_runs", column)

    if "ai_pending_actions" not in tables:
        op.create_table(
            "ai_pending_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("ai_conversations.id"), nullable=False),
            sa.Column("tool_name", sa.String(length=120), nullable=False),
            sa.Column("arguments_json", sa.Text(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_ai_pending_actions_user_id", "ai_pending_actions", ["user_id"])
        op.create_index("ix_ai_pending_actions_conversation_id", "ai_pending_actions", ["conversation_id"])
        op.create_index("ix_ai_pending_actions_tool_name", "ai_pending_actions", ["tool_name"])
        op.create_index("ix_ai_pending_actions_expires_at", "ai_pending_actions", ["expires_at"])
        op.create_index("ix_ai_pending_actions_status", "ai_pending_actions", ["status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "ai_pending_actions" in tables:
        for index in [
            "ix_ai_pending_actions_status",
            "ix_ai_pending_actions_expires_at",
            "ix_ai_pending_actions_tool_name",
            "ix_ai_pending_actions_conversation_id",
            "ix_ai_pending_actions_user_id",
        ]:
            op.drop_index(index, table_name="ai_pending_actions")
        op.drop_table("ai_pending_actions")
