from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AiConversation, AiMessage, AiRun
from app.services import ai_tools
from app.services.ai_context import build_context
from app.services.ai_costs import estimate_cost
from app.services.ai_prompt import PROMPT_VERSION, assistant_instructions
from app.services.ai_tool_executor import ToolExecutionError, execute_tool
from app.services.ai_tool_registry import tool_definitions
from app.services.openai_client import OpenAIResponsesClient, OpenAIUnavailable
from app.services.timezone import local_today, utc_now

logger = logging.getLogger(__name__)


def create_conversation(db: Session, user_id: int, title: str | None = None) -> AiConversation:
    conversation = AiConversation(user_id=user_id, title=title or "Nova conversa")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(db: Session, user_id: int) -> list[AiConversation]:
    return list(
        db.scalars(
            select(AiConversation)
            .where(AiConversation.user_id == user_id, AiConversation.archived_at.is_(None))
            .order_by(AiConversation.updated_at.desc())
        )
    )


def get_conversation(db: Session, user_id: int, conversation_id: int) -> AiConversation:
    conversation = db.get(AiConversation, conversation_id)
    if not conversation or conversation.user_id != user_id or conversation.archived_at is not None:
        raise ValueError("Conversa nao encontrada.")
    return conversation


def archive_conversation(db: Session, user_id: int, conversation_id: int) -> None:
    conversation = get_conversation(db, user_id, conversation_id)
    conversation.archived_at = utc_now()
    db.commit()


def conversation_messages(db: Session, conversation_id: int) -> list[AiMessage]:
    return list(
        db.scalars(
            select(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .order_by(AiMessage.created_at)
        )
    )


def send_message(
    db: Session,
    user_id: int,
    conversation_id: int,
    content: str,
    client: Any | None = None,
) -> dict[str, object]:
    settings = get_settings()
    if len(content) > settings.ai_chat_max_message_chars:
        raise ValueError("Mensagem muito longa.")
    conversation = get_conversation(db, user_id, conversation_id)
    started = time.perf_counter()
    db.add(AiMessage(conversation_id=conversation.id, role="user", content=content))
    db.flush()
    run = AiRun(
        conversation_id=conversation.id,
        model=settings.openai_model if settings.openai_chat_enabled else "local-deterministic",
        prompt_version=PROMPT_VERSION,
        status="running",
    )
    db.add(run)
    db.flush()
    tool_cards: list[dict[str, Any]] = []
    try:
        if _should_use_openai(db, user_id):
            text, usage, tool_cards, response_id = _openai_response(
                db, user_id, conversation, content, client
            )
            run.model = settings.openai_model
            run.provider_response_id = response_id
        else:
            response = _local_response(db, user_id, content, include_mode=True)
            text = str(response["text"])
            tool_cards = list(response["tool_cards"])
            usage = _usage_from_text(content, text)
            run.model = "local-deterministic"
        status = "completed"
        error = None
    except Exception as exc:
        logger.warning("AI chat caiu para modo local: %s", _sanitize_error(exc))
        response = _local_response(db, user_id, content, include_mode=True)
        text = response["text"]
        tool_cards = response["tool_cards"]
        usage = _usage_from_text(content, text)
        status = "fallback"
        error = _sanitize_error(exc)
        run.model = "local-deterministic"

    assistant_message = AiMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=text,
        status="completed" if status in {"completed", "fallback"} else status,
    )
    db.add(assistant_message)
    latency = int((time.perf_counter() - started) * 1000)
    _apply_run_usage(run, usage, latency, status, error, settings.openai_model)
    if conversation.title == "Nova conversa":
        conversation.title = _title_from(content)
    conversation.updated_at = utc_now()
    db.commit()
    db.refresh(run)
    db.refresh(assistant_message)
    return {
        "message": _message_payload(assistant_message),
        "run": _run_payload(run),
        "tool_cards": tool_cards,
    }


def stream_run(db: Session, user_id: int, run_id: int):
    run = _owned_run(db, user_id, run_id)
    message = db.scalar(
        select(AiMessage)
        .where(AiMessage.conversation_id == run.conversation_id, AiMessage.role == "assistant")
        .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .limit(1)
    )
    yield _sse("status", {"status": run.status})
    if not message:
        yield _sse("done", {"ok": False})
        return
    words = message.content.split()
    for index in range(0, len(words), 18):
        yield _sse("delta", {"text": " ".join(words[index : index + 18]) + " "})
    yield _sse("done", {"ok": True, "run": _run_payload(run)})


def cancel_run(db: Session, user_id: int, run_id: int) -> dict[str, object]:
    run = _owned_run(db, user_id, run_id)
    if run.status == "running":
        run.status = "cancelled"
        db.commit()
    return _run_payload(run)


def _should_use_openai(db: Session, user_id: int) -> bool:
    settings = get_settings()
    if not settings.openai_chat_enabled or not settings.openai_api_key:
        return False
    since = utc_now() - timedelta(days=1)
    count = db.scalar(
        select(func.count(AiRun.id))
        .join(AiConversation, AiConversation.id == AiRun.conversation_id)
        .where(
            AiConversation.user_id == user_id,
            AiRun.created_at >= since,
            AiRun.model == settings.openai_model,
            AiRun.status == "completed",
        )
    )
    return int(count or 0) < settings.openai_daily_request_limit


def _openai_response(
    db: Session,
    user_id: int,
    conversation: AiConversation,
    content: str,
    client: Any | None,
) -> tuple[str, dict[str, int], list[dict[str, Any]], str | None]:
    settings = get_settings()
    openai_client = client or OpenAIResponsesClient()
    history = _history_input(db, conversation.id, content)
    instructions = assistant_instructions(
        build_context(db, user_id, content, local_today(settings.app_timezone))
    )
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cached_tokens": 0}
    tool_cards: list[dict[str, Any]] = []
    previous_response_id: str | None = None
    response_id: str | None = None
    final_text = ""
    for _round in range(settings.openai_max_tool_rounds + 1):
        response = openai_client.create(
            model=settings.openai_model,
            instructions=instructions,
            input=history,
            tools=tool_definitions(),
            max_output_tokens=settings.openai_max_output_tokens,
            previous_response_id=previous_response_id,
        )
        response_id = getattr(response, "id", None)
        previous_response_id = response_id
        _merge_usage(usage, getattr(response, "usage", None))
        calls = _function_calls(response)
        if not calls:
            final_text = _response_text(response)
            break
        for call in calls:
            try:
                result = execute_tool(db, user_id, conversation.id, call["name"], call["arguments"])
            except ToolExecutionError as exc:
                result = {"ok": False, "message": str(exc)}
            tool_cards.append({"tool": call["name"], "status": "ok" if result.get("ok") else "attention", "data": result})
            db.add(
                AiMessage(
                    conversation_id=conversation.id,
                    role="tool",
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_name=call["name"],
                    tool_call_id=call["call_id"],
                )
            )
            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
    if not final_text:
        final_text = _tool_fallback_response(tool_cards)
    usage["tool_call_count"] = len(tool_cards)
    return final_text, usage, tool_cards, response_id


def _tool_fallback_response(tool_cards: list[dict[str, Any]]) -> str:
    if not tool_cards:
        return "Nao consegui concluir com a IA online. A resposta final nao chegou."
    ok_cards = [card for card in tool_cards if card.get("status") == "ok"]
    attention_cards = [card for card in tool_cards if card.get("status") != "ok"]
    action_labels = {
        "create_shoe": "cadastrei o tenis",
        "associate_shoe_with_activity": "associei o tenis a atividade",
        "create_meal_log": "registrei a refeicao",
        "sync_integrations": "iniciei a sincronizacao",
        "create_manual_shoe_usage": "registrei o uso do tenis",
        "save_daily_note": "salvei a nota do dia",
        "save_planned_activity": "salvei o treino planejado",
    }
    counts: dict[str, int] = {}
    for card in ok_cards:
        label = action_labels.get(str(card.get("tool")))
        if label:
            counts[label] = counts.get(label, 0) + 1
    if counts:
        parts = [f"{count}x {label}" if count > 1 else label for label, count in counts.items()]
        text = "Consegui executar: " + "; ".join(parts) + "."
        if attention_cards:
            text += " Algumas etapas precisam de atencao."
        return text
    if ok_cards:
        return "Consegui consultar os dados solicitados, mas a resposta final da IA online nao chegou."
    return "Nao consegui concluir a acao. Revisei as ferramentas disponiveis e encontrei uma pendencia."


def _local_response(db: Session, user_id: int, content: str, include_mode: bool = False) -> dict[str, object]:
    settings = get_settings()
    today = local_today(settings.app_timezone)
    text = content.strip()
    lower = text.lower()
    prefix = "Modo local: " if include_mode else ""
    if not text:
        return {"text": prefix + "Escreva sua pergunta ou o registro que deseja fazer.", "tool_cards": []}
    if any(token in lower for token in ["como estou", "hoje", "recovery", "sono"]):
        recovery = ai_tools.get_recovery(db, user_id, today)
        sleep = ai_tools.get_sleep(db, user_id, today)
        parts = []
        cards = []
        if recovery.data.get("quality") != "insuficiente":
            parts.append(
                f"Recovery {recovery.data.get('recovery_score')}%, HRV {recovery.data.get('hrv_ms')} ms e FC repouso {recovery.data.get('resting_hr')} bpm."
            )
            cards.append({"tool": "get_recovery", "status": "ok", "data": recovery.data})
        else:
            parts.append("Nao tenho recovery registrado para hoje.")
        if sleep.data.get("quality") != "insuficiente":
            parts.append(f"Sono registrado: {sleep.data.get('duration_seconds')} segundos.")
            cards.append({"tool": "get_sleep", "status": "ok", "data": sleep.data})
        else:
            parts.append("Nao tenho sono registrado para hoje.")
        return {"text": prefix + " ".join(parts), "tool_cards": cards}
    if "vou comer" in lower or "estou pensando em comer" in lower:
        return {
            "text": prefix
            + "Entendi como plano ou intencao. Nao registrei como consumido. Se voce ja comeu, diga 'comi ...' ou confirme que deseja registrar.",
            "tool_cards": [],
        }
    if lower.startswith("comi ") or " eu comi " in lower:
        description = _strip_meal_prefix(text)
        result = ai_tools.create_meal_log(db, user_id, description=description)
        return {
            "text": prefix + f"Registrado: {description}. Nao inventei calorias nem macros; esses campos ficaram vazios.",
            "tool_cards": [{"tool": "create_meal_log", "status": "registered", "data": result.data}],
        }
    if "tenis" in lower or "tênis" in lower or "tÃªnis" in lower:
        shoes = ai_tools.get_shoes(db, user_id)
        if shoes.data.get("quality") == "insuficiente":
            return {"text": prefix + "Nao tenho tenis registrado.", "tool_cards": [{"tool": "get_shoes", "status": "empty", "data": shoes.data}]}
        return {
            "text": prefix + "Encontrei estes tenis cadastrados. Para associar a uma atividade, informe o tenis e a atividade ou confirme a opcao.",
            "tool_cards": [{"tool": "get_shoes", "status": "ok", "data": shoes.data}],
        }
    if "memoria" in lower or "preferencia" in lower or "alergia" in lower:
        memories = ai_tools.get_user_preferences(db, user_id)
        if memories.data.get("quality") == "insuficiente":
            return {"text": prefix + "Nao tenho esse dado registrado.", "tool_cards": [{"tool": "get_user_preferences", "status": "empty", "data": memories.data}]}
        return {"text": prefix + "Encontrei memorias confirmadas relevantes.", "tool_cards": [{"tool": "get_user_preferences", "status": "ok", "data": memories.data}]}
    return {
        "text": prefix + "Nao tenho esse dado registrado. Posso consultar recovery, sono, atividades, refeicoes registradas, memorias confirmadas ou tenis cadastrados.",
        "tool_cards": [],
    }


def _history_input(db: Session, conversation_id: int, content: str) -> list[dict[str, str]]:
    messages = conversation_messages(db, conversation_id)[-12:]
    items = [{"role": message.role, "content": message.content} for message in messages if message.role in {"user", "assistant"}]
    if not items or items[-1]["content"] != content:
        items.append({"role": "user", "content": content})
    return items


def _function_calls(response: Any) -> list[dict[str, Any]]:
    calls = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        raw_args = getattr(item, "arguments", "{}") or "{}"
        calls.append(
            {
                "name": item.name,
                "arguments": json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args),
                "call_id": getattr(item, "call_id", None) or getattr(item, "id", ""),
            }
        )
    return calls


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text)
    parts = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            value = getattr(content, "text", None)
            if value:
                parts.append(str(value))
    return "\n".join(parts).strip()


def _merge_usage(target: dict[str, int], usage: Any) -> None:
    if not usage:
        return
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    target["input_tokens"] += input_tokens
    target["output_tokens"] += output_tokens
    target["total_tokens"] += int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
    details = getattr(usage, "input_tokens_details", None)
    target["cached_tokens"] += int(getattr(details, "cached_tokens", 0) or 0) if details else 0


def _apply_run_usage(
    run: AiRun,
    usage: dict[str, int],
    latency: int,
    status: str,
    error: str | None,
    model: str,
) -> None:
    run.input_tokens = int(usage.get("input_tokens") or 0)
    run.output_tokens = int(usage.get("output_tokens") or 0)
    run.total_tokens = int(usage.get("total_tokens") or run.input_tokens + run.output_tokens)
    run.cached_tokens = int(usage.get("cached_tokens") or 0)
    run.tool_call_count = int(usage.get("tool_call_count") or 0)
    cost = estimate_cost(model, run.input_tokens, run.output_tokens, run.cached_tokens)
    run.estimated_cost = cost.amount if run.model != "local-deterministic" else 0.0
    run.currency = cost.currency if run.model != "local-deterministic" else "USD"
    run.latency_ms = latency
    run.status = status
    run.error_sanitized = error


def _usage_from_text(input_text: str, output_text: str) -> dict[str, int]:
    return {
        "input_tokens": max(1, len(input_text) // 4),
        "output_tokens": max(1, len(output_text) // 4),
        "total_tokens": max(2, (len(input_text) + len(output_text)) // 4),
        "cached_tokens": 0,
        "tool_call_count": 0,
    }


def _owned_run(db: Session, user_id: int, run_id: int) -> AiRun:
    run = db.get(AiRun, run_id)
    if not run:
        raise ValueError("Execucao nao encontrada.")
    conversation = db.get(AiConversation, run.conversation_id)
    if not conversation or conversation.user_id != user_id:
        raise ValueError("Execucao nao encontrada.")
    return run


def _strip_meal_prefix(text: str) -> str:
    lowered = text.lower()
    for prefix in ["comi ", "eu comi "]:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    return text.strip()


def _title_from(content: str) -> str:
    clean = " ".join(content.strip().split())
    return clean[:60] or "Nova conversa"


def _sanitize_error(exc: Exception) -> str:
    if isinstance(exc, OpenAIUnavailable):
        return str(exc)
    return exc.__class__.__name__


def _message_payload(message: AiMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _run_payload(run: AiRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "latency_ms": run.latency_ms,
        "estimated_cost": run.estimated_cost,
        "currency": run.currency,
        "model": run.model,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "total_tokens": run.total_tokens,
        "tool_call_count": run.tool_call_count,
    }


def conversation_payload(db: Session, conversation: AiConversation, include_messages: bool = False) -> dict[str, object]:
    payload = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }
    if include_messages:
        payload["messages"] = [_message_payload(message) for message in conversation_messages(db, conversation.id) if message.role != "tool"]
    return payload


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
