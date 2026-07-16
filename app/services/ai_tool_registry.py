from __future__ import annotations

from typing import Any

READ_TOOLS = {
    "get_recovery",
    "get_sleep",
    "get_recent_activities",
    "get_activity_details",
    "get_metric_history",
    "get_today_meals",
    "get_meal_history",
    "get_user_preferences",
    "get_user_goals",
    "get_shoes",
    "get_shoe_details",
    "get_shoe_usage_history",
    "get_shoe_recommendation_context",
    "get_data_quality",
    "get_sync_status",
}

WRITE_TOOLS = {
    "create_meal_log",
    "create_shoe",
    "associate_shoe_with_activity",
    "create_manual_shoe_usage",
    "save_daily_note",
    "save_planned_activity",
    "sync_integrations",
}

CONFIRMATION_TOOLS = {
    "save_confirmed_memory",
    "update_meal_log",
    "delete_meal_log",
    "update_memory",
    "delete_memory",
    "update_shoe",
    "retire_shoe",
}

ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS | CONFIRMATION_TOOLS


def tool_definitions() -> list[dict[str, Any]]:
    return [
        _tool("get_recovery", "Consulta recovery WHOOP de um dia.", {"day": _date("Dia YYYY-MM-DD.")}, ["day"]),
        _tool("get_sleep", "Consulta sono de um dia.", {"day": _date("Dia YYYY-MM-DD.")}, ["day"]),
        _tool(
            "get_recent_activities",
            "Lista atividades recentes por periodo.",
            {
                "start_day": _date("Inicio YYYY-MM-DD."),
                "end_day": _date("Fim YYYY-MM-DD."),
                "activity_type": _string("Tipo opcional.", nullable=True),
            },
            ["start_day", "end_day"],
        ),
        _tool("get_activity_details", "Detalhes de uma atividade.", {"activity_id": _integer("ID da atividade.")}, ["activity_id"]),
        _tool(
            "get_metric_history",
            "Historico de metrica.",
            {
                "metric": _enum(["recovery", "hrv", "resting_hr", "strain", "sleep", "distance", "activity_duration"]),
                "start_day": _date("Inicio YYYY-MM-DD."),
                "end_day": _date("Fim YYYY-MM-DD."),
                "source": _string("Fonte opcional.", nullable=True),
            },
            ["metric", "start_day", "end_day"],
        ),
        _tool("get_today_meals", "Consulta refeicoes de hoje.", {}, []),
        _tool(
            "get_meal_history",
            "Consulta refeicoes por periodo.",
            {"start_day": _date("Inicio YYYY-MM-DD."), "end_day": _date("Fim YYYY-MM-DD.")},
            ["start_day", "end_day"],
        ),
        _tool("get_user_preferences", "Consulta memorias confirmadas.", {"category": _string("Categoria opcional.", nullable=True)}, []),
        _tool("get_user_goals", "Consulta objetivos confirmados.", {}, []),
        _tool("get_shoes", "Lista tenis cadastrados.", {"status": _string("active, retired ou vazio.", nullable=True)}, []),
        _tool("get_shoe_details", "Detalhes de um tenis.", {"shoe_id": _integer("ID do tenis.")}, ["shoe_id"]),
        _tool("get_shoe_usage_history", "Historico de uso de um tenis.", {"shoe_id": _integer("ID do tenis.")}, ["shoe_id"]),
        _tool(
            "get_shoe_recommendation_context",
            "Contexto para recomendacao de tenis.",
            {"planned_activity": _object("Treino planejado opcional.", nullable=True)},
            [],
        ),
        _tool("get_data_quality", "Consulta qualidade dos dados de um dia.", {"day": _date("Dia YYYY-MM-DD.")}, ["day"]),
        _tool(
            "get_sync_status",
            "Consulta o estado da ultima sincronizacao de Strava e/ou WHOOP.",
            {"source": _enum(["all", "strava", "whoop"])},
            ["source"],
        ),
        _tool(
            "sync_integrations",
            "Inicia sincronizacao Strava e/ou WHOOP somente quando o usuario pedir claramente para atualizar ou sincronizar dados agora.",
            {"source": _enum(["all", "strava", "whoop"])},
            ["source"],
        ),
        _tool(
            "create_meal_log",
            "Registra refeicao ja consumida. Nao use para intencao futura.",
            {
                "description": _string("Descricao informada pelo usuario."),
                "consumed_at": _string("Data/hora ISO opcional.", nullable=True),
                "meal_type": _string("Tipo da refeicao.", nullable=True),
                "items": _array("Itens opcionais.", nullable=True),
            },
            ["description"],
        ),
        _tool(
            "save_confirmed_memory",
            "Salva memoria confirmada pelo usuario.",
            {"category": _string("Categoria."), "key": _string("Chave."), "value": _string("Valor em texto ou JSON.")},
            ["category", "key", "value"],
        ),
        _tool(
            "create_shoe",
            "Cadastra tenis novo.",
            {
                "name": _string("Nome do tenis."),
                "brand": _string("Marca.", nullable=True),
                "model": _string("Modelo.", nullable=True),
                "color": _string("Cor.", nullable=True),
                "initial_distance_km": _number("Km inicial.", nullable=True),
                "expected_min_km": _number("Vida minima esperada.", nullable=True),
                "expected_max_km": _number("Vida maxima esperada.", nullable=True),
            },
            ["name"],
        ),
        _tool(
            "associate_shoe_with_activity",
            "Associa tenis a atividade existente.",
            {"shoe_id": _integer("ID do tenis."), "activity_id": _integer("ID da atividade."), "confidence": _number("Confianca.", nullable=True)},
            ["shoe_id", "activity_id"],
        ),
        _tool("retire_shoe", "Aposenta tenis. Exige confirmacao.", {"shoe_id": _integer("ID do tenis."), "notes": _string("Observacao.", nullable=True)}, ["shoe_id"]),
        _tool(
            "create_manual_shoe_usage",
            "Registra um uso manual de tenis (ex.: corrida sem Strava/Garmin), informando data e distancia.",
            {
                "shoe_id": _integer("ID do tenis."),
                "usage_date": _date("Data do uso YYYY-MM-DD."),
                "distance_km": _number("Distancia percorrida em km."),
                "activity_type": _string("Tipo de atividade.", nullable=True),
                "notes": _string("Observacao opcional.", nullable=True),
            },
            ["shoe_id", "usage_date", "distance_km"],
        ),
        _tool(
            "save_daily_note",
            "Salva uma nota livre associada a um dia.",
            {"day": _date("Dia YYYY-MM-DD."), "note": _string("Texto da nota.")},
            ["day", "note"],
        ),
        _tool(
            "save_planned_activity",
            "Salva um treino planejado para uma data futura ou de hoje.",
            {
                "planned_for": _date("Data planejada YYYY-MM-DD."),
                "activity_type": _string("Tipo de atividade planejada."),
                "distance_km": _number("Distancia planejada em km.", nullable=True),
                "intensity": _string("Intensidade (leve, moderada, forte).", nullable=True),
                "surface": _string("Superficie (asfalto, trilha, esteira...).", nullable=True),
                "notes": _string("Observacao opcional.", nullable=True),
            },
            ["planned_for", "activity_type"],
        ),
        _tool(
            "update_meal_log",
            "Atualiza uma refeicao ja registrada. Exige confirmacao.",
            {
                "meal_id": _integer("ID da refeicao."),
                "description": _string("Nova descricao.", nullable=True),
                "meal_type": _string("Novo tipo.", nullable=True),
                "notes": _string("Nova observacao.", nullable=True),
            },
            ["meal_id"],
        ),
        _tool(
            "delete_meal_log",
            "Exclui uma refeicao registrada. Exige confirmacao.",
            {"meal_id": _integer("ID da refeicao.")},
            ["meal_id"],
        ),
        _tool(
            "update_memory",
            "Atualiza o valor de uma memoria confirmada. Exige confirmacao.",
            {"memory_id": _integer("ID da memoria."), "value": _string("Novo valor (texto ou JSON).")},
            ["memory_id", "value"],
        ),
        _tool(
            "delete_memory",
            "Apaga (desativa) uma memoria confirmada. Exige confirmacao.",
            {"memory_id": _integer("ID da memoria.")},
            ["memory_id"],
        ),
        _tool(
            "update_shoe",
            "Atualiza dados de um tenis cadastrado. Exige confirmacao.",
            {
                "shoe_id": _integer("ID do tenis."),
                "name": _string("Novo nome.", nullable=True),
                "brand": _string("Nova marca.", nullable=True),
                "model": _string("Novo modelo.", nullable=True),
                "color": _string("Nova cor.", nullable=True),
                "status": _string("active ou retired.", nullable=True),
                "condition_notes": _string("Nova observacao de estado.", nullable=True),
                "initial_distance_km": _number("Novo km inicial.", nullable=True),
                "expected_min_km": _number("Nova vida minima esperada.", nullable=True),
                "expected_max_km": _number("Nova vida maxima esperada.", nullable=True),
            },
            ["shoe_id"],
        ),
    ]


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties.keys()),
            "additionalProperties": False,
        },
    }


def _string(description: str, nullable: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "description": description}
    return _nullable(schema, nullable)


def _date(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description, "pattern": r"^\d{4}-\d{2}-\d{2}$"}


def _integer(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _number(description: str, nullable: bool = False) -> dict[str, Any]:
    return _nullable({"type": "number", "description": description}, nullable)


def _enum(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


def _object(description: str, nullable: bool = False) -> dict[str, Any]:
    return _nullable(
        {
            "type": "object",
            "description": description,
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        nullable,
    )


def _array(description: str, nullable: bool = False) -> dict[str, Any]:
    return _nullable(
        {
            "type": "array",
            "description": description,
            "items": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
        nullable,
    )


def _nullable(schema: dict[str, Any], nullable: bool) -> dict[str, Any]:
    if not nullable:
        return schema
    copy = dict(schema)
    copy["type"] = [schema["type"], "null"]
    return copy
