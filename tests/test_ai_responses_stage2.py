from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Activity,
    AiMemory,
    AiPendingAction,
    DataSource,
    MealLog,
    OAuthCredential,
    RunningShoe,
    SyncLog,
    User,
)
from app.services import ai_tools
from app.services.ai_chat import cancel_run, create_conversation, send_message, stream_run
from app.services.ai_context import build_context
from app.services.ai_costs import estimate_cost
from app.services.ai_tool_executor import cancel_pending_action, confirm_pending_action


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise RuntimeError("sem resposta fake")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fake_text(text: str, input_tokens: int = 20, output_tokens: int = 10):
    return SimpleNamespace(
        id="resp-final",
        output_text=text,
        output=[],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens),
    )


def fake_call(name: str, arguments: dict, call_id: str = "call-1"):
    return SimpleNamespace(
        id="resp-call",
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                name=name,
                arguments=json.dumps(arguments),
                call_id=call_id,
            )
        ],
        usage=SimpleNamespace(input_tokens=15, output_tokens=3, total_tokens=18),
    )


def _user(db_session):
    user = User(name="Humberto")
    db_session.add(user)
    db_session.commit()
    return user


def _settings_online():
    settings = get_settings()
    settings.openai_chat_enabled = True
    settings.openai_api_key = "test-key-not-real"
    settings.openai_model = "gpt-5-mini"
    settings.openai_max_tool_rounds = 3
    settings.openai_daily_request_limit = 50
    return settings


def _activity(db_session, user):
    source = DataSource(name="test-source", kind="file")
    db_session.add(source)
    db_session.flush()
    activity = Activity(
        user_id=user.id,
        data_source_id=source.id,
        external_id="act-1",
        activity_type="running",
        started_at=datetime(2026, 7, 12, 10, 0, tzinfo=UTC),
        total_duration_seconds=1800,
        distance_meters=5000,
    )
    db_session.add(activity)
    db_session.commit()
    return activity


def test_openai_text_response_is_persisted_without_network(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient([fake_text("Resposta baseada nos dados locais.")])

    response = send_message(db_session, user.id, conversation.id, "como estou?", client=client)

    assert response["message"]["content"] == "Resposta baseada nos dados locais."
    assert response["run"]["model"] == "gpt-5-mini"
    assert response["run"]["total_tokens"] == 30
    assert client.calls[0]["tools"]


def test_openai_function_call_executes_tool_round(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient(
        [
            fake_call("get_recovery", {"day": date.today().isoformat()}),
            fake_text("Nao tenho recovery registrado para hoje."),
        ]
    )

    response = send_message(db_session, user.id, conversation.id, "qual meu recovery?", client=client)

    assert "Nao tenho recovery" in response["message"]["content"]
    assert response["run"]["tool_call_count"] == 1
    assert len(client.calls) == 2


def test_unknown_tool_returns_safe_tool_error_then_final_response(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient([fake_call("tool_inexistente", {}), fake_text("Nao executei ferramenta desconhecida.")])

    response = send_message(db_session, user.id, conversation.id, "apague tudo", client=client)

    assert "Nao executei" in response["message"]["content"]
    assert response["tool_cards"][0]["status"] == "attention"


def test_openai_failure_falls_back_to_local_mode(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient([TimeoutError("timeout com segredo test-key-not-real")])

    response = send_message(db_session, user.id, conversation.id, "como estou hoje?", client=client)

    assert response["run"]["status"] == "fallback"
    assert response["message"]["content"].startswith("Modo local:")
    assert "test-key-not-real" not in response["run"].values()


def test_missing_key_uses_local_mode_without_calling_client(db_session):
    user = _user(db_session)
    settings = _settings_online()
    settings.openai_api_key = ""
    conversation = create_conversation(db_session, user.id)
    client = FakeClient([fake_text("nao deveria chamar")])

    response = send_message(db_session, user.id, conversation.id, "qual minha alergia alimentar?", client=client)

    assert response["message"]["content"].startswith("Modo local:")
    assert client.calls == []


def test_pending_action_requires_confirmation_before_memory_write(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient(
        [
            fake_call(
                "save_confirmed_memory",
                {"category": "alergias", "key": "amendoim", "value": {"texto": "evitar amendoim"}},
            ),
            fake_text("Criei uma confirmacao pendente."),
        ]
    )

    response = send_message(db_session, user.id, conversation.id, "grave que tenho alergia a amendoim", client=client)

    assert response["tool_cards"][0]["data"]["pending_confirmation"] is True
    assert db_session.scalar(select(AiMemory)) is None
    pending = db_session.scalar(select(AiPendingAction))
    assert pending is not None


def test_confirm_pending_action_executes_write(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient(
        [
            fake_call(
                "save_confirmed_memory",
                {"category": "alergias", "key": "amendoim", "value": {"texto": "evitar amendoim"}},
            ),
            fake_text("Pendente."),
        ]
    )
    send_message(db_session, user.id, conversation.id, "grave alergia", client=client)
    pending = db_session.scalar(select(AiPendingAction))

    result = confirm_pending_action(db_session, user.id, pending.id)

    assert result["ok"] is True
    assert db_session.scalar(select(AiMemory)).key == "amendoim"


def test_expired_pending_action_is_not_executed(db_session):
    user = _user(db_session)
    conversation = create_conversation(db_session, user.id)
    pending = AiPendingAction(
        user_id=user.id,
        conversation_id=conversation.id,
        tool_name="save_confirmed_memory",
        arguments_json=json.dumps({"category": "x", "key": "y", "value": {"z": 1}}),
        summary="expirada",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        status="pending",
    )
    db_session.add(pending)
    db_session.commit()

    result = confirm_pending_action(db_session, user.id, pending.id)

    assert result["status"] == "expired"
    assert db_session.scalar(select(AiMemory)) is None


def test_other_user_cannot_cancel_pending_action(db_session):
    user = _user(db_session)
    other = _user(db_session)
    conversation = create_conversation(db_session, user.id)
    pending = AiPendingAction(
        user_id=user.id,
        conversation_id=conversation.id,
        tool_name="retire_shoe",
        arguments_json=json.dumps({"shoe_id": 1, "notes": None}),
        summary="aposentar",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    )
    db_session.add(pending)
    db_session.commit()

    with pytest.raises(ValueError):
        cancel_pending_action(db_session, other.id, pending.id)


def test_unconfirmed_memory_is_not_loaded_into_context(db_session):
    user = _user(db_session)
    memory = AiMemory(
        user_id=user.id,
        category="alergias",
        key="amendoim",
        value_json=json.dumps({"texto": "evitar"}),
        confirmed_by_user=False,
        active=True,
    )
    db_session.add(memory)
    db_session.commit()

    context = build_context(db_session, user.id, "qual minha alergia?")

    assert "amendoim" not in context


def test_comi_tool_call_records_meal_without_macros(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient(
        [
            fake_call("create_meal_log", {"description": "arroz e ovo", "consumed_at": None, "meal_type": "refeicao", "items": None}),
            fake_text("Refeicao registrada."),
        ]
    )

    send_message(db_session, user.id, conversation.id, "comi arroz e ovo", client=client)

    meal = db_session.scalar(select(MealLog))
    assert meal.description == "arroz e ovo"


def test_vou_comer_online_does_not_record_when_model_only_replies(db_session):
    user = _user(db_session)
    _settings_online()
    conversation = create_conversation(db_session, user.id)
    client = FakeClient([fake_text("Entendi como plano; nao registrei.")])

    send_message(db_session, user.id, conversation.id, "vou comer banana", client=client)

    assert db_session.scalar(select(MealLog)) is None


def test_duplicate_shoe_association_tool_is_idempotent(db_session):
    user = _user(db_session)
    _settings_online()
    activity = _activity(db_session, user)
    shoe = RunningShoe(user_id=user.id, name="Novablast")
    db_session.add(shoe)
    db_session.commit()
    conversation = create_conversation(db_session, user.id)
    args = {"shoe_id": shoe.id, "activity_id": activity.id, "confidence": 1.0}
    client = FakeClient(
        [
            fake_call("associate_shoe_with_activity", args),
            fake_call("associate_shoe_with_activity", args),
            fake_text("Associacao conferida."),
        ]
    )

    response = send_message(db_session, user.id, conversation.id, "usei o tenis na corrida", client=client)

    assert response["run"]["tool_call_count"] == 2


def test_successful_tools_without_final_text_get_action_summary(db_session):
    user = _user(db_session)
    settings = _settings_online()
    settings.openai_max_tool_rounds = 0
    conversation = create_conversation(db_session, user.id)
    client = FakeClient([fake_call("create_meal_log", {"description": "arroz e ovo"})])

    response = send_message(db_session, user.id, conversation.id, "comi arroz e ovo", client=client)

    assert response["run"]["status"] == "completed"
    assert "Consegui executar" in response["message"]["content"]
    assert "registrei a refeicao" in response["message"]["content"]
    assert "Nao consegui concluir" not in response["message"]["content"]


def test_unknown_model_cost_is_none():
    estimate = estimate_cost("modelo-futuro", 100, 100)

    assert estimate.amount is None
    assert estimate.currency is None


def test_daily_request_limit_uses_local_mode(db_session):
    user = _user(db_session)
    settings = _settings_online()
    settings.openai_daily_request_limit = 0
    conversation = create_conversation(db_session, user.id)
    client = FakeClient([fake_text("nao chamar")])

    response = send_message(db_session, user.id, conversation.id, "como estou hoje?", client=client)

    assert response["run"]["model"] == "local-deterministic"
    assert client.calls == []


def test_run_stream_and_cancel_are_scoped(db_session):
    user = _user(db_session)
    settings = _settings_online()
    settings.openai_api_key = ""
    conversation = create_conversation(db_session, user.id)
    response = send_message(db_session, user.id, conversation.id, "como estou hoje?")

    chunks = list(stream_run(db_session, user.id, response["run"]["id"]))
    cancelled = cancel_run(db_session, user.id, response["run"]["id"])

    assert any("event: done" in chunk for chunk in chunks)
    assert cancelled["id"] == response["run"]["id"]


def test_ai_can_start_all_integrations_sync_when_requested(db_session, monkeypatch):
    user = _user(db_session)
    _settings_online()
    strava = DataSource(name="strava", kind="oauth")
    whoop = DataSource(name="whoop", kind="oauth")
    db_session.add_all([strava, whoop])
    db_session.flush()
    db_session.add_all(
        [
            OAuthCredential(user_id=user.id, data_source_id=strava.id, encrypted_access_token="x"),
            OAuthCredential(user_id=user.id, data_source_id=whoop.id, encrypted_access_token="x"),
        ]
    )
    db_session.commit()
    started = []
    monkeypatch.setattr(ai_tools, "_start_background_sync", lambda user_id, source: started.append((user_id, source)))
    conversation = create_conversation(db_session, user.id)
    client = FakeClient(
        [
            fake_call("sync_integrations", {"source": "all"}),
            fake_text("Sincronizacao iniciada para Strava e WHOOP."),
        ]
    )

    response = send_message(db_session, user.id, conversation.id, "atualize meus dados agora", client=client)

    assert response["run"]["status"] == "completed"
    assert response["run"]["tool_call_count"] == 1
    assert started == [(user.id, "strava"), (user.id, "whoop")]
    assert db_session.scalar(select(MealLog)) is None
    assert db_session.scalar(select(AiMemory)) is None


def test_ai_sync_tool_does_not_start_duplicate_running_sync(db_session, monkeypatch):
    user = _user(db_session)
    _settings_online()
    strava = DataSource(name="strava", kind="oauth")
    db_session.add(strava)
    db_session.flush()
    db_session.add(OAuthCredential(user_id=user.id, data_source_id=strava.id, encrypted_access_token="x"))
    db_session.add(SyncLog(data_source_id=strava.id, action="strava_sync", status="running"))
    db_session.commit()
    started = []
    monkeypatch.setattr(ai_tools, "_start_background_sync", lambda user_id, source: started.append((user_id, source)))
    conversation = create_conversation(db_session, user.id)
    client = FakeClient(
        [
            fake_call("sync_integrations", {"source": "strava"}),
            fake_text("Strava ja esta sincronizando."),
        ]
    )

    response = send_message(db_session, user.id, conversation.id, "sincroniza o strava agora", client=client)

    assert response["run"]["status"] == "completed"
    assert started == []
    assert response["tool_cards"][0]["data"]["data"]["sources"][0]["state"] == "running"
