"""Command line interface: `gen` and `alias`, same as the original C# tool."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from .data import CHAMP_ALIASES, LANE_INPUTS, normalize_patch
from .excel import write_excel
from .models import guess_name, resolve_champions
from .scraper import ScrapeError, ScraperOptions, UggScraper


def _patch_arg(text: str) -> str:
    try:
        return normalize_patch(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lol-matchup",
        description="LoL Matchup CLI Tool - scrapes matchup win rates from u.gg into an Excel sheet.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser(
        "gen",
        help="Generate the Excel file with matchups.",
        description="Example: lol-matchup gen --lane mid --champs vlad lb",
    )
    gen.add_argument("--lane", "--role", required=True, type=str.lower, choices=sorted(LANE_INPUTS),
                     metavar="LANE", help="top, jg (jungle), mid, adc (bot) or sup (support)")
    gen.add_argument("--champs", required=True, nargs="+", metavar="CHAMP",
                     help="Champions you play (names or aliases, see `alias`).")
    gen.add_argument("--out", default="Matchups.xlsx", help="Output Excel path (default: Matchups.xlsx).")
    gen.add_argument("--patch", type=_patch_arg, default=None, metavar="PATCH",
                     help="Patch to pull data for, e.g. 26.18 (default: latest).")
    gen.add_argument("--min-pick-rate", type=float, default=0.5, metavar="PERCENT",
                     help="Only include opponents picked in at least this %% of the lane's games "
                          "(default: 0.5, same as u.gg's tier list). 0 = every champion u.gg has games for.")
    gen.add_argument("--workers", type=int, default=4, help="Pages scraped in parallel (default: 4).")
    gen.add_argument("--retries", type=int, default=3, help="Attempts per page (default: 3).")
    gen.add_argument("--timeout", type=int, default=60, help="Seconds per page (default: 60).")
    gen.add_argument("--show-browser", action="store_true",
                     help="Run the browser visibly. Try this if Cloudflare keeps blocking headless mode.")
    gen.add_argument("--real-chrome", action="store_true",
                     help="Use your installed Google Chrome instead of Scrapling's bundled Chromium.")
    gen.add_argument("--browser-path", help="Path to a specific Chromium/Chrome executable.")
    gen.add_argument("--no-block-ads", action="store_true", help="Don't block ad/tracker requests.")
    gen.add_argument("--debug", action="store_true",
                     help="Verbose logs; saves the HTML of pages that failed into ./debug.")

    sub.add_parser("alias", help="List all champion aliases.")
    return parser


def cmd_alias() -> int:
    for alias, champ in CHAMP_ALIASES.items():
        print(f"Alias : '{alias}', Champion : '{champ}'")
    return 0


async def cmd_gen(args: argparse.Namespace) -> int:
    started = time.monotonic()
    lane = LANE_INPUTS[args.lane]
    options = ScraperOptions(
        workers=max(1, args.workers),
        retries=max(1, args.retries),
        timeout_s=max(10, args.timeout),
        headless=not args.show_browser,
        real_chrome=args.real_chrome,
        executable_path=args.browser_path,
        block_ads=not args.no_block_ads,
        debug=args.debug,
        patch=args.patch,
        min_pick_rate=max(0.0, args.min_pick_rate),
    )

    print(f"Patch: {args.patch}" if args.patch else "Patch: latest")
    async with UggScraper(options) as scraper:
        lane_champs = await scraper.get_lane_champions(lane)
        if scraper.patch_shown and not scraper.patch_mismatch:
            print(f"u.gg is showing data for patch {scraper.patch_shown}.")
        if scraper.patch_mismatch:
            print(f"Continuing with patch {scraper.patch_shown} data. Press Ctrl+C to stop.")
        patch_label = scraper.patch_shown or args.patch or "latest"

        my_champs, unknown = resolve_champions(args.champs, lane_champs)
        if unknown and scraper.lane_all_champions:
            # below the pick-rate cutoff, but u.gg still has games for them in this lane
            rare, unknown = resolve_champions(unknown, scraper.lane_all_champions)
            for name in rare:
                if name not in my_champs:
                    print(f"{name} is below the {args.min_pick_rate}% pick-rate cutoff in {lane.value}, "
                          "but u.gg has games for it. Including it (matchup data may be thin).")
                    my_champs.append(name)
        still_unknown = []
        for raw in unknown:
            # Not on the tier list (off-meta pick, or the list didn't fully load):
            # include it anyway if u.gg has a page for it in this lane.
            guess = guess_name(raw)
            if guess in my_champs:
                continue
            if raw.strip().lower() in CHAMP_ALIASES:
                # aliases always point at real champions, no need to check
                print(f"'{raw}' -> {guess} isn't on the u.gg {lane.value} tier list we read. Including it anyway.")
                my_champs.append(guess)
            elif await scraper.champion_has_page(lane, guess):
                print(f"'{raw}' -> {guess} isn't on the u.gg {lane.value} tier list, but has a {lane.value} page. Including it.")
                my_champs.append(guess)
            else:
                still_unknown.append(raw)
        if still_unknown:
            print(f"These champions were not found on u.gg for {lane.value} (typo?) and will be skipped:")
            for name in still_unknown:
                print(f" - {name}")
        else:
            print("All champions were recognized!")
        if not my_champs:
            print("Nothing to do: none of your champions were recognized. Run `alias` to see shortcuts.")
            return 2
        if "Shaco" in my_champs:
            print("Shaco detected. Your teammates have been notified.")

        matchups, failures = await scraper.get_matchups(lane, lane_champs, my_champs)

    path = write_excel(args.out, my_champs, lane_champs, matchups, patch_label=patch_label)
    print(f"Excel saved to : \n'{path}'")

    if failures:
        print(f"{len(failures)} matchup(s) could not be scraped and are marked N/A:")
        for playing, against, reason in failures:
            print(f" - {playing} vs {against}: {reason}")
        if not args.debug:
            print("Re-run with --debug to save the failing pages' HTML into ./debug.")

    elapsed = int(time.monotonic() - started)
    print(f"Program was running for : [{elapsed // 3600:02}:{elapsed % 3600 // 60:02}:{elapsed % 60:02}]")
    return 0 if matchups else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "alias":
        return cmd_alias()
    try:
        return asyncio.run(cmd_gen(args))
    except ScrapeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
