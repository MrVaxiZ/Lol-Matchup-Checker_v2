"""Static data: lanes, u.gg endpoints, champion aliases and URL slugs."""

from __future__ import annotations

import re
from enum import Enum


class Lane(str, Enum):
    TOP = "top"
    JG = "jungle"
    MID = "mid"
    ADC = "adc"
    SUP = "support"

    @property
    def tier_list_url(self) -> str:
        return TIER_LIST_URLS[self]

    def matchup_url(self, champ_against: str, champ_playing: str) -> str:
        # u.gg shows the stats of the champion in the path *against* the `opp` champion,
        # so the win rate on this page is ChampAgainst's win rate vs ChampPlaying.
        return (
            f"https://u.gg/lol/champions/{champion_slug(champ_against)}"
            f"/build/{self.value}?opp={champion_slug(champ_playing)}"
        )


TIER_LIST_URLS: dict[Lane, str] = {
    Lane.TOP: "https://u.gg/lol/top-lane-tier-list",
    Lane.JG: "https://u.gg/lol/jungle-tier-list",
    Lane.MID: "https://u.gg/lol/mid-lane-tier-list",
    Lane.ADC: "https://u.gg/lol/adc-tier-list",
    Lane.SUP: "https://u.gg/lol/support-tier-list",
}

LANE_INPUTS: dict[str, Lane] = {
    "top": Lane.TOP,
    "jg": Lane.JG,
    "jng": Lane.JG,
    "jungle": Lane.JG,
    "mid": Lane.MID,
    "middle": Lane.MID,
    "adc": Lane.ADC,
    "bot": Lane.ADC,
    "sup": Lane.SUP,
    "supp": Lane.SUP,
    "support": Lane.SUP,
}

CHAMP_ALIASES: dict[str, str] = {
    "lb": "LeBlanc",
    "vlad": "Vladimir",
    "kata": "Katarina",
    "morde": "Mordekaiser",
    "yas": "Yasuo",
    "cho": "Cho'Gath",
    "cas": "Cassiopeia",
    "cait": "Caitlyn",
    "mundo": "Dr. Mundo",
    "fiddle": "Fiddlesticks",
    "gp": "Gangplank",
    "heca": "Hecarim",
    "jarvan": "Jarvan IV",
    "j4": "Jarvan IV",
    "kaisa": "Kai'Sa",
    "robal": "Kha'Zix",
    "robak": "Kha'Zix",
    "kog": "Kog'Maw",
    "miss": "Miss Fortune",
    "mf": "Miss Fortune",
    "yi": "Master Yi",
    "morg": "Morgana",
    "noc": "Nocturne",
    "nunu": "Nunu & Willump",
    "panth": "Pantheon",
    "rene": "Renekton",
    "reksai": "Rek'Sai",
    "gazownik": "Singed",
    "tahm": "Tahm Kench",
    "trynda": "Tryndamere",
    "tf": "Twisted Fate",
    "velkoz": "Vel'Koz",
    "ww": "Warwick",
    "xin": "Xin Zhao",
    "lee": "Lee Sin",
    "asol": "Aurelion Sol",
    "ksante": "K'Sante",
    "renata": "Renata Glasc",
    "belveth": "Bel'Veth",
}

# u.gg URL slugs that are not simply "lowercase name with non-letters removed".
_SPECIAL_SLUGS: dict[str, str] = {
    "Nunu & Willump": "nunu",
    "Renata Glasc": "renata",
}


_PATCH_RE = re.compile(r"^\s*(\d{1,2})\s*[._,-]\s*(\d{1,2})\s*$")


def normalize_patch(text: str) -> str:
    """'26.18', '26_18', '26-18' -> '26.18'. Raises ValueError for anything else."""
    match = _PATCH_RE.match(text or "")
    if not match:
        raise ValueError(f"'{text}' is not a patch number, expected something like 26.18")
    return f"{int(match.group(1))}.{int(match.group(2))}"


def with_patch(url: str, patch: str | None) -> str:
    """Add u.gg's patch filter to a URL; u.gg writes patches with an underscore (patch=26_18)."""
    if not patch:
        return url
    return f"{url}{'&' if '?' in url else '?'}patch={patch.replace('.', '_')}"


def fetch_known_champion_names(timeout: float = 10.0) -> dict[str, str]:
    """slug -> display name for every champion, from Riot's public Data Dragon.
    Only used to name champions that the tier list shows as icons. Returns {} on any failure."""
    import json
    import urllib.request

    def get(url: str):
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs)
            return json.loads(resp.read().decode("utf-8"))

    try:
        version = get("https://ddragon.leagueoflegends.com/api/versions.json")[0]
        champs = get(f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json")["data"]
        return {champion_slug(c["name"]): c["name"] for c in champs.values()}
    except Exception:
        return {}


def champion_slug(name: str) -> str:
    """'Kha'Zix' -> 'khazix', 'Dr. Mundo' -> 'drmundo', 'Nunu & Willump' -> 'nunu'."""
    if name in _SPECIAL_SLUGS:
        return _SPECIAL_SLUGS[name]
    return re.sub(r"[^a-z0-9]", "", name.lower())
