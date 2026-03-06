"""Fetch SearchListings API directly using credentials from HAR files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

SNAPSHOTS_DIR = Path("snapshots")


def find_snapshot(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg)
        if path.exists():
            return path
        raise FileNotFoundError(f"Snapshot not found: {path}")

    candidates = [item for item in SNAPSHOTS_DIR.iterdir() if item.is_dir()] if SNAPSHOTS_DIR.exists() else []
    if not candidates:
        raise FileNotFoundError("No snapshots found. Run init-snapshot first or pass --snapshot.")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def default_output_path(snapshot: Path, dataset_label: str) -> Path:
    raw_dir = snapshot / "raw" / "fetched"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir / f"searchlistings_{dataset_label}.json"


def mutate_url(url: str, zip_code: str | None, for_sale: str | None) -> str:
    updated = url
    if zip_code:
        if "zip_code=" in updated:
            updated = re.sub(r"zip_code=\d+", f"zip_code={zip_code}", updated)
        else:
            separator = "&" if "?" in updated else "?"
            updated = f"{updated}{separator}zip_code={zip_code}"

    if for_sale:
        if "for_sale=" in updated:
            updated = re.sub(r"for_sale=[01]", f"for_sale={for_sale}", updated)
        else:
            separator = "&" if "?" in updated else "?"
            updated = f"{updated}{separator}for_sale={for_sale}"
    return updated


def extract_headers_from_har(har_path: Path) -> dict:
    """Extract auth headers and cookies from a HAR file for API requests."""
    with har_path.open("r", encoding="utf-8") as handle:
        har = json.load(handle)

    entries = har.get("log", {}).get("entries", [])

    # Find SearchListings request
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "SearchListings" not in url:
            continue

        headers = entry.get("request", {}).get("headers", [])
        cookie = None
        ua = None

        for h in headers:
            name = h.get("name", "")
            value = h.get("value", "")

            if name.lower() == "cookie":
                cookie = value
            elif name.lower() == "user-agent":
                ua = value

        if cookie and ua:
            return {
                "Cookie": cookie,
                "User-Agent": ua,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            }

    raise ValueError("No SearchListings request found in HAR file")


def fetch_listings(url: str, headers: dict | None = None) -> dict:
    """Fetch SearchListings API payloads."""
    if headers is None:
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        }

    print(f"Fetching: {url[:100]}...\n")

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        print(f"✓ Successfully fetched listing data")
        print(f"  Data entries: {len(data.get('data', []))}")
        print(f"  Sold entries: {len(data.get('sold_data', []))}\n")

        return data

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SearchListings API data into a snapshot pack")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--har", type=Path, help="Extract URL + auth from HAR file, then fetch")
    group.add_argument("--url", help="Direct SearchListings API URL")
    parser.add_argument("--snapshot", help="Snapshot pack path (defaults to latest)")
    parser.add_argument("--zip", type=str, help="Modify ZIP code in URL (e.g., --zip 77055)")
    parser.add_argument("--for-sale", choices=["0", "1"], help="Filter: 0=rentals, 1=sales")
    parser.add_argument("--output", type=Path, help="Output JSON file path")

    args = parser.parse_args()
    snapshot = find_snapshot(args.snapshot)

    if args.har:
        if not args.har.exists():
            print(f"Error: HAR file not found: {args.har}")
            sys.exit(1)

        # Extract headers and URL from HAR
        headers = extract_headers_from_har(args.har)
        with args.har.open("r", encoding="utf-8") as handle:
            har = json.load(handle)

        # Find SearchListings URL
        url = None
        entries = har.get("log", {}).get("entries", [])
        for entry in entries:
            u = entry.get("request", {}).get("url", "")
            if "SearchListings" in u:
                url = u
                break

        if not url:
            print("Error: SearchListings URL not found in HAR file")
            sys.exit(1)

    else:
        url = args.url
        headers = None  # Use defaults

    url = mutate_url(url, args.zip, args.for_sale)
    if args.zip:
        print(f"Modified ZIP code to: {args.zip}")

    # Fetch data
    data = fetch_listings(url, headers)

    # Save to file
    dataset_label = "for_sale" if args.for_sale == "1" else "for_rent" if args.for_sale == "0" else "fetched"
    output_path = args.output or default_output_path(snapshot, dataset_label)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Snapshot: {snapshot}")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()
