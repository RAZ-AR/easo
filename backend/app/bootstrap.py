"""First-run data for a real deployment (no demo companies): `python -m app.bootstrap you@example.com`.

Creates the product categories if there are none and makes the given e-mail an operator (platform admin).
That person then signs in the normal way (e-mail code, Google, Telegram) and sees the moderation queue.
Safe to run twice.
"""

import asyncio
import sys

from sqlalchemy import func, select

from app.auth.service import normalize_email
from app.db import get_sessionmaker
from app.models import Category, Locale, User
from app.seed import CATEGORIES


async def bootstrap(admin_email: str) -> None:
    email = normalize_email(admin_email)
    async with get_sessionmaker()() as s:
        if not await s.scalar(select(func.count()).select_from(Category)):
            s.add_all(
                Category(slug=slug, name=name, sort=i, default_commission_bp=bp)
                for i, (slug, name, bp) in enumerate(CATEGORIES)
            )
            print(f"created {len(CATEGORIES)} categories")
        user = await s.scalar(select(User).where(User.email == email))
        if user is None:
            s.add(User(name="Easo Operator", email=email, locale=Locale.ru, is_platform_admin=True))
            print(f"created operator {email}")
        elif not user.is_platform_admin:
            user.is_platform_admin = True
            print(f"{email} is now an operator")
        else:
            print(f"{email} is already an operator")
        await s.commit()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.bootstrap <operator-email>")
    asyncio.run(bootstrap(sys.argv[1]))
