"""Outstanding: the recommender ranks destinations by fit with a reason; precision@10 helper."""
import uuid
from datetime import datetime, timedelta, timezone

from app.recommender import as_dict, precision_at_k, rank

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
SURFACES = {"lemmy": ["text", "image", "video_link", "article"], "devto": ["article", "text"],
            "peertube": ["video", "video_link"], "assisted": ["text", "article", "image", "video", "video_link"]}


def dest(i, name, connector="lemmy", **kw):
    return {"id": f"d{i}", "name": name, "url": f"https://d{i}.example", "connector_key": connector,
            "certification_status": "certified", "relevance_score": 4, "language": "en", **kw}


CATALOGUE = [
    dest(1, "AI video creators", description="Creators using AI tools for video editing", audience_tags=["ai", "video"]),
    dest(2, "Video editing help", description="Editors discussing Premiere and DaVinci workflows"),
    dest(3, "SaaS founders", description="Founders building SaaS startups", audience_tags=["saas", "founders"]),
    dest(4, "Knitting circle", description="Yarn and patterns", relevance_score=1),
    dest(5, "No promo creators", description="Video creators", self_promo_allowed=False),
    dest(6, "Demoted video forum", description="Video creators", certification_status="failed"),
    dest(7, "Dev blog", "devto", description="AI and SaaS developer articles"),
    dest(8, "Tube for creators", "peertube", description="Video creators channel"),
]
CONTENT = {"title": "How we edit AI-assisted videos twice as fast", "body": "Our video editing workflow with AI tools.",
           "tags": ["video", "ai"], "content_type": "text", "language": "en"}


def test_ranks_by_topic_and_explains():
    ranked = rank(CONTENT, CATALOGUE, now=NOW, surfaces=SURFACES)
    names = [c.destination["name"] for c in ranked if not c.excluded]
    assert names[0] == "AI video creators", names
    assert names.index("Knitting circle") > names.index("Video editing help")
    top = as_dict(ranked[0])
    assert "audience match: AI, video" in top["reason"] and 0 < top["score"] <= 1
    assert set(top["components"]) == {"topic", "relevance", "past", "headroom"}


def test_exclusions_carry_reasons():
    ranked = {c.destination["name"]: c for c in rank({**CONTENT, "is_promotional": True}, CATALOGUE, now=NOW,
                                                     surfaces=SURFACES)}
    assert "rules" in ranked["No promo creators"].excluded and "self-promotion" in ranked["No promo creators"].excluded
    assert ranked["Demoted video forum"].excluded == "demoted after a failed certification"
    assert "cannot carry 'text'" in ranked["Tube for creators"].excluded


def test_video_content_goes_to_video_destinations():
    video = {**CONTENT, "content_type": "video"}
    ranked = {c.destination["name"]: c for c in rank(video, CATALOGUE, now=NOW, surfaces=SURFACES)}
    assert not ranked["Tube for creators"].excluded and "cannot carry 'video'" in ranked["Dev blog"].excluded


def test_uncertified_and_capped_destinations_rank_lower_with_reason():
    a = dest(10, "Video A", description="video creators")
    b = dest(11, "Video B", description="video creators", certification_status="uncertified")
    c = dest(12, "Video C", description="video creators", frequency_cap={"posts": 1, "per_days": 7})
    history = {"d12": [{"created_at": NOW - timedelta(days=1), "status": "published", "is_test": False}]}
    ranked = {x.destination["name"]: x for x in rank(CONTENT, [a, b, c], history, NOW, SURFACES)}
    assert ranked["Video A"].score > ranked["Video B"].score and "not certified yet" in ranked["Video B"].reasons
    assert ranked["Video A"].score > ranked["Video C"].score
    assert any(r.startswith("frequency cap reached") for r in ranked["Video C"].reasons)


def test_past_results_lift_a_destination():
    a, b = dest(20, "Video A", description="video creators"), dest(21, "Video B", description="video creators")
    history = {"d21": [{"created_at": NOW - timedelta(days=40), "status": "published", "is_test": False,
                        "metrics": {"likes": 50, "comments": 10, "views": 3000}}]}
    ranked = rank(CONTENT, [a, b], history, NOW, SURFACES)
    assert ranked[0].destination["name"] == "Video B" and "past posts average" in "; ".join(ranked[0].reasons)


def test_precision_at_k():
    assert precision_at_k(["a", "b", "c", "x"], {"a", "c"}, k=4) == 0.5


def test_recommendations_endpoint(client):
    url = f"https://video-{uuid.uuid4().hex[:6]}.example"
    client.post("/api/v1/destinations", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "name": f"AI video creators {url}", "url": url, "connector_key": "assisted",
        "description": "Creators using AI for video", "audience_tags": ["ai", "video"], "relevance_score": 5})
    r = client.post("/api/v1/recommendations", headers={"Idempotency-Key": uuid.uuid4().hex},
                    json={"content": CONTENT, "limit": 5, "include_excluded": True}).json()
    assert r["items"] and len(r["items"]) <= 5 and r["items"][0]["reason"]
    assert r["considered"] >= 1 and isinstance(r["excluded"], list)
