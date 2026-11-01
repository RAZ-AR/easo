"""Sending Telegram messages through the Bot API (the bot process only receives /start)."""

import httpx

from app.config import get_settings


def enabled() -> bool:
    return bool(get_settings().telegram_bot_token)


async def send_message(chat_id: int, text: str, url: str | None = None) -> None:
    token = get_settings().telegram_bot_token
    body: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if url and url.startswith("https://"):
        body["reply_markup"] = {"inline_keyboard": [[{"text": "Easo ↗", "url": url}]]}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
        r.raise_for_status()
