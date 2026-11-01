"""Build web/src/data/yerevan-streets.json: named streets of Yerevan with their districts, from OpenStreetMap.

Run rarely (streets do not change often):  python3 scripts/build_yerevan_streets.py
One Overpass query; data © OpenStreetMap contributors, ODbL — the app credits it under the address field.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

MIRRORS = (  # public Overpass servers; the first one is often busy
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
YEREVAN_AREA = 3600000000 + 364087  # relation "Երևան", admin_level 4
OUT = Path(__file__).resolve().parent.parent / "web" / "src" / "data" / "yerevan-streets.json"

# OSM district names -> the names Easo uses (web/src/lib/districts.ts)
DISTRICTS = {
    "kentron": "Kentron",
    "arabkir": "Arabkir",
    "ajapnyak": "Ajapnyak",
    "davtashen": "Davtashen",
    "nor nork": "Nor Nork",
    "malatia-sebastia": "Malatia-Sebastia",
    "shengavit": "Shengavit",
    "erebuni": "Erebuni",
    "kanaker-zeytun": "Kanaker-Zeytun",
    "avan": "Avan",
    "nubarashen": "Nubarashen",
    "nork-marash": "Nork-Marash",
    # spellings used in OSM
    "nor norq": "Nor Nork",
    "norq marash": "Nork-Marash",
    "norq-marash": "Nork-Marash",
    "qanaqer-zeytun": "Kanaker-Zeytun",
}

DISTRICTS_QUERY = f"""
[out:json][timeout:120];
area({YEREVAN_AREA})->.city;
rel(area.city)["boundary"="administrative"]["admin_level"="5"];
out geom;
"""

STREETS_QUERY = f"""
[out:json][timeout:180];
area({YEREVAN_AREA})->.city;
way(area.city)["highway"]["name"]
   ["highway"!~"^(footway|path|cycleway|steps|track|bus_stop|platform|construction|proposed)$"];
out tags center;
"""


def overpass(query: str) -> list[dict]:
    body = urllib.parse.urlencode({"data": query}).encode()
    headers = {"User-Agent": "Easo-streets-build/1.0 (+https://easo.ayoo.space)"}
    for url in MIRRORS:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=240) as r:
                return json.load(r)["elements"]
        except (OSError, ValueError) as e:
            print(f"{url}: {e}", file=sys.stderr)
    sys.exit("no Overpass server answered")


def rings(relation: dict) -> list[list[tuple[float, float]]]:
    """Outer member ways joined into closed rings of (lon, lat)."""
    parts = [
        [(p["lon"], p["lat"]) for p in m["geometry"]]
        for m in relation.get("members", [])
        if m.get("role") == "outer" and m.get("geometry")
    ]
    out = []
    while parts:
        ring = parts.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, p in enumerate(parts):
                if p[0] == ring[-1]:
                    ring += p[1:]
                elif p[-1] == ring[-1]:
                    ring += p[-2::-1]
                elif p[-1] == ring[0]:
                    ring = p[:-1] + ring
                elif p[0] == ring[0]:
                    ring = p[:0:-1] + ring
                else:
                    continue
                parts.pop(i)
                changed = True
                break
        out.append(ring)
    return out


def inside(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def district_name(tags: dict) -> str | None:
    key = re.sub(r"[‐–—]", "-", (tags.get("name:en") or "").lower()).replace(" district", "").strip()
    return DISTRICTS.get(key)


def main() -> None:
    districts = []
    unknown: set[str] = set()
    for rel in overpass(DISTRICTS_QUERY):
        name = district_name(rel.get("tags", {}))
        if name is None:
            unknown.add(rel.get("tags", {}).get("name:en") or rel.get("tags", {}).get("name", "?"))
            continue
        districts.append((name, rings(rel)))

    streets: dict[str, dict] = {}
    for e in overpass(STREETS_QUERY):
        tags = e.get("tags", {})
        hy = tags.get("name", "").strip()
        if not hy:
            continue
        s = streets.setdefault(hy, {"hy": hy, "ru": "", "en": "", "d": []})
        s["ru"] = s["ru"] or tags.get("name:ru", "").strip()
        s["en"] = s["en"] or tags.get("name:en", "").strip()
        c = e.get("center")
        if c:
            point = (c["lon"], c["lat"])
            for name, rs in districts:
                if name not in s["d"] and any(inside(point, r) for r in rs):
                    s["d"].append(name)

    rows = sorted(streets.values(), key=lambda s: s["hy"])
    for s in rows:  # drop empty translations to keep the file small
        for k in ("ru", "en"):
            if not s[k]:
                del s[k]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(rows)} streets -> {OUT} ({OUT.stat().st_size // 1024} KB)")
    if unknown:
        print("districts without a Easo name:", ", ".join(sorted(unknown)), file=sys.stderr)


if __name__ == "__main__":
    main()
