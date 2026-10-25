"""The labelled-set review workbook: export, a person's marks, import back into the JSON."""
import json
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

from scripts import labelled_set_sheet as sheet

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "catalogue_data" / "labelled_set.json"


@pytest.fixture
def workbook(tmp_path):
    labels = tmp_path / "labelled_set.json"
    shutil.copy(LABELS, labels)
    xlsx = sheet.export(labels, sheet.CATALOGUE, tmp_path / "review.xlsx")
    return labels, xlsx


def sign(xlsx: Path, name="Test Reviewer", when="2026-10-26"):
    wb = load_workbook(xlsx)
    for row in wb[sheet.GUIDE].iter_rows(min_col=1, max_col=2):
        if row[0].value == sheet.REVIEWER:
            row[1].value = name
        if row[0].value == sheet.REVIEW_DATE:
            row[1].value = when
    wb.save(xlsx)


def test_file_layout_survives_a_round_trip():
    text = LABELS.read_text(encoding="utf-8")
    assert sheet.dump_labels(json.loads(text)) == text


def test_export_has_one_sheet_per_item_with_every_destination(workbook):
    labels, xlsx = workbook
    data, catalogue = sheet.load_labels(labels), sheet.load_catalogue()
    wb = load_workbook(xlsx)
    assert wb.sheetnames[:2] == [sheet.GUIDE, sheet.SUMMARY] and len(wb.sheetnames) == 2 + len(data["items"])
    ws = wb[sheet.sheet_name(1, data["items"][0]["id"])]
    rows = [r for r in ws.iter_rows(min_row=sheet.FIRST_ROW, values_only=True) if r[3]]
    assert [r[3] for r in rows] == [d["name"] for d in catalogue]
    assert {r[3] for r in rows if r[0] == "Y"} == set(data["items"][0]["relevant"])
    assert [r[0] for r in rows] == [r[1] for r in rows]  # before any edit, the marks equal the AI draft


def test_unchanged_workbook_keeps_every_label_and_records_the_review(workbook):
    labels, xlsx = workbook
    before = sheet.load_labels(labels)
    sign(xlsx)
    after = sheet.do_import(xlsx, labels)
    for old, new in zip(before["items"], after["items"]):
        assert new["relevant"] == old["relevant"] == new["ai_draft"]
        assert new["review"] == {"added_vs_ai_draft": [], "removed_vs_ai_draft": []}
    assert after["reviewed_by"] == "Test Reviewer, 2026-10-26"
    assert "0 added and 0 removed" in after["labelled_by"]
    assert json.loads(labels.read_text(encoding="utf-8")) == after


def test_marks_and_notes_are_written_back(workbook):
    labels, xlsx = workbook
    item = sheet.load_labels(labels)["items"][0]
    wb = load_workbook(xlsx)
    ws = wb[sheet.sheet_name(1, item["id"])]
    rows = list(ws.iter_rows(min_row=sheet.FIRST_ROW))
    dropped = next(r for r in rows if r[0].value == "Y")
    added = next(r for r in rows if r[0].value is None and r[3].value)
    dropped[0].value, added[0].value = "N", " y "
    added[sheet.NOTE_COL - 1].value = "Good fit"
    wb[sheet.SUMMARY]["I4"] = "Checked against each forum's rules"
    wb.save(xlsx)
    sign(xlsx)
    new = sheet.do_import(xlsx, labels)["items"][0]
    assert dropped[3].value not in new["relevant"] and new["relevant"][-1] == added[3].value
    assert new["review"] == {"added_vs_ai_draft": [added[3].value], "removed_vs_ai_draft": [dropped[3].value],
                             "note": "Checked against each forum's rules",
                             "destination_notes": {added[3].value: "Good fit"}}
    assert new["ai_draft"] == item["relevant"]  # the draft is kept, not overwritten by the review


def test_bad_values_unknown_names_and_missing_signature_write_nothing(workbook):
    labels, xlsx = workbook
    original = labels.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="Reviewer name"):
        sheet.do_import(xlsx, labels)
    wb = load_workbook(xlsx)
    ws = wb.worksheets[2]
    ws.cell(row=sheet.FIRST_ROW, column=1).value = "maybe"
    ws.cell(row=sheet.FIRST_ROW + 1, column=4).value = "No Such Forum"
    wb.save(xlsx)
    sign(xlsx)
    with pytest.raises(SystemExit) as err:
        sheet.do_import(xlsx, labels)
    assert f"!A{sheet.FIRST_ROW}: 'maybe'" in str(err.value) and "'No Such Forum' is not in the catalogue" in str(err.value)
    assert labels.read_text(encoding="utf-8") == original
