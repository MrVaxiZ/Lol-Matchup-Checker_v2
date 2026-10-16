"""HTML parsing for u.gg pages, kept separate from fetching so it can be tested offline.

Everything here works on a Scrapling `Selector` (a fetched `Response` is one too).
"""

from __future__ import annotations

import re

from scrapling.parser import Selector

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
    return f"{int(match.group(1))}.{int(match.group(2))}" if match else None


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
