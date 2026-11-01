"""Demo data with a few orders in different states, so every screen has something to show.

`python -m app.demo` (compose: `docker compose run --rm api python -m app.demo`)
Runs app.seed, then plays a handful of purchases through the real API, in process. Safe to run twice.
"""

import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth.tokens import issue_token
from app.db import get_sessionmaker
from app.main import create_app
from app.models import Order, Organization, User
from app.seed import seed

# Cafe Arax, Bar Nova, Ararat Bakery from app.seed: only their orders count as demo orders
DEMO_BUYER_TINS = ("00000011", "00000012", "00000021")


async def _token(email: str) -> dict[str, str]:
    async with get_sessionmaker()() as s:
        uid = await s.scalar(select(User.id).where(User.email == email))
    return {"Authorization": f"Bearer {issue_token(uid)}"}


async def play() -> None:
    async with get_sessionmaker()() as s:
        demo_buyers = select(Organization.id).where(Organization.tin.in_(DEMO_BUYER_TINS))
        if await s.scalar(select(func.count()).select_from(Order).where(Order.buyer_id.in_(demo_buyers))):
            # Partly created (a run failed midway)? Remove those orders by hand and run again.
            print("demo orders already present")
            return

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://demo") as c:
        h = {e: await _token(f"{e}@easo.local") for e in ("arax", "nova", "lori", "pack", "baker")}

        async def pid(who: str, name_en: str) -> str:
            page = (await c.get("/products", headers=h[who], params={"q": name_en, "limit": 100})).json()
            return next(p["id"] for p in page["items"] if p["name"]["en"] == name_en)

        async def put(who: str, name_en: str, qty, offer=None) -> None:
            r = await c.put(
                f"/cart/items/{await pid(who, name_en)}",
                headers=h[who],
                json={"qty": str(qty), "offer_price_amd": offer},
            )
            assert r.status_code == 200, r.text

        async def checkout(who: str) -> list[dict]:
            r = await c.post("/checkout", headers=h[who], json={})
            assert r.status_code == 201, r.text
            return r.json()["orders"]

        async def post(who: str, path: str, **body) -> dict:
            r = await c.post(path, headers=h[who], json=body)
            assert r.status_code == 200, r.text
            return r.json()

        # 1. Autopilot accepts the offer at once (3 300 is the seller's "accept from" price)
        await put("arax", "Lori cheese", 12, offer=3300)
        await checkout("arax")

        # 2. Autopilot answers with a counter-offer: the buyer's turn
        await put("arax", "Lori cheese", 15, offer=3100)
        await put("arax", "Sour cream 20%", 10)
        await checkout("arax")

        # 3. No rule for this product: waiting for the seller (Lori) to answer by hand
        await put("nova", "Chanakh cheese", 7, offer=2900)
        await checkout("nova")

        # 4. A plain order, confirmed by the seller, on its way
        await put("nova", "Paper cup 250 ml", 10)
        await put("nova", "Napkins 24x24", 2)
        [order] = await checkout("nova")
        await post("pack", f"/orders/{order['id']}/confirm")
        await post("pack", f"/orders/{order['id']}/ship")

        # 5. A finished order: delivered, accepted, paid
        await put("arax", "Matsun", 20)  # 18 000 ֏: above Lori's 15 000 minimum
        [order] = await checkout("arax")
        await post("lori", f"/orders/{order['id']}/confirm")
        await post("lori", f"/orders/{order['id']}/deliver")
        await post("arax", f"/orders/{order['id']}/accept")
        await post("lori", f"/orders/{order['id']}/mark_paid")

        # 6. The bakery buys as a company that also sells: a fresh order for Pack
        await put("baker", "Espresso beans", 5)
        await checkout("baker")
    print("demo orders created")


async def main() -> None:
    await seed()
    await play()


if __name__ == "__main__":
    asyncio.run(main())
