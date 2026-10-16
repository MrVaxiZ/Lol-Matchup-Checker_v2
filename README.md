# LoL Matchup CLI Tool

A command-line tool that scrapes champion matchup win rates from [u.gg](https://u.gg) and exports them into a colour-coded Excel spreadsheet, so you can see at a glance which of your champions to pick into each enemy laner.

**v2 is a rewrite in Python on top of [Scrapling](https://github.com/D4Vinci/Scrapling).** u.gg started putting pages behind Cloudflare, which broke the old Selenium/C# version. Scrapling's stealth browser gets through Cloudflare's checks (and solves Turnstile challenges when they show up), so the tool works again.

---

## 💡 Features

- 🛡️ Gets through u.gg's Cloudflare protection using Scrapling's `AsyncStealthySession`.
- ⚡ Parallel scraping in one browser with several tabs (`--workers`, default 4).
- 🔎 Reads the champion list for your lane straight from the u.gg tier list.
- 🧠 Champion aliases and forgiving name matching (`vlad`, `VLADIMIR`, `ksante`, `k'sante` all work).
- 📊 Excel export with the same layout and colour legend as before, plus a **Raw Data** sheet and hover comments with the exact win rate and match count.
- ♻️ Per-page retries; Cloudflare's solver is only switched on for pages that actually show a challenge.
- 🛠️ `--debug` saves the HTML of pages that failed so you can see what u.gg returned.

---

## 🚀 Quick Start

### 🔧 Requirements

- Python 3.10+

### 📦 Install

```bash
git clone https://github.com/MrVaxiZ/Lol-Matchup-Checker
cd Lol-Matchup-Checker
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e .
scrapling install        # one-time download of the browser Scrapling drives
```

### 🧪 Run

```bash
lol-matchup gen --lane top --champs vlad darius ww
# or, without installing the command:
python -m lol_matchup_checker gen --lane top --champs vlad darius ww
```

---

## 🧾 Command Line Arguments

| Argument          | Description                                                                 | Example                     |
|-------------------|-----------------------------------------------------------------------------|-----------------------------|
| `gen`             | Main command, generates the Excel file.                                     | `gen`                       |
| `--lane`/`--role` | Required. `top`, `jg`/`jng`/`jungle`, `mid`, `adc`/`bot`, `sup`/`support`  | `--lane mid`                |
| `--champs`        | Required. Champions you play (names or aliases).                            | `--champs lb vlad`          |
| `--out`           | Output Excel path (default `Matchups.xlsx`).                                | `--out "C:\mid_champs.xlsx"`|
| `--patch`         | Patch to pull data for (default: latest). u.gg only keeps recent patches.   | `--patch 26.18`             |
| `--workers`       | Pages scraped in parallel (default 4). Lower it if you get blocked.         | `--workers 2`               |
| `--retries`       | Attempts per page (default 3).                                              | `--retries 5`               |
| `--timeout`       | Seconds per page (default 60).                                              | `--timeout 90`              |
| `--show-browser`  | Run the browser visibly; try it if Cloudflare keeps blocking headless mode. | `--show-browser`            |
| `--real-chrome`   | Use your installed Google Chrome instead of the bundled Chromium.           | `--real-chrome`             |
| `--browser-path`  | Use a specific Chrome/Chromium executable.                                  | `--browser-path "C:\...\chrome.exe"` |
| `--no-block-ads`  | Don't block ad/tracker requests (blocked by default for speed).             | `--no-block-ads`            |
| `--debug`         | Verbose logs; saves HTML of failed pages into `./debug`.                    | `--debug`                   |
| `alias`           | Lists all available aliases.                                                | `alias`                     |

---

## 📂 Output

- Saved to `Matchups.xlsx` in the current directory (or `--out`). Cell A1 shows which patch the data is from.
- **Matchups** sheet: one row per champion on the lane's tier list (A–Z), one column per champion you play, each cell a rating. Hover a cell to see the win rate and number of matches.
- **Raw Data** sheet: every scraped matchup with win rate, matches and rating, filterable.
- Matchups that couldn't be scraped, and your champion against itself, are `N/A`.

### 📘 Excel Legend

| Symbol | Meaning                  | Your win rate      |
|--------|--------------------------|--------------------|
| `-`    | Do not pick this champ.  | below 49%          |
| `S`    | Skill-based matchup.     | 49%–51%            |
| `+`    | Can pick this champ.     | 51%–53%            |
| `D`    | Distinguished pick.      | 53%–55%            |
| `D+`   | Distinguished pick plus. | 55%–60%            |
| `UD`   | Ultra Distinguished.     | 60% and above      |

---

## 🗃️ File Structure

```
lol_matchup_checker/
├── cli.py       # `gen` / `alias` commands
├── scraper.py   # Scrapling stealth session, retries, Cloudflare handling
├── parsing.py   # u.gg HTML parsing (tier list + matchup stats)
├── models.py    # Matchup, ratings, champion-name resolution
├── data.py      # lanes, URLs, aliases, URL slugs
└── excel.py     # Excel export (openpyxl)
tests/           # offline tests: pytest
```

---

## 🧠 How it works

1. Opens the lane's u.gg tier list with Scrapling (`solve_cloudflare=True`), scrolls until all rows are loaded and reads the champion names.
2. For each *(your champ, enemy champ)* pair it opens `u.gg/lol/champions/<enemy>/build/<lane>?opp=<your champ>` and reads **Win Rate** and **Matches** from the stats bar. That page shows the enemy's win rate, so your win rate is `100 − value`.
3. Pages share one browser (and its Cloudflare clearance cookie); up to `--workers` tabs run at once.

---

## 🔍 Troubleshooting

- **Blocked / stuck on "Just a moment…"**: try `--show-browser`, `--real-chrome`, and/or fewer `--workers`.
- **Many `N/A` cells**: run with `--debug` and open the HTML in `./debug`. If u.gg changed its page layout, the selectors live in `parsing.py`.
- **Champion not recognized**: it must be on the u.gg tier list for that lane. Run `lol-matchup alias` for shortcuts.

Run the tests with `pip install -e ".[dev]"` then `pytest`.

---

## 📜 License

This project is released under the MIT License. Do whatever you want with it, but give credit if you use it publicly.

## 👤 Author

Made by [MrVaxiZ](https://github.com/MrVaxiZ)
Inspired by the need for accurate matchup prep for ranked games.

**GLHF on the Rift!**
