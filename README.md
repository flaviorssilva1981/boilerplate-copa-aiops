# Kubernetes AIOps — FastAPI + LangChain + MCP

An intelligent agent that **automatically detects, diagnoses, and remediates** Kubernetes problems using Claude Sonnet, LangChain, and a live MCP Server for `kubectl` access.

---

## Architecture

![System Architecture](src/my_agent_app/static/architecture.jpg)

### How it works

```
Kubernetes Cluster
       │  Warning Events (every 3 min)
       ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Application                       │
│                                                              │
│  ┌─────────────┐    ┌──────────────────┐    ┌────────────┐  │
│  │  Collector  │───▶│   RCA Agent      │───▶│ PostgreSQL │  │
│  │  (asyncio)  │    │  (LangChain +    │    │  Reports   │  │
│  │  3min loop  │    │   Claude Sonnet) │    │            │  │
│  └─────────────┘    └────────┬─────────┘    └────────────┘  │
│                              │ kubectl_get                   │
│                              │ kubectl_describe              │
│                              │ kubectl_logs                  │
│                              ▼                               │
│  ┌─────────────┐    ┌──────────────────┐                    │
│  │  Fix Agent  │    │   MCP Server     │                    │
│  │  (LangChain │◀──▶│  (npx k8s MCP)  │                    │
│  │  + Claude)  │    │  port 3001/HTTP  │                    │
│  └─────────────┘    └──────────────────┘                    │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                  Web UI (Jinja2)                       │  │
│  │  / Home  │  /reports  │  /reports/:id  │  /health     │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
       │  Direct Kubernetes API (service account)
       ▼
  Health Dashboard metrics (CPU, Memory, Pods)
```

### The 4-step AIOps loop

| Step | What happens |
|------|-------------|
| **1. Collect** | The `Collector` polls Kubernetes warning events every 3 minutes. New events not already covered by a report are grouped and forwarded to the RCA agent. |
| **2. Diagnose** | The `RCA Agent` (LangChain ReAct + Claude Sonnet via Requesty AI) investigates each event group using read-only MCP tools (`kubectl_get`, `kubectl_describe`, `kubectl_logs`). It writes a structured Markdown report with root cause, severity, affected resources, and suggested fixes. |
| **3. Persist** | The Markdown report is saved to PostgreSQL with status `ANALYZING → COMPLETE`. Reports are deduplicated by event UID to prevent repeated analysis. |
| **4. Remediate** | The operator clicks **Execute Fix** in the web UI. The `Fix Agent` (LangChain + Claude Sonnet) reads the report and executes the suggested remediation commands via MCP (`kubectl_apply`, `kubectl_patch`, `kubectl_rollout`, etc.), streaming each step in real time to a terminal-like modal. The report status is updated to `FIXED` or `FIX_FAILED`. |

---

## Features

### Web UI
- **Home page** — architecture overview, quick links
- **Reports list** — all RCA reports, sorted by date, with status badges
- **Report detail** — full Markdown report with rendered tables and code blocks
- **⚡ Real-time Fix Terminal** — click "Execute Fix" to open a terminal modal showing every `kubectl` command streamed live, exactly like a CI log
- **Health Dashboard** — live cluster metrics (CPU%, Memory%, Pod counts) fetched directly from the Kubernetes metrics API, auto-refreshing every 30 seconds

### Agents
- **RCA Agent** — read-only investigation using `kubectl_get`, `kubectl_describe`, `kubectl_logs`
- **Fix Agent** — remediation using `kubectl_apply`, `kubectl_patch`, `kubectl_delete`, `kubectl_scale`, `kubectl_rollout`, and more
- Both agents use **LangChain `create_agent`** (ReAct pattern) with **Claude Sonnet** via **Requesty AI**

### Security
- **HTTP Basic Authentication** (configurable via environment variables) on all web routes
- `/api/health` excluded from auth (for Kubernetes liveness probes)

### Health Dashboard (Lens-style)
- **CPU ring chart** — cluster-wide utilisation % (from metrics-server)
- **Memory ring chart** — cluster-wide memory usage %
- **Pods ring chart** — running pods / allocatable capacity
- **Nodes table** — per-node CPU and memory with mini progress bars
- **Pod status table** — breakdown by phase (Running / Pending / Succeeded / Failed)
- **Reports summary** — doughnut chart with status breakdown (COMPLETE, FIXED, FIX_FAILED…)

---

## Tech Stack

| Technology | Role |
|-----------|------|
| **FastAPI + Uvicorn** | Async HTTP backend |
| **LangChain 1.x** | ReAct agent framework (`create_agent`) |
| **Claude Sonnet 4.5** | LLM via Requesty AI (Anthropic proxy) |
| **MCP Server** (`npx mcp-server-kubernetes`) | `kubectl` tool server over HTTP/SSE |
| **`langchain-mcp-adapters`** | Bridges LangChain tools ↔ MCP protocol |
| **PostgreSQL 17 + asyncpg** | Report persistence |
| **SQLAlchemy 2.0 (async)** | ORM |
| **Server-Sent Events (SSE)** | Real-time fix execution streaming |
| **Chart.js 4.4** | Health dashboard ring/doughnut charts |
| **Jinja2** | Server-side HTML templating (dark theme) |
| **Kubernetes API (in-cluster)** | Direct metrics fetch via service account |
| **UV + hatchling** | Package manager and build system |
| **Docker Compose** | Local PostgreSQL + pgAdmin |
| **Docker Hub** | Container image registry (`flaviorssilva/aiops`) |
| **GitHub Actions** | CI/CD pipeline (build → push → deploy) |
| **Oracle OKE** | Production Kubernetes cluster |

---

## Project Structure

```
.
├── .github/
│   └── workflows/
│       └── deploy.yml             # GitHub Actions CI/CD pipeline
│
├── Dockerfile                     # Multi-stage build (UV + Python 3.12-slim)
│
├── deploy/
│   ├── oke-deploy.ps1             # First-time OKE setup script (PowerShell)
│   ├── oke-deploy.sh              # First-time OKE setup script (Bash)
│   └── generate-github-kubeconfig.ps1  # Generates KUBECONFIG_DATA GitHub Secret
│
├── k8s/aiops/
│   ├── namespace.yaml             # aiops namespace
│   ├── rbac.yaml                  # ServiceAccounts, ClusterRoles, ClusterRoleBindings
│   ├── github-actions-sa.yaml     # CI/CD service account + Role + RoleBinding
│   ├── postgres.yaml              # PostgreSQL 17 PVC + Deployment + Service
│   ├── mcp-server.yaml            # MCP Kubernetes server Deployment
│   ├── app.yaml                   # FastAPI application Deployment + Service
│   └── ingress.yaml               # NGINX Ingress with TLS
│
└── src/my_agent_app/
    ├── main.py                    # FastAPI app, lifespan, Basic Auth middleware
    ├── database.py                # SQLAlchemy async engine + session factory
    │
    ├── api/
    │   └── router.py              # /api/health, /api/health/cluster, /api/agent/ping
    │
    ├── web/
    │   └── router.py              # Web routes: /, /health, /reports, /reports/:id/fix/stream
    │
    ├── templates/
    │   ├── base.html              # Base layout (nav, dark theme, status badge CSS)
    │   ├── home.html              # Home page + architecture summary
    │   ├── health.html            # Lens-style health dashboard
    │   ├── reports.html           # Report list
    │   ├── report_detail.html     # Report detail + real-time fix terminal modal
    │   └── error.html             # Error page
    │
    ├── static/
    │   └── architecture.jpg       # Architecture diagram shown on home page
    │
    ├── agents/
    │   ├── llm.py                 # ChatAnthropic factory (Requesty AI)
    │   ├── rca_agent.py           # RCA agent: diagnose events, write Markdown reports
    │   └── fix_agent.py           # Fix agent: execute kubectl commands + SSE streaming
    │
    ├── collector/
    │   ├── collector.py           # asyncio background loop (every 3 min)
    │   └── event_handler.py       # Event deduplication + RCA dispatch
    │
    └── models/
        └── report.py              # SQLAlchemy Report model, ReportStatus enum, helpers
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Requesty AI key (Anthropic-compatible) | *(required)* |
| `ANTHROPIC_BASE_URL` | LLM proxy base URL | `https://router.requesty.ai` |
| `AGENT_MODEL_NAME` | Claude model slug | `anthropic/claude-sonnet-4-5` |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://aiops:aiops123@localhost:5432/aiops_k8s` |
| `MCP_SERVER_URL` | HTTP endpoint of the MCP Kubernetes server | `http://localhost:3001/mcp` |
| `MCP_AUTH_TOKEN` | Optional bearer token for MCP server auth | *(none)* |
| `BASIC_AUTH_USER` | Username for web UI Basic Auth | `admin` |
| `BASIC_AUTH_PASSWORD` | Password for web UI Basic Auth | *(required)* |

Copy `.env.example` to `.env` and fill in the values before running locally.

---

## Running Locally

### Prerequisites
- Python ≥ 3.12 with [UV](https://docs.astral.sh/uv/)
- Docker (for PostgreSQL)
- Node.js + npx (for MCP server)
- A valid `~/.kube/config` pointing to a cluster

### 1. Clone and configure

```bash
git clone <repo>
cd boilerplate-copa-aiops
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and BASIC_AUTH_PASSWORD
```

### 2. Start the database

```bash
docker compose up -d
# PostgreSQL on :5432, pgAdmin on :5050 (admin@admin.com / admin123)
```

### 3. Install dependencies

```bash
uv sync
```

### 4. Start the MCP Kubernetes server

```bash
ENABLE_UNSAFE_STREAMABLE_HTTP_TRANSPORT=1 PORT=3001 npx mcp-server-kubernetes
```

### 5. Run the application

```bash
uv run uvicorn my_agent_app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` — log in with the credentials from `.env`.

---

## CI/CD Pipeline (GitHub Actions)

Every push to `main` (i.e. after merging a PR) automatically triggers the pipeline defined in `.github/workflows/deploy.yml`.

```
push to main
     │
     ▼
┌─────────────────────────────────────────┐
│  Job 1: build                           │
│  ─────────────────────────────────────  │
│  1. checkout                            │
│  2. docker buildx build                 │
│  3. push flaviorssilva/aiops:<sha>      │
│     push flaviorssilva/aiops:latest     │
└──────────────────┬──────────────────────┘
                   │ (on success)
                   ▼
┌─────────────────────────────────────────┐
│  Job 2: deploy                          │
│  ─────────────────────────────────────  │
│  1. configure kubeconfig (SA token)     │
│  2. sync secrets → cluster              │
│  3. kubectl set image (if changed)      │
│  4. kubectl rollout status --timeout    │
│  5. smoke test /api/health              │
└─────────────────────────────────────────┘
```

### GitHub Secrets required

| Secret | Description |
|--------|-------------|
| `DOCKER_TOKEN` | Docker Hub personal access token |
| `KUBECONFIG_DATA` | Base64-encoded static kubeconfig (SA token, no OCI CLI needed) |
| `POSTGRES_PASSWORD` | PostgreSQL password injected into `postgres-credentials` k8s Secret |
| `BASIC_AUTH_PASSWORD` | Web UI Basic Auth password injected into `aiops-secrets` k8s Secret |
| `DATABASE_URL` | Full async connection string (derived from `POSTGRES_PASSWORD`) |

> All secrets were created in the repo automatically. To regenerate `KUBECONFIG_DATA` after cluster changes, run `deploy/generate-github-kubeconfig.ps1`.

### Anti-duplication guard

The deploy job reads the current image tag from the running deployment and **skips the rollout if the tag hasn't changed** — so re-triggering the workflow on a non-code commit (e.g. README update) won't cause an unnecessary pod restart.

### Concurrency

```yaml
concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: true
```

If two pushes arrive in quick succession, the older run is cancelled automatically.

### Secret sync on every deploy

Before updating the image, the pipeline patches both Kubernetes Secrets from GitHub Secrets:

- `postgres-credentials` → `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- `aiops-secrets` → `DATABASE_URL`, `BASIC_AUTH_PASSWORD`

This means **no passwords are ever stored in git**. Rotating a credential is a single GitHub Secret update — the next deploy propagates it to the cluster automatically.

---

## Deploying to Kubernetes (OKE)

### First-time setup (manual)

The `deploy/oke-deploy.ps1` script (PowerShell) creates all Kubernetes Secrets and rolls out all manifests from scratch:

```powershell
$env:ANTHROPIC_API_KEY   = "<your-requesty-key>"
$env:BASIC_AUTH_PASSWORD = "<your-web-ui-password>"
$env:POSTGRES_PASSWORD   = "<your-db-password>"
.\deploy\oke-deploy.ps1
```

### What it deploys

| Manifest | What it creates |
|----------|----------------|
| `k8s/aiops/namespace.yaml` | `aiops` namespace |
| `k8s/aiops/rbac.yaml` | ServiceAccounts, ClusterRoles, ClusterRoleBindings |
| `k8s/aiops/github-actions-sa.yaml` | CI/CD service account + Role + RoleBinding |
| `k8s/aiops/postgres.yaml` | PostgreSQL 17 PVC + Deployment + Service |
| `k8s/aiops/mcp-server.yaml` | `npx mcp-server-kubernetes` Deployment |
| `k8s/aiops/app.yaml` | FastAPI application Deployment (uses `flaviorssilva/aiops:latest`) |
| `k8s/aiops/ingress.yaml` | NGINX Ingress with TLS |

### RBAC summary

| ServiceAccount | Permissions |
|---------------|-------------|
| `aiops-app` | `events` list/watch + `nodes`/`pods`/`metrics.k8s.io` read (health dashboard) |
| `aiops-mcp-server` | `edit` ClusterRole + RBAC resources read/write (kubectl via MCP) |
| `github-actions` | `deployments` patch/update + `pods` list/watch (CI/CD only) |

---

## Report lifecycle

```
              ┌──────────────┐
  New event   │   ANALYZING  │  RCA agent running
──────────────▶              │
              └──────┬───────┘
                     │
          ┌──────────┴───────────┐
          │                      │
          ▼                      ▼
   ┌─────────────┐      ┌──────────────┐
   │  COMPLETE   │      │  INCOMPLETE  │
   │  (report    │      │  (agent      │
   │   written)  │      │   failed)    │
   └──────┬──────┘      └──────────────┘
          │ Click "Execute Fix"
          ▼
   ┌─────────────┐
   │   FIXING    │  Fix agent streaming
   └──────┬──────┘
          │
   ┌──────┴───────────┐
   │                  │
   ▼                  ▼
┌────────┐      ┌────────────┐
│ FIXED  │      │ FIX_FAILED │
└────────┘      └────────────┘
```

---

## Adapting this boilerplate

This project is designed to be forked and adapted for any domain that has:
- A data source that produces events/alerts (replace the Kubernetes collector)
- Tools that an LLM can use to investigate (replace the MCP server)
- Actions an LLM can take to remediate (extend the fix agent's tool list)

Key extension points:
1. **`collector/collector.py`** — change the polling source (e.g. Datadog alerts, PagerDuty, CloudWatch)
2. **`agents/rca_agent.py`** — tune the `SYSTEM_PROMPT` for your domain
3. **`agents/fix_agent.py`** — add/remove tools from `FIX_TOOLS`
4. **`models/report.py`** — extend `ReportStatus` or add new model fields
