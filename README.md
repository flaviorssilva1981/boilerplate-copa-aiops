# Boilerplate: FastAPI + LangChain Agent + MCP

Boilerplate para criar agentes inteligentes com:

- **FastAPI** (async) como backend
- **LangChain + Claude Sonnet** como framework de agentes (padrao ReAct)
- **MCP Server** para integracao com ferramentas externas
- **PostgreSQL** + SQLAlchemy async para persistencia
- **Alembic** para migrations
- **Jinja2** para interface web com dark theme

## Como usar

### 1. Copiar o boilerplate

```bash
cp -r boilerplate/ ~/projetos/meu-novo-agente/
cd ~/projetos/meu-novo-agente/
```

### 2. Renomear o package

Renomeie `my_agent_app` para o nome do seu projeto em todos os arquivos:

```bash
# Renomear diretorio
mv src/my_agent_app src/meu_agente

# Substituir em todos os arquivos
find . -type f -name "*.py" -o -name "*.toml" -o -name "*.ini" -o -name "*.md" -o -name "*.html" | \
  xargs sed -i 's/my_agent_app/meu_agente/g; s/my-agent-app/meu-agente/g; s/My Agent App/Meu Agente/g'
```

### 3. Configurar ambiente

```bash
cp .env.example .env
# Edite .env com suas configuracoes (ANTHROPIC_API_KEY, etc)
```

### 4. Subir infraestrutura

```bash
docker compose up -d
```

### 5. Instalar dependencias

```bash
uv sync
```

### 6. Executar

```bash
uv run uvicorn meu_agente.main:app --host 0.0.0.0 --port 8000
```

## Estrutura

```
src/my_agent_app/
├── main.py              # FastAPI + lifespan
├── database.py          # SQLAlchemy async engine
├── api/router.py        # Health check
├── web/router.py        # Pagina inicial (Jinja2)
├── templates/           # Templates HTML (dark theme)
├── agents/              # (placeholder) Agentes LangChain + MCP
├── collector/           # (placeholder) Loop de coleta de dados
└── models/              # (placeholder) Modelos e queries
```

## Stack

| Tecnologia | Uso |
|------------|-----|
| FastAPI + Uvicorn | Backend async |
| SQLAlchemy 2.0 + asyncpg | ORM async com PostgreSQL |
| Alembic | Migrations |
| LangChain + Claude Sonnet | Framework de agentes IA |
| MCP Server | Integracao com ferramentas externas |
| Jinja2 | Interface web server-side |
| Docker Compose | Infraestrutura local |
| UV + hatchling | Package manager + build system |
