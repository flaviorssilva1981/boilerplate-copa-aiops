# CLAUDE.md

## About this project

Kubernetes AIOps agent — automatically detects, diagnoses, and remediates Kubernetes problems using FastAPI, LangChain (ReAct), Claude Sonnet via Requesty AI, and a live MCP Kubernetes server.

## Commands

```bash
# Install dependencies
uv sync

# Run the application locally
uv run uvicorn my_agent_app.main:app --host 0.0.0.0 --port 8000

# Start the database (Docker provisions PostgreSQL + pgAdmin only)
docker compose up -d

# Start the MCP Kubernetes server locally via npx (HTTP/streamable transport, port 3001)
# Requires Node.js/npx and a valid kubeconfig at ~/.kube/config
ENABLE_UNSAFE_STREAMABLE_HTTP_TRANSPORT=1 PORT=3001 npx mcp-server-kubernetes

# Deploy to OKE (set ANTHROPIC_API_KEY first)
.\deploy\oke-deploy.ps1
```

The application runs locally via `uv`; Docker is only used for the database.
The MCP Server runs via `npx` exposing HTTP transport; LangChain connects via `MCP_SERVER_URL`.

No tests or linter configured yet.

## Architecture

Code lives in `src/my_agent_app/` and is packaged via hatchling (`pyproject.toml`).

### AIOps loop (4 steps)

1. **Collect** — `collector/collector.py` polls Kubernetes warning events every 3 minutes via the in-cluster API. New events not already covered by a report are grouped and sent to the RCA agent.
2. **Diagnose** — `agents/rca_agent.py` (LangChain ReAct + Claude Sonnet) investigates events using read-only MCP tools (`kubectl_get`, `kubectl_describe`, `kubectl_logs`). Writes a structured Markdown report.
3. **Persist** — report saved to PostgreSQL (`models/report.py`) with status `ANALYZING → COMPLETE`. Events are deduplicated by UID.
4. **Remediate** — operator clicks "Execute Fix" in the web UI. `agents/fix_agent.py` reads the report and executes fixes via MCP (`kubectl_apply`, `kubectl_patch`, etc.), streaming each step live to the browser via Server-Sent Events.

### Modules

- **`main.py`** — FastAPI app, lifespan (starts collector), Basic Auth middleware
- **`database.py`** — async SQLAlchemy engine + session factory
- **`api/router.py`** — `/api/health`, `/api/health/cluster` (live k8s metrics), `/api/agent/ping`
- **`web/router.py`** — web routes: `/`, `/health`, `/reports`, `/reports/:id`, `GET /reports/:id/fix/stream` (SSE)
- **`templates/`** — Jinja2 HTML templates (dark theme, Chart.js health dashboard)
- **`agents/rca_agent.py`** — RCA agent: LangChain + MCP read-only tools, structured Markdown output
- **`agents/fix_agent.py`** — Fix agent: LangChain + MCP write tools, SSE streaming via `astream_events`
- **`collector/`** — asyncio background loop + event deduplication
- **`models/report.py`** — SQLAlchemy `Report` model, `ReportStatus` enum, `title()` / `severity()` helpers

### Health Dashboard

`GET /api/health/cluster` calls the Kubernetes API **directly** (using the pod's service account token) — not via MCP:
- `/apis/metrics.k8s.io/v1beta1/nodes` — live CPU and memory usage
- `/api/v1/nodes` — allocatable capacity per node
- `/api/v1/pods` — pod counts by phase

RBAC ClusterRole `aiops-dashboard-reader` grants the `aiops-app` service account read access to these resources.

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Requesty AI key (Anthropic-compatible) | (required) |
| `ANTHROPIC_BASE_URL` | LLM proxy base URL | `https://router.requesty.ai` |
| `AGENT_MODEL_NAME` | Claude model slug | `anthropic/claude-sonnet-4-5` |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://aiops:aiops123@localhost:5432/aiops_k8s` |
| `MCP_SERVER_URL` | HTTP endpoint of the MCP Kubernetes server | `http://localhost:3001/mcp` |
| `MCP_AUTH_TOKEN` | Optional bearer token for MCP auth | (none) |
| `BASIC_AUTH_USER` | Web UI username | `admin` |
| `BASIC_AUTH_PASSWORD` | Web UI password | (required) |
| `GITHUB_TOKEN` | GitHub PAT for GitOps fixes (injected from GitHub Secret `GITOPS_GITHUB_TOKEN`) | (optional) |
| `GITOPS_REPO` | GitOps repository (`owner/repo`) | `flaviorssilva1981/guiadodevops` |
| `GITOPS_WORK_BRANCH` | Branch to create fix branches from | `dev` |
| `GITOPS_DEPLOY_BRANCH` | PR target / Argo CD sync branch | `main` |
| `GITOPS_AUTO_MERGE` | Auto-merge PRs after creation (`true`/`false`) | `false` |

## Infrastructure (docker-compose — local only)

Docker provisions **only the database**. The app and MCP server run locally.

| Service | Port | Credentials |
|---------|------|-------------|
| PostgreSQL 17 | 5432 | aiops / aiops123 / aiops_k8s |
| pgAdmin | 5050 | admin@admin.com / admin123 |

## Kubernetes manifests (k8s/aiops/)

| Manifest | Creates |
|----------|---------|
| `namespace.yaml` | `aiops` namespace |
| `rbac.yaml` | ServiceAccounts, ClusterRoles, ClusterRoleBindings |
| `postgres.yaml` | PostgreSQL 17 StatefulSet |
| `mcp-server.yaml` | `npx mcp-server-kubernetes` Deployment |
| `app.yaml` | FastAPI Deployment (source from ConfigMap) |
| `ingress.yaml` | NGINX Ingress with TLS |
