"""Review the recommender's labelled set in a spreadsheet, then write the marks back into the JSON.

    python scripts/labelled_set_sheet.py export [--xlsx catalogue_data/labelled_set_review.xlsx]
    python scripts/labelled_set_sheet.py import [--xlsx ...] [--dry-run]

export: one sheet per content item listing every catalogue destination. Column A ("Relevant") holds the current
label and is the one to edit (Y = relevant); column B is the AI draft, kept for comparison.
import: reads column A of every item sheet, checks each value and destination name, and rewrites
catalogue_data/labelled_set.json: the new `relevant` lists, the AI draft (`ai_draft`, kept once), what changed per
item (`review`), and who reviewed it. Then re-score with  python scripts/eval_recommender.py
"""
import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "catalogue_data" / "labelled_set.json"
CATALOGUE = ROOT / "catalogue_data" / "destinations.csv"
XLSX = ROOT / "catalogue_data" / "labelled_set_review.xlsx"

GUIDE, SUMMARY = "How to review", "Summary"
FIRST_ROW = 9  # first destination row on an item sheet (header on row 8)
REVIEWER, REVIEW_DATE = "Reviewer name", "Date reviewed"
COLUMNS = [  # (header, catalogue field or None, width)
    ("Relevant (Y = yes)", None, 11), ("AI draft", None, 9), ("Changed", None, 9),
    ("Destination", "name", 34), ("Connector", "connector_key", 11), ("Category", "category", 13),
    ("Audience tags", "audience_tags", 18), ("Language", "language", 9),
    ("Self-promotion allowed", "self_promo_allowed", 12), ("Link policy", "link_policy", 11),
    ("Automation allowed", "automation_allowed", 12), ("Relevance (1-5)", "relevance_score", 10),
    ("Est. audience", "est_audience", 11), ("Description", "description", 70), ("URL", "url", 40),
    ("Your note (optional)", None, 40)]
NOTE_COL = len(COLUMNS)  # 16 = P

FONT = Font(name="Arial", size=10)
BOLD = Font(name="Arial", size=10, bold=True)
TITLE = Font(name="Arial", size=14, bold=True)
GREY = Font(name="Arial", size=10, color="808080")
LINK = Font(name="Arial", size=10, color="0563C1", underline="single")
EDIT = PatternFill("solid", fgColor="FFFF00")
HEAD = PatternFill("solid", fgColor="D9E1F2")
YES = PatternFill("solid", fgColor="C6EFCE")
CHANGED = PatternFill("solid", fgColor="FFC7CE")


# ---------------------------------------------------------------- the JSON file
def load_labels(path: Path = LABELS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_labels(data: dict) -> str:
    """Same layout as the hand-written file: one block per item, so a review shows up as a small diff."""
    def j(value):
        return json.dumps(value, ensure_ascii=False)
    out = ["{"] + [f"  {j(k)}: {j(v)}," for k, v in data.items() if k != "items"] + ['  "items": [']
    items = data["items"]
    for i, item in enumerate(items):
        out.append("    {" + ",\n     ".join(f"{j(k)}: {j(v)}" for k, v in item.items()) + "}"
                   + ("," if i < len(items) - 1 else ""))
    return "\n".join(out + ["  ]", "}"]) + "\n"


def load_catalogue(path: Path = CATALOGUE) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


def sheet_name(index: int, item_id: str) -> str:
    return f"{index:02d} {item_id}"[:31]


def details(content: dict) -> str:
    media = ", ".join(m.get("type", "file") for m in content.get("media", [])) or "none"
    return (f"Content type: {content.get('content_type', 'text')}  ·  Language: {content.get('language', '?')}  ·  "
            f"Promotional: {'yes' if content.get('is_promotional') else 'no'}  ·  "
            f"Link: {content.get('link_url') or 'none'}  ·  Media: {media}")


# ---------------------------------------------------------------- export
def _cell(ws, row, col, value, font=FONT, fill=None):
    c = ws.cell(row=row, column=col, value=value)
    c.font = font
    if fill:
        c.fill = fill
    return c


def _guide(ws, data: dict, example: dict):
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 26, 110
    _cell(ws, 1, 1, "Review the recommender's labelled set", TITLE)
    lines = [
        ("What this is", "10 sample content items. For each, mark the catalogue destinations you would really post it "
                         "to. Precision@10 is the share of the recommender's top 10 that you marked."),
        ("What to edit", "Column A ('Relevant') on each item sheet, the yellow cells: Y = relevant, N or blank = not. "
                         "It starts with the current labels. Column B is the AI draft, for comparison only. "
                         "Optional: a note per destination (column P) and per item (Summary sheet)."),
        ("Relevant means", "1) the topic and audience fit: that community would want this post; 2) its rules allow "
                           "it: a promotional item only counts where self-promotion is allowed (column I); 3) it can "
                           "carry the content type: a video item needs a destination that takes video."),
        ("Judge independently", "Decide from the item and the destination only. Don't look at what the recommender "
                                "ranked (test_pack/recommender_eval/report.md) while reviewing: adding a destination "
                                "because it was ranked high makes the score look better without the recommender "
                                "getting better."),
        ("Tips", "Use the filter arrows on row 8 (Connector, Category, Audience tags). Column C turns red where your "
                 "mark differs from the AI draft. The Summary sheet counts what you added and removed."),
        ("When done", "Fill in your name and the date below, save, close Excel, then run:  "
                      "python scripts/labelled_set_sheet.py import   and   python scripts/eval_recommender.py"),
        ("Currently in the JSON", f"labelled_by = {data.get('labelled_by')!r};  reviewed_by = "
                                  f"{data.get('reviewed_by')!r}. The import replaces both from the fields below."),
    ]
    row = 3
    for label, text in lines:
        _cell(ws, row, 1, label, BOLD)
        _cell(ws, row, 2, text).alignment = Alignment(wrap_text=True, vertical="top")
        row += 1
    row += 1
    _cell(ws, row, 1, REVIEWER, BOLD)
    _cell(ws, row, 2, None, fill=EDIT)
    _cell(ws, row + 1, 1, REVIEW_DATE, BOLD)
    _cell(ws, row + 1, 2, None, fill=EDIT)
    _cell(ws, row + 2, 2, "Date as YYYY-MM-DD, for example 2026-10-26.", GREY)
    row += 4
    _cell(ws, row, 1, "Example row", BOLD)
    _cell(ws, row, 2, "How one marked destination looks on an item sheet (columns A, B, C, D and P):", GREY)
    example_row = [("Relevant (Y = yes)", "Y"), ("AI draft", "Y"), ("Changed", "(empty: same as the draft)"),
                   ("Destination", example["name"]), ("Your note (optional)", "Main forum for fine-tuning questions")]
    for offset, (head, value) in enumerate(example_row, 1):
        _cell(ws, row + offset, 1, head, BOLD)
        _cell(ws, row + offset, 2, value, fill=EDIT if head.startswith(("Relevant", "Your")) else None)


def _item_sheet(ws, item: dict, catalogue: list[dict], draft: set[str], current: set[str]):
    last = FIRST_ROW + len(catalogue) - 1
    c = item["content"]
    for row, (label, value) in enumerate([("Item id", item["id"]), ("Title", c.get("title")),
                                          ("Body", c.get("body")), ("Tags", ", ".join(c.get("tags", []))),
                                          ("Details", details(c))], 1):
        _cell(ws, row, 1, label, BOLD)
        _cell(ws, row, 2, value, BOLD if row <= 2 else FONT)
    _cell(ws, 6, 1, "Marked Y", BOLD)
    _cell(ws, 6, 2, f'=COUNTIF($A${FIRST_ROW}:$A${last},"Y")', BOLD)
    _cell(ws, 6, 3, "Changed", BOLD)
    _cell(ws, 6, 4, f'=COUNTIF($C${FIRST_ROW}:$C${last},"changed")', BOLD)
    _cell(ws, 7, 1, "Edit column A only (yellow): Y = relevant, N or blank = not. Column B is the AI draft.", GREY)
    for col, (head, _field, width) in enumerate(COLUMNS, 1):
        _cell(ws, 8, col, head, BOLD, HEAD).alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions[ws.cell(row=8, column=col).column_letter].width = width
    ws.row_dimensions[8].height = 28
    for row, dest in enumerate(catalogue, FIRST_ROW):
        _cell(ws, row, 1, "Y" if dest["name"] in current else None, fill=EDIT)
        _cell(ws, row, 2, "Y" if dest["name"] in draft else None, GREY)
        _cell(ws, row, 3, f'=IF((UPPER(TRIM(A{row}))="Y")=(B{row}="Y"),"","changed")')
        for col, (_head, field, _width) in enumerate(COLUMNS[3:-1], 4):
            value = dest.get(field, "")
            if field in ("relevance_score", "est_audience") and str(value).isdigit():
                value = int(value)
            cell = _cell(ws, row, col, value)
            if field == "url" and value:
                cell.hyperlink, cell.font = value, LINK
        _cell(ws, row, NOTE_COL, None)
    rows = f"A{FIRST_ROW}:A{last}"
    dv = DataValidation(type="list", formula1='"Y,N"', allow_blank=True, showErrorMessage=True,
                        errorTitle="Y or N", error="Type Y (relevant), N, or leave it blank.")
    dv.add(rows)
    ws.add_data_validation(dv)
    ws.conditional_formatting.add(rows, CellIsRule(operator="equal", formula=['"Y"'], fill=YES))
    ws.conditional_formatting.add(f"C{FIRST_ROW}:C{last}",
                                  CellIsRule(operator="equal", formula=['"changed"'], fill=CHANGED))
    ws.freeze_panes = f"E{FIRST_ROW}"
    ws.auto_filter.ref = f"A8:{ws.cell(row=8, column=len(COLUMNS)).column_letter}{last}"


def export(labels: Path = LABELS, catalogue_path: Path = CATALOGUE, xlsx: Path = XLSX) -> Path:
    data, catalogue = load_labels(labels), load_catalogue(catalogue_path)
    wb = Workbook()
    wb._named_styles["Normal"].font = FONT  # Arial everywhere, including cells left empty
    guide = wb.active
    guide.title = GUIDE
    example = next((d for d in catalogue if d["name"] == "Hugging Face Forums"), catalogue[0])
    _guide(guide, data, example)
    summary = wb.create_sheet(SUMMARY)
    last = FIRST_ROW + len(catalogue) - 1
    heads = [("#", 5), ("Item id", 28), ("Sheet", 30), ("Title", 60), ("AI draft count", 10), ("Your count", 10),
             ("Added", 9), ("Removed", 9), ("Note for this item (optional)", 60)]
    _cell(summary, 1, 1, "Summary: what you changed per item", TITLE)
    for col, (head, width) in enumerate(heads, 1):
        _cell(summary, 3, col, head, BOLD, HEAD).alignment = Alignment(wrap_text=True, vertical="top")
        summary.column_dimensions[summary.cell(row=3, column=col).column_letter].width = width
    for i, item in enumerate(data["items"], 1):
        name = sheet_name(i, item["id"])
        _item_sheet(wb.create_sheet(name), item, catalogue, set(item.get("ai_draft", item["relevant"])),
                    set(item["relevant"]))
        a, b, row = f"'{name}'!$A${FIRST_ROW}:$A${last}", f"'{name}'!$B${FIRST_ROW}:$B${last}", 3 + i
        for col, value in enumerate([i, item["id"], name, item["content"].get("title"),
                                     f'=COUNTIF({b},"Y")', f'=COUNTIF({a},"Y")',
                                     f'=COUNTIFS({a},"Y",{b},"<>Y")', f'=COUNTIFS({b},"Y",{a},"<>Y")'], 1):
            _cell(summary, row, col, value)
        _cell(summary, row, 9, (item.get("review") or {}).get("note"), fill=EDIT)
    total = 4 + len(data["items"])
    _cell(summary, total, 4, "Total", BOLD)
    for col in "EFGH":
        _cell(summary, total, "EFGH".index(col) + 5, f"=SUM({col}4:{col}{total - 1})", BOLD)
    summary.freeze_panes = "A4"
    wb.calculation.fullCalcOnLoad = True  # Excel computes the counts on open
    xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx)
    return xlsx


# ---------------------------------------------------------------- import
def _text_date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip() if value is not None else ""


def read_review(xlsx: Path, data: dict, catalogue: list[dict]) -> tuple[dict, list[str]]:
    """Return {item_id: {"relevant": [...], "notes": {...}}, "reviewer": ..., "date": ..., "item_notes": ...} and
    a list of problems (wrong values, unknown names, missing sheets)."""
    wb = load_workbook(xlsx)
    names = {d["name"] for d in catalogue}
    problems: list[str] = []
    guide = {str(r[0].value).strip(): r[1].value for r in wb[GUIDE].iter_rows(min_col=1, max_col=2)
             if r[0].value}
    item_notes = {r[1].value: r[8].value for r in wb[SUMMARY].iter_rows(min_row=4) if r[1].value}
    marks: dict[str, dict] = {}
    for ws in wb.worksheets:
        if ws.title in (GUIDE, SUMMARY):
            continue
        item_id = ws["B1"].value
        relevant, notes = [], {}
        for row in ws.iter_rows(min_row=FIRST_ROW):
            mark, name, note = row[0].value, row[3].value, row[NOTE_COL - 1].value
            if not name:
                continue
            value = str(mark).strip().upper() if mark is not None else ""
            if value not in ("Y", "N", ""):
                problems.append(f"{ws.title}!A{row[0].row}: {mark!r} (use Y, N or leave it blank)")
            if name not in names:
                problems.append(f"{ws.title}!D{row[0].row}: {name!r} is not in the catalogue")
            if value == "Y":
                relevant.append(name)
            if note:
                notes[name] = str(note).strip()
        marks[item_id] = {"relevant": relevant, "notes": notes, "note": item_notes.get(item_id)}
    for item in data["items"]:
        if item["id"] not in marks:
            problems.append(f"no sheet for item {item['id']!r}")
    return {"items": marks, "reviewer": str(guide.get(REVIEWER) or "").strip(),
            "date": _text_date(guide.get(REVIEW_DATE))}, problems


def apply_review(data: dict, review: dict) -> dict:
    out = {k: v for k, v in data.items() if k != "items"}
    added_total = removed_total = 0
    items = []
    for item in data["items"]:
        marked = review["items"][item["id"]]
        before = item["relevant"]
        chosen = set(marked["relevant"])
        relevant = [n for n in before if n in chosen] + [n for n in marked["relevant"] if n not in before]
        draft = item.get("ai_draft", before)
        new = {k: v for k, v in item.items() if k not in ("relevant", "ai_draft", "review")}
        new["relevant"], new["ai_draft"] = relevant, draft
        new["review"] = {"added_vs_ai_draft": [n for n in relevant if n not in draft],
                         "removed_vs_ai_draft": [n for n in draft if n not in chosen]}
        if marked.get("note"):
            new["review"]["note"] = str(marked["note"]).strip()
        if marked["notes"]:
            new["review"]["destination_notes"] = marked["notes"]
        added_total += len(new["review"]["added_vs_ai_draft"])
        removed_total += len(new["review"]["removed_vs_ai_draft"])
        items.append(new)
    who, when = review["reviewer"], review["date"]
    out["labelled_by"] = (f"Drafted by the AI coding assistant (Claude); reviewed by {who} on {when}: "
                          f"{added_total} added and {removed_total} removed against the draft (see each item's "
                          f"`review`)")
    out["reviewed_by"] = f"{who}, {when}"
    out["items"] = items
    return out


def do_import(xlsx: Path = XLSX, labels: Path = LABELS, catalogue_path: Path = CATALOGUE,
              dry_run: bool = False) -> dict:
    data, catalogue = load_labels(labels), load_catalogue(catalogue_path)
    review, problems = read_review(xlsx, data, catalogue)
    if not review["reviewer"] or not review["date"]:
        problems.append(f"fill in '{REVIEWER}' and '{REVIEW_DATE}' on the '{GUIDE}' sheet")
    if problems and not dry_run:
        raise SystemExit("Nothing written. Fix these in the workbook:\n  " + "\n  ".join(problems))
    updated = apply_review(data, review)
    for item in updated["items"]:
        r = item["review"]
        print(f"  {item['id']:28} {len(item['relevant']):3} relevant   +{len(r['added_vs_ai_draft'])} "
              f"-{len(r['removed_vs_ai_draft'])} vs AI draft")
    if dry_run:
        print("dry run: nothing written" + ("; problems:\n  " + "\n  ".join(problems) if problems else ""))
    else:
        labels.write_text(dump_labels(updated), encoding="utf-8", newline="\n")
        print(f"wrote {labels}; now run  python scripts/eval_recommender.py")
    return updated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["export", "import"])
    ap.add_argument("--xlsx", type=Path, default=XLSX)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--catalogue", type=Path, default=CATALOGUE)
    ap.add_argument("--dry-run", action="store_true", help="import: show what would change, write nothing")
    args = ap.parse_args(argv)
    if args.command == "export":
        print(f"wrote {export(args.labels, args.catalogue, args.xlsx)}")
    else:
        do_import(args.xlsx, args.labels, args.catalogue, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
