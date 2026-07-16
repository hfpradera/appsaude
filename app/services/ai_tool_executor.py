from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import AiPendingAction
from app.services import ai_tools
from app.services.ai_tool_registry import ALLOWED_TOOLS, CONFIRMATION_TOOLS
from app.services.timezone import utc_now


class ToolExecutionError(ValueError):
    pass


def execute_tool(
    db: Session,
    user_id: int,
    conversation_id: int,
    tool_name: str,
    arguments: dict[str, Any],
    confirmed: bool = False,
) -> dict[str, Any]:
    if tool_name not in ALLOWED_TOOLS:
        raise ToolExecutionError("Ferramenta nao permitida.")
    if tool_name in CONFIRMATION_TOOLS and not confirmed:
        pending = create_pending_action(db, user_id, conversation_id, tool_name, arguments)
        return {
            "ok": False,
            "pending_confirmation": True,
            "pending_action": pending_payload(pending),
            "message": "Preciso da sua confirmacao antes de executar esta alteracao.",
        }
    try:
        result = _dispatch(db, user_id, tool_name, arguments)
    except ai_tools.AmbiguousToolRequest as exc:
        return {"ok": False, "needs_choice": True, "message": str(exc), "candidates": exc.candidates}
    except (ai_tools.ToolError, KeyError, TypeError, ValueError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return {"ok": result.ok, "message": result.message, "data": result.data}


def create_pending_action(
    db: Session,
    user_id: int,
    conversation_id: int,
    tool_name: str,
    arguments: dict[str, Any],
) -> AiPendingAction:
    pending = AiPendingAction(
        user_id=user_id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        arguments_json=json.dumps(arguments, ensure_ascii=False, default=str),
        summary=_summary(tool_name, arguments),
        expires_at=utc_now() + timedelta(hours=24),
        status="pending",
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    return pending


def confirm_pending_action(db: Session, user_id: int, pending_id: int) -> dict[str, Any]:
    pending = _pending_owned(db, user_id, pending_id)
    if pending.status != "pending":
        return {"ok": False, "status": pending.status, "message": "Acao pendente nao esta ativa."}
    if _as_aware_utc(pending.expires_at) < utc_now():
        pending.status = "expired"
        db.commit()
        return {"ok": False, "status": "expired", "message": "Acao pendente expirada."}
    args = json.loads(pending.arguments_json)
    result = execute_tool(db, user_id, pending.conversation_id, pending.tool_name, args, confirmed=True)
    pending.status = "confirmed"
    pending.confirmed_at = utc_now()
    db.add(pending)
    db.commit()
    result["pending_action"] = pending_payload(pending)
    return result


def cancel_pending_action(db: Session, user_id: int, pending_id: int) -> dict[str, Any]:
    pending = _pending_owned(db, user_id, pending_id)
    if pending.status == "pending":
        pending.status = "cancelled"
        pending.cancelled_at = utc_now()
        db.commit()
    return {"ok": True, "pending_action": pending_payload(pending)}


def pending_payload(pending: AiPendingAction) -> dict[str, Any]:
    return {
        "id": pending.id,
        "tool_name": pending.tool_name,
        "summary": pending.summary,
        "status": pending.status,
        "expires_at": pending.expires_at.isoformat() if pending.expires_at else None,
    }


def _dispatch(db: Session, user_id: int, tool_name: str, arguments: dict[str, Any]):
    if tool_name == "get_recovery":
        return ai_tools.get_recovery(db, user_id, _date(arguments["day"]))
    if tool_name == "get_sleep":
        return ai_tools.get_sleep(db, user_id, _date(arguments["day"]))
    if tool_name == "get_recent_activities":
        return ai_tools.get_recent_activities(
            db,
            user_id,
            _date(arguments["start_day"]),
            _date(arguments["end_day"]),
            arguments.get("activity_type"),
        )
    if tool_name == "get_activity_details":
        return ai_tools.get_activity_details(db, user_id, int(arguments["activity_id"]))
    if tool_name == "get_metric_history":
        return ai_tools.get_metric_history(
            db,
            user_id,
            str(arguments["metric"]),
            _date(arguments["start_day"]),
            _date(arguments["end_day"]),
            arguments.get("source"),
        )
    if tool_name == "get_today_meals":
        return ai_tools.get_today_meals(db, user_id)
    if tool_name == "get_meal_history":
        return ai_tools.get_meal_history(db, user_id, _date(arguments["start_day"]), _date(arguments["end_day"]))
    if tool_name == "get_user_preferences":
        return ai_tools.get_user_preferences(db, user_id, arguments.get("category"))
    if tool_name == "get_user_goals":
        return ai_tools.get_user_goals(db, user_id)
    if tool_name == "get_shoes":
        return ai_tools.get_shoes(db, user_id, arguments.get("status"))
    if tool_name == "get_shoe_details":
        return ai_tools.get_shoe_details(db, user_id, int(arguments["shoe_id"]))
    if tool_name == "get_shoe_usage_history":
        return ai_tools.get_shoe_usage_history(db, user_id, int(arguments["shoe_id"]))
    if tool_name == "get_shoe_recommendation_context":
        return ai_tools.get_shoe_recommendation_context(db, user_id, arguments.get("planned_activity"))
    if tool_name == "get_data_quality":
        return ai_tools.get_data_quality(db, user_id, _date(arguments["day"]))
    if tool_name == "get_sync_status":
        return ai_tools.get_sync_status(db, user_id, str(arguments.get("source") or "all"))
    if tool_name == "get_activities_without_shoe":
        return ai_tools.get_activities_without_shoe(
            db, user_id, _date(arguments["day"]), arguments.get("activity_type")
        )
    if tool_name == "sync_integrations":
        return ai_tools.sync_integrations(db, user_id, str(arguments.get("source") or "all"))
    if tool_name == "create_meal_log":
        consumed_at = _datetime_or_none(arguments.get("consumed_at"))
        return ai_tools.create_meal_log(
            db,
            user_id,
            description=str(arguments["description"]),
            consumed_at=consumed_at,
            meal_type=str(arguments.get("meal_type") or "refeicao"),
            items=arguments.get("items") or [],
        )
    if tool_name == "save_confirmed_memory":
        return ai_tools.save_confirmed_memory(
            db,
            user_id,
            str(arguments["category"]),
            str(arguments["key"]),
            arguments["value"],
        )
    if tool_name == "create_shoe":
        attrs = dict(arguments)
        name = str(attrs.pop("name"))
        return ai_tools.create_shoe(db, user_id, name, **attrs)
    if tool_name == "associate_shoe_with_activity":
        return ai_tools.associate_shoe_with_activity(
            db,
            user_id,
            int(arguments["shoe_id"]),
            int(arguments["activity_id"]),
            float(arguments.get("confidence") or 1.0),
        )
    if tool_name == "retire_shoe":
        return ai_tools.retire_shoe(db, user_id, int(arguments["shoe_id"]), arguments.get("notes"))
    if tool_name == "create_manual_shoe_usage":
        return ai_tools.create_manual_shoe_usage(
            db,
            user_id,
            int(arguments["shoe_id"]),
            _date(arguments["usage_date"]),
            float(arguments["distance_km"]),
            activity_type=str(arguments.get("activity_type") or "running"),
            notes=arguments.get("notes"),
        )
    if tool_name == "save_daily_note":
        return ai_tools.save_daily_note(db, user_id, _date(arguments["day"]), str(arguments["note"]))
    if tool_name == "save_planned_activity":
        distance_km = arguments.get("distance_km")
        return ai_tools.save_planned_activity(
            db,
            user_id,
            _date(arguments["planned_for"]),
            str(arguments["activity_type"]),
            distance_km=float(distance_km) if distance_km is not None else None,
            intensity=arguments.get("intensity"),
            surface=arguments.get("surface"),
            notes=arguments.get("notes"),
        )
    if tool_name == "update_meal_log":
        changes = {k: v for k, v in arguments.items() if k != "meal_id"}
        return ai_tools.update_meal_log(db, user_id, int(arguments["meal_id"]), **changes)
    if tool_name == "delete_meal_log":
        return ai_tools.delete_meal_log(db, user_id, int(arguments["meal_id"]))
    if tool_name == "update_memory":
        return ai_tools.update_memory(db, user_id, int(arguments["memory_id"]), arguments["value"])
    if tool_name == "delete_memory":
        return ai_tools.delete_memory(db, user_id, int(arguments["memory_id"]))
    if tool_name == "update_shoe":
        attrs = {k: v for k, v in arguments.items() if k != "shoe_id"}
        return ai_tools.update_shoe(db, user_id, int(arguments["shoe_id"]), **attrs)
    raise ToolExecutionError("Ferramenta nao implementada.")


def _pending_owned(db: Session, user_id: int, pending_id: int) -> AiPendingAction:
    pending = db.get(AiPendingAction, pending_id)
    if not pending or pending.user_id != user_id:
        raise ToolExecutionError("Acao pendente nao encontrada.")
    return pending


def _date(value: str) -> date:
    return date.fromisoformat(str(value))


def _datetime_or_none(value: object) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _summary(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "retire_shoe":
        return f"Aposentar tenis #{arguments.get('shoe_id')}."
    if tool_name == "save_confirmed_memory":
        return f"Salvar memoria {arguments.get('category')} / {arguments.get('key')}."
    if tool_name == "update_meal_log":
        return f"Atualizar refeicao #{arguments.get('meal_id')}."
    if tool_name == "delete_meal_log":
        return f"Excluir refeicao #{arguments.get('meal_id')}."
    if tool_name == "update_memory":
        return f"Atualizar memoria #{arguments.get('memory_id')}."
    if tool_name == "delete_memory":
        return f"Apagar memoria #{arguments.get('memory_id')}."
    if tool_name == "update_shoe":
        return f"Atualizar tenis #{arguments.get('shoe_id')}."
    if tool_name == "create_shoe":
        return f"Cadastrar tenis novo '{arguments.get('name')}'."
    return f"Executar {tool_name}."
