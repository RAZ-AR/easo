"""Outgoing e-mail behind a port: "console" logs messages (development), "resend" sends through resend.com."""

import logging
from typing import Protocol

import httpx

from app.config import get_settings

log = logging.getLogger("easo.mail")


class Mailer(Protocol):
    name: str

    async def send(self, to: str, subject: str, text: str) -> None: ...


class ConsoleMailer:
    name = "console"

    async def send(self, to: str, subject: str, text: str) -> None:
        log.warning("MAIL to=%s subject=%s\n%s", to, subject, text)


class ResendMailer:
    name = "resend"

    def __init__(self, api_key: str, sender: str) -> None:
        self.api_key, self.sender = api_key, sender

    async def send(self, to: str, subject: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"from": self.sender, "to": [to], "subject": subject, "text": text},
            )
            r.raise_for_status()


def get_mailer() -> Mailer:
    s = get_settings()
    if s.mail_provider == "resend" and s.resend_api_key:
        return ResendMailer(s.resend_api_key, s.mail_from)
    return ConsoleMailer()
