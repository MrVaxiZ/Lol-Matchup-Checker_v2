"""Offline tests: parsing, name resolution, ratings and Excel export (no network/browser)."""

from pathlib import Path

import pytest
from openpyxl import load_workbook
from scrapling.parser import Selector

from lol_matchup_checker.data import Lane, champion_slug
from lol_matchup_checker.excel import write_excel
from lol_matchup_checker.models import Matchup, rate, resolve_champions
from lol_matchup_checker.parsing import (
    ParseError,
    is_cloudflare_challenge,
    parse_lane_champions,
    parse_matchup_stats,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Selector:
    return Selector((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Kha'Zix", "khazix"),
        ("Dr. Mundo", "drmundo"),
        ("Nunu & Willump", "nunu"),
        ("Renata Glasc", "renata"),
        ("Jarvan IV", "jarvaniv"),
        ("Master Yi", "masteryi"),
        ("Darius", "darius"),
    ],
)
def test_slug(name, slug):
    assert champion_slug(name) == slug


def test_matchup_url_points_path_at_opponent():
    assert Lane.TOP.matchup_url("Garen", "Darius") == "https://u.gg/lol/champions/garen/build/top?opp=darius"
    assert Lane.JG.matchup_url("Lee Sin", "Xin Zhao") == "https://u.gg/lol/champions/leesin/build/jungle?opp=xinzhao"


@pytest.mark.parametrize(
    "wr,rating",
    [(48.99, "-"), (49.0, "S"), (51.0, "S"), (51.01, "+"), (53.0, "D"), (55.0, "D+"), (59.99, "D+"), (60.0, "UD")],
)
def test_rating_thresholds(wr, rating):
    assert rate(wr) == rating


def test_parse_lane_champions_dedupes_and_keeps_order():
    assert parse_lane_champions(load("tier_list.html")) == ["Darius", "Garen", "K'Sante", "Dr. Mundo", "Vladimir"]


def test_parse_matchup_uses_top_stats_bar():
    assert parse_matchup_stats(load("matchup.html")) == (48.37, 12345)


def test_parse_matchup_missing_raises():
    with pytest.raises(ParseError):
        parse_matchup_stats(load("tier_list.html"))


def test_cloudflare_detection():
    assert is_cloudflare_challenge(load("cloudflare.html"))
    assert not is_cloudflare_challenge(load("matchup.html"))


def test_resolve_champions():
    lane = ["Darius", "Garen", "K'Sante", "Dr. Mundo", "Vladimir"]
    resolved, unknown = resolve_champions(["vlad", "DARIUS", "ksante", "mundo", "k'sante", "Teemo"], lane)
    assert resolved == ["Vladimir", "Darius", "K'Sante", "Dr. Mundo"]
    assert unknown == ["Teemo"]


def test_excel_layout(tmp_path):
    lane = ["Garen", "Darius", "Vladimir"]
    mine = ["Darius", "Vladimir"]
    matchups = [
        Matchup("Darius", "Garen", 1000, 51.63),
        Matchup("Darius", "Vladimir", 800, 47.0),
        Matchup("Vladimir", "Garen", 500, 61.2),
    ]
    out = write_excel(tmp_path / "out", mine, lane, matchups)
    assert out.name == "out.xlsx"

    ws = load_workbook(out)["Matchups"]
    assert [c.value for c in ws[1]] == [None, "MyChamp#1", "MyChamp#2"]
    assert [c.value for c in ws[2]] == ["Champion Against", "Darius", "Vladimir"]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=3)]
    assert rows == [
        ["Darius", "N/A", "N/A"],  # Vladimir vs Darius not scraped -> N/A, Darius vs Darius -> N/A
        ["Garen", "+", "UD"],
        ["Vladimir", "-", "N/A"],
    ]
    assert ws["B4"].fill.start_color.rgb.endswith("ADFF2F")

    raw = load_workbook(out)["Raw Data"]
    assert raw.max_row == 4


def test_merge_tier_list_adds_icon_only_champions_for_this_lane():
    from lol_matchup_checker.models import merge_tier_list
    links = {
        "jarvaniv": [True, "Jarvan IV"],    # icon link with alt text
        "leesin": [True, ""],               # no alt -> known names
        "khazix": [True, ""],               # not in known names -> capitalised slug
        "elise": [True, "Elise"],           # already in the table names
        "ahri": [False, "Ahri"],            # nav/sidebar link without the lane -> ignored
    }
    merged = merge_tier_list(["Rammus", "Elise"], links, {"leesin": "Lee Sin"})
    assert merged == ["Rammus", "Elise", "Jarvan IV", "Lee Sin", "Khazix"]


def test_guess_name():
    from lol_matchup_checker.models import guess_name
    assert guess_name("j4") == "Jarvan IV"
    assert guess_name("elise") == "Elise"


@pytest.mark.parametrize("text,expected", [("26.18", "26.18"), ("26_18", "26.18"), ("26-8", "26.8"), (" 26.08 ", "26.8")])
def test_normalize_patch(text, expected):
    from lol_matchup_checker.data import normalize_patch
    assert normalize_patch(text) == expected


@pytest.mark.parametrize("bad", ["latest", "26", "26.18.1", ""])
def test_normalize_patch_rejects(bad):
    from lol_matchup_checker.data import normalize_patch
    with pytest.raises(ValueError):
        normalize_patch(bad)


def test_with_patch():
    from lol_matchup_checker.data import with_patch
    assert with_patch("https://u.gg/lol/jungle-tier-list", "26.18") == "https://u.gg/lol/jungle-tier-list?patch=26_18"
    assert with_patch("https://u.gg/lol/champions/garen/build/top?opp=darius", "26.18").endswith("?opp=darius&patch=26_18")
    assert with_patch("https://u.gg/x", None) == "https://u.gg/x"


def test_parse_page_patch():
    from lol_matchup_checker.parsing import parse_page_patch
    page = Selector("<html><head><title>U.GG Jungle Tier List - Patch 26.20 Best Jungle Champions</title></head></html>")
    assert parse_page_patch(page) == "26.20"
    assert parse_page_patch(load("matchup.html")) is None


def test_excel_patch_label(tmp_path):
    out = write_excel(tmp_path / "p.xlsx", ["Darius"], ["Garen"], [Matchup("Darius", "Garen", 10, 52.0)], patch_label="26.18")
    assert load_workbook(out)["Matchups"]["A1"].value == "Patch 26.18"
