# Humberto Performance

Sistema pessoal para consolidar treinos, sono, recuperacao e check-ins subjetivos.

## Rodar localmente no Windows

```powershell
cd W:\saude
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Acesse: <http://127.0.0.1:8000>

Senha local inicial: valor de `APP_LOCAL_PASSWORD` no `.env`. Em desenvolvimento, se nao houver `.env`, a senha provisoria e `humberto-dev`.

## Testes e lint

```powershell
.\.venv\Scripts\pytest
.\.venv\Scripts\ruff check .
```

## Docker local

```powershell
docker compose up --build
```

A aplicacao sobe em <http://127.0.0.1:8000> e usa PostgreSQL.

## Synology Container Manager

1. Copie o projeto para uma pasta persistente do NAS.
2. Crie um arquivo `.env` a partir de `.env.example`.
3. Troque `APP_SECRET_KEY` e `APP_LOCAL_PASSWORD`.
4. Configure volumes persistentes para banco e uploads.
5. Suba com o compose pelo Container Manager.

## Importacao manual

- CSV: uma linha por atividade. Campos aceitos incluem `activity_type`, `started_at`, `ended_at`, `duration_seconds`, `distance_meters`, `avg_hr`, `max_hr`, `calories`, `notes`.
- JSON: aceita uma lista de atividades ou um objeto com chaves `activities`, `sleep`, `recovery`, `checkins`.
- FIT: usa `fitparse` para extrair sessoes, voltas e amostras quando os campos existirem.

## Privacidade

- `.env` fica fora do Git.
- Tokens e credenciais nao devem ser registrados em logs.
- O MVP nao envia dados para servicos externos.
- Exportacao e exclusao de dados estao previstas; exportacao JSON/Markdown ja existe.

## Proximas fases

- Strava OAuth 2.0 apos consulta da documentacao oficial.
- WHOOP OAuth apos consulta da documentacao oficial.
- Garmin via API oficial aprovada ou FIT/Strava.
