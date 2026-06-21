import json as _json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import markdown as md_lib
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from my_agent_app.models import Report, ReportStatus

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html")


@router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    db_ok = False
    report_stats = []
    try:
        async with request.app.state.sessionmaker() as session:
            result = await session.execute(
                select(Report.status, func.count().label("count"))
                .group_by(Report.status)
                .order_by(func.count().desc())
            )
            report_stats = [{"status": row.status, "count": row.count} for row in result.all()]
            db_ok = True
    except SQLAlchemyError:
        logger.warning("Could not load report stats for health page", exc_info=True)

    return templates.TemplateResponse(
        request,
        "health.html",
        {
            "db_ok": db_ok,
            "report_stats": [
                {"status": str(r["status"]), "count": r["count"]} for r in report_stats
            ],
            "total_reports": sum(r["count"] for r in report_stats),
            "model": os.environ.get("AGENT_MODEL_NAME", "anthropic/claude-sonnet-4-5"),
            "mcp_url": os.environ.get("MCP_SERVER_URL", "http://mcp-server-kubernetes:3001/mcp"),
            "now": datetime.now(UTC).strftime("%m/%d/%Y %H:%M UTC"),
        },
    )


@router.get("/reports", response_class=HTMLResponse)
async def reports_list(request: Request):
    try:
        async with request.app.state.sessionmaker() as session:
            result = await session.execute(
                select(Report).order_by(Report.created_at.desc())
            )
            reports = result.scalars().all()
    except SQLAlchemyError:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": "Database unavailable, please try again shortly"},
            status_code=503,
        )

    return templates.TemplateResponse(
        request, "reports.html", {"reports": reports}
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(request: Request, report_id: uuid.UUID):
    try:
        async with request.app.state.sessionmaker() as session:
            report = await session.get(Report, report_id)
    except SQLAlchemyError:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": "Database unavailable, please try again shortly"},
            status_code=503,
        )

    if report is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Report {report_id} not found"},
            status_code=404,
        )

    html_content = md_lib.markdown(
        report.markdown or "",
        extensions=["tables", "fenced_code"],
    )

    return templates.TemplateResponse(
        request,
        "report_detail.html",
        {"report": report, "html_content": html_content},
    )


async def _run_fix_in_background(
    report_id: uuid.UUID,
    report_markdown: str,
    sessionmaker,
) -> None:
    from my_agent_app.agents.fix_agent import run_fix_execution

    async def _set_status(status: str, extra_markdown: str = "") -> None:
        try:
            async with sessionmaker() as session:
                obj = await session.get(Report, report_id)
                if obj:
                    obj.status = status
                    obj.updated_at = datetime.now(UTC)
                    if extra_markdown:
                        obj.markdown = obj.markdown + "\n\n---\n\n" + extra_markdown
                    await session.commit()
        except Exception:
            logger.exception("Failed to update report %s status to %s", report_id, status)

    await _set_status(ReportStatus.CORRIGINDO)

    try:
        result_md, success = await run_fix_execution(report_markdown)
        new_status = ReportStatus.CORRIGIDO if success else ReportStatus.FALHA_CORRECAO
        await _set_status(new_status, result_md)
        logger.info("Fix for report %s completed: %s", report_id, new_status)
    except Exception:
        logger.exception("Fix agent failed for report %s", report_id)
        await _set_status(
            ReportStatus.FALHA_CORRECAO,
            (
                "## Fix Result\n\n**Status:** FAILURE\n\n"
                "Internal error while running the fix agent. Check server logs."
            ),
        )


@router.get("/reports/{report_id}/fix/stream")
async def stream_fix_report(request: Request, report_id: uuid.UUID):
    """Server-Sent Events endpoint: initiates and streams the fix execution in real time."""
    _sse_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }

    def _sse(event: dict) -> str:
        return f"data: {_json.dumps(event)}\n\n"

    try:
        async with request.app.state.sessionmaker() as session:
            report = await session.get(Report, report_id)
    except SQLAlchemyError:

        async def _db_err():
            yield _sse({"type": "error", "content": "Database unavailable"})

        return StreamingResponse(_db_err(), media_type="text/event-stream", headers=_sse_headers)

    if report is None:

        async def _not_found():
            yield _sse({"type": "error", "content": f"Report {report_id} not found"})

        return StreamingResponse(
            _not_found(), media_type="text/event-stream", headers=_sse_headers
        )

    if report.status not in (ReportStatus.COMPLETO, ReportStatus.FALHA_CORRECAO):

        async def _not_fixable():
            yield _sse({
                "type": "error",
                "content": (
                    "Report is not in a fixable state (must be COMPLETE or FIX_FAILED)."
                ),
            })

        return StreamingResponse(
            _not_fixable(), media_type="text/event-stream", headers=_sse_headers
        )

    report_markdown = report.markdown or ""
    sessionmaker = request.app.state.sessionmaker

    async def _generate():
        from my_agent_app.agents.fix_agent import stream_fix_execution

        async with sessionmaker() as session:
            obj = await session.get(Report, report_id)
            if obj:
                obj.status = ReportStatus.CORRIGINDO
                obj.updated_at = datetime.now(UTC)
                await session.commit()

        ai_parts: list[str] = []
        error_occurred = False

        try:
            async for event in stream_fix_execution(report_markdown):
                yield _sse(event)
                if event["type"] == "ai_token":
                    ai_parts.append(event.get("content", ""))
                elif event["type"] == "error":
                    error_occurred = True
        except Exception as exc:
            logger.exception("SSE fix stream failed for report %s", report_id)
            error_occurred = True
            yield _sse({"type": "error", "content": str(exc)})

        full_output = "".join(ai_parts)
        success = not error_occurred and bool(full_output) and "SUCCESS" in full_output.upper()
        new_status = ReportStatus.CORRIGIDO if success else ReportStatus.FALHA_CORRECAO

        try:
            async with sessionmaker() as session:
                obj = await session.get(Report, report_id)
                if obj:
                    obj.status = new_status
                    obj.updated_at = datetime.now(UTC)
                    if full_output.strip():
                        obj.markdown = (obj.markdown or "") + "\n\n---\n\n" + full_output
                    await session.commit()
        except Exception:
            logger.exception("Failed to persist fix result for report %s", report_id)

        yield _sse({"type": "status_update", "status": new_status, "success": success})

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_sse_headers)


@router.post("/reports/{report_id}/fix")
async def fix_report(
    request: Request,
    report_id: uuid.UUID,
    background_tasks: BackgroundTasks,
):
    try:
        async with request.app.state.sessionmaker() as session:
            report = await session.get(Report, report_id)
    except SQLAlchemyError:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": "Database unavailable"},
            status_code=503,
        )

    if report is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Report {report_id} not found"},
            status_code=404,
        )

    if report.status not in (ReportStatus.COMPLETO, ReportStatus.FALHA_CORRECAO):
        return RedirectResponse(url=f"/reports/{report_id}?msg=already_running", status_code=303)

    background_tasks.add_task(
        _run_fix_in_background,
        report_id=report_id,
        report_markdown=report.markdown or "",
        sessionmaker=request.app.state.sessionmaker,
    )

    return RedirectResponse(url=f"/reports/{report_id}?msg=fix_started", status_code=303)
