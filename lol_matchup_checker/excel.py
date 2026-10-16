"""Excel export with the same layout and colour legend as the original ClosedXML version,
plus a 'Raw Data' sheet with the actual win rates and match counts."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Matchup

NA = "N/A"

# rating -> (fill colour, font colour); same colours as the old XLColor values
RATING_STYLES: dict[str, tuple[str, str]] = {
    "-": ("F0004D", "000000"),   # RedNcs
    "S": ("FFFF00", "000000"),   # Yellow
    "+": ("ADFF2F", "000000"),   # GreenYellow
    "D": ("008000", "FFFFFF"),   # Green
    "D+": ("0000FF", "FFFFFF"),  # Blue
    "UD": ("800080", "FFFFFF"),  # Purple
    NA: ("000000", "FFFFFF"),    # Black
}

_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center")


def write_excel(path: str | Path, my_champs: list[str], lane_champs: list[str], matchups: list[Matchup],
                patch_label: str | None = None) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        path = path.with_name(path.name + ".xlsx")

    lookup = {(m.champ_playing, m.champ_against): m for m in matchups}

    wb = Workbook()
    ws = wb.active
    ws.title = "Matchups"

    # Row 1: MyChamp#1..n, row 2: "Champion Against" + my champs, then one row per opponent.
    ws.cell(row=2, column=1, value="Champion Against")
    if patch_label:
        ws.cell(row=1, column=1, value=f"Patch {patch_label}")
    for j, champ in enumerate(my_champs, start=2):
        ws.cell(row=1, column=j, value=f"MyChamp#{j - 1}")
        ws.cell(row=2, column=j, value=champ)

    for i, against in enumerate(sorted(lane_champs, key=str.lower), start=3):
        ws.cell(row=i, column=1, value=against)
        for j, playing in enumerate(my_champs, start=2):
            m = lookup.get((playing, against))
            cell = ws.cell(row=i, column=j, value=m.rating if m else NA)
            if m:
                cell.comment = Comment(f"{playing} vs {against}\n{m.win_rate:.2f}% WR\n{m.matches:,} matches", "u.gg", width=180, height=70)

    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            cell.border = _BORDER
            cell.alignment = _CENTER
            style = RATING_STYLES.get(str(cell.value)) if cell.row > 2 else None
            if style:
                fill, font = style
                cell.fill = PatternFill("solid", start_color=fill, end_color=fill)
                cell.font = Font(color=font, bold=True)
    for cell in ws[2]:
        cell.font = Font(bold=True)
    ws.cell(row=1, column=1).font = Font(bold=True, italic=True)

    _autofit(ws)
    ws.freeze_panes = "B3"  # keep the two header rows and the opponent column visible

    # ---- Raw data sheet
    raw = wb.create_sheet("Raw Data")
    raw.append(["My Champion", "Against", "Win Rate %", "Matches", "Rating"])
    for m in sorted(matchups, key=lambda m: (m.champ_playing, -m.win_rate)):
        raw.append([m.champ_playing, m.champ_against, m.win_rate, m.matches, m.rating])
    for cell in raw[1]:
        cell.font = Font(bold=True)
    raw.auto_filter.ref = raw.dimensions
    raw.freeze_panes = "A2"
    _autofit(raw)

    wb.save(path)
    return path.resolve()


def _autofit(ws) -> None:
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=4)
        ws.column_dimensions[get_column_letter(col[0].column)].width = width + 3
