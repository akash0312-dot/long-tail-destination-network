import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref")


def is_internal_host(host: str | None) -> bool:
    """SSRF guard for URLs a user hands us (catalogue rows, rules pages, CSV URLs): loopback, private, link-local
    and single-label hosts point into our own network."""
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".internal", ".local")) or "." not in host \
            and ":" not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


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
