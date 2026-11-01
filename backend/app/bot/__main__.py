"""Telegram bot: `python -m app.bot`. /start opens the web app; /start <token> links the chat to a Easo user
so that notifications arrive here (the token comes from "Link Telegram" in the web app).

Notifications themselves are sent by the scheduler through the Bot API (app/integrations/telegram_sender.py).
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.config import get_settings
from app.db import get_sessionmaker
from app.platform.errors import ValidationFailed
from app.services import notifications

log = logging.getLogger("easo.bot")
dp = Dispatcher()

GREETING = {
    "hy": "Բարի գալուստ Easo՝ ապրանքների պատվեր HoReCa-ի համար։",
    "ru": "Добро пожаловать в Easo — закупки для HoReCa.",
    "en": "Welcome to Easo — purchasing for HoReCa.",
}


LINKED = {
    "hy": "Պատրաստ է․ Easo-ի ծանուցումները կգան այստեղ։",
    "ru": "Готово: уведомления Easo будут приходить сюда.",
    "en": "Done: Easo notifications will arrive here.",
}
LINK_FAILED = {
    "hy": "Հղումը հնացել է։ Բացեք Easo-ն և նորից սեղմեք «Կապել Telegram-ը»։",
    "ru": "Ссылка устарела. Откройте Easo и снова нажмите «Привязать Telegram».",
    "en": "The link has expired. Open Easo and press “Link Telegram” again.",
}


@dp.message(CommandStart(deep_link=True))
async def start_with_token(message: Message, command: CommandObject) -> None:
    lang = (message.from_user.language_code or "hy")[:2] if message.from_user else "hy"
    if not message.from_user or not command.args:
        return await start(message)
    async with get_sessionmaker()() as session:
        try:
            await notifications.link_telegram(session, command.args, message.from_user.id)
            await session.commit()
        except ValidationFailed:
            await message.answer(LINK_FAILED.get(lang, LINK_FAILED["ru"]))
            return None
    await message.answer(LINKED.get(lang, LINKED["ru"]))
    return None


@dp.message(CommandStart())
async def start(message: Message) -> None:
    lang = (message.from_user.language_code or "hy")[:2] if message.from_user else "hy"
    url = get_settings().web_app_url
    button = (
        InlineKeyboardButton(text="Easo", web_app=WebAppInfo(url=url))
        if url.startswith("https://")
        else InlineKeyboardButton(text="Easo", url=url)
    )
    await message.answer(
        GREETING.get(lang, GREETING["hy"]), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]])
    )


async def main() -> None:
    token = get_settings().telegram_bot_token
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN is empty: bot is idle")
        await asyncio.Event().wait()
    await dp.start_polling(Bot(token))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
