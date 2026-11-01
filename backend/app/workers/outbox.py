"""Outbox dispatcher: hands committed events to their handlers (docs/03-architecture.md §3.6).

Order events become notifications (app/services/notifications.py); other topics are only logged.
A new side effect plugs in as a handler per topic without touching the services.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent
from app.services import notifications

log = logging.getLogger("easo.outbox")

Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]


async def _log_only(session: AsyncSession, event: OutboxEvent) -> None:
    log.info("event %s %s", event.topic, event.payload.get("number", ""))


HANDLERS: dict[str, Handler] = {topic: notifications.fan_out for topic in notifications.KINDS}
MAX_ATTEMPTS = 10
KEEP_DAYS = 7


async def dispatch_pending(session: AsyncSession, now: datetime, limit: int = 100) -> int:
    events = list(
        await session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.processed_at.is_(None), OutboxEvent.attempts < MAX_ATTEMPTS)
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for event in events:
        handler = HANDLERS.get(event.topic, _log_only)
        event.attempts += 1
        try:
            async with session.begin_nested():
                await handler(session, event)
            event.processed_at = now
            event.last_error = None
        except Exception as e:  # noqa: BLE001 — one bad event must not block the rest
            event.last_error = repr(e)[:2000]
            log.exception("outbox handler failed for %s", event.topic)
    await session.execute(
        delete(OutboxEvent).where(OutboxEvent.processed_at < now - timedelta(days=KEEP_DAYS))
    )
    return len(events)
