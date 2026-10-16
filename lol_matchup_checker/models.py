"""Matchup model, rating thresholds and champion-name resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import CHAMP_ALIASES, champion_slug


def rate(win_rate: float) -> str:
    """Same thresholds as the original tool (see README legend)."""
    if win_rate < 49.0:
        return "-"
    if win_rate <= 51.0:
        return "S"
    if win_rate < 53.0:
        return "+"
    if win_rate < 55.0:
        return "D"
    if win_rate < 60.0:
        return "D+"
    return "UD"


@dataclass
class Matchup:
    champ_playing: str
    champ_against: str
    matches: int
    win_rate: float  # win rate of champ_playing vs champ_against, in percent
    rating: str = field(init=False)

    def __post_init__(self) -> None:
        self.rating = rate(self.win_rate)


def merge_tier_list(names: list[str], links: dict[str, list], known_names: dict[str, str] | None = None) -> list[str]:
    """Combine champion names from the table with champion links found on the tier-list page.

    links: slug -> [link mentions the lane, img alt text]. If some links mention the lane,
    only those count (skips nav/sidebar links to other roles). Slugs become display names via
    the table names, the image alt text, `known_names` (slug -> name, e.g. from Data Dragon),
    or finally the capitalised slug.
    """
    result: dict[str, str] = {}  # slug -> display name, insertion-ordered
    for name in names:
        result.setdefault(champion_slug(name), name)

    if any(mentions for mentions, _ in links.values()):
        links = {slug: v for slug, v in links.items() if v[0]}
    known_names = known_names or {}
    for slug, (_, alt) in links.items():
        if slug in result:
            continue
        if alt and champion_slug(alt) == slug:
            result[slug] = alt
        elif slug in known_names:
            result[slug] = known_names[slug]
        else:
            result[slug] = slug.capitalize()
    return list(result.values())


def guess_name(raw: str) -> str:
    """Best guess at a champion's display name from user input ('j4' -> 'Jarvan IV', 'elise' -> 'Elise')."""
    text = raw.strip()
    if text.lower() in CHAMP_ALIASES:
        return CHAMP_ALIASES[text.lower()]
    return text[:1].upper() + text[1:]


def resolve_champions(user_input: list[str], lane_champions: list[str]) -> tuple[list[str], list[str]]:
    """Map what the user typed (aliases, any casing, with or without spaces/apostrophes)
    to the exact names from the u.gg tier list.

    Returns (resolved, unrecognized). Duplicates are dropped, order is kept.
    """
    by_slug = {champion_slug(name): name for name in lane_champions}
    resolved: list[str] = []
    unrecognized: list[str] = []

    for raw in user_input:
        text = raw.strip()
        candidate = CHAMP_ALIASES.get(text.lower(), text)
        name = by_slug.get(champion_slug(candidate))
        if name is None:
            unrecognized.append(raw)
        elif name not in resolved:
            resolved.append(name)

    return resolved, unrecognized
