from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import (
    Activity,
    AiMemory,
    DataSource,
    MealLog,
    ShoeActivityLink,
    User,
)
from app.services.ai_chat import create_conversation, send_message
from app.services.ai_tools import (
    AmbiguousToolRequest,
    associate_shoe_with_activity,
    create_manual_shoe_usage,
    create_shoe,
    find_activity_candidates,
    get_shoe_recommendation_context,
    resolve_single_shoe,
    save_confirmed_memory,
)


def _user(db_session):
    user = User(name="Humberto")
    db_session.add(user)
    db_session.commit()
    return user


def _activity(db_session, user, started_at=None, distance_meters=6000):
    source = DataSource(name=f"fit-{distance_meters}", kind="file")
    db_session.add(source)
    db_session.flush()
    activity = Activity(
        user_id=user.id,
        data_source_id=source.id,
        external_id=f"a-{distance_meters}",
        activity_type="running",
        started_at=started_at or datetime(2026, 7, 12, 10, 0, tzinfo=UTC),
        total_duration_seconds=1800,
        distance_meters=distance_meters,
    )
    db_session.add(activity)
    db_session.commit()
    return activity


def test_chat_does_not_invent_missing_data(db_session):
    user = _user(db_session)
    conversation = create_conversation(db_session, user.id)

    response = send_message(db_session, user.id, conversation.id, "qual minha alergia alimentar?")

    assert "Nao tenho esse dado registrado" in response["message"]["content"]


def test_vou_comer_is_plan_and_does_not_create_meal(db_session):
    user = _user(db_session)
    conversation = create_conversation(db_session, user.id)

    response = send_message(db_session, user.id, conversation.id, "vou comer banana")

    assert "Nao registrei como consumido" in response["message"]["content"]
    assert db_session.query(MealLog).count() == 0


def test_comi_creates_meal_without_inventing_macros(db_session):
    user = _user(db_session)
    conversation = create_conversation(db_session, user.id)

    response = send_message(db_session, user.id, conversation.id, "comi arroz e ovo")

    meal = db_session.scalar(select(MealLog))
    assert meal is not None
    assert meal.description == "arroz e ovo"
    assert "Nao inventei calorias" in response["message"]["content"]


def test_memory_only_after_confirmed_tool(db_session):
    user = _user(db_session)

    result = save_confirmed_memory(
        db_session,
        user.id,
        category="preferencias",
        key="cafe",
        value={"texto": "prefere cafe sem acucar"},
    )

    memory = db_session.get(AiMemory, result.data["memory"]["id"])
    assert memory.confirmed_by_user is True
    assert memory.active is True


def test_two_shoes_with_same_color_require_choice(db_session):
    user = _user(db_session)
    create_shoe(db_session, user.id, "Tenis A preto", color="preto")
    create_shoe(db_session, user.id, "Tenis B preto", color="preto")

    with pytest.raises(AmbiguousToolRequest) as exc:
        resolve_single_shoe(db_session, user.id, "preto")

    assert len(exc.value.candidates) == 2


def test_two_compatible_activities_require_choice(db_session):
    user = _user(db_session)
    _activity(db_session, user, distance_meters=6000)
    _activity(db_session, user, started_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC), distance_meters=6100)

    candidates = find_activity_candidates(db_session, user.id, date(2026, 7, 12), distance_km=6)

    assert len(candidates) == 2


def test_prevents_duplicate_shoe_activity_association(db_session):
    user = _user(db_session)
    activity = _activity(db_session, user)
    shoe = create_shoe(db_session, user.id, "Novablast").data["shoe"]

    associate_shoe_with_activity(db_session, user.id, shoe["id"], activity.id)
    associate_shoe_with_activity(db_session, user.id, shoe["id"], activity.id)

    assert db_session.query(ShoeActivityLink).count() == 1


def test_shoe_total_distance_includes_initial_and_manual_usage(db_session):
    user = _user(db_session)
    shoe = create_shoe(db_session, user.id, "Superblast", initial_distance_km=50).data["shoe"]

    result = create_manual_shoe_usage(db_session, user.id, shoe["id"], date(2026, 7, 12), 6)

    assert result.data["shoe"]["total_distance_km"] == 56


def test_recommendation_context_does_not_invent_when_no_range(db_session):
    user = _user(db_session)
    create_shoe(db_session, user.id, "Rodagem")

    context = get_shoe_recommendation_context(db_session, user.id).data

    assert context["candidates"][0]["shoe"]["estimate_status"] == "Estimativa ainda nao configurada."
