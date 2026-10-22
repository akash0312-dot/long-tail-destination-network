"""MRSS feed connector (feed tier). We host one Media RSS feed per destination; CTV apps and video aggregators pull
it from GET /api/v1/feeds/{feed_key}.xml. publish = add an item, delete = remove it, status = is it in the feed.

State lives in object storage (feeds/<key>/items.json and the rendered feed.xml), never in the client's database.
Idempotency: the item guid is derived from the idempotency key, so a retry finds the item already in the feed.
"""
import json
import re
import threading
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from app.config import get_settings
from app.connectors.base import BaseConnector, key_marker
from app.connectors.schemas import ConnectionRef, DestinationRef, Rendition
from app.errors import AppError, ErrorCode
from app.storage import get_storage

MEDIA_NS = "http://search.yahoo.com/mrss/"
ET.register_namespace("media", MEDIA_NS)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def feed_url(feed_key: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/api/v1/feeds/{feed_key}.xml"


def _lock(feed_key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(feed_key, threading.Lock())


def render_feed(feed_key: str, title: str, items: list[dict]) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = title
    ET.SubElement(ch, "link").text = feed_url(feed_key)
    ET.SubElement(ch, "description").text = f"Video feed {feed_key}"
    for it in items:
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = it["title"]
        ET.SubElement(item, "description").text = it["description"]
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = it["guid"]
        ET.SubElement(item, "pubDate").text = it["pub_date"]
        if it.get("link"):
            ET.SubElement(item, "link").text = it["link"]
        content = ET.SubElement(item, f"{{{MEDIA_NS}}}content",
                                {"url": it["video_url"], "type": it["video_type"], "medium": "video"})
        ET.SubElement(content, f"{{{MEDIA_NS}}}title").text = it["title"]
        if it.get("thumbnail"):
            ET.SubElement(item, f"{{{MEDIA_NS}}}thumbnail", {"url": it["thumbnail"]})
        for tag in it.get("tags", []):
            ET.SubElement(item, "category").text = tag
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


class Connector(BaseConnector):
    @staticmethod
    def _key(destination: DestinationRef | None, connection: ConnectionRef) -> str:
        key = (destination.params.get("feed_key") if destination else None) or connection.params.get("feed_key")
        if not key or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", key):
            raise AppError(ErrorCode.VALIDATION_ERROR,
                           "MRSS destination needs params.feed_key (lowercase letters, digits, - or _)")
        return key

    @staticmethod
    def _load(feed_key: str) -> list[dict]:
        try:
            return json.loads(get_storage().get_bytes(f"feeds/{feed_key}/items.json"))
        except Exception:
            return []

    @staticmethod
    def _save(feed_key: str, title: str, items: list[dict]) -> None:
        storage = get_storage()
        storage.put_bytes(f"feeds/{feed_key}/items.json", json.dumps(items).encode(), "application/json")
        storage.put_bytes(f"feeds/{feed_key}/feed.xml", render_feed(feed_key, title, items), "application/rss+xml")

    def publish(self, rendition: Rendition, destination: DestinationRef, connection: ConnectionRef,
                idempotency_key: str) -> dict:
        self.ensure_valid(rendition, destination, connection)
        key = self._key(destination, connection)
        guid = f"ltn-{key_marker(idempotency_key)}"
        url = f"{feed_url(key)}#{guid}"
        with _lock(key):
            items = self._load(key)
            if any(i["guid"] == guid for i in items):
                return {"platform_post_id": guid, "live_url": url, "status": "published",
                        "warnings": ["IDEMPOTENT_REPLAY: this item is already in the feed"]}
            video = next(m for m in rendition.media if m.type == "video")
            thumb = next((m for m in rendition.media if m.type == "image"), None)
            items.insert(0, {"guid": guid, "title": rendition.title, "description": rendition.body,
                             "link": rendition.canonical_url or rendition.link_url, "video_url": video.url,
                             "video_type": video.mime_type or "video/mp4", "thumbnail": thumb.url if thumb else None,
                             "tags": [t.lstrip("#") for t in rendition.tags],
                             "pub_date": format_datetime(datetime.now(timezone.utc))})
            self._save(key, destination.name or key, items)
        return {"platform_post_id": guid, "live_url": url, "status": "published", "warnings": []}

    def get_status(self, platform_post_id: str, connection: ConnectionRef,
                   destination: DestinationRef | None = None) -> dict:
        key = self._key(destination, connection)
        present = any(i["guid"] == platform_post_id for i in self._load(key))
        return {"status": "published", "live_url": f"{feed_url(key)}#{platform_post_id}"} if present \
            else {"status": "deleted", "live_url": None}

    def delete(self, platform_post_id: str, connection: ConnectionRef,
               destination: DestinationRef | None = None) -> dict:
        key = self._key(destination, connection)
        with _lock(key):
            items = self._load(key)
            remaining = [i for i in items if i["guid"] != platform_post_id]
            if len(remaining) != len(items):
                self._save(key, (destination.name if destination else None) or key, remaining)
        return {"deleted": True}
