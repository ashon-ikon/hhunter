"""Extract sale and rental listing artifacts from HAR.com agent and office pages."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from dateutil import parser as date_parser


LISTING_LINK_HINTS = (
    "/homedetail/",
    "/houston/",
    "/property/",
    "/sale_",
    "/mls/",
)

PRICE_PATTERNS = (
    re.compile(r"(?:sold|sale|closed?)\s+price[^$]*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"price[^$]*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\$([\d,]+(?:\.\d+)?)", re.I),
)
EXACT_PRICE_PATTERNS = (
    re.compile(r"(?:sold|sale)\s+price[^$]*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"rented[^$]*\$([\d,]+(?:\.\d+)?)", re.I),
)
PRICE_RANGE_PATTERNS = (
    re.compile(r"\$([\d,.]+)\s*([KM])?\s*-\s*\$([\d,.]+)\s*([KM])?", re.I),
    re.compile(r"\$([\d,.]+)\s*to\s*\$([\d,.]+)", re.I),
)
LISTED_PRICE_PATTERNS = (
    re.compile(r"listed\s+for\s+\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"list(?:ed)?\s+price[^$]*\$([\d,]+(?:\.\d+)?)", re.I),
)

DATE_PATTERNS = (
    re.compile(
        r"(?:sold|closed?)(?:\s+on|\s+date)?\s*:?\s*"
        r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})",
        re.I,
    ),
    re.compile(r"(\d{4}-\d{2}-\d{2})"),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"),
)

LOT_PATTERNS = (
    re.compile(
        r"(?:lot(?:\s+size)?|lotsize)\s*:?\s*"
        r"([\d,.]+)\s*(acres?|acre|sq\.?\s*ft|sqft|sf)",
        re.I,
    ),
    re.compile(r"([\d,.]+)\s*(acres?|acre|sq\.?\s*ft|sqft|sf)\s+lot", re.I),
    re.compile(r"([\d,.]+)\s*lot\s*(acres?|acre|sq\.?\s*ft\.?|sqft\.?|sf\.?)", re.I),
)

ZIP_PATTERN = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
JSON_SCRIPT_RE = re.compile(
    r"<script[^>]*type=[\"']application/(?:ld\+json|json)[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)
URL_ZIP_RE = re.compile(r"-tx-(\d{5})(?:[/?-]|$)", re.I)
BEDS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:beds?|bedrooms?)\b", re.I)
FULL_BATHS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*full\s*baths?\b", re.I)
HALF_BATHS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*half\s*baths?\b", re.I)
STORIES_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*stor(?:y|ies)\b", re.I)
YEAR_BUILT_PATTERN = re.compile(r"(\d{4})\s*year\s*built\b", re.I)
SQFT_PATTERN = re.compile(r"(?<!lot\s)([\d,]+)\s*sqft\b", re.I)
LOT_SQFT_PATTERN = re.compile(r"([\d,]+)\s*lot\s*sqft\b", re.I)
PHOTO_COUNT_PATTERN = re.compile(r"(\d+)\s+photos?\b", re.I)
REPRESENTED_SIDE_PATTERN = re.compile(r"represented:\s*(buyer|seller)\b", re.I)
ADDRESS_PATTERN = re.compile(
    r"\b([0-9][0-9A-Za-z &'./-]+?,\s*[A-Za-z][A-Za-z .'-]+,\s*TX\s*\d{5})\b",
    re.I,
)
STYLE_MARKET_PATTERN = re.compile(
    r"([A-Za-z0-9/&,\- ]+?)\s+style\s+in\s+([A-Za-z0-9 '&/\-]+?)\s+in\s+([A-Za-z0-9 '&/\-]+?)\s*\(marketarea\)",
    re.I,
)
NEIGHBORHOOD_MARKET_PATTERN = re.compile(
    r"\bin\s+([A-Za-z0-9 '&/\-]+?)\s+in\s+([A-Za-z0-9 '&/\-]+?)\s*\(marketarea\)",
    re.I,
)


@dataclass(slots=True)
class ListingRecord:
    source_har: str
    target_url: str
    page_url: str
    listing_url: str
    sold_date: str | None
    sold_year: int | None
    price: int | None
    price_raw: str | None
    listed_price: int | None
    listed_price_raw: str | None
    price_band_low: int | None
    price_band_high: int | None
    lot_size: str | None
    lot_sqft: int | None
    zip_code: str | None
    address: str | None
    property_type: str | None
    represented_side: str | None
    beds: float | None
    full_baths: float | None
    half_baths: float | None
    building_sqft: int | None
    stories: float | None
    year_built: int | None
    style: str | None
    neighborhood: str | None
    market_area: str | None
    photo_count: int | None
    category: str
    extraction_mode: str


@dataclass(slots=True)
class TargetPage:
    source_har: str
    url: str
    headers: dict[str, str]
    embedded_html: str | None


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        self._href = attr_map.get("href")
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = normalize_space(" ".join(self._chunks))
        self.links.append((self._href, text))
        self._href = None
        self._chunks = []


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def strip_tags(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return normalize_space(text)


def parse_int_money(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"([\d,]+(?:\.\d+)?)", value.replace(",", ""))
    if not match:
        return None
    cleaned = match.group(1).strip()
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def parse_number_token(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int_token(value: str | None) -> int | None:
    number = parse_number_token(value)
    if number is None:
        return None
    return int(number)


def expand_km_number(value: str, suffix: str | None) -> int | None:
    number = parse_number_token(value)
    if number is None:
        return None
    if not suffix:
        return int(number)
    suffix = suffix.upper()
    if suffix == "K":
        return int(number * 1_000)
    if suffix == "M":
        return int(number * 1_000_000)
    return int(number)


def parse_date(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    try:
        parsed = date_parser.parse(value, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None, None
    return parsed.date().isoformat(), parsed.year


def parse_unix_date(value: object) -> tuple[str | None, int | None]:
    if value in (None, ""):
        return None, None
    try:
        number = int(float(str(value)))
    except ValueError:
        return parse_date(str(value))
    if number > 10_000_000_000:
        number //= 1000
    try:
        dt = datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None, None
    return dt.date().isoformat(), dt.year


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    normalized_query = urlencode(
        [(key, value) for key in sorted(query) for value in query[key]],
        doseq=True,
    )
    cleaned = parsed._replace(query=normalized_query, fragment="")
    return urlunparse(cleaned)


def listing_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.netloc.lower(), parsed.path


def listing_identity_matches(left: str, right: str) -> bool:
    left_host, left_path = listing_identity(left)
    right_host, right_path = listing_identity(right)
    if left_host != right_host:
        return False
    left_path = left_path.rstrip("/")
    right_path = right_path.rstrip("/")
    if not left_path or not right_path:
        return False
    return (
        left_path == right_path
        or left_path.startswith(right_path + "/")
        or right_path.startswith(left_path + "/")
    )


def set_query_param(url: str, name: str, value: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[name] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def request_headers_to_dict(request_obj: dict) -> dict[str, str]:
    blocked = {"host", "content-length", "connection", "cookie"}
    headers = request_obj.get("headers", [])
    output: dict[str, str] = {}
    if not isinstance(headers, list):
        return output
    for header in headers:
        name = str(header.get("name", "")).strip()
        value = str(header.get("value", "")).strip()
        if not name or not value or value == "[REDACTED]":
            continue
        if name.lower() in blocked:
            continue
        output[name] = value
    return output


def collect_har_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        if expanded.is_dir():
            files.extend(sorted(expanded.rglob("*.har")))
        elif expanded.suffix.lower() == ".har":
            files.append(expanded)
    return files


def is_target_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"www.har.com", "har.com"}:
        return False
    lower_path = parsed.path.lower()
    if "/realestatepro/sold-by-agent/" in lower_path:
        return True
    if "/search/dosearch" in lower_path:
        params = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
        if "soldoffice" in params or "soldagent" in params:
            return True
        if "all_status" in params and any(value.lower() == "closd" for value in params["all_status"]):
            return True
    return False


def is_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc.lower() not in {"www.har.com", "har.com"}:
        return False
    lower_path = parsed.path.lower()
    return any(hint in lower_path for hint in LISTING_LINK_HINTS)


def discover_targets(har_paths: list[Path]) -> list[TargetPage]:
    targets: list[TargetPage] = []
    seen: set[str] = set()

    for har_path in har_paths:
        raw_text = har_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        for entry in data.get("log", {}).get("entries", []):
            request_obj = entry.get("request", {})
            response_obj = entry.get("response", {})
            url = str(request_obj.get("url", "")).strip()
            if not is_target_url(url):
                continue

            canonical = canonicalize_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)

            content = response_obj.get("content", {})
            body_text = content.get("text")
            embedded_html = body_text if isinstance(body_text, str) and body_text.strip() else None
            targets.append(
                TargetPage(
                    source_har=str(har_path),
                    url=url,
                    headers=request_headers_to_dict(request_obj),
                    embedded_html=embedded_html,
                )
            )
    return targets


def find_first(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return normalize_space(match.group(1))
    return None


def extract_lot_size(text: str) -> str | None:
    for pattern in LOT_PATTERNS:
        match = pattern.search(text)
        if match:
            unit = normalize_space(match.group(2)).rstrip(".").lower()
            if unit in {"sq ft", "sqft", "sf"}:
                unit = "sqft"
            elif unit in {"acre", "acres"}:
                unit = "acres" if unit == "acres" else "acre"
            return normalize_space(f"{match.group(1)} {unit}")
    return None


def extract_zip(text: str) -> str | None:
    match = ZIP_PATTERN.search(text)
    return match.group(1) if match else None


def extract_zip_from_url(url: str) -> str | None:
    match = URL_ZIP_RE.search(url)
    return match.group(1) if match else None


def parse_price_range(text: str) -> tuple[int | None, int | None]:
    for pattern in PRICE_RANGE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if len(match.groups()) == 4:
            low = expand_km_number(match.group(1), match.group(2))
            high = expand_km_number(match.group(3), match.group(4))
        else:
            low = parse_int_money(match.group(1))
            high = parse_int_money(match.group(2))
        return low, high
    return None, None


def first_group_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    return parse_int_token(match.group(1))


def first_group_float(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    return parse_number_token(match.group(1))


def normalize_property_type(text: str) -> str | None:
    direct_match = re.search(
        r"\b(Single-Family|Lots|Multi-Family\s*-\s*[A-Za-z]+(?:\s+[A-Za-z]+)*|Townhouse(?:/Condo)?|Condo(?:minium)?|Mid/High-Rise Condo)\b",
        text,
        re.I,
    )
    if direct_match:
        value = normalize_space(direct_match.group(1))
        return re.split(r"\s+In\s+", value, maxsplit=1)[0].strip()

    lines = [normalize_space(line) for line in text.splitlines()]
    for line in lines:
        if not line:
            continue
        lowered = line.lower()
        if "represented:" in lowered or "listed for" in lowered or "sold:" in lowered:
            continue
        if "beds" in lowered or "baths" in lowered or "sqft" in lowered:
            continue
        if "(marketarea)" in lowered:
            continue
        if "style in" in lowered:
            continue
        if line in {"Sold", "Buyer", "Seller", "Select"}:
            continue
        if any(token in line for token in ("Single-Family", "Lots", "Multi-Family", "Townhouse", "Condo")):
            return line
    return None


def extract_card_metadata(html: str, text: str) -> dict[str, object]:
    metadata: dict[str, object] = {}

    represented = REPRESENTED_SIDE_PATTERN.search(text)
    if represented:
        metadata["represented_side"] = represented.group(1).title()

    listed_raw = find_first(LISTED_PRICE_PATTERNS, text)
    if listed_raw:
        metadata["listed_price_raw"] = listed_raw
        metadata["listed_price"] = parse_int_money(listed_raw)

    band_low, band_high = parse_price_range(text)
    if band_low is not None:
        metadata["price_band_low"] = band_low
    if band_high is not None:
        metadata["price_band_high"] = band_high

    property_type = normalize_property_type(text)
    if property_type:
        metadata["property_type"] = property_type

    metadata["beds"] = first_group_float(BEDS_PATTERN, text)
    metadata["full_baths"] = first_group_float(FULL_BATHS_PATTERN, text)
    metadata["half_baths"] = first_group_float(HALF_BATHS_PATTERN, text)
    metadata["stories"] = first_group_float(STORIES_PATTERN, text)
    metadata["year_built"] = first_group_int(YEAR_BUILT_PATTERN, text)
    metadata["photo_count"] = first_group_int(PHOTO_COUNT_PATTERN, text)

    lot_sqft = first_group_int(LOT_SQFT_PATTERN, text)
    if lot_sqft is not None:
        metadata["lot_sqft"] = lot_sqft

    building_sqft = None
    for match in SQFT_PATTERN.finditer(text):
        candidate = parse_int_token(match.group(1))
        if candidate is None:
            continue
        if lot_sqft is not None and candidate == lot_sqft:
            continue
        building_sqft = candidate
        break
    if building_sqft is not None:
        metadata["building_sqft"] = building_sqft

    address_match = re.search(r"([0-9][^\n<]{2,})\s+([A-Za-z][A-Za-z .'-]+,\s*TX\s*\d{5})", text, re.I)
    compact_address = ADDRESS_PATTERN.search(text)
    if compact_address:
        metadata["address"] = normalize_space(compact_address.group(1))
    elif address_match:
        metadata["address"] = normalize_space(f"{address_match.group(1)} {address_match.group(2)}")

    style_match = STYLE_MARKET_PATTERN.search(text)
    if style_match:
        metadata["style"] = normalize_space(style_match.group(1))
        metadata["neighborhood"] = normalize_space(style_match.group(2))
        metadata["market_area"] = normalize_space(style_match.group(3))
    else:
        neighborhood_match = NEIGHBORHOOD_MARKET_PATTERN.search(text)
        if neighborhood_match:
            metadata["neighborhood"] = normalize_space(neighborhood_match.group(1))
            metadata["market_area"] = normalize_space(neighborhood_match.group(2))

    return {key: value for key, value in metadata.items() if value not in (None, "")}


def derive_price_per_sqft(record: ListingRecord) -> float | None:
    if record.listed_price is None or record.building_sqft in (None, 0):
        return None
    return round(record.listed_price / record.building_sqft, 2)


def format_float(value: float | None) -> str:
    if value is None:
        return ""
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def combine_address(name: str | None, address: dict | None) -> str | None:
    base_name = normalize_space(name or "")
    if not isinstance(address, dict):
        return base_name or None

    street = normalize_space(str(address.get("streetAddress") or ""))
    locality = normalize_space(str(address.get("addressLocality") or ""))
    region = normalize_space(str(address.get("addressRegion") or ""))
    postal_code = normalize_space(str(address.get("postalCode") or ""))

    primary = base_name or street
    primary_lower = primary.lower()

    if street and primary_lower == street.lower():
        street = ""
    if locality and locality.lower() in primary_lower:
        locality = ""
    if region and re.search(rf"\b{re.escape(region)}\b", primary, re.I):
        region = ""
    if postal_code and postal_code in primary:
        postal_code = ""

    locality_region = ", ".join(part for part in (locality, region) if part)
    if postal_code:
        locality_region = f"{locality_region} {postal_code}".strip() if locality_region else postal_code

    parts = [part for part in (primary, locality_region) if part]
    return ", ".join(parts) if parts else None


def style_from_description(description: str | None) -> tuple[str | None, str | None, str | None]:
    if not description:
        return None, None, None
    style_match = STYLE_MARKET_PATTERN.search(description)
    if style_match:
        return (
            normalize_space(style_match.group(1)),
            normalize_space(style_match.group(2)),
            normalize_space(style_match.group(3)),
        )
    neighborhood_match = NEIGHBORHOOD_MARKET_PATTERN.search(description)
    if neighborhood_match:
        return (
            None,
            normalize_space(neighborhood_match.group(1)),
            normalize_space(neighborhood_match.group(2)),
        )
    return None, None, None


def split_bath_total(total: float | None) -> tuple[float | None, float | None]:
    if total is None:
        return None, None
    full = int(total)
    half = 1.0 if abs(total - full - 0.5) < 0.001 else 0.0
    return float(full), half or None


def normalize_category(value: str | None) -> str:
    if not value:
        return "sale"
    lowered = value.lower()
    if "rent" in lowered or "rented" in lowered or "rental" in lowered or "lease" in lowered:
        return "rental"
    return "sale"


def address_needs_cleanup(address: str | None) -> bool:
    if not address:
        return True
    return (
        len(address) > 120
        or "- HAR.com" in address
        or "Buy/Rent" in address
        or "Listed for" in address
        or address.startswith("$")
        or " Photos " in f" {address} "
    )


def style_needs_cleanup(style: str | None) -> bool:
    if not style:
        return True
    return "Sold " in style or "Listed for" in style or style[:1].isdigit()


def category_from_text(text: str) -> str:
    lowered = text.lower()
    if "rented on" in lowered or "recently rented" in lowered or "property type rental" in lowered:
        return "rental"
    return "sale"


def candidate_anchor_matches(html: str) -> list[re.Match[str]]:
    anchor_re = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    matches: list[re.Match[str]] = []
    for match in anchor_re.finditer(html):
        href = match.group(1)
        if is_listing_url(href):
            matches.append(match)
    return matches


def records_from_anchor_blocks(
    html: str,
    *,
    base_url: str,
    target: TargetPage,
    extraction_mode: str,
) -> list[ListingRecord]:
    matches = candidate_anchor_matches(html)
    if not matches:
        return []

    records: list[ListingRecord] = []
    seen_urls: set[str] = set()

    for index, match in enumerate(matches):
        href = unescape(match.group(1))
        listing_url = urljoin(base_url, href)
        if listing_url in seen_urls:
            continue
        seen_urls.add(listing_url)

        snippet_before = html[max(0, match.start() - 1500) : match.start()]
        snippet_after = html[match.start() : min(len(html), match.start() + 6000)]
        text_after = strip_tags(snippet_after)
        context_text = strip_tags(f"{snippet_before} {snippet_after}")
        sold_raw = find_first(DATE_PATTERNS, text_after)
        sold_date, sold_year = parse_date(sold_raw)
        price_raw = find_first(EXACT_PRICE_PATTERNS, text_after)
        metadata = extract_card_metadata(f"{snippet_before} {snippet_after}", context_text)
        category = category_from_text(text_after)
        records.append(
            ListingRecord(
                source_har=target.source_har,
                target_url=target.url,
                page_url=base_url,
                listing_url=listing_url,
                sold_date=sold_date,
                sold_year=sold_year,
                price=parse_int_money(price_raw),
                price_raw=price_raw,
                listed_price=metadata.get("listed_price"),  # type: ignore[arg-type]
                listed_price_raw=metadata.get("listed_price_raw"),  # type: ignore[arg-type]
                price_band_low=metadata.get("price_band_low"),  # type: ignore[arg-type]
                price_band_high=metadata.get("price_band_high"),  # type: ignore[arg-type]
                lot_size=extract_lot_size(text_after),
                lot_sqft=metadata.get("lot_sqft"),  # type: ignore[arg-type]
                zip_code=extract_zip_from_url(listing_url) or extract_zip(text_after),
                address=metadata.get("address"),  # type: ignore[arg-type]
                property_type=metadata.get("property_type"),  # type: ignore[arg-type]
                represented_side=metadata.get("represented_side"),  # type: ignore[arg-type]
                beds=metadata.get("beds"),  # type: ignore[arg-type]
                full_baths=metadata.get("full_baths"),  # type: ignore[arg-type]
                half_baths=metadata.get("half_baths"),  # type: ignore[arg-type]
                building_sqft=metadata.get("building_sqft"),  # type: ignore[arg-type]
                stories=metadata.get("stories"),  # type: ignore[arg-type]
                year_built=metadata.get("year_built"),  # type: ignore[arg-type]
                style=metadata.get("style"),  # type: ignore[arg-type]
                neighborhood=metadata.get("neighborhood"),  # type: ignore[arg-type]
                market_area=metadata.get("market_area"),  # type: ignore[arg-type]
                photo_count=metadata.get("photo_count"),  # type: ignore[arg-type]
                category=category,
                extraction_mode=extraction_mode,
            )
        )

    return records


def iter_candidate_json_nodes(node: object) -> list[dict]:
    candidates: list[dict] = []
    stack: list[object] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "url" in current and any(key in current for key in ("price", "offers", "address")):
                candidates.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return candidates


def iter_flat_js_objects(html: str) -> list[dict]:
    decoder = json.JSONDecoder()
    keys = ('"property_url"', '"web_url"')
    seen_starts: set[int] = set()
    objects: list[dict] = []

    for key in keys:
        search_at = 0
        while True:
            key_index = html.find(key, search_at)
            if key_index == -1:
                break

            start = html.rfind("{", 0, key_index)
            while start != -1:
                if start in seen_starts:
                    break
                try:
                    candidate, _end = decoder.raw_decode(html[start:])
                except json.JSONDecodeError:
                    start = html.rfind("{", 0, start)
                    continue

                seen_starts.add(start)
                if isinstance(candidate, dict) and (
                    candidate.get("property_url") or candidate.get("web_url")
                ):
                    objects.append(candidate)
                    break
                start = html.rfind("{", 0, start)

            search_at = key_index + len(key)

    return objects


def normalize_lot_size_from_object(item: dict) -> str | None:
    acres = item.get("acres")
    lot_value = item.get("lotsize")
    lot_unit = str(item.get("lotsizeunit") or "").strip().lower()
    if lot_value not in (None, ""):
        if lot_unit.startswith("squar") or lot_unit in {"sf", "sqft", "sq ft"}:
            try:
                return f"{int(float(str(lot_value))):,} sqft"
            except ValueError:
                return f"{lot_value} sqft"
        if lot_unit.startswith("acre") and acres not in (None, "", 0, "0"):
            return f"{acres} acres"
        if lot_unit:
            return f"{lot_value} {lot_unit}"

    if acres not in (None, "", 0, "0"):
        return f"{acres} acres"
    return None


def extract_js_object_records(
    html: str,
    target: TargetPage,
    page_url: str,
    extraction_mode: str,
) -> list[ListingRecord]:
    records: list[ListingRecord] = []
    seen_urls: set[str] = set()

    for item in iter_flat_js_objects(html):
        relative_url = item.get("property_url") or item.get("web_url")
        if not relative_url:
            continue
        listing_url = urljoin(page_url, str(relative_url))
        if not is_listing_url(listing_url) or listing_url in seen_urls:
            continue
        seen_urls.add(listing_url)

        sold_date, sold_year = parse_unix_date(item.get("sdate"))
        if sold_date is None:
            for key in ("closedate", "closed_date", "sold_date"):
                if item.get(key):
                    sold_date, sold_year = parse_date(str(item[key]))
                    break

        price_raw = None
        sales_price = item.get("salesprice")
        if sales_price not in (None, "", 0, "0"):
            price_raw = str(sales_price)
        elif item.get("listprice"):
            price_raw = str(item["listprice"])

        full_address = normalize_space(str(item.get("fullstreetaddress") or ""))
        zip_code = (
            str(item.get("zip") or "").strip()
            or extract_zip_from_url(listing_url)
            or extract_zip(full_address)
        )
        zip_code = zip_code or None

        records.append(
            ListingRecord(
                source_har=target.source_har,
                target_url=target.url,
                page_url=page_url,
                listing_url=listing_url,
                sold_date=sold_date,
                sold_year=sold_year,
                price=parse_int_money(price_raw),
                price_raw=price_raw,
                listed_price=parse_int_money(price_raw),
                listed_price_raw=price_raw,
                price_band_low=None,
                price_band_high=None,
                lot_size=normalize_lot_size_from_object(item),
                lot_sqft=parse_int_token(str(item.get("lotsize"))) if item.get("lotsize") not in (None, "") else None,
                zip_code=zip_code,
                address=full_address or None,
                property_type=str(item.get("property_type") or "").strip() or None,
                represented_side=None,
                beds=parse_number_token(str(item.get("bedroom"))) if item.get("bedroom") not in (None, "") else None,
                full_baths=parse_number_token(str(item.get("bathfull"))) if item.get("bathfull") not in (None, "") else None,
                half_baths=parse_number_token(str(item.get("bathhalf"))) if item.get("bathhalf") not in (None, "") else None,
                building_sqft=parse_int_token(str(item.get("bldgsqft"))) if item.get("bldgsqft") not in (None, "") else None,
                stories=parse_number_token(str(item.get("stories"))) if item.get("stories") not in (None, "") else None,
                year_built=parse_int_token(str(item.get("yearbuilt"))) if item.get("yearbuilt") not in (None, "") else None,
                style=str(item.get("style") or "").strip() or None,
                neighborhood=str(item.get("subdivision") or "").strip() or None,
                market_area=str(item.get("marketarea") or "").strip() or None,
                photo_count=parse_int_token(str(item.get("photo_count"))) if item.get("photo_count") not in (None, "") else None,
                category="sale",
                extraction_mode=extraction_mode,
            )
        )

    return records


def extract_json_records(html: str, target: TargetPage, page_url: str, extraction_mode: str) -> list[ListingRecord]:
    records: list[ListingRecord] = []
    seen_urls: set[str] = set()

    for block in JSON_SCRIPT_RE.findall(html):
        try:
            payload = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in iter_candidate_json_nodes(payload):
            url_value = str(node.get("url", "")).strip()
            if not url_value:
                continue
            listing_url = urljoin(page_url, url_value)
            if not is_listing_url(listing_url) or listing_url in seen_urls:
                continue
            seen_urls.add(listing_url)

            sold_raw = None
            for key in ("soldDate", "dateSold", "availabilityStarts", "datePosted"):
                if node.get(key):
                    sold_raw = str(node[key])
                    break
            sold_date, sold_year = parse_date(sold_raw)

            price_raw = None
            if isinstance(node.get("offers"), dict):
                offer_price = node["offers"].get("price")
                if offer_price is not None:
                    price_raw = str(offer_price)
            elif node.get("price") is not None:
                price_raw = str(node["price"])

            zip_code = None
            address = node.get("address")
            if isinstance(address, dict):
                zip_code = str(address.get("postalCode") or "").strip() or None
            zip_code = zip_code or extract_zip_from_url(listing_url)

            lot_size = None
            if isinstance(node.get("lotSize"), dict):
                lot_value = node["lotSize"].get("value")
                lot_unit = node["lotSize"].get("unitText") or node["lotSize"].get("unitCode")
                if lot_value and lot_unit:
                    lot_size = f"{lot_value} {lot_unit}"

            records.append(
                ListingRecord(
                    source_har=target.source_har,
                    target_url=target.url,
                    page_url=page_url,
                    listing_url=listing_url,
                    sold_date=sold_date,
                    sold_year=sold_year,
                    price=parse_int_money(price_raw),
                    price_raw=price_raw,
                    listed_price=parse_int_money(price_raw),
                    listed_price_raw=price_raw,
                    price_band_low=None,
                    price_band_high=None,
                    lot_size=lot_size,
                    lot_sqft=parse_int_token(lot_size.split()[0]) if lot_size else None,
                    zip_code=zip_code,
                    address=None,
                    property_type=None,
                    represented_side=None,
                    beds=None,
                    full_baths=None,
                    half_baths=None,
                    building_sqft=None,
                    stories=None,
                    year_built=None,
                    style=None,
                    neighborhood=None,
                    market_area=None,
                    photo_count=None,
                    category="sale",
                    extraction_mode=extraction_mode,
                )
            )

    return records


def extract_detail_page_record(
    html: str,
    *,
    listing_url: str,
    target: TargetPage,
    extraction_mode: str,
) -> ListingRecord | None:
    sold_raw = None
    price_raw = None
    zip_code = extract_zip_from_url(listing_url)
    lot_size = None
    address = None
    property_type = None
    style = None
    neighborhood = None
    market_area = None
    building_sqft = None
    beds = None
    full_baths = None
    half_baths = None
    stories = None
    year_built = None
    category = "sale"

    for block in JSON_SCRIPT_RE.findall(html):
        try:
            payload = json.loads(block.strip())
        except json.JSONDecodeError:
            continue

        for node in iter_candidate_json_nodes(payload):
            url_value = str(node.get("url", "")).strip()
            if not url_value:
                continue
            candidate_url = urljoin(listing_url, url_value)
            if not listing_identity_matches(candidate_url, listing_url):
                continue

            subject_node = node
            item_offered = None
            if isinstance(node.get("offers"), dict) and isinstance(node["offers"].get("itemOffered"), dict):
                item_offered = node["offers"]["itemOffered"]

            address_data = subject_node.get("address")
            if not isinstance(address_data, dict) and isinstance(item_offered, dict):
                address_data = item_offered.get("address")
            if isinstance(address_data, dict) and address_data.get("postalCode"):
                zip_code = str(address_data["postalCode"]).strip() or zip_code
            structured_address = combine_address(
                subject_node.get("name") or (item_offered or {}).get("name"),
                address_data if isinstance(address_data, dict) else None,
            )
            if structured_address:
                address = structured_address

            if isinstance(node.get("offers"), dict) and node["offers"].get("price") is not None:
                price_raw = str(node["offers"]["price"])
            elif node.get("price") is not None:
                price_raw = str(node["price"])

            for key in ("soldDate", "dateSold", "availabilityStarts", "datePosted"):
                if node.get(key):
                    sold_raw = str(node[key])
                    break

            additional_lists = []
            if isinstance(subject_node.get("additionalProperty"), list):
                additional_lists.append(subject_node["additionalProperty"])
            if isinstance(item_offered, dict) and isinstance(item_offered.get("additionalProperty"), list):
                additional_lists.append(item_offered["additionalProperty"])
            for additional in additional_lists:
                for item in additional:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip().lower()
                    value = str(item.get("value", "")).strip()
                    if not value:
                        continue
                    if name == "lot size" and not lot_size:
                        lot_size = normalize_space(value.replace("sq ft", "sqft"))
                    elif name == "closed date" and not sold_raw:
                        sold_raw = value
                    elif name == "property type" and not property_type:
                        property_type = value
                    elif name == "architecture style" and not style:
                        style = value
                    elif name == "square footage" and building_sqft is None:
                        building_sqft = parse_int_token(value)
                    elif name == "year built" and year_built is None:
                        year_built = parse_int_token(value)
                    elif name == "stories" and stories is None:
                        stories = parse_number_token(value)
                    elif name == "subdivision" and not neighborhood:
                        neighborhood = value
                    elif name == "market area" and not market_area:
                        market_area = value
                    elif name == "listingtype" and not property_type and "rent" in value.lower():
                        property_type = "Rental"
                    elif name == "listingtype":
                        category = normalize_category(value)

            floor_size = subject_node.get("floorSize")
            if not isinstance(floor_size, dict) and isinstance(item_offered, dict):
                floor_size = item_offered.get("floorSize")
            if isinstance(floor_size, dict) and building_sqft is None:
                building_sqft = parse_int_token(str(floor_size.get("value")))

            bedrooms_value = subject_node.get("numberOfBedrooms")
            if bedrooms_value is None and isinstance(item_offered, dict):
                bedrooms_value = item_offered.get("numberOfBedrooms")
            if bedrooms_value is not None and beds is None:
                beds = parse_number_token(str(bedrooms_value))

            baths_value = subject_node.get("numberOfBathroomsTotal")
            if baths_value is None and isinstance(item_offered, dict):
                baths_value = item_offered.get("numberOfBathroomsTotal")
            if baths_value is not None and full_baths is None and half_baths is None:
                full_baths, half_baths = split_bath_total(parse_number_token(str(baths_value)))

            desc_style, desc_neighborhood, desc_market = style_from_description(str(node.get("description") or ""))
            style = style or desc_style
            neighborhood = neighborhood or desc_neighborhood
            market_area = market_area or desc_market
            category = normalize_category(property_type or category)

    text = strip_tags(html)
    metadata = extract_card_metadata(html, text)
    category = normalize_category(property_type or category or category_from_text(text))
    sold_date, sold_year = parse_date(sold_raw or find_first(DATE_PATTERNS, text))
    if not lot_size:
        lot_size = extract_lot_size(text)
    if not zip_code:
        zip_code = extract_zip(text)
    if not any([sold_date, price_raw, lot_size, zip_code]):
        return None

    return ListingRecord(
        source_har=target.source_har,
        target_url=target.url,
        page_url=listing_url,
        listing_url=listing_url,
        sold_date=sold_date,
        sold_year=sold_year,
        price=parse_int_money(price_raw),
        price_raw=price_raw,
        listed_price=metadata.get("listed_price") or parse_int_money(price_raw),  # type: ignore[arg-type]
        listed_price_raw=metadata.get("listed_price_raw") or price_raw,  # type: ignore[arg-type]
        price_band_low=metadata.get("price_band_low"),  # type: ignore[arg-type]
        price_band_high=metadata.get("price_band_high"),  # type: ignore[arg-type]
        lot_size=lot_size,
        lot_sqft=metadata.get("lot_sqft") or (parse_int_token(lot_size.split()[0]) if lot_size and not lot_size.startswith("0 ") else None),  # type: ignore[arg-type]
        zip_code=zip_code,
        address=address or metadata.get("address"),  # type: ignore[arg-type]
        property_type=property_type or metadata.get("property_type"),  # type: ignore[arg-type]
        represented_side=metadata.get("represented_side"),  # type: ignore[arg-type]
        beds=beds or metadata.get("beds"),  # type: ignore[arg-type]
        full_baths=full_baths or metadata.get("full_baths"),  # type: ignore[arg-type]
        half_baths=half_baths or metadata.get("half_baths"),  # type: ignore[arg-type]
        building_sqft=building_sqft or metadata.get("building_sqft"),  # type: ignore[arg-type]
        stories=stories or metadata.get("stories"),  # type: ignore[arg-type]
        year_built=year_built or metadata.get("year_built"),  # type: ignore[arg-type]
        style=style or metadata.get("style"),  # type: ignore[arg-type]
        neighborhood=neighborhood or metadata.get("neighborhood"),  # type: ignore[arg-type]
        market_area=market_area or metadata.get("market_area"),  # type: ignore[arg-type]
        photo_count=metadata.get("photo_count"),  # type: ignore[arg-type]
        category=category,
        extraction_mode=extraction_mode,
    )


def merge_record_values(primary: ListingRecord, secondary: ListingRecord) -> ListingRecord:
    sold_date = primary.sold_date or secondary.sold_date
    sold_year = primary.sold_year or secondary.sold_year
    if sold_date and sold_year is None:
        _date, sold_year = parse_date(sold_date)

    return ListingRecord(
        source_har=primary.source_har or secondary.source_har,
        target_url=primary.target_url or secondary.target_url,
        page_url=primary.page_url or secondary.page_url,
        listing_url=primary.listing_url or secondary.listing_url,
        sold_date=sold_date,
        sold_year=sold_year,
        price=primary.price if primary.price is not None else secondary.price,
        price_raw=primary.price_raw or secondary.price_raw,
        listed_price=primary.listed_price if primary.listed_price is not None else secondary.listed_price,
        listed_price_raw=primary.listed_price_raw or secondary.listed_price_raw,
        price_band_low=primary.price_band_low if primary.price_band_low is not None else secondary.price_band_low,
        price_band_high=primary.price_band_high if primary.price_band_high is not None else secondary.price_band_high,
        lot_size=primary.lot_size or secondary.lot_size,
        lot_sqft=primary.lot_sqft if primary.lot_sqft is not None else secondary.lot_sqft,
        zip_code=primary.zip_code or secondary.zip_code,
        address=secondary.address if address_needs_cleanup(primary.address) and secondary.address else (primary.address or secondary.address),
        property_type=secondary.property_type if (not primary.property_type or (primary.property_type == "Lots" and secondary.property_type == "Rental")) and secondary.property_type else (primary.property_type or secondary.property_type),
        represented_side=primary.represented_side or secondary.represented_side,
        beds=primary.beds if primary.beds is not None else secondary.beds,
        full_baths=primary.full_baths if primary.full_baths is not None else secondary.full_baths,
        half_baths=primary.half_baths if primary.half_baths is not None else secondary.half_baths,
        building_sqft=primary.building_sqft if primary.building_sqft is not None else secondary.building_sqft,
        stories=primary.stories if primary.stories is not None else secondary.stories,
        year_built=primary.year_built if primary.year_built is not None else secondary.year_built,
        style=secondary.style if style_needs_cleanup(primary.style) and secondary.style else (primary.style or secondary.style),
        neighborhood=primary.neighborhood or secondary.neighborhood,
        market_area=primary.market_area or secondary.market_area,
        photo_count=primary.photo_count if primary.photo_count is not None else secondary.photo_count,
        category=secondary.category if secondary.category == "rental" else normalize_category(primary.category or secondary.category),
        extraction_mode=primary.extraction_mode,
    )


def merge_record_lists(record_lists: list[list[ListingRecord]]) -> list[ListingRecord]:
    merged: dict[str, ListingRecord] = {}
    for records in record_lists:
        for record in records:
            current = merged.get(record.listing_url)
            if current is None:
                merged[record.listing_url] = record
            else:
                merged[record.listing_url] = merge_record_values(current, record)
    return list(merged.values())


def needs_detail_enrichment(record: ListingRecord) -> bool:
    return (
        record.sold_date is None
        or record.zip_code is None
        or record.lot_size is None
        or record.address is None
        or len(record.address or "") > 120
        or "- HAR.com" in (record.address or "")
        or "Buy/Rent" in (record.address or "")
        or " Photos " in f" {record.address or ''} "
        or (record.style is not None and "Sold " in record.style)
    )


def should_attempt_detail_enrichment(
    record: ListingRecord,
    desired_year: int | None,
    desired_zip: str | None,
    desired_category: str,
) -> bool:
    if not needs_detail_enrichment(record) and not (
        desired_category == "rental" and record.category != "rental"
    ):
        return False
    if desired_zip is not None and record.zip_code is not None and record.zip_code != desired_zip:
        return False
    if desired_year is not None and record.sold_year is not None and record.sold_year != desired_year:
        return False
    return True


def extract_listings_from_html(
    html: str,
    *,
    page_url: str,
    target: TargetPage,
    extraction_mode: str,
) -> list[ListingRecord]:
    js_records = extract_js_object_records(html, target, page_url, extraction_mode)
    json_records = extract_json_records(html, target, page_url, extraction_mode)
    anchor_records = records_from_anchor_blocks(
        html,
        base_url=page_url,
        target=target,
        extraction_mode=extraction_mode,
    )
    return merge_record_lists([js_records, json_records, anchor_records])


def discover_pagination_links(html: str, current_url: str) -> list[str]:
    parser = LinkCollector()
    parser.feed(html)

    current = urlparse(current_url)
    try:
        current_page = int(parse_qs(current.query).get("page", ["1"])[0])
    except ValueError:
        current_page = 1
    links: set[str] = set()

    for href, _text in parser.links:
        absolute = urljoin(current_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc.lower() != current.netloc.lower():
            continue
        if parsed.path != current.path:
            continue
        page_values = parse_qs(parsed.query).get("page", [])
        if not page_values:
            continue
        try:
            page_num = int(page_values[0])
        except ValueError:
            continue
        if page_num > current_page:
            links.add(canonicalize_url(absolute))

    next_page_guess = set_query_param(current_url, "page", str(current_page + 1))
    if "page=" not in current.query and current_page == 1:
        links.add(canonicalize_url(next_page_guess))

    return sorted(links)


def fetch_page(
    session: requests.Session,
    *,
    url: str,
    headers: dict[str, str],
    timeout: int,
    cookie_header: str | None,
) -> tuple[str | None, str | None]:
    request_headers = dict(headers)
    if cookie_header:
        request_headers["Cookie"] = cookie_header
    request_headers.setdefault(
        "User-Agent",
        "Mozilla/5.0 (X11; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0",
    )
    request_headers.setdefault(
        "Accept",
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    )

    try:
        response = session.get(url, headers=request_headers, timeout=timeout)
    except requests.RequestException as exc:
        return None, f"request_error:{type(exc).__name__}"

    if response.status_code >= 400:
        return None, f"http_{response.status_code}"
    return response.text, None


def dedupe_records(records: list[ListingRecord]) -> list[ListingRecord]:
    deduped: list[ListingRecord] = []
    seen: set[tuple[str, str, str | None]] = set()
    for record in records:
        key = (record.target_url, record.listing_url, record.sold_date)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def filter_records(records: list[ListingRecord], year: int | None, zip_code: str | None) -> list[ListingRecord]:
    filtered = records
    if year is not None:
        filtered = [record for record in filtered if record.sold_year == year]
    if zip_code is not None:
        filtered = [record for record in filtered if record.zip_code == zip_code]
    return filtered


def filter_records_since(records: list[ListingRecord], since_date: str | None) -> list[ListingRecord]:
    if since_date is None:
        return records
    return [record for record in records if record.sold_date is not None and record.sold_date >= since_date]


def filter_records_category(records: list[ListingRecord], category: str) -> list[ListingRecord]:
    if category == "all":
        return records
    return [record for record in records if record.category == category]


def format_price(value: int | None, raw: str | None) -> str:
    if value is not None:
        return f"${value:,}"
    if raw:
        return raw if raw.startswith("$") else f"${raw}"
    return ""


def format_price_range(low: int | None, high: int | None) -> str:
    if low is None and high is None:
        return ""
    if low is not None and high is not None:
        return f"${low:,}-${high:,}"
    if low is not None:
        return f"${low:,}"
    return f"${high:,}"


def format_baths(record: ListingRecord) -> str:
    full = format_float(record.full_baths)
    half = format_float(record.half_baths)
    if full and half:
        return f"{full}+{half}h"
    if full:
        return full
    if half:
        return f"{half}h"
    return ""


def render_markdown(records: list[ListingRecord], profile: str = "basic") -> str:
    if profile == "comp":
        lines = [
            "| URL | Sold Date | Category | ZIP | Type | Side | Listed | Band | Sqft | Lot | Beds | Baths | Yr | Stories | Neighborhood | Market Area | PPSF | Mode |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for record in records:
            ppsf = derive_price_per_sqft(record)
            lines.append(
                "| {url} | {sold_date} | {category} | {zip_code} | {property_type} | {represented_side} | {listed_price} | {band} | {sqft} | {lot} | {beds} | {baths} | {year_built} | {stories} | {neighborhood} | {market_area} | {ppsf} | {mode} |".format(
                    url=record.listing_url,
                    sold_date=record.sold_date or "",
                    category=record.category,
                    zip_code=record.zip_code or "",
                    property_type=record.property_type or "",
                    represented_side=record.represented_side or "",
                    listed_price=format_price(record.listed_price, record.listed_price_raw),
                    band=format_price_range(record.price_band_low, record.price_band_high),
                    sqft=f"{record.building_sqft:,}" if record.building_sqft is not None else "",
                    lot=record.lot_size or (f"{record.lot_sqft:,} sqft" if record.lot_sqft is not None else ""),
                    beds=format_float(record.beds),
                    baths=format_baths(record),
                    year_built=record.year_built or "",
                    stories=format_float(record.stories),
                    neighborhood=record.neighborhood or "",
                    market_area=record.market_area or "",
                    ppsf=f"{ppsf:,.2f}" if ppsf is not None else "",
                    mode=record.extraction_mode,
                )
            )
        return "\n".join(lines)

    lines = [
        "| URL | Sold Date | Category | Price | Lot Size | ZIP | Mode |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        lines.append(
            "| {url} | {sold_date} | {category} | {price} | {lot_size} | {zip_code} | {mode} |".format(
                url=record.listing_url,
                sold_date=record.sold_date or "",
                price=format_price(record.price, record.price_raw),
                category=record.category,
                lot_size=record.lot_size or "",
                zip_code=record.zip_code or "",
                mode=record.extraction_mode,
            )
        )
    return "\n".join(lines)


def csv_text(records: list[ListingRecord], delimiter: str = ",") -> str:
    from io import StringIO

    buffer = StringIO()
    fieldnames = [
        "listing_url",
        "sold_date",
        "sold_year",
        "price",
        "price_raw",
        "listed_price",
        "listed_price_raw",
        "price_band_low",
        "price_band_high",
        "lot_size",
        "lot_sqft",
        "zip_code",
        "address",
        "property_type",
        "represented_side",
        "beds",
        "full_baths",
        "half_baths",
        "building_sqft",
        "stories",
        "year_built",
        "style",
        "neighborhood",
        "market_area",
        "photo_count",
        "price_per_sqft",
        "category",
        "target_url",
        "page_url",
        "source_har",
        "extraction_mode",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, delimiter=delimiter)
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "listing_url": record.listing_url,
                "sold_date": record.sold_date or "",
                "sold_year": record.sold_year or "",
                "price": record.price or "",
                "price_raw": record.price_raw or "",
                "listed_price": record.listed_price or "",
                "listed_price_raw": record.listed_price_raw or "",
                "price_band_low": record.price_band_low or "",
                "price_band_high": record.price_band_high or "",
                "lot_size": record.lot_size or "",
                "lot_sqft": record.lot_sqft or "",
                "zip_code": record.zip_code or "",
                "address": record.address or "",
                "property_type": record.property_type or "",
                "represented_side": record.represented_side or "",
                "beds": format_float(record.beds),
                "full_baths": format_float(record.full_baths),
                "half_baths": format_float(record.half_baths),
                "building_sqft": record.building_sqft or "",
                "stories": format_float(record.stories),
                "year_built": record.year_built or "",
                "style": record.style or "",
                "neighborhood": record.neighborhood or "",
                "market_area": record.market_area or "",
                "photo_count": record.photo_count or "",
                "price_per_sqft": derive_price_per_sqft(record) or "",
                "category": record.category,
                "target_url": record.target_url,
                "page_url": record.page_url,
                "source_har": record.source_har,
                "extraction_mode": record.extraction_mode,
            }
        )
    return buffer.getvalue()


def render_output(records: list[ListingRecord], output_format: str, profile: str = "basic") -> str:
    if output_format == "json":
        return json.dumps([asdict(record) for record in records], indent=2)
    if output_format == "csv":
        return csv_text(records, delimiter=",")
    if output_format == "tsv":
        return csv_text(records, delimiter="\t")
    return render_markdown(records, profile=profile)


def collect_listings(
    targets: list[TargetPage],
    *,
    fetch_live: bool,
    max_pages: int,
    timeout: int,
    cookie_header: str | None,
    desired_year: int | None = None,
    desired_zip: str | None = None,
    desired_category: str = "sale",
) -> tuple[list[ListingRecord], list[str]]:
    records: list[ListingRecord] = []
    notes: list[str] = []
    session = requests.Session()

    def enrich_records(target: TargetPage, existing_records: list[ListingRecord]) -> list[ListingRecord]:
        if not fetch_live:
            return existing_records

        enriched: list[ListingRecord] = []
        for record in existing_records:
            if not should_attempt_detail_enrichment(record, desired_year, desired_zip, desired_category):
                enriched.append(record)
                continue

            html, error = fetch_page(
                session,
                url=record.listing_url,
                headers=target.headers,
                timeout=timeout,
                cookie_header=cookie_header,
            )
            if error:
                notes.append(f"{error}: {record.listing_url}")
                enriched.append(record)
                continue

            detail_record = extract_detail_page_record(
                html or "",
                listing_url=record.listing_url,
                target=target,
                extraction_mode="detail_fetch",
            )
            if detail_record is None:
                enriched.append(record)
                continue
            enriched.append(merge_record_values(record, detail_record))

        return enriched

    for target in targets:
        if target.embedded_html:
            embedded_records = extract_listings_from_html(
                    target.embedded_html,
                    page_url=target.url,
                    target=target,
                    extraction_mode="embedded",
                )
            records.extend(enrich_records(target, embedded_records))
            continue

        if not fetch_live:
            notes.append(f"no_embedded_html: {target.url}")
            continue

        queue = [canonicalize_url(target.url)]
        seen_pages: set[str] = set()

        while queue and len(seen_pages) < max_pages:
            page_url = queue.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            html, error = fetch_page(
                session,
                url=page_url,
                headers=target.headers,
                timeout=timeout,
                cookie_header=cookie_header,
            )
            if error:
                notes.append(f"{error}: {page_url}")
                continue

            page_records = extract_listings_from_html(
                html or "",
                page_url=page_url,
                target=target,
                extraction_mode="live_fetch",
            )
            if not page_records and "captcha" in (html or "").lower():
                notes.append(f"captcha_detected: {page_url}")
            records.extend(enrich_records(target, page_records))

            for next_link in discover_pagination_links(html or "", page_url):
                if next_link not in seen_pages and next_link not in queue:
                    queue.append(next_link)

    return dedupe_records(records), notes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract HAR.com sale and rental listing URLs, dates, prices, and lot sizes from "
            "agent and office pages discovered in one or more HAR files."
        )
    )
    parser.add_argument("inputs", nargs="+", help="HAR files or directories containing HAR files")
    time_filter_group = parser.add_mutually_exclusive_group()
    time_filter_group.add_argument("--year", type=int, help="Keep only records with this sold year")
    time_filter_group.add_argument(
        "--since",
        help="Keep only records sold on or after this date in YYYY-MM-DD format",
    )
    parser.add_argument("--zip", dest="zip_code", help="Keep only records with this ZIP code")
    parser.add_argument(
        "--category",
        choices=("sale", "rental", "all"),
        default="sale",
        help="Listing category filter; use rental to pull leased/rented listings",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "csv", "tsv", "json"),
        default="markdown",
        help="Output format",
    )
    parser.add_argument(
        "--profile",
        choices=("basic", "comp"),
        default="basic",
        help="Column profile for markdown output; CSV/TSV/JSON always include the full record",
    )
    parser.add_argument("--output", help="Write results to this file instead of stdout")
    parser.add_argument(
        "--embedded-only",
        action="store_true",
        help="Do not live-fetch target pages when response bodies are missing from the HAR",
    )
    parser.add_argument(
        "--cookie-header",
        help="Optional Cookie header to use for live fetches when HAR cookies were stripped",
    )
    parser.add_argument("--max-pages", type=int, default=10, help="Maximum pages to fetch per target")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds")
    parser.add_argument(
        "--show-targets",
        action="store_true",
        help="Print discovered HAR.com target pages to stderr",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.since is not None:
        try:
            parsed_since = datetime.strptime(args.since, "%Y-%m-%d").date().isoformat()
        except ValueError:
            parser.error("--since must be in YYYY-MM-DD format.")
    else:
        parsed_since = None

    har_files = collect_har_files([Path(value) for value in args.inputs])
    if not har_files:
        parser.error("No HAR files found in the provided inputs.")

    try:
        targets = discover_targets(har_files)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        parser.error(f"Failed to read HAR inputs: {exc}")

    if not targets:
        parser.error("No HAR.com sold search targets were found in the provided HAR files.")

    if args.show_targets:
        for target in targets:
            print(target.url, file=sys.stderr)

    records, notes = collect_listings(
        targets,
        fetch_live=not args.embedded_only,
        max_pages=args.max_pages,
        timeout=args.timeout,
        cookie_header=args.cookie_header,
        desired_year=args.year,
        desired_zip=args.zip_code,
        desired_category=args.category,
    )
    records = filter_records(records, args.year, args.zip_code)
    records = filter_records_since(records, parsed_since)
    records = filter_records_category(records, args.category)

    rendered = render_output(records, args.format, profile=args.profile)
    if args.output:
        Path(args.output).write_text(rendered + ("\n" if not rendered.endswith("\n") else ""), encoding="utf-8")
    else:
        if rendered:
            print(rendered)

    if notes:
        print("\n".join(f"note: {note}" for note in notes), file=sys.stderr)

    if not records:
        print(
            "note: no listing records extracted. For stripped HARs, rerun without "
            "--embedded-only and consider supplying --cookie-header from a live browser session.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
