# Humberto Performance

Sistema pessoal para consolidar treinos, sono, recuperacao e check-ins subjetivos.

## Endereco Do NAS

Na rede local, a aplicacao deve ficar acessivel em:

<http://192.168.1.16:3030>

O container escuta internamente em `0.0.0.0:8000`, e o Docker Compose publica `3030:8000`.

## Rodar Localmente No Windows

```powershell
cd W:\saude
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 3030
```

Acesse: <http://127.0.0.1:3030>

O `.env` local contem a senha de acesso. A senha pode ser trocada editando `APP_LOCAL_PASSWORD` no `.env` e reiniciando a aplicacao. O projeto ainda compara essa senha local em texto puro; a migracao para `APP_LOCAL_PASSWORD_HASH` com bcrypt/argon2 esta pendente para uma etapa futura.

## Variaveis Principais

- `APP_PUBLIC_BASE_URL`: endereco publico usado para links externos e OAuth.
- `APP_SECRET_KEY`: segredo de assinatura de sessao. Nao reutilize valores antigos.
- `APP_LOCAL_PASSWORD`: senha local de acesso enquanto o hash ainda nao foi ativado.
- `DATABASE_URL`: banco SQLite persistente.
- `APP_DEMO_DATA`: use `false` em producao.
- `UPLOAD_DIR` e `EXPORT_DIR`: diretorios persistentes.
- `STRAVA_ENABLED`: liga/desliga a integracao.
- `STRAVA_CLIENT_ID`: ID publico do app Strava.
- `STRAVA_CLIENT_SECRET`: preencher manualmente no `.env`.
- `STRAVA_REDIRECT_URI`: `http://192.168.1.16:3030/integrations/strava/callback`.
- `TOKEN_ENCRYPTION_KEY`: chave Fernet separada de `APP_SECRET_KEY`.

## Strava

No painel do Strava, cadastre:

- Authorization Callback Domain: `192.168.1.16`
- Redirect URI usado pela aplicacao: `http://192.168.1.16:3030/integrations/strava/callback`
- Escopo solicitado: `activity:read`

Nao cole access token ou refresh token manualmente no `.env`. A aplicacao obtem tokens pelo OAuth e grava os tokens criptografados.

Enquanto `STRAVA_CLIENT_SECRET` estiver vazio, a aplicacao inicia normalmente e a tela `/integracoes` informa que falta configurar o Client Secret. O botao de conexao fica desabilitado.

## Docker No Synology Container Manager

1. Copie o projeto para uma pasta persistente do NAS, por exemplo `/volume1/docker/saude`.
2. Crie os diretorios:
   ```bash
   mkdir -p data/uploads data/exports
   ```
3. Copie ou crie o `.env` manualmente a partir de `.env.example`.
4. Preencha os segredos no `.env`, sem versionar o arquivo.
5. Confirme que `APP_PUBLIC_BASE_URL=http://192.168.1.16:3030`.
6. Confirme que `DATABASE_URL=sqlite:///./data/humberto_performance.db` no `.env`.
7. Suba:
   ```bash
   docker compose up -d --build
   ```
8. Teste:
   <http://192.168.1.16:3030/health>
9. Abra:
   <http://192.168.1.16:3030/integracoes>
10. Preencha `STRAVA_CLIENT_SECRET` no `.env`, reinicie o container e conecte o Strava.
11. Depois da primeira sincronizacao, valide as atividades no dashboard.
12. Crie backups periodicos de `data/humberto_performance.db`.

Para atualizar o container no futuro:

```bash
docker compose down
docker compose build
docker compose up -d
```

Nao use opcoes que removam volumes ou apaguem `data/`.

## Testes E Lint

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
```

## Banco E Alembic

O banco real deve estar em `0003_activity_source_links (head)`.

Para bancos antigos criados por `create_all()` sem `alembic_version`, existe o script seguro:

```powershell
.\.venv\Scripts\python scripts\adopt_existing_database.py --database W:\saude\data\humberto_performance.db --yes
```

Esse script valida schema, cria backup timestampado e nao apaga dados.

## Importacao Manual

- CSV: uma linha por atividade. Campos aceitos incluem `activity_type`, `started_at`, `ended_at`, `duration_seconds`, `distance_meters`, `avg_hr`, `max_hr`, `calories`, `notes`.
- JSON: aceita uma lista de atividades ou um objeto com chaves `activities`, `sleep`, `recovery`, `checkins`.
- FIT: usa `fitparse` para extrair sessoes, voltas e amostras quando os campos existirem.

## Privacidade E Seguranca

- `.env` fica fora do Git.
- Banco, backups, uploads e FITs ficam fora do Git.
- Tokens e credenciais nao devem aparecer em logs.
- Coordenadas de FIT/Strava nao sao persistidas em amostras.

Este endereco funciona na rede local. Para acesso externo futuro, use dominio HTTPS e proxy reverso; nesse caso atualize `APP_PUBLIC_BASE_URL` e `STRAVA_REDIRECT_URI`.
