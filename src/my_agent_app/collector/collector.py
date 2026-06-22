import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from my_agent_app.collector.event_handler import EventHandler

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MINUTES = 3


def get_collection_interval_minutes() -> int:
    raw = os.environ.get("EVENT_COLLECTION_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES))
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid EVENT_COLLECTION_INTERVAL_MINUTES=%r; using default %s",
            raw,
            DEFAULT_INTERVAL_MINUTES,
        )
        return DEFAULT_INTERVAL_MINUTES

    if value <= 0:
        logger.warning(
            "Invalid EVENT_COLLECTION_INTERVAL_MINUTES=%s; using default %s",
            value,
            DEFAULT_INTERVAL_MINUTES,
        )
        return DEFAULT_INTERVAL_MINUTES

    return value


def _load_kubernetes_config() -> None:
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes configuration")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Loaded local kubeconfig")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _event_recency(event: dict) -> datetime | None:
    series = event.get("series") or {}
    if series.get("lastObservedTime"):
        return _parse_timestamp(series["lastObservedTime"])

    if event.get("eventTime"):
        return _parse_timestamp(event["eventTime"])

    deprecated = event.get("deprecatedLastTimestamp")
    if deprecated:
        return _parse_timestamp(deprecated)

    return None


def _event_to_dict(event: dict) -> dict | None:
    recency = _event_recency(event)
    if recency is None:
        metadata = event.get("metadata") or {}
        logger.warning(
            "Skipping event %s/%s without recency timestamp",
            metadata.get("namespace"),
            metadata.get("name"),
        )
        return None

    regarding = event.get("regarding") or {}
    metadata = event.get("metadata") or {}

    return {
        "uid": metadata.get("uid", ""),
        "type": event.get("type", ""),
        "reason": event.get("reason") or "",
        "message": event.get("note") or "",
        "namespace": metadata.get("namespace") or "",
        "involved_object": {
            "kind": regarding.get("kind") or "",
            "name": regarding.get("name") or "",
            "namespace": regarding.get("namespace") or "",
        },
        "timestamp": recency.isoformat(),
    }


def _is_trivy_scan_noise(event: dict) -> bool:
    """Skip Trivy operator scan-job failures — not actionable cluster incidents."""
    if event.get("reason") != "BackoffLimitExceeded":
        return False
    involved = event.get("involved_object") or {}
    if involved.get("kind") != "Job":
        return False
    return (involved.get("name") or "").startswith("scan-vulnerabilityreport-")


def _list_warning_events_since(watermark: datetime) -> list[dict]:
    api = client.EventsV1Api()

    try:
        response = api.list_event_for_all_namespaces(
            watch=False,
            _preload_content=False,
        )
        payload = json.loads(response.data)
    except ApiException as exc:
        if exc.status == 403:
            logger.error(
                "RBAC denied listing cluster events. Grant get/list/watch on "
                "events.events.k8s.io and events (core)."
            )
        else:
            logger.exception("Failed to list Kubernetes events: %s", exc.reason)
        raise

    collected: list[dict] = []
    for event in payload.get("items", []):
        if event.get("type") != "Warning":
            continue

        recency = _event_recency(event)
        if recency is None or recency <= watermark:
            continue

        payload_item = _event_to_dict(event)
        if payload_item is not None:
            if _is_trivy_scan_noise(payload_item):
                continue
            collected.append(payload_item)

    return collected


class EventCollector:
    def __init__(self, handler: EventHandler | None = None) -> None:
        self._handler = handler or EventHandler()
        self._interval_minutes = get_collection_interval_minutes()
        self._watermark: datetime | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return

        await asyncio.to_thread(_load_kubernetes_config)
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="event-collector")
        logger.info(
            "Event collector started (interval=%s minute(s))",
            self._interval_minutes,
        )

    async def stop(self) -> None:
        if self._task is None:
            return

        self._stop_event.set()
        await self._task
        self._task = None
        logger.info("Event collector stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            cycle_started = datetime.now(UTC)
            watermark = self._watermark
            if watermark is None:
                watermark = cycle_started - timedelta(minutes=self._interval_minutes)

            try:
                events = await asyncio.to_thread(_list_warning_events_since, watermark)
                self._watermark = cycle_started

                if events:
                    asyncio.create_task(
                        self._dispatch(events),
                        name="event-handler-dispatch",
                    )
            except Exception:
                logger.exception("Event collection cycle failed")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_minutes * 60,
                )
            except TimeoutError:
                continue

    async def _dispatch(self, events: list[dict]) -> None:
        try:
            await self._handler.handle(events)
        except Exception:
            logger.exception("Event handler failed for batch of %s event(s)", len(events))
