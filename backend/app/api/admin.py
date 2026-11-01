"""Operator tools for trying the product out: sign in as a demo user (docs/05-cabinets-and-auth.md)."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth.deps import CurrentUser, Session
from app.auth.tokens import issue_token
from app.models import Membership, User
from app.schemas import TokenOut
from app.services.verification import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])

DEMO_DOMAIN = "@easo.local"  # fictional people from app.seed; real people are never offered here


class DemoUserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    company: str | None
    roles: list[str]
    role: str | None


@router.get("/demo-users", response_model=list[DemoUserOut])
async def demo_users(user: CurrentUser, session: Session) -> list[DemoUserOut]:
    require_admin(user)
    rows = await session.scalars(
        select(User)
        .where(User.email.like(f"%{DEMO_DOMAIN}"))
        .options(selectinload(User.memberships).selectinload(Membership.organization))
        .order_by(User.email)
    )
    out = []
    for u in rows:
        m = u.memberships[0] if u.memberships else None
        org = m.organization if m else None
        out.append(
            DemoUserOut(
                id=u.id,
                name=u.name,
                email=u.email or "",
                company=org.display_name if org else None,
                roles=[
                    r for r, on in (("buyer", org and org.is_buyer), ("seller", org and org.is_seller)) if on
                ],
                role=m.role.value if m else ("operator" if u.is_platform_admin else None),
            )
        )
    return out


@router.post("/demo-users/{user_id}/token", response_model=TokenOut)
async def demo_user_token(user_id: uuid.UUID, user: CurrentUser, session: Session) -> TokenOut:
    require_admin(user)
    target = await session.get(User, user_id)
    if target is None or not (target.email or "").endswith(DEMO_DOMAIN):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return TokenOut(access_token=issue_token(target.id))
