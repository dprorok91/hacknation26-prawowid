"""HTTP URL syntax and canonical resource identity."""

import re
from ipaddress import ip_address
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_NAMES = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def parse_http_url(value: str) -> SplitResult:
    if "\\" in value or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("invalid URL characters")
    parts = urlsplit(value)
    hostname = parts.hostname
    if (
        parts.scheme not in {"http", "https"}
        or not hostname
        or parts.username is not None
        or parts.port == 0
        or "%" in hostname
    ):
        raise ValueError("a valid HTTP(S) URL without credentials is required")
    try:
        ip_address(hostname)
    except ValueError:
        ascii_name = hostname.rstrip(".").encode("idna").decode("ascii")
        if len(ascii_name) > 253 or any(
            not DNS_LABEL.fullmatch(label) for label in ascii_name.lower().split(".")
        ):
            raise ValueError("invalid hostname") from None
    return parts


def canonical_url(value: str) -> str:
    parts = parse_http_url(value)
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii")
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None and parts.port != (443 if parts.scheme == "https" else 80):
        netloc += f":{parts.port}"
    # Preserve unknown query values and their order, since they can identify a resource.
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
        if not (key.lower().startswith("utm_") or key.lower() == "utm" or key.lower() in TRACKING_NAMES)
    ]
    return urlunsplit((parts.scheme, netloc, parts.path or "/", urlencode(query), ""))
