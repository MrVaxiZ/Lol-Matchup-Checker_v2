"""u.gg scraping on top of Scrapling's stealth browser (gets through Cloudflare)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scrapling.fetchers import AsyncStealthySession

from .data import Lane, champion_slug, fetch_known_champion_names, with_patch
from .models import Matchup, merge_tier_list
from .parsing import (
    ParseError,
    TierListData,
    parse_tier_list_data,
    is_cloudflare_challenge,
    parse_lane_champions,
    parse_matchup_stats,
    parse_page_patch,
)


class ScrapeError(Exception):
    pass


@dataclass
class ScraperOptions:
    workers: int = 4  # browser tabs open at once
    retries: int = 3  # attempts per page
    timeout_s: int = 60  # per page; Cloudflare solving needs ~60s headroom
    headless: bool = True
    real_chrome: bool = False  # use the locally installed Google Chrome instead of bundled Chromium
    executable_path: str | None = None  # explicit browser binary
    block_ads: bool = True  # u.gg is ad-heavy; blocking them makes pages load much faster
    debug: bool = False  # verbose logs + dump HTML of failed pages
    debug_dir: Path = Path("debug")
    patch: str | None = None  # e.g. "26.18"; None = whatever u.gg shows by default (latest)
    min_pick_rate: float = 0.5  # % of games in the lane; 0.5 matches u.gg's own tier list


# ----------------------------------------------------------------- page actions
# These run inside the browser tab after navigation (Playwright async Page API).

async def _accept_cookies(page) -> None:
    try:
        button = page.locator("button.fc-cta-consent").first
        if await button.is_visible(timeout=1500):
            await button.click(timeout=2000)
    except Exception:
        pass


# Everything on the page that identifies a champion: names in the classic table
# (strong.champion-name) and links to champion pages (/lol/champions/<slug>/build...),
# which also covers icon-only layouts. Returns {names: [...], links: [[slug, mentionsLane, imgAlt]]}.
_JS_COLLECT = """(lane) => {
    const names = Array.from(document.querySelectorAll('strong.champion-name'))
        .map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean);
    const links = [];
    for (const a of document.querySelectorAll('a[href*="/lol/champions/"]')) {
        const href = (a.getAttribute('href') || '').toLowerCase();
        const m = href.match(/\\/lol\\/champions\\/([a-z0-9]+)\\/(build|counter|items|runes|matchups)/);
        if (!m) continue;
        const img = a.querySelector('img[alt]');
        links.push([m[1], href.includes(lane), img ? img.getAttribute('alt').trim() : '']);
    }
    return {names, links};
}"""

_JS_HAS_EMBEDDED_RANKING = """() => {
    const s = document.getElementById('reactn-preloaded-state');
    return !!(s && s.textContent.includes('champion_ranking'));
}"""

_CHAMP_ELEMENTS ='strong.champion-name, a[href*="/lol/champions/"]'

# Scrolls the element that actually scrolls the champion list (an inner container or the window)
# by `step` px. Returns true if anything moved.
_JS_SCROLL_STEP = """(step) => {
    const rows = document.querySelectorAll('strong.champion-name, a[href*="/lol/champions/"]');
    let node = rows.length ? rows[rows.length - 1].parentElement : null;
    while (node && node !== document.body && node !== document.documentElement) {
        const s = getComputedStyle(node);
        if (/(auto|scroll)/.test(s.overflowY) && node.scrollHeight > node.clientHeight + 5) {
            const before = node.scrollTop;
            node.scrollTop += step;
            node.dispatchEvent(new Event('scroll'));
            return node.scrollTop !== before;
        }
        node = node.parentElement;
    }
    const before = window.scrollY;
    window.scrollBy(0, step);
    return window.scrollY !== before;
}"""


@dataclass
class _TierListFindings:
    names: dict  # display names from the table, in order
    links: dict  # slug -> [mentions lane, img alt], in order
    requests: list  # debug: XHR/fetch URLs the page made
    screenshot: Path | None = None


def _make_tier_list_hooks(found: _TierListFindings, lane: Lane, debug_shot: Path | None):
    """The tier list may keep only on-screen rows in the DOM, and u.gg's newer layout isn't
    the old table, so champions are collected step by step while scrolling from top to
    bottom, from both table names and champion links."""

    async def setup(page) -> None:
        found.requests.clear()
        if debug_shot is not None:
            page.on("request", lambda r: found.requests.append(f"{r.resource_type}\t{r.method}\t{r.url}")
                    if r.resource_type in ("xhr", "fetch") else None)

    async def collect(page) -> int:
        data = await page.evaluate(_JS_COLLECT, lane.value)
        new = 0
        for name in data["names"]:
            if name not in found.names:
                found.names[name] = None
                new += 1
        for slug, mentions_lane, alt in data["links"]:
            if slug not in found.links:
                found.links[slug] = [mentions_lane, alt]
                new += 1
            else:
                found.links[slug][0] |= mentions_lane
                found.links[slug][1] = found.links[slug][1] or alt
        return new

    async def action(page) -> None:
        found.names.clear()
        found.links.clear()
        await _accept_cookies(page)
        try:
            if await page.evaluate(_JS_HAS_EMBEDDED_RANKING):
                return  # u.gg's data is in the page; no need to scroll through the layout
        except Exception:
            pass
        try:
            await page.wait_for_selector(_CHAMP_ELEMENTS, timeout=20_000)
        except Exception:
            return  # let the parser/debug dump report what we actually got
        await page.wait_for_timeout(1000)  # let the client-side app finish rendering
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)

        step = max(200, int(await page.evaluate("window.innerHeight")) // 2)
        stale = 0
        for _ in range(400):
            new = await collect(page)
            stale = 0 if new else stale + 1

            moved = await page.evaluate(_JS_SCROLL_STEP, step)
            if not moved:
                # At the bottom: maybe there's a "Load more" button, or rows still loading.
                try:
                    more = page.get_by_role("button", name=re.compile(r"(load|show|see) (more|all)", re.I)).first
                    if await more.is_visible(timeout=300):
                        await more.click(timeout=2000)
                        stale = 0
                except Exception:
                    pass
                if stale >= 4:
                    break
            await page.wait_for_timeout(250)

        if debug_shot is not None:
            try:
                await page.evaluate("window.scrollTo(0, 0)")
                debug_shot.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(debug_shot), full_page=True)
                found.screenshot = debug_shot
            except Exception:
                pass

    return setup, action


async def _matchup_action(page) -> None:
    await _accept_cookies(page)
    await page.evaluate("window.scrollBy(0, 300)")
    try:
        await page.wait_for_selector("text=Win Rate", timeout=20_000)
        await page.wait_for_selector("text=Matches", timeout=5_000)
    except Exception:
        pass


# ----------------------------------------------------------------------- scraper

class UggScraper:
    """Use as `async with UggScraper(options) as scraper: ...` so the browser is closed."""

    def __init__(self, options: ScraperOptions, log: Callable[[str], None] = print):
        self.opt = options
        self.log = log
        self._session: AsyncStealthySession | None = None
        self.patch_shown: str | None = None  # patch u.gg says it's showing (from the tier list title)
        self.patch_mismatch = False
        self.lane_all_champions: list[str] = []  # everyone with games in the lane, incl. below the pick-rate cutoff
        logging.getLogger("scrapling").setLevel(logging.WARNING if options.debug else logging.CRITICAL)

    async def __aenter__(self) -> "UggScraper":
        kwargs: dict = dict(
            headless=self.opt.headless,
            real_chrome=self.opt.real_chrome,
            max_pages=max(1, self.opt.workers),
            timeout=self.opt.timeout_s * 1000,
            block_ads=self.opt.block_ads,
            locale="en-US",  # keeps labels ("Win Rate", "Matches") and number formats predictable
            retries=1,  # retries are handled here so parse failures are retried too
        )
        if self.opt.executable_path:
            kwargs["executable_path"] = self.opt.executable_path
        self._session = AsyncStealthySession(**kwargs)
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session is not None:
            await self._session.__aexit__(*exc)
            self._session = None

    # -------------------------------------------------------------- tier list

    def _use_tier_data(self, data: TierListData, lane: Lane) -> list[str]:
        """Opponents = champions above the pick-rate cutoff (u.gg's own default list is >= 0.5%)."""
        self.lane_all_champions = [e.name for e in data.entries if e.matches > 0]
        picked = [e for e in data.entries if e.pick_rate >= self.opt.min_pick_rate]
        for i, e in enumerate(picked):
            self.log(f"[{i}] Found champion : '{e.name}' ({e.pick_rate:.2f}% pick rate, {e.matches:,} games)")
        self.log(f"Found total of [{len(picked)}] {lane.value} champions with pick rate >= {self.opt.min_pick_rate}% "
                 f"(out of {len(self.lane_all_champions)} played there).")
        return [e.name for e in picked]

    def _debug_tier_data(self, page, lane: Lane, data: TierListData, champions: list[str], stamp: str) -> None:
        html = self._dump(page, f"tierlist_{lane.value}")
        try:
            report = self.opt.debug_dir / f"_debug_{stamp}_tierlist_{lane.value}.txt"
            lines = [f"url: {page.url}", f"patch in data: {data.patch}", f"min pick rate: {self.opt.min_pick_rate}%",
                     f"selected ({len(champions)}): {champions}", "", "all entries [name, id, pick rate %, matches, win rate %]:"]
            lines += [f"  {e.name}\t{e.champion_id}\t{e.pick_rate:.3f}\t{e.matches}\t{e.win_rate:.2f}" for e in data.entries]
            report.write_text("\n".join(lines), encoding="utf-8")
            self.log(f"[debug] tier list saved: {html}, {report}")
        except Exception as exc:
            self.log(f"[debug] could not write tier list report: {exc}")

    def _check_patch(self, page, data_patch: str | None = None) -> None:
        """Remember which patch u.gg is showing; warn if it ignored the requested one."""
        shown = data_patch or parse_page_patch(page)
        self.patch_shown = shown
        if self.opt.patch and shown and shown != self.opt.patch:
            self.log(f"WARNING: asked for patch {self.opt.patch} but u.gg is showing {shown}. "
                     "u.gg probably has no data for that patch.")
            self.patch_mismatch = True

    async def get_lane_champions(self, lane: Lane) -> list[str]:
        url = with_patch(lane.tier_list_url, self.opt.patch)
        last_error = "unknown error"
        page = None
        found = _TierListFindings(names={}, links={}, requests=[])
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shot = self.opt.debug_dir / f"_debug_{stamp}_tierlist_{lane.value}.png" if self.opt.debug else None
        setup, action = _make_tier_list_hooks(found, lane, shot)
        known_names: dict | None = None  # only needed for the fallback

        for attempt in range(1, self.opt.retries + 1):
            try:
                page = await self._session.fetch(
                    url,
                    solve_cloudflare=True,
                    page_setup=setup,
                    page_action=action,
                    timeout=max(self.opt.timeout_s, 60) * 1000,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if is_cloudflare_challenge(page):
                    last_error = "still on the Cloudflare challenge page"
                else:
                    # 1) u.gg's own data embedded in the page: every champion, with pick rates
                    tier_data = parse_tier_list_data(page, lane.value)
                    if tier_data and tier_data.entries:
                        champions = self._use_tier_data(tier_data, lane)
                        if self.opt.debug:
                            self._debug_tier_data(page, lane, tier_data, champions, stamp)
                        if champions:
                            self._check_patch(page, tier_data.patch)
                            return champions
                        last_error = f"no champions with pick rate >= {self.opt.min_pick_rate}%"
                        break
                    # 2) fallback: names/links seen while scrolling + whatever is in the final HTML
                    self.log("Embedded tier list data not found, reading the page instead.")
                    if known_names is None:
                        known_names = await asyncio.to_thread(fetch_known_champion_names)
                    champions = merge_tier_list([*found.names, *parse_lane_champions(page)], found.links, known_names)
                    if self.opt.debug:
                        self._debug_tier_list(page, lane, found, champions, stamp)
                    if champions:
                        if len(champions) < 30:
                            self.log(f"WARNING: only {len(champions)} champions found on the tier list, "
                                     "it may not have loaded fully. Re-run with --debug if matchups are missing.")
                        for i, name in enumerate(champions):
                            self.log(f"[{i}] Found champion : '{name}'")
                        self.log(f"Found total of [{len(champions)}] champions.")
                        self._check_patch(page)
                        return champions
                    last_error = "no champion rows found on the tier list"
            self.log(f"Tier list attempt {attempt}/{self.opt.retries} failed: {last_error}")
            await asyncio.sleep(attempt * 2)

        dump = self._dump(page, f"tierlist_{lane.value}", force=True)
        hint = f" Page saved to '{dump}'." if dump else ""
        raise ScrapeError(f"Could not read the {lane.value} tier list ({last_error}).{hint}")

    def _debug_tier_list(self, page, lane: Lane, found: _TierListFindings, champions: list[str], stamp: str) -> None:
        html = self._dump(page, f"tierlist_{lane.value}")
        try:
            report = self.opt.debug_dir / f"_debug_{stamp}_tierlist_{lane.value}.txt"
            lines = [f"url: {page.url}", f"status: {page.status}", "",
                     f"names from table ({len(found.names)}): {list(found.names)}", "",
                     f"champion links ({len(found.links)}) [slug, mentions lane, img alt]:"]
            lines += [f"  {slug}\t{v[0]}\t{v[1]}" for slug, v in found.links.items()]
            lines += ["", f"final list ({len(champions)}): {champions}", "", "XHR/fetch requests:"]
            lines += [f"  {r}" for r in found.requests]
            report.write_text("\n".join(lines), encoding="utf-8")
            self.log(f"[debug] tier list saved: {html}, {report}" + (f", {found.screenshot}" if found.screenshot else ""))
        except Exception as exc:
            self.log(f"[debug] could not write tier list report: {exc}")

    async def champion_has_page(self, lane: Lane, name: str) -> bool:
        """True if u.gg has a build page with stats for this champion in this lane.
        Used for your champions that aren't on the lane's tier list (off-meta picks)."""
        url = with_patch(lane.matchup_url(name, name).split("?")[0], self.opt.patch)
        page, reason = None, "no response"
        for solve in (False, True):
            try:
                page = await self._session.fetch(
                    url, solve_cloudflare=solve, page_action=_matchup_action,
                    timeout=(max(self.opt.timeout_s, 60) if solve else self.opt.timeout_s) * 1000,
                )
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                continue
            if page.status == 404:
                reason = "404"
                break
            if is_cloudflare_challenge(page):
                reason = "Cloudflare challenge"
                continue
            try:
                parse_matchup_stats(page)
                return True
            except ParseError as exc:
                reason = str(exc)
                break
        if self.opt.debug:
            dump = self._dump(page, f"check_{champion_slug(name)}")
            self.log(f"[debug] {name}: no usable page at {url} ({reason}); saved {dump}")
        return False

    # --------------------------------------------------------------- matchups

    async def get_matchups(self, lane: Lane, lane_champions: list[str], my_champs: list[str]) -> tuple[list[Matchup], list[tuple[str, str, str]]]:
        """Scrape every (my champ vs lane champ) pair. Returns (matchups, failures)."""
        jobs = [(against, playing) for playing in my_champs for against in lane_champions if against != playing]
        total = len(jobs)
        self.log(f"Scraping {total} matchups with {self.opt.workers} parallel tabs...")

        semaphore = asyncio.Semaphore(max(1, self.opt.workers))

        async def run(against: str, playing: str):
            async with semaphore:
                return against, playing, await self._fetch_matchup(lane, against, playing)

        matchups: list[Matchup] = []
        failures: list[tuple[str, str, str]] = []
        done = 0
        for future in asyncio.as_completed([run(a, p) for a, p in jobs]):
            against, playing, result = await future
            done += 1
            if isinstance(result, Matchup):
                matchups.append(result)
                self.log(f"[{done}/{total}] {playing} vs {against} : [{result.win_rate}%] WR, [{result.matches}] Matches")
            else:
                failures.append((playing, against, result))
                self.log(f"[{done}/{total}] {playing} vs {against} : FAILED ({result})")
        return matchups, failures

    async def _fetch_matchup(self, lane: Lane, against: str, playing: str) -> Matchup | str:
        url = with_patch(lane.matchup_url(against, playing), self.opt.patch)
        solve_cloudflare = False  # only pay for the solver when a challenge actually shows up
        last_error = "unknown error"
        page = None

        for attempt in range(1, self.opt.retries + 1):
            try:
                page = await self._session.fetch(
                    url,
                    solve_cloudflare=solve_cloudflare,
                    page_action=_matchup_action,
                    timeout=(max(self.opt.timeout_s, 60) if solve_cloudflare else self.opt.timeout_s) * 1000,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if page.status == 404:
                    return "page not found (404)"
                if is_cloudflare_challenge(page):
                    solve_cloudflare = True
                    last_error = "Cloudflare challenge"
                    continue  # retry right away, this time with the solver on
                try:
                    win_rate_against, matches = parse_matchup_stats(page)
                except ParseError as exc:
                    last_error = str(exc)
                else:
                    return Matchup(
                        champ_playing=playing,
                        champ_against=against,
                        matches=matches,
                        win_rate=round(100.0 - win_rate_against, 2),
                    )
            if self.opt.debug:
                self.log(f"  {playing} vs {against}: attempt {attempt}/{self.opt.retries} failed: {last_error}")
            await asyncio.sleep(attempt)

        self._dump(page, f"{champion_slug(playing)}_vs_{champion_slug(against)}")
        return last_error

    def _dump(self, page, name: str, force: bool = False) -> Path | None:
        if page is None or not (force or self.opt.debug):
            return None
        try:
            self.opt.debug_dir.mkdir(parents=True, exist_ok=True)
            path = self.opt.debug_dir / f"_debug_{time.strftime('%Y%m%d_%H%M%S')}_{name}.html"
            path.write_text(str(page.html_content), encoding="utf-8")
            return path
        except Exception:
            return None
