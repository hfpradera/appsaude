# 0001 - Arquitetura do MVP

## Status

Aceita.

## Contexto

O sistema deve rodar primeiro no Windows e depois no Synology NAS, sem depender de integracoes externas na Fase 1.

## Decisao

Usar FastAPI, SQLAlchemy, SQLite em desenvolvimento, PostgreSQL no Docker, Jinja para interface e importadores manuais.

## Consequencias

- Menos complexidade inicial que uma SPA React.
- Facilidade para testar regras de negocio e importadores.
- Migração futura para PostgreSQL ja prevista.
- OAuth fica isolado para fases futuras.
