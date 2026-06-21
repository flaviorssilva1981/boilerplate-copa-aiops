"""Real EventHandler: deduplication + RCA agent + PostgreSQL persistence."""

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, cast, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.types import String

from my_agent_app.agents.rca_agent import run_rca_analysis
from my_agent_app.models import Report, ReportStatus

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {
    ReportStatus.EM_ANALISE,
    ReportStatus.COMPLETO,
    ReportStatus.CORRIGINDO,
    ReportStatus.FALHA_CORRECAO,
}
REANALYZE_STATUSES = {ReportStatus.CORRIGIDO, ReportStatus.INCOMPLETO}


def _get_stale_timeout() -> int:
    raw = os.environ.get("ANALYSIS_STALE_TIMEOUT_MINUTES", "30")
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid ANALYSIS_STALE_TIMEOUT_MINUTES=%r; using 30", raw)
        return 30
    if value > 0:
        return value
    logger.warning("Invalid ANALYSIS_STALE_TIMEOUT_MINUTES=%r; using 30", raw)
    return 30


async def _mark_stale_reports(session: AsyncSession) -> None:
    stale_cutoff = datetime.now(UTC) - timedelta(minutes=_get_stale_timeout())
    await session.execute(
        update(Report)
        .where(
            and_(
                Report.status == ReportStatus.EM_ANALISE,
                Report.created_at < stale_cutoff,
            )
        )
        .values(status=ReportStatus.INCOMPLETO, updated_at=datetime.now(UTC))
    )
    await session.commit()


async def _filter_new_events(
    session: AsyncSession, events: list[dict]
) -> list[dict]:
    """Return only events whose UIDs are not already in an active report."""
    await _mark_stale_reports(session)

    input_uids = [e["uid"] for e in events if e.get("uid")]
    if not input_uids:
        return events

    uid_array = cast(input_uids, ARRAY(String))

    result = await session.execute(
        select(Report.event_uids, Report.status).where(
            and_(
                Report.status.in_([s.value for s in ACTIVE_STATUSES]),
                Report.event_uids.overlap(uid_array),
            )
        )
    )
    active_rows = result.all()

    blocked_uids: set[str] = set()
    for row in active_rows:
        blocked_uids.update(row.event_uids or [])

    new_events = [e for e in events if e.get("uid") not in blocked_uids]

    skipped = len(events) - len(new_events)
    if skipped:
        logger.info("Deduplication: skipping %s already-active event(s)", skipped)

    return new_events


class EventHandler:
    """Receives Warning events from the collector and runs the RCA agent pipeline."""

    def __init__(self, sessionmaker: async_sessionmaker | None = None) -> None:
        self._sessionmaker = sessionmaker

    def set_sessionmaker(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def handle(self, events: list[dict]) -> None:
        if not events:
            return

        if self._sessionmaker is None:
            logger.warning(
                "EventHandler has no sessionmaker; logging %s event(s) without analysis",
                len(events),
            )
            for event in events:
                logger.info("K8s event (no DB): %s", event)
            return

        async with self._sessionmaker() as session:
            try:
                new_events = await _filter_new_events(session, events)
            except Exception:
                logger.exception("Deduplication query failed; discarding batch")
                return

        if not new_events:
            logger.info("All events already in active reports; skipping analysis")
            return

        logger.info("Starting RCA analysis for %s new event(s)", len(new_events))
        event_uids = [e["uid"] for e in new_events if e.get("uid")]

        async with self._sessionmaker() as session:
            placeholder = Report(
                id=uuid.uuid4(),
                markdown="",
                status=ReportStatus.EM_ANALISE,
                event_uids=event_uids,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(placeholder)
            await session.commit()
            placeholder_id = placeholder.id

        try:
            problems = await run_rca_analysis(new_events)
        except Exception:
            logger.exception("RCA agent failed; marking report INCOMPLETO")
            async with self._sessionmaker() as session:
                await session.execute(
                    update(Report)
                    .where(Report.id == placeholder_id)
                    .values(
                        status=ReportStatus.INCOMPLETO,
                        updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            return

        async with self._sessionmaker() as session:
            # Delete the batch placeholder — it is superseded by the per-problem reports.
            placeholder_obj = await session.get(Report, placeholder_id)
            if placeholder_obj:
                await session.delete(placeholder_obj)

            for problem in problems:
                md = problem.get("markdown", "").strip()
                uids = problem.get("event_uids", [])
                status = ReportStatus.COMPLETO if md else ReportStatus.INCOMPLETO
                report = Report(
                    id=uuid.uuid4(),
                    markdown=md,
                    status=status,
                    event_uids=uids,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(report)

            await session.commit()

        logger.info(
            "RCA complete: %s problem report(s) persisted for %s event(s)",
            len(problems),
            len(new_events),
        )
