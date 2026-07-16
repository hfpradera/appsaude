"""Add AI assistant, meal logs, and running shoes."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_ai_assistant_meals_shoes"
down_revision: str | None = "0004_whoop_external_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "ai_conversations" not in tables:
        op.create_table(
            "ai_conversations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("provider_conversation_id", sa.String(length=255), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_ai_conversations_user_id", "ai_conversations", ["user_id"])
    if "ai_messages" not in tables:
        op.create_table(
            "ai_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("ai_conversations.id"), nullable=False),
            sa.Column("role", sa.String(length=40), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("tool_name", sa.String(length=120), nullable=True),
            sa.Column("tool_call_id", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ai_messages_conversation_id", "ai_messages", ["conversation_id"])
    if "ai_runs" not in tables:
        op.create_table(
            "ai_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("ai_conversations.id"), nullable=False),
            sa.Column("model", sa.String(length=120), nullable=False),
            sa.Column("prompt_version", sa.String(length=40), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False),
            sa.Column("output_tokens", sa.Integer(), nullable=False),
            sa.Column("estimated_cost", sa.Float(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("error_sanitized", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ai_runs_conversation_id", "ai_runs", ["conversation_id"])
    if "ai_memories" not in tables:
        op.create_table(
            "ai_memories",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("category", sa.String(length=80), nullable=False),
            sa.Column("key", sa.String(length=160), nullable=False),
            sa.Column("value_json", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("confirmed_by_user", sa.Boolean(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", "category", "key", name="uq_ai_memory_user_category_key"),
        )
        op.create_index("ix_ai_memories_user_id", "ai_memories", ["user_id"])
        op.create_index("ix_ai_memories_category", "ai_memories", ["category"])
    if "ai_audit_logs" not in tables:
        op.create_table(
            "ai_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("tool_name", sa.String(length=120), nullable=False),
            sa.Column("target_type", sa.String(length=80), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=80), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ai_audit_logs_user_id", "ai_audit_logs", ["user_id"])
        op.create_index("ix_ai_audit_logs_tool_name", "ai_audit_logs", ["tool_name"])
    if "meal_logs" not in tables:
        op.create_table(
            "meal_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("meal_type", sa.String(length=80), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("confirmed", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_meal_logs_user_id", "meal_logs", ["user_id"])
        op.create_index("ix_meal_logs_consumed_at", "meal_logs", ["consumed_at"])
    if "meal_items" not in tables:
        op.create_table(
            "meal_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("meal_log_id", sa.Integer(), sa.ForeignKey("meal_logs.id"), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=40), nullable=True),
            sa.Column("calories", sa.Float(), nullable=True),
            sa.Column("protein_g", sa.Float(), nullable=True),
            sa.Column("carbohydrate_g", sa.Float(), nullable=True),
            sa.Column("fat_g", sa.Float(), nullable=True),
            sa.Column("nutrition_source", sa.String(length=80), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
        )
        op.create_index("ix_meal_items_meal_log_id", "meal_items", ["meal_log_id"])
    if "running_shoes" not in tables:
        op.create_table(
            "running_shoes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("brand", sa.String(length=120), nullable=True),
            sa.Column("model", sa.String(length=160), nullable=True),
            sa.Column("color", sa.String(length=80), nullable=True),
            sa.Column("purchase_date", sa.Date(), nullable=True),
            sa.Column("first_use_date", sa.Date(), nullable=True),
            sa.Column("initial_distance_km", sa.Float(), nullable=False),
            sa.Column("preferred_uses_json", sa.Text(), nullable=True),
            sa.Column("surfaces_json", sa.Text(), nullable=True),
            sa.Column("expected_min_km", sa.Float(), nullable=True),
            sa.Column("expected_max_km", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("condition_notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_running_shoes_user_id", "running_shoes", ["user_id"])
        op.create_index("ix_running_shoes_name", "running_shoes", ["name"])
    if "manual_shoe_usages" not in tables:
        op.create_table(
            "manual_shoe_usages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("distance_km", sa.Float(), nullable=False),
            sa.Column("activity_type", sa.String(length=80), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_manual_shoe_usages_user_id", "manual_shoe_usages", ["user_id"])
        op.create_index("ix_manual_shoe_usages_date", "manual_shoe_usages", ["date"])
    if "shoe_activity_links" not in tables:
        op.create_table(
            "shoe_activity_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("shoe_id", sa.Integer(), sa.ForeignKey("running_shoes.id"), nullable=False),
            sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id"), nullable=True),
            sa.Column("manual_usage_id", sa.Integer(), sa.ForeignKey("manual_shoe_usages.id"), nullable=True),
            sa.Column("distance_km", sa.Float(), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("shoe_id", "activity_id", name="uq_shoe_activity"),
            sa.UniqueConstraint("shoe_id", "manual_usage_id", name="uq_shoe_manual_usage"),
        )
        op.create_index("ix_shoe_activity_links_shoe_id", "shoe_activity_links", ["shoe_id"])
        op.create_index("ix_shoe_activity_links_activity_id", "shoe_activity_links", ["activity_id"])
        op.create_index("ix_shoe_activity_links_manual_usage_id", "shoe_activity_links", ["manual_usage_id"])
        op.create_index("ix_shoe_activity_links_used_at", "shoe_activity_links", ["used_at"])
    if "daily_notes" not in tables:
        op.create_table(
            "daily_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_daily_notes_user_id", "daily_notes", ["user_id"])
        op.create_index("ix_daily_notes_day", "daily_notes", ["day"])
    if "planned_activities" not in tables:
        op.create_table(
            "planned_activities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("planned_for", sa.Date(), nullable=False),
            sa.Column("activity_type", sa.String(length=80), nullable=False),
            sa.Column("distance_km", sa.Float(), nullable=True),
            sa.Column("intensity", sa.String(length=80), nullable=True),
            sa.Column("surface", sa.String(length=80), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_planned_activities_user_id", "planned_activities", ["user_id"])
        op.create_index("ix_planned_activities_planned_for", "planned_activities", ["planned_for"])


def downgrade() -> None:
    for index, table in [
        ("ix_planned_activities_planned_for", "planned_activities"),
        ("ix_planned_activities_user_id", "planned_activities"),
        ("ix_daily_notes_day", "daily_notes"),
        ("ix_daily_notes_user_id", "daily_notes"),
        ("ix_shoe_activity_links_used_at", "shoe_activity_links"),
        ("ix_shoe_activity_links_manual_usage_id", "shoe_activity_links"),
        ("ix_shoe_activity_links_activity_id", "shoe_activity_links"),
        ("ix_shoe_activity_links_shoe_id", "shoe_activity_links"),
        ("ix_manual_shoe_usages_date", "manual_shoe_usages"),
        ("ix_manual_shoe_usages_user_id", "manual_shoe_usages"),
        ("ix_running_shoes_name", "running_shoes"),
        ("ix_running_shoes_user_id", "running_shoes"),
        ("ix_meal_items_meal_log_id", "meal_items"),
        ("ix_meal_logs_consumed_at", "meal_logs"),
        ("ix_meal_logs_user_id", "meal_logs"),
        ("ix_ai_audit_logs_tool_name", "ai_audit_logs"),
        ("ix_ai_audit_logs_user_id", "ai_audit_logs"),
        ("ix_ai_memories_category", "ai_memories"),
        ("ix_ai_memories_user_id", "ai_memories"),
        ("ix_ai_runs_conversation_id", "ai_runs"),
        ("ix_ai_messages_conversation_id", "ai_messages"),
        ("ix_ai_conversations_user_id", "ai_conversations"),
    ]:
        op.drop_index(index, table_name=table)
    for table in [
        "planned_activities",
        "daily_notes",
        "shoe_activity_links",
        "manual_shoe_usages",
        "running_shoes",
        "meal_items",
        "meal_logs",
        "ai_audit_logs",
        "ai_memories",
        "ai_runs",
        "ai_messages",
        "ai_conversations",
    ]:
        op.drop_table(table)
