"""Scheduler process: `python -m app.scheduler`. Every job finds what is due and calls a service.

Jobs are idempotent and each runs in its own transaction (docs/03-architecture.md §3.7).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.platform import monitoring
from app.platform.clock import utcnow
from app.services import agreements, negotiation, notifications, orders
from app.workers.outbox import dispatch_pending

log = logging.getLogger("easo.scheduler")

Job = Callable[[AsyncSession, datetime], Awaitable[int]]

EVERY_MINUTE: dict[str, Job] = {
    "expire_negotiations": negotiation.expire_due,
    "auto_accept_deliveries": orders.auto_accept_due,
    "expire_agreements": agreements.expire_due,
}


async def run_job(name: str, job: Job, now: datetime | None = None) -> int:
    async with get_sessionmaker()() as session:
        try:
            done = await job(session, now or utcnow())
            await session.commit()
        except Exception as e:
            await session.rollback()
            log.exception("job %s failed", name)
            await monitoring.report_error(e, f"job {name}")
            return 0
    if done:
        log.info("%s: %d", name, done)
    return done


async def tick() -> None:
    for name, job in EVERY_MINUTE.items():
        await run_job(name, job)
    await monitoring.heartbeat()


async def outbox_tick() -> None:
    await run_job("dispatch_outbox", dispatch_pending)
    # Separate transaction: a slow e-mail or Telegram call never holds the outbox rows
    await run_job("deliver_notifications", notifications.deliver_pending)


async def main() -> None:
    scheduler = AsyncIOScheduler(timezone="UTC")
    # max_instances=1 + coalesce: a slow run never overlaps the next one
    scheduler.add_job(tick, "cron", second=5, max_instances=1, coalesce=True)
    scheduler.add_job(outbox_tick, "interval", seconds=2, max_instances=1, coalesce=True)
    scheduler.start()
    log.info("scheduler started: %s + dispatch_outbox", ", ".join(EVERY_MINUTE))
    await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # a line every 2 s otherwise
    asyncio.run(main())
