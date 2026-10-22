from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref")


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication: lowercase host without www, no default port, fragment,
    tracking params or trailing slash. 'https://WWW.Example.com:443/c/AI/?utm_source=x' -> 'https://example.com/c/ai'.
    Paths are lowercased because community slugs are case-insensitive on the supported platforms."""
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    p = urlsplit(raw)
    scheme = "https" if p.scheme in ("http", "https") else p.scheme.lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = p.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = p.path.rstrip("/").lower()
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(TRACKING_PREFIXES)))
    return urlunsplit((scheme, netloc, path, query, ""))
