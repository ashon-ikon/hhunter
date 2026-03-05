"""Extract listing payloads from one or more HAR files into snapshot artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse


KNOWN_ENDPOINT_HINTS = [
    "searchlistings",
    "zillow",
    "graphql",
    "search",
    "map",
    "listings",
]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def parse_json(text: str) -> dict | list | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def is_candidate(url: str, payload: dict | list | None) -> bool:
    if payload is None:
        return False
    lower_url = url.lower()
    if any(h in lower_url for h in KNOWN_ENDPOINT_HINTS):
        return True
    if isinstance(payload, dict) and ("data" in payload or "sold_data" in payload):
        return True
    return False


def infer_dataset_from_query(params: dict[str, list[str]]) -> str:
    p = {k.lower(): v for k, v in params.items()}
    sold_period = p.get("soldperiod", [])
    for_sale = p.get("for_sale", [])
    for_rent = p.get("for_rent", [])

    if sold_period and any(v not in {"", "0", "false", "False"} for v in sold_period):
        return "sold"
    if for_rent and any(v not in {"", "0", "false", "False"} for v in for_rent):
        return "rental"
    if for_sale and any(v in {"0", "false", "False"} for v in for_sale):
        return "rental"
    return "active"


def extract_listing_arrays(payload: dict | list, fallback_dataset: str) -> list[tuple[str, dict]]:
    output: list[tuple[str, dict]] = []

    if isinstance(payload, dict):
        has_explicit_sold = isinstance(payload.get("sold_data"), list)
        if isinstance(payload.get("data"), list):
            data_dataset = "active" if has_explicit_sold else fallback_dataset
            for row in payload["data"]:
                if isinstance(row, dict):
                    output.append((data_dataset, row))

        if isinstance(payload.get("sold_data"), list):
            for row in payload["sold_data"]:
                if isinstance(row, dict):
                    output.append(("sold", row))

        if not output:
            for value in payload.values():
                if isinstance(value, list):
                    for row in value:
                        if isinstance(row, dict) and (
                            "MLSNUM" in row
                            or "LISTINGID" in row
                            or "zpid" in row
                            or "hdpData" in row
                        ):
                            output.append((fallback_dataset, row))
    elif isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                output.append((fallback_dataset, row))

    return output


def endpoint_hint(url: str) -> str:
    lower = url.lower()
    for hint in KNOWN_ENDPOINT_HINTS:
        if hint in lower:
            return hint
    return "unknown"


def collect_har_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.har")))
        elif path.suffix.lower() == ".har":
            files.append(path)
    return files


def extract_snapshot(inputs: list[Path], snapshot_path: Path) -> dict:
    snapshot_id = snapshot_path.name
    extracted_dir = snapshot_path / "out" / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    ndjson_path = extracted_dir / "har_responses.ndjson"
    index_path = extracted_dir / "requests_index.csv"
    listings_path = extracted_dir / "listings_raw.json"

    files = collect_har_files([p.expanduser() for p in inputs])
    if not files:
        raise SystemExit("No HAR files found in the provided inputs.")

    seen_fingerprints: set[str] = set()
    ndjson_rows: list[dict] = []
    index_rows: list[dict] = []
    merged_listings: list[dict] = []

    for har_file in files:
        har = json.loads(har_file.read_text(encoding="utf-8"))
        entries = har.get("log", {}).get("entries", [])

        for idx, entry in enumerate(entries):
            req = entry.get("request", {})
            resp = entry.get("response", {})
            content = resp.get("content", {})

            url = req.get("url", "")
            method = req.get("method", "")
            status = resp.get("status")
            mime = content.get("mimeType", "")
            text = content.get("text", "") or ""
            started = entry.get("startedDateTime")
            body_size = content.get("size") if isinstance(content.get("size"), int) else len(text)

            parsed = urlparse(url) if url else None
            params = parse_qs(parsed.query) if parsed else {}

            index_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "har_file": str(har_file),
                    "entry_index": idx,
                    "method": method,
                    "url": url,
                    "status": status,
                    "content_type": mime,
                    "bytes": body_size,
                    "timestamp": started,
                }
            )

            payload = parse_json(text)
            if not is_candidate(url, payload):
                continue

            url_hash = sha256_text(url)
            response_hash = sha256_text(text)
            fingerprint = f"{url_hash}:{response_hash}"
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)

            request_params = {k: v[0] if len(v) == 1 else v for k, v in params.items()}
            dataset_guess = infer_dataset_from_query(params)
            source = "har" if "har.com" in url.lower() else ("zillow" if "zillow" in url.lower() else "other")

            ndjson_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "har_file": str(har_file),
                    "entry_index": idx,
                    "timestamp": started,
                    "method": method,
                    "url": url,
                    "status": status,
                    "content_type": mime,
                    "bytes": body_size,
                    "url_hash": url_hash,
                    "response_hash": response_hash,
                    "endpoint_hint": endpoint_hint(url),
                    "source": source,
                    "dataset_hint": dataset_guess,
                    "request_params": request_params,
                    "payload": payload,
                }
            )

            for dataset, listing in extract_listing_arrays(payload, dataset_guess):
                listing_copy = dict(listing)
                listing_copy["__source"] = source
                listing_copy["__snapshot_id"] = snapshot_id
                listing_copy["__dataset"] = dataset
                listing_copy["__har_file"] = str(har_file)
                listing_copy["__request_url"] = url
                listing_copy["__request_timestamp"] = started
                listing_copy["__request_params"] = request_params
                merged_listings.append(listing_copy)

    with ndjson_path.open("w", encoding="utf-8") as handle:
        for row in ndjson_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    with index_path.open("w", newline="", encoding="utf-8") as handle:
        if index_rows:
            writer = csv.DictWriter(handle, fieldnames=list(index_rows[0].keys()))
            writer.writeheader()
            writer.writerows(index_rows)

    listings_path.write_text(json.dumps(merged_listings, indent=2), encoding="utf-8")
    return {
        "snapshot_id": snapshot_id,
        "har_files_processed": len(files),
        "indexed_requests": len(index_rows),
        "extracted_payloads": len(ndjson_rows),
        "merged_listings": len(merged_listings),
        "ndjson_path": ndjson_path,
        "index_path": index_path,
        "listings_path": listings_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract listing payloads from HAR files")
    parser.add_argument("inputs", nargs="+", type=Path, help="HAR files or directories")
    parser.add_argument("--snapshot", required=True, type=Path, help="Snapshot pack path")
    args = parser.parse_args()

    result = extract_snapshot(args.inputs, args.snapshot)
    print(f"Snapshot: {result['snapshot_id']}")
    print(f"HAR files processed: {result['har_files_processed']}")
    print(f"Indexed requests: {result['indexed_requests']}")
    print(f"Extracted payloads: {result['extracted_payloads']}")
    print(f"Merged listings: {result['merged_listings']}")
    print(f"Wrote: {result['ndjson_path']}")
    print(f"Wrote: {result['index_path']}")
    print(f"Wrote: {result['listings_path']}")


if __name__ == "__main__":
    main()
