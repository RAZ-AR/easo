"""A typed address → the house on the map and its Yerevan district, via OpenStreetMap Nominatim.

Nominatim's usage policy allows looking up a finished address, not suggestions as you type, at most one
request a second, with an identifying User-Agent. So the street suggestions come from our own list
(web/src/data/yerevan-streets.json), and only the final address is resolved here: through our server,
one request a second at most for the whole app (a Redis lock), answers cached for a month.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx
from redis import asyncio as aioredis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

USER_AGENT = "Easo/1.0 (+https://easo.ayoo.space; address check at sign-up)"
YEREVAN_VIEWBOX = "44.36,40.25,44.65,40.06"  # lon1,lat1,lon2,lat2
CACHE_S = 30 * 24 * 3600

# Nominatim district names in the asked language -> the names Easo uses (web/src/lib/districts.ts)
_DISTRICT_NAMES = {
    "Kentron": ("Կենտրոն", "Кентрон", "Kentron"),
    "Arabkir": ("Արաբկիր", "Арабкир", "Arabkir"),
    "Ajapnyak": ("Աջափնյակ", "Аджапняк", "Ajapnyak"),
    "Davtashen": ("Դավթաշեն", "Давташен", "Davtashen"),
    "Nor Nork": ("Նոր Նորք", "Нор-Норк", "Нор Норк", "Nor Nork", "Nor Norq"),
    "Malatia-Sebastia": ("Մալաթիա-Սեբաստիա", "Малатия-Себастия", "Malatia-Sebastia"),
    "Shengavit": ("Շենգավիթ", "Шенгавит", "Shengavit"),
    "Erebuni": ("Էրեբունի", "Эребуни", "Erebuni"),
    "Kanaker-Zeytun": ("Քանաքեռ-Զեյթուն", "Канакер-Зейтун", "Kanaker-Zeytun", "Qanaqer-Zeytun"),
    "Avan": ("Ավան", "Аван", "Avan"),
    "Nubarashen": ("Նուբարաշեն", "Нубарашен", "Nubarashen"),
    "Nork-Marash": ("Նորք-Մարաշ", "Норк-Мараш", "Nork-Marash", "Norq Marash"),
}
DISTRICTS = {
    re.sub(r"[\s-]+", " ", name).lower(): district
    for district, names in _DISTRICT_NAMES.items()
    for name in names
}


def district_of(raw: str) -> str | None:
    """'Арабкир', 'Arabkir district', 'Արաբկիր վարչական շրջան' -> 'Arabkir'."""
    key = re.sub(r"[\s-]+", " ", raw).lower()
    for noise in (" district", " район", " վարչական շրջան", "район "):
        key = key.replace(noise, "")
    return DISTRICTS.get(key.strip())


class GeocoderUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Place:
    text: str  # "Աբովյան փողոց 10, Երևան" in the asked language
    street: str | None
    house: str | None
    district: str | None  # Easo district name, None outside Yerevan or unknown
    city: str | None
    lat: float
    lng: float


def to_place(item: dict) -> Place:
    a = item.get("address", {})
    street = a.get("road") or a.get("pedestrian") or a.get("square")
    house = a.get("house_number")
    city = a.get("city") or a.get("town") or a.get("village")
    raw_district = a.get("suburb") or a.get("city_district") or a.get("district") or ""
    district = district_of(raw_district)
    head = " ".join(x for x in (street, house) if x) or item.get("name") or ""
    text = ", ".join(x for x in (head, city) if x) or item.get("display_name", "")
    return Place(text, street, house, district, city, float(item["lat"]), float(item["lon"]))


def _key(query: str, lang: str) -> str:  # v2: raw Nominatim items
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    return f"geo2:{lang}:{normalized}"


class NominatimGeocoder:
    def __init__(self, base_url: str, redis: aioredis.Redis, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.redis = redis
        self._client = client

    async def _wait_turn(self) -> None:
        """One request a second for the whole app; give up after ~4 s of queueing."""
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                if await self.redis.set("geo:lock", "1", nx=True, px=1100):
                    return
            except (RedisError, OSError):
                return  # no Redis: rely on the per-user rate limit in the API
            await asyncio.sleep(0.25)
        raise GeocoderUnavailable("geocoder busy")

    async def resolve(self, query: str, lang: str = "hy") -> Place | None:
        # The raw answer is cached, so better parsing (districts) applies to cached addresses too
        key = _key(query, lang)
        try:
            cached = await self.redis.get(key)
        except (RedisError, OSError):
            cached = None
        if cached is not None:
            item = json.loads(cached)
            return to_place(item) if item else None

        await self._wait_turn()
        q = query if re.search(r"(Ереван|Yerevan|Երևան)", query, re.I) else f"{query}, Երևան"
        params = {
            "q": q,
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": "1",
            "countrycodes": "am",
            "viewbox": YEREVAN_VIEWBOX,
            "accept-language": lang,
        }
        client = self._client or httpx.AsyncClient(timeout=6, headers={"User-Agent": USER_AGENT})
        try:
            r = await client.get(f"{self.base_url}/search", params=params)
            if r.status_code != 200:
                raise GeocoderUnavailable(f"nominatim HTTP {r.status_code}")
            items = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise GeocoderUnavailable(f"nominatim: {e.__class__.__name__}") from e
        finally:
            if self._client is None:
                await client.aclose()

        item = items[0] if items else None
        try:
            await self.redis.set(key, json.dumps(item, ensure_ascii=False), ex=CACHE_S)
        except (RedisError, OSError) as e:
            log.warning("geocoder cache unavailable: %s", e)
        return to_place(item) if item else None
