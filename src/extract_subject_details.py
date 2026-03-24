"""Extract subject-level detail packets from HAR detail-page sessions."""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse


HAR_BASE_URL = "https://www.har.com"
REPLAY_PRIORITY = {
    "media_gallery": 1,
    "similar_sold": 2,
    "similar_sale": 3,
    "similar_rent": 4,
    "tax_info": 5,
    "calculator": 6,
    "traffic_report": 7,
    "sound_score": 8,
    "homevalues_history": 9,
}

DETAIL_ENDPOINT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("calculator", re.compile(r"/api/getCalculator/\d+", re.I)),
    ("tax_info", re.compile(r"/api/getTaxInfo/\d+", re.I)),
    ("traffic_report", re.compile(r"/api/getTrafficReport/\d+", re.I)),
    ("similar_sale", re.compile(r"/api/similar_listing\?type=sale", re.I)),
    ("similar_rent", re.compile(r"/api/similar_listing\?type=rent", re.I)),
    ("similar_sold", re.compile(r"/api/similar_listing\?type=sold", re.I)),
    ("media_gallery", re.compile(r"/api/getMediaGallery/\d+", re.I)),
    ("sound_score", re.compile(r"/api/getSoundScore/\d+", re.I)),
    ("homevalues_history", re.compile(r"/api/homevalues/checkhistory/\d+", re.I)),
    ("neighborhood_section", re.compile(r"/api/neighborhood-section", re.I)),
]

DISCOVERY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("calculator", re.compile(r"/api/getCalculator/[^\"'\s<]+", re.I)),
    ("tax_info", re.compile(r"/api/getTaxInfo/[^\"'\s<]+", re.I)),
    ("traffic_report", re.compile(r"/api/getTrafficReport/[^\"'\s<]+", re.I)),
    ("similar_sale", re.compile(r"/api/similar_listing\?type=sale[^\"'\s<]*", re.I)),
    ("similar_rent", re.compile(r"/api/similar_listing\?type=rent[^\"'\s<]*", re.I)),
    ("similar_sold", re.compile(r"/api/similar_listing\?type=sold[^\"'\s<]*", re.I)),
    ("media_gallery", re.compile(r"/api/getMediaGallery/[^\"'\s<]+", re.I)),
    ("sound_score", re.compile(r"/api/getSoundScore/[^\"'\s<]+", re.I)),
    ("homevalues_history", re.compile(r"/api/homevalues/checkhistory/[^\"'\s<]+", re.I)),
    ("neighborhood_section", re.compile(r"/api/neighborhood-section[^\"'\s<]*", re.I)),
]

SUBJECT_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "listing_id": re.compile(r'["\']?LISTINGID["\']?\s*[:=]\s*["\']?(\d+)', re.I),
    "mlsnum": re.compile(r'["\']?MLSNUM["\']?\s*[:=]\s*["\']?(\d+)', re.I),
    "har_page_id": re.compile(r'["\']?harid["\']?\s*[:=]\s*["\']?(\d+)', re.I),
    "lid": re.compile(r'["\']lid["\']\s*[:=]\s*["\']?(\d+)', re.I),
    "sid": re.compile(r'["\']sid["\']\s*[:=]\s*["\']?(\d+)', re.I),
}

LATITUDE_RE = re.compile(r'"latitude"\s*:\s*"?([0-9.-]+)', re.I)
LONGITUDE_RE = re.compile(r'"longitude"\s*:\s*"?([0-9.-]+)', re.I)
META_DESCRIPTION_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    re.I | re.S,
)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
REMARKS_RE = re.compile(
    r'<div[^>]+id=["\']remarksCollapse["\'][^>]*>(.*?)</div>',
    re.I | re.S,
)
VISIBLE_TEXT_BLOCK_RE = re.compile(
    r'<(?:div|p|span)[^>]*(?:remarks|description|about)[^>]*>(.*?)</(?:div|p|span)>',
    re.I | re.S,
)
CARD_RE = re.compile(
    r'(<div class="cardv2.*?data-lid=.*?</div>\s*</div>\s*</div>)',
    re.I | re.S,
)
SECTION_ITEM_RE = re.compile(
    r'<div class="col-md-4 col-6 mb-4">.*?<div class="font_weight--bold font_size--small_extra">\s*(.*?)\s*</div>.*?<div class="font_size--large text-break">\s*(.*?)\s*</div>',
    re.I | re.S,
)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--har", type=Path, required=True)
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Override output directory. Defaults to <snapshot>/subject-details/<har-stem> when --har is inside a snapshot.",
    )
    parser.add_argument("--addresses-file", type=Path)
    parser.add_argument("--candidate-csv", type=Path)
    return parser.parse_args()


def resolve_output_dir(har_path: Path, out_dir: Path | None) -> Path:
    if out_dir is not None:
        return out_dir.expanduser()

    parts = har_path.parts
    if "snapshots" in parts:
        snapshot_index = parts.index("snapshots") + 2
        if snapshot_index <= len(parts):
            snapshot_root = Path(*parts[:snapshot_index])
            return snapshot_root / "subject-details" / har_path.stem

    return har_path.parent / f"{har_path.stem}_detail"


def read_har(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def content_text(entry: dict) -> str:
    content = entry.get("response", {}).get("content", {}) or {}
    text = content.get("text")
    if not isinstance(text, str):
        return ""
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="ignore")
        except (ValueError, UnicodeDecodeError):
            return ""
    return text


def strip_tags(value: str) -> str:
    text = html.unescape(value or "").replace("##BR##", " ")
    return WHITESPACE_RE.sub(" ", TAG_RE.sub(" ", text)).strip()


def to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    digits = re.sub(r"[^\d.-]", "", str(value))
    if not digits:
        return None
    try:
        return int(float(digits))
    except ValueError:
        return None


def to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    digits = re.sub(r"[^\d.-]", "", str(value))
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def normalize_url(url: str) -> str:
    if not url:
        return ""
    resolved = urljoin(HAR_BASE_URL, html.unescape(url))
    parsed = urlparse(resolved)
    normalized = parsed._replace(fragment="").geturl()
    return normalized


def classify_endpoint(url: str) -> str:
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    lower = normalized.lower()
    if parsed.path.lower().startswith("/homedetail/"):
        return "detail_html"
    if "spatialstream.com/getbygeometry" in lower:
        return "parcel_geometry"
    if "parcelstream.com" in lower or "/api/dmp_auth_layers" in lower:
        return "parcel_auth"
    for kind, pattern in DETAIL_ENDPOINT_PATTERNS:
        if pattern.search(normalized):
            return kind
    return "other"


def parse_jsonld_blocks(html_text: str) -> list[dict | list]:
    blocks: list[dict | list] = []
    for match in JSONLD_RE.finditer(html_text):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blocks


def flatten_jsonld_objects(value: object) -> list[dict]:
    items: list[dict] = []
    if isinstance(value, dict):
        if isinstance(value.get("@graph"), list):
            for item in value["@graph"]:
                items.extend(flatten_jsonld_objects(item))
        else:
            items.append(value)
    elif isinstance(value, list):
        for item in value:
            items.extend(flatten_jsonld_objects(item))
    return items


def pick_property_jsonld(blocks: list[dict | list]) -> dict:
    candidates: list[dict] = []
    for block in blocks:
        for item in flatten_jsonld_objects(block):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "Organization" in types or "BreadcrumbList" in types:
                continue
            if any(key in item for key in ("floorSize", "numberOfRooms", "geo", "address")):
                candidates.append(item)
    return candidates[0] if candidates else {}


def first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text or "")
    if not match:
        return None
    return match.group(1)


def extract_subject_identifiers(url: str, html_text: str) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    parsed = urlparse(normalize_url(url))
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        identifiers["slug"] = path_parts[-2] if len(path_parts) >= 2 else path_parts[-1]
        if path_parts[-1].isdigit():
            identifiers["har_page_id"] = path_parts[-1]

    query = parse_qs(parsed.query)
    for key in ("lid", "sid", "mlsnum"):
        values = query.get(key)
        if values:
            identifiers[key] = values[0]

    for key, pattern in SUBJECT_ID_PATTERNS.items():
        if key not in identifiers:
            value = first_match(pattern, html_text)
            if value:
                identifiers[key] = value
    return identifiers


def extract_address_from_jsonld(property_jsonld: dict) -> dict[str, str]:
    address = property_jsonld.get("address")
    if not isinstance(address, dict):
        offers = property_jsonld.get("offers")
        if isinstance(offers, dict):
            item_offered = offers.get("itemOffered")
            if isinstance(item_offered, dict):
                address = item_offered.get("address")
    if not isinstance(address, dict):
        return {}
    return {
        "street": str(address.get("streetAddress", "")).strip(),
        "city": str(address.get("addressLocality", "")).strip(),
        "state": str(address.get("addressRegion", "")).strip(),
        "zip": str(address.get("postalCode", "")).strip(),
    }


def extract_property_value(property_jsonld: dict, name: str) -> str | None:
    offers = property_jsonld.get("offers")
    if not isinstance(offers, dict):
        return None
    item_offered = offers.get("itemOffered")
    if not isinstance(item_offered, dict):
        return None
    properties = item_offered.get("additionalProperty")
    if not isinstance(properties, list):
        return None
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        if str(prop.get("name", "")).strip().lower() == name.lower():
            value = str(prop.get("value", "")).strip()
            return value or None
    return None


def extract_property_values(property_jsonld: dict) -> dict[str, list[object]]:
    values: dict[str, list[object]] = {}
    offers = property_jsonld.get("offers")
    if not isinstance(offers, dict):
        return values
    item_offered = offers.get("itemOffered")
    if not isinstance(item_offered, dict):
        return values
    properties = item_offered.get("additionalProperty")
    if not isinstance(properties, list):
        return values
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip()
        if not name:
            continue
        values.setdefault(name, []).append(prop.get("value"))
    return values


def extract_remarks(html_text: str, property_jsonld: dict, summary: str) -> dict[str, str]:
    candidates: list[tuple[str, str]] = []
    direct_remarks = strip_tags(first_match(REMARKS_RE, html_text) or "")
    if direct_remarks:
        candidates.append(("remarks_section", direct_remarks))

    for match in VISIBLE_TEXT_BLOCK_RE.finditer(html_text):
        visible = strip_tags(match.group(1))
        if len(visible) >= 30:
            candidates.append(("visible_html_block", visible))
            break

    jsonld_description = strip_tags(str(property_jsonld.get("description", "") or ""))
    if jsonld_description:
        candidates.append(("jsonld_description", jsonld_description))

    if summary:
        candidates.append(("meta_description", summary))

    chosen_source = "missing"
    chosen_text = ""
    for source, text in candidates:
        if text:
            chosen_source = source
            chosen_text = text
            break

    return {
        "remarks_raw": chosen_text,
        "remarks_clean": chosen_text,
        "remarks_source": chosen_source,
    }


def extract_detail_page(url: str, html_text: str) -> dict:
    blocks = parse_jsonld_blocks(html_text)
    property_jsonld = pick_property_jsonld(blocks)
    property_values = extract_property_values(property_jsonld)
    title = strip_tags(first_match(TITLE_RE, html_text) or "")
    description = strip_tags(first_match(META_DESCRIPTION_RE, html_text) or "")
    address = extract_address_from_jsonld(property_jsonld)
    remarks = extract_remarks(html_text, property_jsonld, description)

    subject = {
        "title": title,
        "address": address.get("street") or title.replace(" - HAR.com", ""),
        "city": address.get("city"),
        "state": address.get("state"),
        "zip": address.get("zip"),
        "lat": to_float(first_match(LATITUDE_RE, html_text)),
        "lng": to_float(first_match(LONGITUDE_RE, html_text)),
    }
    offers = property_jsonld.get("offers")
    if isinstance(offers, dict):
        subject["list_price"] = to_int(offers.get("price"))

    if isinstance(property_jsonld.get("numberOfBedrooms"), (int, float, str)):
        subject["beds"] = to_int(property_jsonld.get("numberOfBedrooms"))
    if isinstance(property_jsonld.get("numberOfBathroomsTotal"), (int, float, str)):
        subject["baths"] = to_float(property_jsonld.get("numberOfBathroomsTotal"))
    if isinstance(property_jsonld.get("floorSize"), dict):
        subject["sqft"] = to_int(property_jsonld["floorSize"].get("value"))
    subject["county"] = extract_property_value(property_jsonld, "County")
    subject["subdivision"] = extract_property_value(property_jsonld, "Subdivision")
    subject["property_type"] = extract_property_value(property_jsonld, "Property Type")
    subject["year_built"] = to_int(extract_property_value(property_jsonld, "Year Built"))
    subject["acres"] = to_float(extract_property_value(property_jsonld, "Lot Size"))

    return {
        "subject": subject,
        "detail_page": {
            "summary": description,
            **remarks,
            "jsonld": property_jsonld,
            "property_values": property_values,
        },
    }


def discover_endpoints(html_text: str) -> dict[str, str]:
    discovered: dict[str, str] = {}
    for kind, pattern in DISCOVERY_PATTERNS:
        match = pattern.search(html_text)
        if match:
            discovered[kind] = normalize_url(match.group(0))
    return discovered


def build_response_index(entries: list[dict]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    by_url: dict[str, dict] = {}
    parcel_urls: dict[str, list[str]] = {"parcel_auth_urls": [], "parcel_geometry_urls": []}
    for entry_index, entry in enumerate(entries):
        entry["_entry_index"] = entry_index
        url = normalize_url(entry.get("request", {}).get("url", ""))
        if not url:
            continue
        kind = classify_endpoint(url)
        if kind in {"detail_html", "other"}:
            continue
        by_url[url] = entry
        if kind == "parcel_auth":
            parcel_urls["parcel_auth_urls"].append(url)
        if kind == "parcel_geometry":
            parcel_urls["parcel_geometry_urls"].append(url)
    return by_url, parcel_urls


def parse_currency(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def parse_query_params(url: str) -> dict[str, list[str]]:
    return {key: values for key, values in parse_qs(urlparse(url).query).items()}


def serialize_jsonish(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def address_from_har_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(normalize_url(url))
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[0] != "homedetail":
        return None
    slug = parts[1]
    words = slug.split("-")
    if len(words) >= 4 and re.fullmatch(r"\d{5}", words[-1]):
        zip_code = words[-1]
        state = words[-2].upper()
        city = words[-3].capitalize()
        street_words = words[:-3]
        street = " ".join(word.upper() if len(word) == 2 and word.isalpha() else word.capitalize() for word in street_words)
        return f"{street}, {city}, {state} {zip_code}"
    return " ".join(word.upper() if word.lower() == "tx" else word.capitalize() for word in words)


def extract_page_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = [part for part in urlparse(normalize_url(url)).path.split("/") if part]
    if parts and parts[-1].isdigit():
        return parts[-1]
    return None


def extract_comp_address(block: str, card_url: str | None) -> str | None:
    labels = re.findall(r'(?:aria-label|title)="([^"]+)"', block, re.I)
    for label in labels:
        cleaned = strip_tags(label).replace(" as favorite", "").strip()
        if "TX" in cleaned and not cleaned.lower().startswith("save "):
            return cleaned
    return address_from_har_url(card_url)


def split_comp_address(value: str | None) -> tuple[str | None, str | None, str | None, str | None]:
    if not value:
        return None, None, None, None
    if "," in value:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) >= 3:
            state_zip = parts[-1].split()
            if len(state_zip) >= 2:
                return parts[0], parts[1], state_zip[0], state_zip[1]
    match = re.match(r"(.+?)\s+([A-Za-z .'-]+)\s+([A-Z]{2})\s+(\d{5})$", value.strip())
    if not match:
        return value, None, None, None
    return match.group(1), match.group(2), match.group(3), match.group(4)


def extract_between(text: str, start_marker: str, end_markers: list[str], window: int = 12000) -> str:
    start = text.lower().find(start_marker.lower())
    if start == -1:
        return ""
    slice_text = text[start : start + window]
    end_positions = [slice_text.lower().find(marker.lower()) for marker in end_markers]
    end_positions = [pos for pos in end_positions if pos > 0]
    if end_positions:
        slice_text = slice_text[: min(end_positions)]
    return slice_text


def median_int(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def parse_tax_info(fragment: str) -> dict:
    points = []
    row_re = re.compile(r"\['(\d{4})',\s*([0-9]+)\s*,'([^']*)','([^']*)'\]", re.I)
    for year, market_value, display_value, tooltip in row_re.findall(fragment):
        points.append(
            {
                "year": year,
                "market_value": int(market_value),
                "display_value": display_value,
                "tooltip": tooltip,
            }
        )
    latest = points[-1] if points else None
    return {"history_points": points, "latest": latest}


def parse_calculator(fragment: str) -> dict:
    summary = strip_tags(fragment[:1200])
    prices = re.findall(r"\$[0-9][\d,]*", fragment)
    return {
        "title": "Estimate your mortgage payments" if "MortgageCalculatorTab" in fragment else None,
        "currency_mentions": prices[:8],
        "summary_excerpt": summary[:300],
    }


def parse_similar_listings(fragment: str) -> list[dict]:
    comps: list[dict] = []
    for block in CARD_RE.findall(fragment):
        url_match = re.search(r'href="(/homedetail/[^"]+)"', block, re.I)
        card_url = normalize_url(url_match.group(1)) if url_match else None
        address_text = extract_comp_address(block, card_url)
        street, city, state, zip_code = split_comp_address(address_text)
        prices = re.findall(r"\$[\d,]+", block)
        numeric_prices = [parse_currency(price) for price in prices]
        numeric_prices = [price for price in numeric_prices if price is not None]
        listed_for_match = re.search(r"Listed for \$([\d,]+)", block, re.I)
        if listed_for_match:
            chosen_price = parse_currency(listed_for_match.group(1))
        elif numeric_prices:
            chosen_price = max(numeric_prices)
        else:
            chosen_price = None
        beds_match = re.search(r"(\d+)\s*Bed", block, re.I)
        if not beds_match:
            beds_match = re.search(r"<span>(\d+)</span>\s*beds", block, re.I)
        baths_match = re.search(r"([\d.]+)\s*(?:full\s+)?Bath", block, re.I)
        if not baths_match:
            baths_match = re.search(r"<span>([\d.]+)</span>\s*full baths", block, re.I)
        sqft_match = re.search(r"([\d,]+)\s*Sqft", block, re.I)
        ppsf_match = re.search(r"\$([\d,]+)\s*/\s*Sqft", block, re.I)
        dom_match = re.search(r"(\d+)\s*Day(?:s)? on HAR", block, re.I)
        year_built_match = re.search(r"Built in\s*(\d{4})", block, re.I)
        distance_match = re.search(r"([\d.]+)\s*(?:mi|miles?)", block, re.I)
        status_match = re.search(r'<div class="label [^"]*"[^>]*title="([^"]+)"', block, re.I)
        comp = {
            "address": street or address_text,
            "city": city,
            "state": state,
            "zip": zip_code,
            "price": chosen_price,
            "beds": to_int(beds_match.group(1)) if beds_match else None,
            "baths": to_float(baths_match.group(1)) if baths_match else None,
            "sqft": parse_currency(sqft_match.group(1)) if sqft_match else None,
            "ppsf": parse_currency(ppsf_match.group(1)) if ppsf_match else None,
            "dom": int(dom_match.group(1)) if dom_match else None,
            "year_built": to_int(year_built_match.group(1)) if year_built_match else None,
            "distance": to_float(distance_match.group(1)) if distance_match else None,
            "status": strip_tags(status_match.group(1)) if status_match else None,
            "mlsnum": first_match(re.compile(r"[?&](?:lid|sid)=(\d+)", re.I), card_url or ""),
            "page_url": card_url,
            "page_id": extract_page_id_from_url(card_url),
            "source_type": "har_similar_listing",
            "price_mentions": prices[:4],
        }
        if not comp["page_url"] and not comp["price"]:
            continue
        if any(value is not None for value in comp.values()):
            comps.append(comp)
    price_values = [comp["price"] for comp in comps if isinstance(comp.get("price"), int)]
    ppsf_values = [comp["ppsf"] for comp in comps if isinstance(comp.get("ppsf"), int)]
    return {
        "comps": comps,
        "comp_count": len(comps),
        "median_price": median_int(price_values),
        "median_ppsf": median_int(ppsf_values),
    }


def parse_media_gallery(payload_text: str) -> dict:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {}
    photos = payload.get("photos")
    if not isinstance(photos, list):
        photos = payload.get("media")
    if not isinstance(photos, list):
        return {}
    parsed_photos = []
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        parsed_photos.append(
            {
                "media_id": photo.get("id") or photo.get("media_id"),
                "url": photo.get("url") or photo.get("src"),
                "url_xl": photo.get("url_xl") or photo.get("url_large"),
                "caption": photo.get("caption"),
                "order": photo.get("order"),
            }
        )
    return {
        "photo_count": len(parsed_photos),
        "video_count": len([item for item in parsed_photos if "video" in str(item.get("url") or "").lower()]),
        "photos": parsed_photos,
    }


def parse_sound_score(payload_text: str) -> dict:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {}
    html_fragment = payload.get("html", "")
    score_match = re.search(r'class=\\"cn_bignum\\"[^>]*>(\d+)<', html_fragment)
    label_match = re.search(r'class=\\"cn_bigtitle\\"[^>]*>([^<]+)<', html_fragment)
    return {
        "score": int(score_match.group(1)) if score_match else None,
        "label": html.unescape(label_match.group(1)).strip() if label_match else None,
    }


def parse_homevalues_history(payload_text: str) -> dict:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {}
    rows = payload.get("data", [])
    parsed_rows = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        parsed_rows.append(
            {
                "period": row[0],
                "black_knight_value": row[1],
                "corelogic_value": row[3],
            }
        )
    latest = parsed_rows[-1] if parsed_rows else None
    return {
        "point_count": len(parsed_rows),
        "latest": latest,
        "min_black_knight_value": min((row["black_knight_value"] for row in parsed_rows), default=None),
        "max_black_knight_value": max((row["black_knight_value"] for row in parsed_rows), default=None),
    }


def parse_parcel_geometry(entry: dict) -> dict:
    text = content_text(entry)
    locid_match = re.search(r'"LOCID"\s*:\s*"([^"]+)"', text)
    geometry_match = re.search(r'"GEOMETRY"\s*:\s*"([^"]+)"', text)
    geometry_wkt = html.unescape(geometry_match.group(1)) if geometry_match else None
    bounds = None
    centroid = None
    if geometry_wkt:
        coords = [
            (float(lng), float(lat))
            for lng, lat in re.findall(r"(-?\d+\.\d+)\s+(-?\d+\.\d+)", geometry_wkt)
        ]
        if coords:
            lngs = [coord[0] for coord in coords]
            lats = [coord[1] for coord in coords]
            bounds = {
                "min_lng": min(lngs),
                "max_lng": max(lngs),
                "min_lat": min(lats),
                "max_lat": max(lats),
            }
            centroid = {
                "lng": sum(lngs) / len(lngs),
                "lat": sum(lats) / len(lats),
            }
    return {
        "locid": locid_match.group(1) if locid_match else None,
        "geometry_wkt": geometry_wkt,
        "bounds": bounds,
        "centroid": centroid,
        "source_url": normalize_url(entry.get("request", {}).get("url", "")),
    }


def parse_school_cards(html_text: str) -> list[dict]:
    marker = 'Assigned schools</h3>'
    start = html_text.find(marker)
    if start == -1:
        return []
    section_html = html_text[start : start + 12000]
    schools = []
    for block in section_html.split('<div class="col-12 col-md-4">')[1:]:
        level_match = re.search(r'card--portrait_school__label mb-0">\s*(.*?)\s*</div>', block, re.I | re.S)
        name_match = re.search(r'<h3[^>]*>\s*(.*?)\s*</h3>', block, re.I | re.S)
        rating_block_match = re.search(r'<div class="pb-2">(.*?)</div>', block, re.I | re.S)
        grade_match = re.search(
            r'<div class="label label--grade[^"]*">\s*(.*?)\s*</div>\s*<span[^>]*>\s*(.*?)\s*</span>',
            block,
            re.I | re.S,
        )
        level = level_match.group(1) if level_match else ""
        name = name_match.group(1) if name_match else ""
        rating_block = rating_block_match.group(1) if rating_block_match else ""
        grade_letter = grade_match.group(1) if grade_match else ""
        grade_text = grade_match.group(2) if grade_match else ""
        rating = rating_block.count("stars_blue.svg")
        school = {
            "name": strip_tags(name),
            "level": strip_tags(level),
            "rating": rating if rating else None,
            "distance": None,
            "district": None,
            "assigned_flag": True,
            "grade_letter": strip_tags(grade_letter or ""),
            "grade_text": strip_tags(grade_text or ""),
        }
        if school["name"]:
            schools.append(school)
    return schools


def parse_room_entries(property_values: dict[str, list[object]]) -> list[dict]:
    rooms = []
    for name, values in property_values.items():
        if name.lower() not in {"bedroom", "utility room"}:
            continue
        for value in values:
            text = str(value)
            parts = [part.strip() for part in text.split(",")]
            dimensions = parts[0] if parts else ""
            level = parts[1] if len(parts) > 1 else None
            length = None
            width = None
            dim_match = re.match(r"(\d+)\s*x\s*(\d+)", dimensions, re.I)
            if dim_match:
                length = to_int(dim_match.group(1))
                width = to_int(dim_match.group(2))
            rooms.append(
                {
                    "name": name,
                    "level": level,
                    "dimensions": dimensions or None,
                    "length": length,
                    "width": width,
                    "notes": None if name.lower() == "bedroom" else text,
                }
            )
    return rooms


def extract_section_features(html_text: str, heading: str) -> dict[str, str]:
    end_markers = {
        "Home exterior": ["<a name=\"R_block\"></a>", "<h2 tabindex=\"0\">\n  \n                        Rooms"],
        "Home interior": ["<a name=\"P_block\"></a>", "<h2 tabindex=\"0\">\n  \n                                    Home interior", "<a name=\"S_block\"></a>"],
    }
    section_html = extract_between(
        html_text,
        heading,
        end_markers.get(heading, ["<a name=", "<h2 tabindex=\"0\">"]),
        window=10000,
    )
    if not section_html:
        return {}
    parsed = {}
    for label, value in SECTION_ITEM_RE.findall(section_html):
        clean_label = strip_tags(label)
        clean_value = strip_tags(value)
        if clean_label and clean_value:
            parsed[clean_label] = clean_value
    return parsed


def extract_feature_sections(
    html_text: str, property_values: dict[str, list[object]]
) -> tuple[dict[str, dict[str, str]], dict[str, bool]]:
    sections = {
        "interior": extract_section_features(html_text, "Home interior"),
        "exterior": extract_section_features(html_text, "Home exterior"),
    }
    normalized = {}
    for name, values in property_values.items():
        if not values:
            continue
        if len(values) == 1:
            normalized[name] = str(values[0])
        else:
            normalized[name] = ", ".join(str(value) for value in values)
    return sections, {
        **extract_feature_flags(" ".join(str(v) for v in normalized.values())),
        "culdesac_flag": "cul-de-sac" in str(normalized.get("Lot Description", "")).lower(),
    }


def extract_feature_flags(text: str) -> dict:
    lower = (text or "").lower()
    return {
        "pool_flag": "pool" in lower,
        "fireplace_flag": "fireplace" in lower,
        "fenced_yard_flag": "fenced" in lower,
        "covered_patio_flag": "patio" in lower,
        "corner_lot_flag": "corner lot" in lower,
    }


def module_source(entry: dict) -> dict:
    request = entry.get("request", {})
    response = entry.get("response", {})
    content = response.get("content", {}) or {}
    return {
        "entry_index": entry.get("_entry_index"),
        "url": normalize_url(request.get("url", "")),
        "status": response.get("status"),
        "content_type": content.get("mimeType"),
        "timestamp": entry.get("startedDateTime"),
    }


def module_parse_status(parsed: object) -> str:
    if parsed in ({}, [], None):
        return "empty"
    return "ok"


def parse_module(kind: str, entry: dict) -> dict:
    payload_text = content_text(entry)
    parsed: dict | list
    if kind == "tax_info":
        parsed = parse_tax_info(payload_text)
    elif kind == "calculator":
        parsed = parse_calculator(payload_text)
    elif kind in {"similar_sale", "similar_rent", "similar_sold"}:
        parsed = parse_similar_listings(payload_text)
    elif kind == "sound_score":
        parsed = parse_sound_score(payload_text)
    elif kind == "homevalues_history":
        parsed = parse_homevalues_history(payload_text)
    elif kind == "media_gallery":
        parsed = parse_media_gallery(payload_text)
    elif kind == "parcel_geometry":
        parsed = parse_parcel_geometry(entry)
    else:
        parsed = {
            "content_type": entry.get("response", {}).get("content", {}).get("mimeType"),
            "text_length": len(payload_text),
        }
    return {
        "parsed": parsed,
        "source": module_source(entry),
        "parse_status": module_parse_status(parsed),
    }


def detail_parse_status(detail_page: dict, modules: dict) -> tuple[str, str, int]:
    checks = [
        bool(detail_page.get("remarks_clean")),
        bool(detail_page.get("jsonld")),
        bool(detail_page.get("schools")),
        bool(detail_page.get("rooms")),
        any(detail_page.get("features_raw_sections", {}).values()),
        bool(modules.get("tax_info")),
        bool(modules.get("similar_sold")),
        bool(modules.get("media_gallery")),
        bool(modules.get("parcel_geometry")),
    ]
    score = sum(1 for item in checks if item)
    if score >= 7:
        return "ok", "high", score
    if score >= 4:
        return "partial", "medium", score
    return "partial", "low", score


def find_subject_parcel_geometry(
    subject: dict, parcel_urls: dict[str, list[str]], response_index: dict[str, dict]
) -> dict | None:
    lat = subject.get("lat")
    lng = subject.get("lng")
    if lat is None or lng is None:
        return None
    target = f"POINT({lng:.6f} {lat:.6f})"
    fallback_entry = None
    for url in parcel_urls["parcel_geometry_urls"]:
        if target in html.unescape(url):
            return response_index[url]
        if fallback_entry is None:
            parsed = urlparse(url)
            geo = parse_qs(parsed.query).get("geo", [""])[0]
            geo_match = re.search(r"POINT\(([-0-9.]+)\s+([-0-9.]+)\)", geo)
            if geo_match:
                geo_lng = to_float(geo_match.group(1))
                geo_lat = to_float(geo_match.group(2))
                if (
                    geo_lng is not None
                    and geo_lat is not None
                    and abs(geo_lng - lng) < 0.0002
                    and abs(geo_lat - lat) < 0.0002
                ):
                    fallback_entry = response_index[url]
    return fallback_entry


def build_manifest_row(
    address: str,
    subject_key: str,
    identifiers: dict[str, str],
    kind: str,
    endpoint_url: str,
    fetched_entry: dict | None,
) -> dict:
    source = module_source(fetched_entry) if fetched_entry else {}
    request = fetched_entry.get("request", {}) if fetched_entry else {}
    post_data = request.get("postData", {}).get("text") if fetched_entry else None
    return {
        "address": address,
        "subject_key": subject_key,
        "har_page_id": identifiers.get("har_page_id"),
        "listing_id": identifiers.get("listing_id"),
        "mlsnum": identifiers.get("mlsnum"),
        "endpoint_kind": kind,
        "method": request.get("method", "GET"),
        "url": endpoint_url,
        "query_params": serialize_jsonish(parse_query_params(endpoint_url)),
        "post_data": post_data or "",
        "discovery_source": "html_inline_script",
        "entry_index": source.get("entry_index"),
        "status": source.get("status"),
        "content_type": source.get("content_type"),
        "fetched": "yes" if fetched_entry else "no",
    }


def replay_priority(kind: str) -> int:
    return REPLAY_PRIORITY.get(kind, 99)


def is_replayable(kind: str, endpoint_url: str) -> bool:
    if kind == "neighborhood_section":
        return bool(parse_query_params(endpoint_url))
    return True


def normalize_address_filter(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return WHITESPACE_RE.sub(" ", lowered).strip()


def read_filter_addresses(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    return {
        normalize_address_filter(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def read_candidate_addresses(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {
            normalize_address_filter(str(row.get("address", "")))
            for row in reader
            if str(row.get("address", "")).strip()
        }


def matches_filters(subject_address: str, filters: set[str]) -> bool:
    if not filters:
        return True
    return normalize_address_filter(subject_address) in filters


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_ndjson(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row))
            handle.write("\n")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    har_path = args.har.expanduser()
    out_dir = resolve_output_dir(har_path, args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    har = read_har(har_path)
    entries = har.get("log", {}).get("entries", [])
    response_index, parcel_urls = build_response_index(entries)
    filter_addresses = read_filter_addresses(args.addresses_file) | read_candidate_addresses(
        args.candidate_csv
    )

    packets: list[dict] = []
    manifest_rows: list[dict] = []
    missing_rows: list[dict] = []
    replay_rows: list[dict] = []
    parse_issues: list[dict] = []
    seen_subject_keys: set[str] = set()

    for entry in entries:
        url = normalize_url(entry.get("request", {}).get("url", ""))
        if classify_endpoint(url) != "detail_html":
            continue
        html_text = content_text(entry)
        if not html_text:
            continue

        seed = extract_detail_page(url, html_text)
        identifiers = extract_subject_identifiers(url, html_text)
        discovered = discover_endpoints(html_text)
        address = str(seed["subject"].get("address", "")).strip()
        if not matches_filters(address, filter_addresses):
            continue
        subject_key = identifiers.get("har_page_id") or url
        if subject_key in seen_subject_keys:
            continue
        seen_subject_keys.add(subject_key)

        fetched_endpoints: dict[str, str] = {}
        missing_endpoints: dict[str, str] = {}
        modules: dict[str, dict | list] = {}
        for kind, endpoint_url in discovered.items():
            fetched_entry = response_index.get(endpoint_url)
            manifest_rows.append(
                build_manifest_row(
                    address=address,
                    subject_key=subject_key,
                    identifiers=identifiers,
                    kind=kind,
                    endpoint_url=endpoint_url,
                    fetched_entry=fetched_entry,
                )
            )
            if fetched_entry is None:
                missing_endpoints[kind] = endpoint_url
                missing_rows.append(
                    {
                        "address": address,
                        "subject_key": subject_key,
                        "har_page_id": identifiers.get("har_page_id"),
                        "endpoint_kind": kind,
                        "url": endpoint_url,
                    }
                )
                if is_replayable(kind, endpoint_url):
                    replay_rows.append(
                        {
                            "address": address,
                            "subject_key": subject_key,
                            "endpoint_kind": kind,
                            "method": "GET",
                            "url": endpoint_url,
                            "has_post_data": "no",
                            "priority": replay_priority(kind),
                            "reason": "discovered_in_html_missing_in_har",
                        }
                    )
                continue
            fetched_endpoints[kind] = endpoint_url
            modules[kind] = parse_module(kind, fetched_entry)

        parcel_entry = find_subject_parcel_geometry(seed["subject"], parcel_urls, response_index)
        parcel_data = {
            "parcel_auth_urls": sorted(set(parcel_urls["parcel_auth_urls"])),
            "parcel_geometry_urls": sorted(set(parcel_urls["parcel_geometry_urls"])),
        }
        if parcel_entry is not None:
            modules["parcel_geometry"] = parse_module("parcel_geometry", parcel_entry)
            parcel_data.update(modules["parcel_geometry"]["parsed"])
            parcel_data["source"] = modules["parcel_geometry"]["source"]
            parcel_data["parse_status"] = modules["parcel_geometry"]["parse_status"]

        packet = {
            "subject": {
                **seed["subject"],
                "subject_key": subject_key,
                "canonical_url": url,
                "page_url": url,
            },
            "identifiers": identifiers,
            "detail_page": seed["detail_page"],
            "modules": modules,
            "parcel": parcel_data,
            "provenance": {
                "har_path": str(har_path),
                "home_url": url,
                "discovered_endpoints": discovered,
                "fetched_endpoints": fetched_endpoints,
                "missing_endpoints": missing_endpoints,
            },
        }
        schools = parse_school_cards(html_text)
        rooms = parse_room_entries(packet["detail_page"].get("property_values", {}))
        raw_sections, normalized_flags = extract_feature_sections(
            html_text, packet["detail_page"].get("property_values", {})
        )
        packet["detail_page"]["schools"] = schools
        packet["detail_page"]["schools_count"] = len(schools)
        packet["detail_page"]["rooms"] = rooms
        packet["detail_page"]["rooms_count"] = len(rooms)
        packet["detail_page"]["features_raw_sections"] = raw_sections
        packet["detail_page"]["feature_flags"] = normalized_flags
        parse_status, parse_confidence, completeness_score = detail_parse_status(
            packet["detail_page"], packet["modules"]
        )
        packet["quality"] = {
            "parse_status": parse_status,
            "parse_confidence": parse_confidence,
            "completeness_score": completeness_score,
            "has_detail_html": True,
            "has_remarks": bool(packet["detail_page"].get("remarks_clean")),
            "has_schools": bool(packet["detail_page"].get("schools")),
            "has_rooms": bool(packet["detail_page"].get("rooms")),
            "has_features": any(packet["detail_page"].get("features_raw_sections", {}).values()),
            "has_tax_info": "tax_info" in packet["modules"],
            "has_similar_sold": "similar_sold" in packet["modules"],
            "has_media_gallery": "media_gallery" in packet["modules"],
            "has_parcel_geometry": "parcel_geometry" in packet["modules"],
            "feature_flag_count": sum(
                1 for value in packet["detail_page"]["feature_flags"].values() if value
            ),
        }
        if not packet["quality"]["has_schools"]:
            parse_issues.append(
                {
                    "subject_key": subject_key,
                    "module": "schools",
                    "severity": "medium",
                    "issue": "No school cards parsed from detail HTML",
                    "entry_index": entry.get("_entry_index"),
                    "url": url,
                }
            )
        if not packet["quality"]["has_rooms"]:
            parse_issues.append(
                {
                    "subject_key": subject_key,
                    "module": "rooms",
                    "severity": "medium",
                    "issue": "No room entries parsed from JSON-LD property values",
                    "entry_index": entry.get("_entry_index"),
                    "url": url,
                }
            )
        if not packet["quality"]["has_features"]:
            parse_issues.append(
                {
                    "subject_key": subject_key,
                    "module": "features",
                    "severity": "medium",
                    "issue": "No feature sections parsed from detail HTML",
                    "entry_index": entry.get("_entry_index"),
                    "url": url,
                }
            )
        packets.append(packet)

    packets.sort(key=lambda row: (row["subject"].get("address") or "", row["subject"].get("page_url") or ""))
    manifest_rows.sort(key=lambda row: (row["address"] or "", row["endpoint_kind"] or ""))
    missing_rows.sort(key=lambda row: (row["address"] or "", row["endpoint_kind"] or ""))
    replay_rows.sort(key=lambda row: (row["priority"], row["address"] or "", row["endpoint_kind"] or ""))

    write_json(out_dir / "subject_packets.json", packets)
    write_ndjson(out_dir / "subject_packets.ndjson", packets)
    write_csv(
        out_dir / "subject_endpoint_manifest.csv",
        manifest_rows,
        [
            "address",
            "subject_key",
            "har_page_id",
            "listing_id",
            "mlsnum",
            "endpoint_kind",
            "method",
            "url",
            "query_params",
            "post_data",
            "discovery_source",
            "entry_index",
            "status",
            "content_type",
            "fetched",
        ],
    )
    write_json(out_dir / "missing_endpoints.json", missing_rows)
    write_csv(
        out_dir / "replay_queue.csv",
        replay_rows,
        [
            "address",
            "subject_key",
            "endpoint_kind",
            "method",
            "url",
            "has_post_data",
            "priority",
            "reason",
        ],
    )
    write_csv(
        out_dir / "parse_issues.csv",
        parse_issues,
        ["subject_key", "module", "severity", "issue", "entry_index", "url"],
    )
    write_csv(
        out_dir / "subjects_summary.csv",
        [
            {
                "address": packet["subject"].get("address"),
                "subject_key": packet["subject"].get("subject_key"),
                "har_page_id": packet["identifiers"].get("har_page_id"),
                "listing_id": packet["identifiers"].get("listing_id"),
                "mlsnum": packet["identifiers"].get("mlsnum"),
                "lat": packet["subject"].get("lat"),
                "lng": packet["subject"].get("lng"),
                "remarks_len": len(packet["detail_page"].get("remarks_clean") or ""),
                "schools_count": packet["detail_page"].get("schools_count", 0),
                "rooms_count": packet["detail_page"].get("rooms_count", 0),
                "feature_flag_count": packet["quality"].get("feature_flag_count"),
                "has_parcel_geometry": packet["quality"].get("has_parcel_geometry"),
                "similar_sale_count": packet["modules"].get("similar_sale", {}).get("parsed", {}).get("comp_count", 0)
                if isinstance(packet["modules"].get("similar_sale"), dict)
                else 0,
                "similar_rent_count": packet["modules"].get("similar_rent", {}).get("parsed", {}).get("comp_count", 0)
                if isinstance(packet["modules"].get("similar_rent"), dict)
                else 0,
                "similar_sold_count": packet["modules"].get("similar_sold", {}).get("parsed", {}).get("comp_count", 0)
                if isinstance(packet["modules"].get("similar_sold"), dict)
                else 0,
                "photo_count": packet["modules"].get("media_gallery", {}).get("parsed", {}).get("photo_count", 0)
                if isinstance(packet["modules"].get("media_gallery"), dict)
                else 0,
                "fetched_endpoint_count": len(packet["provenance"]["fetched_endpoints"]),
                "missing_endpoint_count": len(packet["provenance"]["missing_endpoints"]),
                "parse_status": packet["quality"].get("parse_status"),
                "completeness_score": packet["quality"].get("completeness_score"),
            }
            for packet in packets
        ],
        [
            "address",
            "subject_key",
            "har_page_id",
            "listing_id",
            "mlsnum",
            "lat",
            "lng",
            "remarks_len",
            "schools_count",
            "rooms_count",
            "feature_flag_count",
            "has_parcel_geometry",
            "similar_sale_count",
            "similar_rent_count",
            "similar_sold_count",
            "photo_count",
            "fetched_endpoint_count",
            "missing_endpoint_count",
            "parse_status",
            "completeness_score",
        ],
    )

    print(f"Extracted {len(packets)} subject packets to {out_dir}")


if __name__ == "__main__":
    main()
