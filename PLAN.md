# Humberto Performance - Plano

## Objetivo

Criar um sistema pessoal, inicialmente local em Windows e depois em Docker no Synology NAS, para consolidar dados de treino, sono, recuperacao e check-ins subjetivos. O MVP cobre a Fase 1: estrutura, banco, interface, importacao manual, relatorios e exportacao.

## Decisoes iniciais seguras e reversiveis

- Backend em Python com FastAPI.
- Interface web server-rendered com Jinja, CSS proprio e um pouco de JavaScript local. E mais simples que React para este MVP.
- SQLite no desenvolvimento inicial, com `DATABASE_URL` preparado para PostgreSQL em Docker/producao.
- SQLAlchemy como ORM e Alembic para migracoes.
- Aplicacao de usuario unico, protegida por senha local via cookie assinado.
- Dados em UTC no banco e exibicao em `America/Sao_Paulo`.
- Nenhuma integracao externa e implementada na Fase 1.
- Importacao FIT usa `fitparse` quando disponivel. Se uma metrica nao existir no arquivo, fica vazia e marcada como dado ausente.
- Regras de treino transparentes, conservadoras e configuraveis em codigo nesta primeira versao.
- Tokens futuros serao guardados criptografados. O MVP ja separa `OAuthCredential`, mas nao grava tokens reais.

## Arquitetura

- `app/main.py`: cria a aplicacao FastAPI, banco, rotas e dados demo.
- `app/models.py`: modelo relacional da Fase 1 e entidades preparadas para OAuth/sync.
- `app/services/importers.py`: importadores JSON, CSV e FIT.
- `app/services/reconciliation.py`: deteccao de duplicidade sem apagar dados.
- `app/services/reports.py`: dashboard, relatorio diario, semanal e regras conservadoras.
- `app/templates`: paginas HTML em portugues do Brasil.
- `tests`: testes de regras, importadores, duplicidade e fuso horario.
- `docs/apis.md`: registro das APIs externas a consultar antes das Fases 2 e 3.
- `docs/decisions`: decisoes tecnicas versionadas.

## Fase 1 - Entregue no MVP

- Estrutura do projeto.
- Banco com entidades principais.
- Tela inicial/Hoje.
- Atividades e detalhe de atividade.
- Sono e recuperacao.
- Check-in manual diario.
- Importacao manual de CSV, JSON e FIT.
- Jobs de importacao e logs.
- Dashboard diario.
- Relatorio semanal.
- Exportacao JSON e Markdown.
- Dados ficticios para demonstracao.
- Dockerfile, docker-compose e instrucoes.
- Testes automatizados focados.

## Riscos

- API oficial Garmin pode nao estar disponivel para uso pessoal sem aprovacao.
- Nem toda metrica exibida no WHOOP/Garmin/Strava fica disponivel via API.
- FIT de diferentes dispositivos pode ter campos variados. O importador deve evoluir com arquivos reais.
- Reconciliacao automatica pode errar. Por isso o MVP apenas sinaliza duplicidades e preserva registros.
- Recomendacoes nao sao medicas. O sistema deve evitar linguagem de liberacao clinica.

## Antes das integracoes externas

Antes de programar Strava ou WHOOP, consultar documentacao oficial atual para endpoints, escopos, rate limits e campos disponiveis. Nao criar endpoints por suposicao. Para Garmin, priorizar API oficial aprovada; como alternativa, manter importacao FIT e atividades vindas do Strava.
