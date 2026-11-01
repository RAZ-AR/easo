"""Sign-in: e-mail codes, Google and Telegram identities -> one Easo user.

A Google account whose verified e-mail matches an existing user signs into that user; Telegram has no e-mail,
so it creates its own user unless linked from the profile (not built yet).
"""

import dataclasses
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity import ExternalIdentity
from app.config import get_settings
from app.integrations.mailer import get_mailer
from app.models import AuthProvider, Locale, LoginCode, User, UserIdentity
from app.platform.errors import TooManyRequests, ValidationFailed

MAX_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 5

SUBJECT = {"hy": "Easo մուտքի կոդ", "ru": "Код входа в Easo", "en": "Your Easo sign-in code"}
BODY = {
    "hy": "Ձեր Easo մուտքի կոդը՝ {code}\nԳործում է {ttl} րոպե։ Եթե դուք չեք խնդրել, անտեսեք այս նամակը։",
    "ru": (
        "Ваш код входа в Easo: {code}\n"
        "Действует {ttl} минут. Если вы не запрашивали код, просто проигнорируйте письмо."
    ),
    "en": "Your Easo sign-in code: {code}\nValid for {ttl} minutes. If you did not ask for it, ignore it.",
}


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 320:
        raise ValidationFailed([{"field": "email", "code": "bad_email"}])
    return email


def _hash(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}:{get_settings().jwt_secret}".encode()).hexdigest()


async def start_email_login(session: AsyncSession, raw_email: str, lang: str, now: datetime) -> str:
    """Create and send a 6-digit code. Returns the code (the API reveals it only in DEV_LOGIN mode)."""
    email = normalize_email(raw_email)
    recent = await session.scalar(
        select(func.count()).where(LoginCode.email == email, LoginCode.created_at > now - timedelta(hours=1))
    )
    if (recent or 0) >= MAX_CODES_PER_HOUR:
        raise TooManyRequests("too_many_codes", "try again in an hour")
    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = get_settings().login_code_ttl_minutes
    session.add(LoginCode(email=email, code_hash=_hash(email, code), expires_at=now + timedelta(minutes=ttl)))
    lang = lang if lang in SUBJECT else "hy"
    await get_mailer().send(email, SUBJECT[lang], BODY[lang].format(code=code, ttl=ttl))
    return code


async def finish_email_login(session: AsyncSession, raw_email: str, code: str, now: datetime) -> User:
    email = normalize_email(raw_email)
    row = await session.scalar(
        select(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None), LoginCode.expires_at > now)
        .order_by(LoginCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if row is None:
        raise ValidationFailed([{"field": "code", "code": "code_expired"}])
    row.attempts += 1
    if row.attempts > MAX_ATTEMPTS:
        row.consumed_at = now
        raise ValidationFailed([{"field": "code", "code": "code_expired"}])
    if not secrets.compare_digest(row.code_hash, _hash(email, code.strip())):
        # The caller commits even on failure, so the attempt still counts
        raise ValidationFailed([{"field": "code", "code": "code_wrong"}])
    row.consumed_at = now
    return await login_or_register(
        session, ExternalIdentity(provider=AuthProvider.email, external_id=email, email=email)
    )


async def login_or_register(session: AsyncSession, ident: ExternalIdentity) -> User:
    """Find the user by this identity; else by the identity's verified e-mail; else create a new user."""
    if ident.email:
        # E-mail-code users are stored lower-case; a provider may keep the original case
        ident = dataclasses.replace(ident, email=ident.email.strip().lower())
    row = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == ident.provider, UserIdentity.external_id == ident.external_id
        )
    )
    if row is not None:
        row.username = ident.username or row.username
        row.email = ident.email or row.email
        user = await session.get(User, row.user_id)
        assert user is not None
        return user

    user = await session.scalar(select(User).where(User.email == ident.email)) if ident.email else None
    if user is None and ident.provider == AuthProvider.telegram:
        # Linked Telegram for notifications through the bot earlier: the same person, not a new account
        user = await session.scalar(select(User).where(User.telegram_id == int(ident.external_id)))
    if user is None:
        name = (
            ident.display_name
            or (ident.email.split("@")[0] if ident.email else "")
            or ident.username
            or "Easo"
        )
        user = User(
            name=name,
            email=ident.email,
            telegram_id=int(ident.external_id) if ident.provider == AuthProvider.telegram else None,
            locale=Locale(ident.locale) if ident.locale in ("hy", "ru", "en") else Locale.hy,
        )
        session.add(user)
        await session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=ident.provider,
            external_id=ident.external_id,
            email=ident.email,
            username=ident.username,
        )
    )
    return user
