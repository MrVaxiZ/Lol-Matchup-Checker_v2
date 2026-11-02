"""HTML parsing for u.gg pages, kept separate from fetching so it can be tested offline.

Everything here works on a Scrapling `Selector` (a fetched `Response` is one too).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from scrapling.parser import Selector

from .data import patch_display

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


class ParseError(Exception):
    pass


def is_cloudflare_challenge(page: Selector) -> bool:
    html = str(page.html_content)
    title = (page.css("title::text").get() or "").strip().lower()
    return (
        "cType: '" in html
        or title.startswith("just a moment")
        or bool(page.css('script[src*="challenges.cloudflare.com"]'))
    )


def parse_page_patch(page: Selector) -> str | None:
    """Patch u.gg says the page is showing, from the title ('... Tier List - Patch 26.20 ...')."""
    title = str(page.css("title::text").get() or "")
    match = re.search(r"Patch\s+(\d{1,2})[._](\d{1,2})", title, re.I)
    return patch_display(f"{match.group(1)}.{match.group(2)}") if match else None


@dataclass
class TierListEntry:
    name: str
    champion_id: str
    win_rate: float
    pick_rate: float  # percent of games in this role
    matches: int


@dataclass
class TierListData:
    entries: list[TierListEntry]  # every champion u.gg has data for in this role, most played first
    patch: str | None  # display form, e.g. "26.21"


_SSR_START = "window.__SSR_DATA__ ="
_SSR_END = "window.__APOLLO_STATE__"


def _ssr_data(html: str) -> dict | None:
    """The JSON u.gg embeds in every page: <script id="reactn-preloaded-state"> ...
    window.__SSR_DATA__ = {...}  window.__APOLLO_STATE__ = ... </script>"""
    start = html.find(_SSR_START)
    if start < 0:
        return None
    start += len(_SSR_START)
    end = html.find(_SSR_END, start)
    blob = html[start:end if end > 0 else None].strip().rstrip(";").strip()
    if blob.endswith("</script>"):
        blob = blob[: -len("</script>")].rstrip().rstrip(";")
    try:
        data = json.loads(blob)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_tier_list_data(page: Selector | str, role: str) -> TierListData | None:
    """Every champion u.gg has ranked data for in `role` ('top', 'jungle', 'mid', 'adc', 'support'),
    read from the data embedded in a tier-list page. None if the page doesn't contain it."""
    html = page if isinstance(page, str) else str(page.html_content)
    ssr = _ssr_data(html)
    if not ssr:
        return None

    ranking_keys = [k for k in ssr if "champion_ranking" in k]
    key = next((k for k in ranking_keys if "ranked_solo_5x5" in k), ranking_keys[0] if ranking_keys else None)
    if key is None:
        return None
    try:
        rows = ssr[key]["data"]["win_rates"][role]
    except (KeyError, TypeError):
        return None

    names: dict[str, str] = {}
    champ_key = next((k for k in ssr if k.endswith("/champion.json")), None)
    if champ_key:
        champs = (ssr[champ_key] or {}).get("data") or {}
        champs = champs.get("data", champs) if isinstance(champs, dict) else {}
        for champ in champs.values():
            if isinstance(champ, dict) and "key" in champ and "name" in champ:
                names[str(champ["key"])] = champ["name"]

    entries = []
    for row in rows:
        try:
            cid = str(row["champion_id"])
            entries.append(TierListEntry(
                name=names.get(cid, f"#{cid}"),
                champion_id=cid,
                win_rate=float(row["win_rate"]),
                pick_rate=float(row["pick_rate"]),
                matches=int(row["matches"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    entries.sort(key=lambda e: e.matches, reverse=True)

    match = re.search(r"/(\d{1,2})_(\d{1,2})/", key)
    patch = patch_display(f"{match.group(1)}_{match.group(2)}") if match else None
    return TierListData(entries=entries, patch=patch)


def parse_lane_champions(page: Selector) -> list[str]:
    """Champion names from a u.gg tier-list page, in tier-list order."""
    names = page.css("div.rt-tr-group strong.champion-name::text").getall()
    if not names:  # fallback if the row wrapper class changes
        names = page.css("strong.champion-name::text").getall()

    seen: dict[str, None] = {}
    for name in names:
        name = str(name).strip()
        if name:
            seen.setdefault(name, None)
    return list(seen)


def _stat_value(page: Selector, label: str) -> str:
    """Text of the value shown right before a stat label, e.g. '51.23%' before 'Win Rate'.

    Mirrors the old Selenium XPath `//div[text()='Win Rate']/preceding-sibling::div`
    but tolerates whitespace and other tag names. The first match in the document is
    the stats bar at the top of the build page.
    """
    hits = page.xpath(f"//*[normalize-space(text())='{label}']/preceding-sibling::*[1]")
    for hit in hits:
        text = str(hit.get_all_text(separator="", strip=True))
        if _NUMBER.search(text):
            return text
    raise ParseError(f"'{label}' value not found on page")


def parse_float(text: str) -> float:
    match = _NUMBER.search(text)
    if not match:
        raise ParseError(f"no number in {text!r}")
    return float(match.group().replace(",", "."))


def parse_int(text: str) -> int:
    digits = re.sub(r"\D", "", text)  # "12,345" / "12.345" / "12 345" -> 12345
    if not digits:
        raise ParseError(f"no number in {text!r}")
    return int(digits)


def parse_matchup_stats(page: Selector) -> tuple[float, int]:
    """(win_rate, matches) as shown on a u.gg build page with ?opp=…

    The win rate is that of the champion in the URL path, not the opponent.
    """
    win_rate = parse_float(_stat_value(page, "Win Rate"))
    matches = parse_int(_stat_value(page, "Matches"))
    if not 0.0 <= win_rate <= 100.0:
        raise ParseError(f"implausible win rate {win_rate}")
    return win_rate, matches
