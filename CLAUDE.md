# CLAUDE.md

## Sobre o projeto

<!-- TODO: Descreva o proposito do seu agente aqui -->
Boilerplate para agente inteligente com FastAPI + LangChain + MCP Server.

## Comandos

```bash
# Instalar dependencias
uv sync

# Executar aplicacao
uv run uvicorn my_agent_app.main:app --host 0.0.0.0 --port 8000

# Subir infraestrutura (PostgreSQL, pgAdmin)
docker compose up -d

# Aplicar migrations do banco de dados (apos criar modelos e configurar Alembic)
# uv run alembic upgrade head
```

Nao ha testes ou linter configurados ainda.

## Arquitetura

O codigo da aplicacao fica em `src/my_agent_app/` e e empacotado via hatchling (`pyproject.toml`).

- **Database** (`database.py`) — Engine async SQLAlchemy + asyncpg, configurado via `DATABASE_URL`
- **API** (`api/router.py`) — Health check
- **Interface Web** (`web/router.py`) — Pagina inicial. Templates Jinja2 em `src/my_agent_app/templates/`

### Modulos placeholder (a implementar)

- **Agents** (`agents/`) — Agentes LangChain + MCP Server
- **Collector** (`collector/`) — Loop periodico de coleta de dados
- **Models** (`models/`) — Modelos SQLAlchemy e queries

## Variaveis de ambiente

| Variavel | Descricao | Padrao |
|----------|-----------|--------|
| `ANTHROPIC_API_KEY` | Chave de API da Anthropic | (obrigatorio) |
| `DATABASE_URL` | Connection string PostgreSQL async | `postgresql+asyncpg://appuser:apppass123@localhost:5432/app_db` |

## Infraestrutura (docker-compose)

| Servico | Porta | Credenciais |
|---------|-------|-------------|
| PostgreSQL 17 | 5432 | appuser / apppass123 / app_db |
| pgAdmin | 5050 | admin@admin.com / admin123 |
