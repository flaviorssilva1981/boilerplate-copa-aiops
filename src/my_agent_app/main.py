import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from my_agent_app.api.router import router as api_router
from my_agent_app.collector import EventCollector, EventHandler
from my_agent_app.database import Base, get_database_url
from my_agent_app.models import Report, ReportStatus
from my_agent_app.web.router import router as web_router

load_dotenv()

logging.basicConfig(level=logging.INFO)

_AUTH_USER = os.environ.get("BASIC_AUTH_USER", "")
_AUTH_PASSWORD = os.environ.get("BASIC_AUTH_PASSWORD", "")

# Paths that bypass authentication (k8s liveness/readiness probes)
_AUTH_SKIP_PREFIXES = ("/api/health",)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _AUTH_PASSWORD:
            return await call_next(request)

        for prefix in _AUTH_SKIP_PREFIXES:
            if request.url.path.startswith(prefix):
                return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        authenticated = False
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, _, submitted_password = decoded.partition(":")
                user_ok = secrets.compare_digest(username, _AUTH_USER)
                pass_ok = secrets.compare_digest(submitted_password, _AUTH_PASSWORD)
                authenticated = user_ok and pass_ok
            except (ValueError, UnicodeDecodeError):
                authenticated = False

        if not authenticated:
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Kubernetes AIOps"'},
            )

        return await call_next(request)


async def _recover_stale_reports(sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        result = await session.execute(
            update(Report)
            .where(Report.status == ReportStatus.EM_ANALISE)
            .values(status=ReportStatus.INCOMPLETO, updated_at=datetime.now(UTC))
            .returning(Report.id)
        )
        recovered = result.scalars().all()
        await session.commit()
    if recovered:
        logging.getLogger(__name__).warning(
            "Startup recovery: marked %s stale ANALYZING report(s) as INCOMPLETE",
            len(recovered),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_async_engine(get_database_url())

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app.state.sessionmaker = sessionmaker

    await _recover_stale_reports(sessionmaker)

    handler = EventHandler(sessionmaker=sessionmaker)
    collector = EventCollector(handler=handler)
    app.state.collector = collector
    await collector.start()

    yield

    await collector.stop()
    await engine.dispose()


app = FastAPI(title="Kubernetes AIOps", lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)

_static_dir = Path(__file__).parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

app.include_router(api_router)
app.include_router(web_router)
