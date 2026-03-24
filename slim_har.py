#!/usr/bin/env python3
import argparse
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import urlparse

KEEP_RESOURCE_TYPES = {"document", "xhr", "fetch"}
DROP_MIME_PREFIXES = (
    "image/",
    "font/",
    "audio/",
    "video/",
)
DROP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".otf",
    ".css", ".map",
    ".mp4", ".webm", ".mp3",
}
USEFUL_URL_PATTERNS = [
    r"/api/",
    r"graphql",
    r"search",
    r"listing",
    r"property",
    r"detail",
    r"media",
    r"parcel",
    r"map",
    r"tax",
    r"school",
    r"similar",
    r"traffic",
]
DROP_HOST_PATTERNS = [
    r"(^|\.)collector-px[^./]*\.",
    r"(^|\.)px-cloud\.net$",
    r"(^|\.)px-cdn\.net$",
    r"(^|\.)token\.awswaf\.com$",
    r"(^|\.)matomo\.har\.com$",
    r"(^|\.)har-beacon\.har\.com$",
    r"(^|\.)sidebar\.bugherd\.com$",
    r"(^|\.)www\.googletagmanager\.com$",
    r"(^|\.)www\.google-analytics\.com$",
    r"(^|\.)googleads\.g\.doubleclick\.net$",
    r"(^|\.)stats\.g\.doubleclick\.net$",
    r"(^|\.)connect\.facebook\.net$",
    r"(^|\.)www\.facebook\.com$",
    r"(^|\.)roomvo\.com$",
    r"google-analytics",
    r"googletagmanager",
    r"doubleclick",
    r"facebook\.com/tr",
    r"hotjar",
    r"segment",
    r"mixpanel",
    r"intercom",
    r"fullstory",
    r"sentry",
    r"nr-data",
    r"newrelic",
]

IMPORTANT_HTML_PATTERNS = [
    r"/homedetail/",
    r"/realestatepro/sold-by-agent/",
    r"/search/dosearch",
    r"/agent_",
    r"/office_listings_",
    r"/sold_by_",
]

IMPORTANT_API_PATTERNS = [
    r"/api/",
    r"/api/getTaxInfo/",
    r"/api/getCalculator/",
    r"/api/getTrafficReport/",
    r"/api/similar_listing",
    r"/api/neighborhood-section",
    r"/api/getSoundScore/",
    r"/api/homevalues/checkhistory/",
    r"/api/listing-highlight/",
]

IMPORTANT_GEO_HOSTS = {
    "dc1.spatialstream.com",
    "dc1.parcelstream.com",
    "parcelstream.com",
}

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
    "csrf-token",
}
SENSITIVE_QUERY_KEYS = {
    "token", "auth", "authorization", "session", "cookie", "csrf", "xsrf"
}

def lower_dict_headers(headers):
    out = []
    for h in headers or []:
        name = h.get("name", "")
        value = h.get("value", "")
        out.append({"name": name, "value": value})
    return out

def redact_headers(headers, keep_sensitive=False):
    redacted = []
    for h in headers or []:
        name = h.get("name", "")
        value = h.get("value", "")
        if not keep_sensitive and name.lower() in SENSITIVE_HEADER_NAMES:
            value = "[REDACTED]"
        redacted.append({"name": name, "value": value})
    return redacted

def redact_query(query_items):
    out = []
    for q in query_items or []:
        name = q.get("name", "")
        value = q.get("value", "")
        if name.lower() in SENSITIVE_QUERY_KEYS:
            value = "[REDACTED]"
        out.append({"name": name, "value": value})
    return out

def looks_useful_url(url):
    u = url.lower()
    return any(re.search(p, u) for p in USEFUL_URL_PATTERNS)


def path_of(url):
    try:
        return urlparse(url).path or ""
    except Exception:
        return ""


def host_of(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_important_html(url):
    u = url.lower()
    return any(re.search(p, u) for p in IMPORTANT_HTML_PATTERNS)

def is_har_html_document(url, mime, rtype):
    host = host_of(url)
    return host in {"www.har.com", "har.com"} and is_html_mime(mime) and rtype == "document"


def is_important_api(url):
    u = url.lower()
    return any(re.search(p, u) for p in IMPORTANT_API_PATTERNS)


def is_important_geo(url):
    return host_of(url) in IMPORTANT_GEO_HOSTS

def is_drop_host(url):
    host = urlparse(url).netloc.lower()
    return any(re.search(p, host) for p in DROP_HOST_PATTERNS)

def ext_from_url(url):
    path = path_of(url).lower()
    p = Path(posixpath.basename(path))
    return p.suffix


def is_html_mime(mime):
    mime = (mime or "").lower()
    return "text/html" in mime or "application/xhtml+xml" in mime


def is_json_like_mime(mime):
    mime = (mime or "").lower()
    return (
        "json" in mime
        or "javascript" in mime
        or "x-javascript" in mime
        or "ecmascript" in mime
        or "xml" in mime
        or "text/plain" in mime
    )

def mime_of(entry):
    return (
        entry.get("response", {})
        .get("content", {})
        .get("mimeType", "") or ""
    ).lower()

def resource_type_of(entry):
    return (entry.get("_resourceType", "") or "").lower()

def response_text(entry):
    return entry.get("response", {}).get("content", {}).get("text")

def response_size(entry):
    content = entry.get("response", {}).get("content", {})
    if "size" in content and isinstance(content["size"], int):
        return content["size"]
    return len(content.get("text", "") or "")

def truncate_text(text, body_cap):
    raw = text.encode("utf-8", errors="ignore")
    if len(raw) <= body_cap:
        return text, False

    head_bytes = body_cap // 2
    tail_bytes = body_cap - head_bytes
    head = raw[:head_bytes].decode("utf-8", errors="ignore")
    tail = raw[-tail_bytes:].decode("utf-8", errors="ignore")
    return head + "\n<!-- TRUNCATED -->\n" + tail, True

def keep_entry(entry, mode):
    req = entry.get("request", {})
    url = req.get("url", "")
    mime = mime_of(entry)
    rtype = resource_type_of(entry)
    ext = ext_from_url(url)

    if is_important_html(url):
        return True
    if is_important_api(url):
        return True
    if is_important_geo(url):
        return True

    if is_drop_host(url):
        return False

    if mime.startswith(DROP_MIME_PREFIXES):
        return False

    if ext in DROP_EXTENSIONS:
        return False

    if rtype in KEEP_RESOURCE_TYPES:
        return True

    if is_json_like_mime(mime) or is_html_mime(mime):
        return True

    if looks_useful_url(url):
        return True

    if mode == "index-only":
        return True

    return False

def slim_entry(entry, mode, keep_sensitive=False, max_body_bytes=500_000):
    req = entry.get("request", {})
    res = entry.get("response", {})
    content = res.get("content", {}) or {}
    text = content.get("text")

    keep_body = False
    mime = mime_of(entry)
    url = req.get("url", "")
    rtype = resource_type_of(entry)
    body_cap = max_body_bytes

    if is_important_html(url):
        keep_body = True
        body_cap = max(max_body_bytes, 5_000_000)
    elif is_har_html_document(url, mime, rtype):
        keep_body = True
        body_cap = max(max_body_bytes, 3_000_000)
    elif is_important_api(url) or is_important_geo(url):
        keep_body = True
        body_cap = max(max_body_bytes, 2_000_000)

    if mode in {"share", "debug"}:
        if is_json_like_mime(mime) or is_html_mime(mime) or looks_useful_url(url):
            keep_body = True

    truncated = False
    if text and isinstance(text, str) and keep_body:
        text, truncated = truncate_text(text, body_cap)

    new_entry = {
        "startedDateTime": entry.get("startedDateTime"),
        "time": entry.get("time"),
        "_resourceType": entry.get("_resourceType"),
        "request": {
            "method": req.get("method"),
            "url": req.get("url"),
            "httpVersion": req.get("httpVersion"),
            "headers": redact_headers(req.get("headers"), keep_sensitive=keep_sensitive),
            "queryString": redact_query(req.get("queryString")),
            "cookies": [] if not keep_sensitive else req.get("cookies", []),
            "headersSize": req.get("headersSize"),
            "bodySize": req.get("bodySize"),
        },
        "response": {
            "status": res.get("status"),
            "statusText": res.get("statusText"),
            "httpVersion": res.get("httpVersion"),
            "headers": redact_headers(res.get("headers"), keep_sensitive=keep_sensitive),
            "cookies": [] if not keep_sensitive else res.get("cookies", []),
            "content": {
                "size": content.get("size"),
                "mimeType": content.get("mimeType"),
            },
            "redirectURL": res.get("redirectURL"),
            "headersSize": res.get("headersSize"),
            "bodySize": res.get("bodySize"),
        },
        "cache": entry.get("cache", {}),
        "timings": entry.get("timings", {}),
        "serverIPAddress": entry.get("serverIPAddress"),
        "connection": entry.get("connection"),
    }

    post_data = req.get("postData")
    if post_data:
        new_entry["request"]["postData"] = post_data
        if not keep_sensitive and isinstance(post_data.get("text"), str):
            txt = post_data["text"]
            txt = re.sub(r'(?i)(token|csrf|xsrf|authorization)=([^&\s]+)', r'\1=[REDACTED]', txt)
            new_entry["request"]["postData"]["text"] = txt

    if keep_body and text is not None:
        new_entry["response"]["content"]["text"] = text
        if truncated:
            new_entry["response"]["content"]["truncated"] = True
        if "encoding" in content:
            new_entry["response"]["content"]["encoding"] = content["encoding"]

    return new_entry

def build_index(entries):
    rows = []
    for i, e in enumerate(entries):
        req = e.get("request", {})
        res = e.get("response", {})
        content = res.get("content", {}) or {}
        url = req.get("url")
        rows.append({
            "entry_index": i,
            "startedDateTime": e.get("startedDateTime"),
            "resourceType": e.get("_resourceType"),
            "method": req.get("method"),
            "url": url,
            "status": res.get("status"),
            "mimeType": content.get("mimeType"),
            "bodySize": res.get("bodySize"),
            "contentSize": content.get("size"),
            "isImportantHtml": is_important_html(url or ""),
            "isImportantApi": is_important_api(url or ""),
            "isImportantGeo": is_important_geo(url or ""),
            "isHarHtmlDocument": is_har_html_document(
                url or "",
                content.get("mimeType") or "",
                e.get("_resourceType") or "",
            ),
            "hasBodyText": bool(content.get("text")),
            "isTruncated": bool(content.get("truncated")),
        })
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_har", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mode", choices=["share", "debug", "index-only"], default="share")
    ap.add_argument("--keep-sensitive", action="store_true")
    ap.add_argument("--max-body-bytes", type=int, default=750_000)
    args = ap.parse_args()

    data = json.loads(args.input_har.read_text(encoding="utf-8"))
    log = data.get("log", {})
    entries = log.get("entries", [])

    kept = [e for e in entries if keep_entry(e, args.mode)]
    slimmed = [
        slim_entry(
            e,
            mode=args.mode,
            keep_sensitive=args.keep_sensitive,
            max_body_bytes=args.max_body_bytes,
        )
        for e in kept
    ]

    out_data = {
        "log": {
            "version": log.get("version"),
            "creator": log.get("creator"),
            "browser": log.get("browser"),
            "pages": log.get("pages", []),
            "entries": slimmed,
        }
    }

    args.out.write_text(json.dumps(out_data, ensure_ascii=False), encoding="utf-8")

    index_path = args.out.with_suffix(".index.json")
    index_path.write_text(
        json.dumps(build_index(slimmed), indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"Input entries:  {len(entries)}")
    print(f"Kept entries:   {len(slimmed)}")
    print(f"Wrote HAR:      {args.out}")
    print(f"Wrote index:    {index_path}")

if __name__ == "__main__":
    main()
