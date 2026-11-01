"""Is the service alive, and tell the operator when it is not.

- `readiness()`: database, Redis and a fresh scheduler heartbeat; /health/ready, for an uptime monitor.
- `heartbeat()`: the scheduler calls it every minute; it also pings a dead-man switch (healthchecks.io) when
  HEALTHCHECK_PING_URL is set, so a stopped server is noticed even though nothing on it can send mail.
- `report_error()`: an unexpected exception e-mails ALERT_EMAIL; the same error at most once per cooldown.
"""

import asyncio
import hashlib
import logging
import time
import traceback
from datetime import UTC, datetime

import httpx
from redis import asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text

from app.config import get_settings
from app.db import get_sessionmaker
from app.integrations.mailer import get_mailer

log = logging.getLogger("easo.monitoring")

HEARTBEAT_KEY = "easo:heartbeat:scheduler"
HEARTBEAT_MAX_AGE_S = 180  # the scheduler beats every minute; three missed beats = down

_client: aioredis.Redis | None = None
_sent_here: dict[str, float] = {}  # dedupe when Redis itself is the thing that broke
_tasks: set[asyncio.Task] = set()  # keep sends alive until they finish


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, socket_timeout=1, socket_connect_timeout=1)
    return _client


async def heartbeat() -> None:
    try:
        await _redis().set(HEARTBEAT_KEY, str(time.time()), ex=HEARTBEAT_MAX_AGE_S * 10)
    except (RedisError, OSError) as e:
        log.warning("heartbeat not stored: %s", e)
    url = get_settings().healthcheck_ping_url
    if url:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.get(url)
        except httpx.HTTPError as e:
            log.warning("healthcheck ping failed: %s", e)


async def readiness() -> dict[str, bool]:
    checks = {"db": False, "redis": False, "scheduler": False}
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception as e:  # any driver error means "not ready"
        log.warning("health: database: %s", e)
    try:
        r = _redis()
        await r.ping()
        checks["redis"] = True
        beat = await r.get(HEARTBEAT_KEY)
        checks["scheduler"] = beat is not None and time.time() - float(beat) < HEARTBEAT_MAX_AGE_S
    except (RedisError, OSError) as e:
        log.warning("health: redis: %s", e)
    return checks


def _fingerprint(exc: BaseException, where: str) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    last = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else ""
    return hashlib.sha1(f"{where}|{type(exc).__name__}|{last}".encode()).hexdigest()[:16]


async def _first_time(key: str, cooldown_s: int) -> bool:
    try:
        return bool(await _redis().set(f"easo:alert:{key}", "1", nx=True, ex=cooldown_s))
    except (RedisError, OSError):
        now = time.monotonic()
        if now - _sent_here.get(key, -cooldown_s - 1) < cooldown_s:
            return False
        _sent_here[key] = now
        return True


async def _send(where: str, exc: BaseException) -> None:
    s = get_settings()
    tb = "".join(traceback.format_exception(exc))[-6000:]
    subject = f"[Easo {s.env}] {type(exc).__name__} in {where}"[:200]
    body = (
        f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC\n{where}\n\n{type(exc).__name__}: {exc}\n\n{tb}\n"
        f"The same error is not reported again for {s.alert_cooldown_s // 60} minutes."
    )
    try:
        await get_mailer().send(s.alert_email, subject, body)
    except Exception as e:  # an alert must never break the caller
        log.warning("alert e-mail failed: %s", e)


async def report_error(exc: BaseException, where: str) -> None:
    """Mail the operator about an unexpected error; returns at once, the e-mail goes in the background."""
    s = get_settings()
    if not s.alert_email:
        return
    if not await _first_time(_fingerprint(exc, where), s.alert_cooldown_s):
        return
    task = asyncio.create_task(_send(where, exc))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
