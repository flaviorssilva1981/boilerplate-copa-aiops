"""Best-effort Telegram notifications for new RCA reports and fix outcomes.

Configured via TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID; a no-op when unset so the
AIOps pipeline behaves identically for operators who haven't set up a bot.
"""

import asyncio
import logging
import os
import uuid

import httpx

from my_agent_app.models import Report, ReportStatus

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


def _report_url(report_id: uuid.UUID) -> str:
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    return f"{base}/reports/{report_id}" if base else f"/reports/{report_id}"


async def _send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
            return
        except Exception:
            if attempt < _MAX_ATTEMPTS:
                wait = 2**attempt
                logger.warning(
                    "Telegram send attempt %d/%d failed; retrying in %ds",
                    attempt,
                    _MAX_ATTEMPTS,
                    wait,
                    exc_info=True,
                )
                await asyncio.sleep(wait)
                continue
            logger.exception(
                "Failed to send Telegram notification after %d attempts", _MAX_ATTEMPTS
            )


async def notify_new_report(report: Report) -> None:
    title = report.title() or "Untitled problem"
    severity = report.severity()
    await _send(f"\U0001f6a8 New AIOps report [{severity}]: {title}\n{_report_url(report.id)}")


async def notify_fix_outcome(report_id: uuid.UUID, status: str) -> None:
    success = status == ReportStatus.CORRIGIDO
    icon = "✅" if success else "❌"
    label = "Fix succeeded" if success else "Fix failed"
    await _send(f"{icon} {label} for report\n{_report_url(report_id)}")
