import asyncio
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import create_app
from app.platform import monitoring


class FakeMailer:
    name = "fake"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, text: str) -> None:
        self.sent.append((to, subject, text))


@pytest.fixture
def mailer(monkeypatch) -> FakeMailer:
    m = FakeMailer()
    monkeypatch.setattr(monitoring, "get_mailer", lambda: m)
    monkeypatch.setattr(get_settings(), "alert_email", "ops@example.com")
    # Each test has its own event loop: never reuse a Redis client from another one
    monkeypatch.setattr(monitoring, "_client", None)
    return m


async def _settle() -> None:
    await asyncio.gather(*monitoring._tasks)


def _boom(msg: str = "boom") -> ValueError:
    try:
        raise ValueError(msg)
    except ValueError as e:
        return e


async def test_same_error_is_mailed_once_per_cooldown(mailer):
    where = f"job test-{uuid.uuid4()}"
    for _ in range(3):
        await monitoring.report_error(_boom(), where)
    await monitoring.report_error(_boom(), f"{where}-other")
    await _settle()
    assert [to for to, *_ in mailer.sent] == ["ops@example.com", "ops@example.com"]
    to, subject, text = mailer.sent[0]
    assert subject.startswith("[Easo test] ValueError in job test-")
    assert "boom" in text and "Traceback" in text


async def test_alerts_off_without_an_address(mailer, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_email", "")
    await monitoring.report_error(_boom(), f"job {uuid.uuid4()}")
    await _settle()
    assert mailer.sent == []


async def test_unexpected_api_error_is_reported_and_hidden(mailer):
    app = create_app()
    # A path of its own: the alert cooldown lives in the real Redis and would mute a rerun
    path = f"/explode-{uuid.uuid4().hex[:8]}"

    @app.get(path)
    async def explode():
        raise RuntimeError(f"secret detail {uuid.uuid4()}")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get(path)
    await _settle()
    assert r.status_code == 500
    assert r.json()["code"] == "internal_error" and "secret" not in r.text
    assert len(mailer.sent) == 1 and f"GET {path}" in mailer.sent[0][1]


async def test_ready_needs_a_fresh_scheduler_heartbeat(client, sessionmaker, monkeypatch):
    monkeypatch.setattr(monitoring, "_client", None)
    monkeypatch.setattr(monitoring, "get_sessionmaker", lambda: sessionmaker)
    r = monitoring._redis()
    await r.set(monitoring.HEARTBEAT_KEY, str(time.time() - monitoring.HEARTBEAT_MAX_AGE_S - 5))
    res = await client.get("/health/ready")
    assert res.status_code == 503
    assert res.json()["checks"] == {"db": True, "redis": True, "scheduler": False}

    await monitoring.heartbeat()
    res = await client.get("/health/ready")
    assert (res.status_code, res.json()["status"]) == (200, "ok")
