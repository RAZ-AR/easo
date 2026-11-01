"""Company data by TIN from the public search of the Armenian state register (e-register.moj.am).

The register has no API, but its company search and free company card are public web pages (robots.txt
allows everything). We read them like a person would: search by TIN, open the card, take the free fields.
Directors and the tax regime are not on the free card, so the user still types them.

Parsing is pure (tests run on saved pages in tests/fixtures/eregister); network errors and pages we cannot
read raise RegistryUnavailable, and sign-up falls back to manual entry.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

from app.integrations.registry import CompanyInfo, RegistryUnavailable

USER_AGENT = "Easo-registry/1.0 (+https://easo.ayoo.space; company lookup at sign-up)"

# Labels of the free card, in Armenian (the page we read) and English (fallback if the language changes)
_LABELS = {
    "registration_number": ("Գրանցման համար", "Registration number"),
    "tin": ("ՀՎՀՀ", "Tax id"),
    "address": ("Գտնվելու վայր", "Address"),
    "status": ("Կարգավիճակ", "Company Status"),
}
# "Nothing about liquidation or termination is recorded" — the only status of a working company
_ACTIVE_STATUS = ("գրառված չեն", "no information recorded")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class CompanyPage:
    name: str | None = None
    fields: dict[str, str] = field(default_factory=dict)  # card label -> value


class _CardParser(HTMLParser):
    """Collects the company name and <dt>/<dd> pairs inside the company sections only (not the menu)."""

    def __init__(self) -> None:
        super().__init__()
        self.page = CompanyPage()
        self._section_depth = 0  # >0 while inside a "Company ..." section
        self._in: str | None = None  # "h4" | "dt" | "dd"
        self._buf: list[str] = []
        self._label: str | None = None
        self._skip = 0  # inside <details>: declaration links are not card values

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "section":
            if self._section_depth or (a.get("aria-label") or "").startswith("Company "):
                self._section_depth += 1
            return
        if not self._section_depth:
            return
        if tag == "details":
            self._skip += 1
        elif tag in ("h4", "dt", "dd") and not self._skip:
            self._in, self._buf = tag, []

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self._section_depth:
            self._section_depth -= 1
            return
        if tag == "details" and self._skip:
            self._skip -= 1
            return
        if tag != self._in:
            return
        text = _clean("".join(self._buf))
        if tag == "h4" and self.page.name is None and text:
            self.page.name = text
        elif tag == "dt":
            self._label = text
        elif tag == "dd" and self._label:
            self.page.fields[self._label] = text
            self._label = None
        self._in = None

    def handle_data(self, data: str) -> None:
        if self._in:
            self._buf.append(data)


def parse_company(html: str) -> CompanyPage:
    p = _CardParser()
    p.feed(html)
    return p.page


def parse_search(html: str) -> list[str]:
    """Company card paths from a search results page, e.g. ['/hy/companies/37191802']."""
    return list(dict.fromkeys(re.findall(r'href="(/[a-z]{2}/companies/\d+)"', html)))


def _field(page: CompanyPage, key: str) -> str | None:
    for label in _LABELS[key]:
        if page.fields.get(label):
            return page.fields[label]
    return None


def to_company(tin: str, hy: CompanyPage, en: CompanyPage | None) -> CompanyInfo | None:
    """The card as CompanyInfo; None if it is another company's card. Raises if the card is unreadable."""
    if not hy.name or _field(hy, "tin") is None:
        raise RegistryUnavailable("eregister: company card has an unknown layout")
    if re.sub(r"\D", "", _field(hy, "tin") or "") != tin:
        return None
    name = hy.name
    if en and en.name and en.name != hy.name:
        name = f"{hy.name} / {en.name}"
    status = (_field(hy, "status") or "").lower()
    address = _field(hy, "address")
    return CompanyInfo(
        tin=tin,
        legal_name=name,
        registration_number=_field(hy, "registration_number"),
        legal_address=address.removesuffix(", ՀՀ") if address else None,
        director_name=None,  # not on the free card
        tax_regime=None,  # not in the register at all (it is the tax service's)
        active=any(s in status for s in _ACTIVE_STATUS),
        source="eregister",
    )


class EregisterRegistry:
    name = "eregister"

    def __init__(self, base_url: str, timeout_s: float = 8.0, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = client  # tests pass one with a mock transport

    async def _get(self, client: httpx.AsyncClient, path: str, **params: str) -> str:
        try:
            r = await client.get(self.base_url + path, params=params or None)
        except httpx.HTTPError as e:
            raise RegistryUnavailable(f"eregister: {e.__class__.__name__}") from e
        if r.status_code != 200:
            raise RegistryUnavailable(f"eregister: HTTP {r.status_code} for {path}")
        return r.text

    async def lookup(self, tin: str) -> CompanyInfo | None:
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout_s, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        try:
            page = await self._get(client, "/hy/search/companies", query=tin)
            if 'aria-label="Company search results"' not in page:
                raise RegistryUnavailable("eregister: search page has an unknown layout")
            # A TIN search returns one company; check a few in case the search also matched other text
            for path in parse_search(page)[:3]:
                hy = parse_company(await self._get(client, path))
                if not hy.name or _field(hy, "tin") is None:
                    raise RegistryUnavailable("eregister: company card has an unknown layout")
                if re.sub(r"\D", "", _field(hy, "tin") or "") != tin:
                    continue
                try:  # the Latin name is a nicety: without it the Armenian one is enough
                    en = parse_company(await self._get(client, path.replace("/hy/", "/en/", 1)))
                except RegistryUnavailable:
                    en = None
                return to_company(tin, hy, en)
            return None
        finally:
            if self._client is None:
                await client.aclose()
