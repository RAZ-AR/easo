"""Notifications: who hears about an order event, and through which channels (docs/05-cabinets-and-auth.md).

Every order event in the outbox becomes one in-app notification per member of the other side (the bell).
E-mail and Telegram copies are separate rows sent later by `deliver_pending`, so a slow or failing channel
never blocks the bell, and a retried outbox event never notifies twice (dedupe_key).
"""

import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations import telegram_sender
from app.integrations.mailer import get_mailer
from app.models import Channel, Membership, Notification, Order, Organization, OutboxEvent, User
from app.platform.errors import ValidationFailed

log = logging.getLogger("easo.notify")

# Outbox topic -> notification type. Anything else (placed, offer_made, ...) is either covered by another
# event or is internal, and stays silent.
KINDS = {
    "order.created": "order_created",
    "order.offer_countered": "offer_countered",
    "order.negotiation_agreed": "negotiation_agreed",
    "order.negotiation_declined": "negotiation_declined",
    "order.negotiation_expired": "negotiation_expired",
    "order.confirmed": "order_confirmed",
    "order.rejected": "order_rejected",
    "order.cancelled": "order_cancelled",
    "order.delivering": "order_delivering",
    "order.delivered": "order_delivered",
    "order.accepted": "order_accepted",
    "order.paid": "order_paid",
    "order.agreement_created": "agreement_created",
    "order.message": "message",
}

TEXT: dict[str, dict[str, str]] = {
    "order_created": {
        "hy": "Նոր պատվեր {number}՝ {counterparty}",
        "ru": "Новый заказ {number} от {counterparty}",
        "en": "New order {number} from {counterparty}",
    },
    "offer_countered": {
        "hy": "{counterparty}-ը առաջարկում է այլ գին՝ {number}",
        "ru": "{counterparty} предлагает другую цену по заказу {number}",
        "en": "{counterparty} proposes another price on {number}",
    },
    "negotiation_agreed": {
        "hy": "Գինը համաձայնեցված է՝ {number}",
        "ru": "Цена по заказу {number} согласована",
        "en": "Price agreed on {number}",
    },
    "negotiation_declined": {
        "hy": "Գնի առաջարկը մերժվեց՝ {number}",
        "ru": "Предложение цены по заказу {number} отклонено",
        "en": "Price offer on {number} declined",
    },
    "negotiation_expired": {
        "hy": "Սակարկության ժամկետը լրացավ՝ {number}",
        "ru": "Время на торг по заказу {number} истекло",
        "en": "Haggling time on {number} ran out",
    },
    "order_confirmed": {
        "hy": "{counterparty}-ը հաստատեց պատվերը {number}",
        "ru": "{counterparty} подтвердил заказ {number}",
        "en": "{counterparty} confirmed order {number}",
    },
    "order_rejected": {
        "hy": "{counterparty}-ը մերժեց պատվերը {number}",
        "ru": "{counterparty} отклонил заказ {number}",
        "en": "{counterparty} rejected order {number}",
    },
    "order_cancelled": {
        "hy": "Պատվերը {number} չեղարկվեց",
        "ru": "Заказ {number} отменён",
        "en": "Order {number} was cancelled",
    },
    "order_delivering": {
        "hy": "Պատվերը {number} ճանապարհին է",
        "ru": "Заказ {number} в пути",
        "en": "Order {number} is on its way",
    },
    "order_delivered": {
        "hy": "Պատվերը {number} առաքված է․ ստուգեք և ընդունեք",
        "ru": "Заказ {number} доставлен: проверьте и примите",
        "en": "Order {number} delivered: check and accept it",
    },
    "order_accepted": {
        "hy": "{counterparty}-ը ընդունեց պատվերը {number}",
        "ru": "{counterparty} принял заказ {number}",
        "en": "{counterparty} accepted order {number}",
    },
    "order_paid": {
        "hy": "Պատվերը {number} նշված է որպես վճարված",
        "ru": "Заказ {number} отмечен как оплаченный",
        "en": "Order {number} marked as paid",
    },
    "agreement_created": {
        "hy": "{counterparty}-ը ամրագրեց գինը ձեզ համար ({number})",
        "ru": "{counterparty} закрепил для вас цену (заказ {number})",
        "en": "{counterparty} fixed a price for you (order {number})",
    },
    "message": {
        "hy": "{counterparty}՝ {number}․ {text}",
        "ru": "{counterparty} · {number}: {text}",
        "en": "{counterparty} · {number}: {text}",
    },
}
SUBJECT = {"hy": "Easo՝ {number}", "ru": "Easo: {number}", "en": "Easo: {number}"}
DEMO_DOMAIN = "@easo.local"  # fictional users: no real mailbox
MAX_SEND_ATTEMPTS = 5


def render(kind: str, lang: str, payload: dict) -> str:
    template = TEXT.get(kind, {}).get(lang) or TEXT.get(kind, {}).get("ru") or kind
    return template.format(
        number=payload.get("number", ""),
        counterparty=payload.get("counterparty", ""),
        text=payload.get("text", ""),
    )


async def _members(session: AsyncSession, org_id: uuid.UUID) -> list[User]:
    rows = await session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.organization_id == org_id)
    )
    return list(rows)


async def _recipient_orgs(session: AsyncSession, payload: dict) -> list[uuid.UUID]:
    """The other side of whoever acted. A system event (expiry, auto-accept) concerns both sides."""
    buyer, seller = uuid.UUID(payload["buyer_id"]), uuid.UUID(payload["seller_id"])
    by = payload.get("by")
    if by in ("buyer", "seller"):
        return [seller] if by == "buyer" else [buyer]
    by_user = payload.get("by_user")
    if by_user:
        in_buyer = await session.scalar(
            select(Membership.id).where(
                Membership.user_id == uuid.UUID(by_user), Membership.organization_id == buyer
            )
        )
        return [seller] if in_buyer else [buyer]
    return [buyer, seller]


async def fan_out(session: AsyncSession, event: OutboxEvent) -> None:
    """Outbox handler: turn one order event into notifications for the people on the other side."""
    kind = KINDS.get(event.topic)
    if kind is None:
        return
    p = event.payload
    order_id = uuid.UUID(p["order_id"])
    names = dict(
        (
            await session.execute(
                select(Organization.id, Organization.display_name).where(
                    Organization.id.in_([uuid.UUID(p["buyer_id"]), uuid.UUID(p["seller_id"])])
                )
            )
        ).all()
    )
    tg = telegram_sender.enabled()
    for org_id in await _recipient_orgs(session, p):
        other = uuid.UUID(p["seller_id"]) if str(org_id) == p["buyer_id"] else uuid.UUID(p["buyer_id"])
        payload = {
            "number": p.get("number"),
            "counterparty": names.get(other, ""),
            "text": (p.get("text") or "")[:300],
        }
        for user in await _members(session, org_id):
            if str(user.id) == p.get("by_user"):
                continue
            channels = [Channel.app]
            if user.notify_email and user.email and not user.email.endswith(DEMO_DOMAIN):
                channels.append(Channel.email)
            if tg and user.notify_telegram and user.telegram_id:
                channels.append(Channel.telegram)
            for channel in channels:
                key = f"{event.id}:{user.id}:{channel}"
                if await session.scalar(select(Notification.id).where(Notification.dedupe_key == key)):
                    continue
                session.add(
                    Notification(
                        user_id=user.id,
                        channel=channel,
                        type=kind,
                        order_id=order_id,
                        payload=payload,
                        dedupe_key=key,
                        # The bell needs no sending
                        sent_at=event.created_at if channel == Channel.app else None,
                    )
                )


async def deliver_pending(session: AsyncSession, now: datetime, limit: int = 50) -> int:
    """Send e-mail and Telegram copies. Called by the scheduler; failures are retried a few times."""
    rows = list(
        await session.scalars(
            select(Notification)
            .where(
                Notification.channel.in_([Channel.email, Channel.telegram]),
                Notification.sent_at.is_(None),
                Notification.attempts < MAX_SEND_ATTEMPTS,
            )
            .order_by(Notification.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    base = get_settings().web_app_url.rstrip("/")
    for n in rows:
        user = await session.get(User, n.user_id)
        n.attempts += 1
        if user is None:
            n.sent_at = now
            continue
        lang = str(user.locale)
        text = render(n.type, lang, n.payload or {})
        url = f"{base}/orders/{n.order_id}" if n.order_id else base
        try:
            if n.channel == Channel.email and user.email and user.notify_email:
                subject = SUBJECT.get(lang, SUBJECT["ru"]).format(number=(n.payload or {}).get("number", ""))
                await get_mailer().send(user.email, subject, f"{text}\n\n{url}")
            elif n.channel == Channel.telegram and user.telegram_id and user.notify_telegram:
                await telegram_sender.send_message(user.telegram_id, text, url)
            n.sent_at = now
        except Exception as e:  # noqa: BLE001 — retried on the next tick
            log.warning("notification %s via %s failed: %r", n.id, n.channel, e)
    return len(rows)


# --- the bell ----------------------------------------------------------------------------------------------


async def list_for_user(session: AsyncSession, user: User, limit: int = 50) -> tuple[list[Notification], int]:
    mine = (Notification.user_id == user.id, Notification.channel == Channel.app)
    rows = await session.scalars(
        select(Notification).where(*mine).order_by(Notification.created_at.desc()).limit(limit)
    )
    unread = await session.scalar(select(func.count()).where(*mine, Notification.read_at.is_(None)))
    return list(rows), unread or 0


async def mark_read(
    session: AsyncSession,
    user: User,
    now: datetime,
    ids: list[uuid.UUID] | None = None,
    order_id: uuid.UUID | None = None,
    kind: str | None = None,
) -> None:
    stmt = update(Notification).where(
        Notification.user_id == user.id, Notification.channel == Channel.app, Notification.read_at.is_(None)
    )
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    if order_id is not None:
        stmt = stmt.where(Notification.order_id == order_id)
    if kind is not None:
        stmt = stmt.where(Notification.type == kind)
    await session.execute(stmt.values(read_at=now))


async def unread_messages(session: AsyncSession, user: User, order: Order) -> int:
    return (
        await session.scalar(
            select(func.count()).where(
                Notification.user_id == user.id,
                Notification.channel == Channel.app,
                Notification.order_id == order.id,
                Notification.type == "message",
                Notification.read_at.is_(None),
            )
        )
        or 0
    )


# --- Telegram linking: the web app gives a t.me link, the bot receives /start <token> ----------------------

LINK_TTL_S = 15 * 60


def _sign(data: str) -> str:
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"tg-link:{data}".encode(), hashlib.sha256).hexdigest()[:16]


def telegram_link_token(user_id: uuid.UUID, now_s: int | None = None) -> str:
    """Fits Telegram's 64-character start parameter: user id, issue time (hex), signature."""
    issued = format(now_s or int(time.time()), "x")
    data = f"{user_id.hex}{issued}"
    return f"{data}{_sign(data)}"


def read_telegram_link_token(token: str, now_s: int | None = None) -> uuid.UUID:
    data, sig = token[:-16], token[-16:]
    if len(data) < 33 or not hmac.compare_digest(sig, _sign(data)):
        raise ValidationFailed([{"field": "token", "code": "bad_link"}])
    user_id, issued = uuid.UUID(hex=data[:32]), int(data[32:], 16)
    if (now_s or int(time.time())) - issued > LINK_TTL_S:
        raise ValidationFailed([{"field": "token", "code": "link_expired"}])
    return user_id


async def link_telegram(session: AsyncSession, token: str, telegram_id: int) -> User:
    user_id = read_telegram_link_token(token)
    user = await session.get(User, user_id)
    if user is None:
        raise ValidationFailed([{"field": "token", "code": "bad_link"}])
    # One Telegram account belongs to one Easo user: move it if it was linked elsewhere
    await session.execute(
        update(User).where(User.telegram_id == telegram_id, User.id != user.id).values(telegram_id=None)
    )
    await session.flush()
    user.telegram_id = telegram_id
    user.notify_telegram = True
    return user
