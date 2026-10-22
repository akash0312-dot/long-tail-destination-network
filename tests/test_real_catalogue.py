"""Outstanding: the researched catalogue imports through the real importer, and the recommender evaluation runs.
Offline: reachability was checked during research (catalogue_data/RESEARCH.md); here it is switched off."""
import csv
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "catalogue_data" / "destinations.csv"


def test_catalogue_has_150_plus_relevant_researched_destinations():
    rows = list(csv.DictReader(CATALOGUE.open(encoding="utf-8")))
    relevant = [r for r in rows if int(r["relevance_score"]) >= 3]
    assert len(rows) >= 150 and len(relevant) >= 150
    for r in rows:
        assert r["connector_key"] and r["relevance_reason"] and r["verification"] and r["verified_at"]
        assert r["frequency_cap"] and r["link_policy"] and r["self_promo_allowed"] in ("true", "false")
        if r["permission_status"] == "needed":
            assert r["automation_allowed"] == "false", "no automated posting to a community without permission"
    assert not any(r["url"].split("/")[2].removeprefix("www.") in {"reddit.com", "youtube.com", "discord.com"}
                   for r in rows), "the 15 major platforms are out of scope"


def test_catalogue_imports_through_the_importer(client):
    r = client.post("/api/v1/catalogue/imports", headers={"Idempotency-Key": uuid.uuid4().hex},
                    files={"file": ("destinations.csv", CATALOGUE.read_bytes(), "text/csv")},
                    data={"check_reachability": "false", "score_relevance": "false"})
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job["error"]
    s = job["result"]["summary"]
    assert s["invalid"] == 0 and s["imported"] + s["existing"] >= 150, s


def test_recommender_evaluation_runs(tmp_path):
    from scripts.eval_recommender import main
    labels = ROOT / "catalogue_data" / "labelled_set.json"
    mean = main(CATALOGUE, labels, tmp_path)
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert 0 <= mean <= 1 and results["items"] == 10 and (tmp_path / "report.md").exists()
    assert results["labelled_by"].startswith("DRAFT"), "labels must stay marked as a draft until reviewed"
