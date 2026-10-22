"""Measure recommender precision@10 on the labelled set against the real catalogue.

    python scripts/eval_recommender.py [catalogue.csv] [labelled_set.json] [out_dir]

Offline and deterministic: the catalogue rows go straight to app.recommender.rank (no database, no network).
The catalogue is not certified yet, so certification is ignored here; everything else (formats, rules) applies.
"""
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.catalogue.importer import _bool, _cap, _links  # noqa: E402
from app.connectors.registry import all_connectors  # noqa: E402
from app.recommender import as_dict, precision_at_k, rank  # noqa: E402

K = 10


def load_catalogue(path: Path) -> list[dict]:
    rows = []
    for i, r in enumerate(csv.DictReader(path.open(encoding="utf-8"))):
        errs: list = []
        rows.append({"id": f"c{i}", "name": r["name"], "url": r["url"], "connector_key": r["connector_key"],
                     "category": r["category"], "description": r["description"],
                     "audience_tags": [t for t in r["audience_tags"].split(";") if t], "language": r["language"],
                     "relevance_score": int(r["relevance_score"]), "frequency_cap": _cap(r["frequency_cap"], errs),
                     "link_policy": _links(r["link_policy"], errs),
                     "self_promo_allowed": _bool(r["self_promo_allowed"], "self_promo_allowed", errs),
                     "disclosure_required": _bool(r["disclosure_required"], "disclosure_required", errs),
                     "automation_allowed": _bool(r["automation_allowed"], "automation_allowed", errs),
                     "certification_status": "uncertified"})
    return rows


def main(catalogue: Path, labels: Path, out_dir: Path) -> float:
    dests = load_catalogue(catalogue)
    names = {d["name"] for d in dests}
    data = json.loads(labels.read_text(encoding="utf-8"))
    surfaces = {k: sorted({t for s in c.manifest.surfaces for t in s.content_types})
                for k, c in all_connectors().items()}
    results = []
    for item in data["items"]:
        unknown = set(item["relevant"]) - names
        if unknown:
            raise SystemExit(f"{item['id']}: labelled destinations not in the catalogue: {sorted(unknown)}")
        ranked = [c for c in rank(item["content"], dests, surfaces=surfaces, ignore_certification=True)
                  if not c.excluded]
        top = [c.destination["name"] for c in ranked[:K]]
        relevant = set(item["relevant"])
        results.append({"id": item["id"], "precision_at_10": precision_at_k(top, relevant, K),
                        "relevant_labelled": len(relevant), "hits": [n for n in top if n in relevant],
                        "misses_in_top10": [n for n in top if n not in relevant],
                        "top10": [as_dict(c) for c in ranked[:K]]})
    mean = sum(r["precision_at_10"] for r in results) / len(results)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"metric": "precision@10", "mean": round(mean, 3), "items": len(results),
               "catalogue_rows": len(dests), "labelled_by": data.get("labelled_by"),
               "reviewed_by": data.get("reviewed_by"), "results": results}
    (out_dir / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
    lines = [f"# Recommender precision@10: **{mean:.2f}** (mean over {len(results)} items)", "",
             f"Catalogue: {len(dests)} real destinations. Labels: {data.get('labelled_by')}. "
             f"Reviewed by: {data.get('reviewed_by') or 'NOT YET REVIEWED'}.", "",
             "| Item | P@10 | Hits in top 10 | Top-10 entries not labelled relevant |", "|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['precision_at_10']:.1f} | {len(r['hits'])} | {', '.join(r['misses_in_top10'])} |"
              for r in results]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"precision@10 = {mean:.3f} over {len(results)} items -> {out_dir}")
    for r in results:
        print(f"  {r['id']:28} {r['precision_at_10']:.1f}   not relevant in top 10: {r['misses_in_top10']}")
    return mean


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "catalogue_data" / "destinations.csv",
         Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "catalogue_data" / "labelled_set.json",
         Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "test_pack" / "recommender_eval")
