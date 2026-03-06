"""Normalize extracted listing objects into canonical CSV outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd

from src.grid_utils import DEFAULT_CELL_SIZE_M, assign_grid_fields, grid_spec_for_listings

CANONICAL_COLUMNS = [
    "source",
    "snapshot_id",
    "dataset",
    "listing_id",
    "mlsnum",
    "status_group",
    "address",
    "zip",
    "city",
    "lat",
    "lng",
    "proptype",
    "beds",
    "baths_full",
    "baths_half",
    "sqft",
    "year_built",
    "lot_sqft",
    "list_price_num",
    "sold_price_num",
    "dom",
    "tax_amount",
    "subdivision",
    "new_construction_flag",
    "vendor_ppsf",
    "url",
    "street_name",
    "calc_ppsf_list",
    "calc_ppsf_sold",
    "era_bucket",
    "size_bucket",
    "flip_box_flag",
    "grid_id",
    "grid_row",
    "grid_col",
    "grid_centroid_lat",
    "grid_centroid_lng",
    "flood_flag",
    "har_file",
    "request_url",
    "request_timestamp",
    "request_params",
]

DATASET_FILE_NAMES = {
    "active": "active",
    "sold": "sold",
    "rental": "rentals",
}


def parse_num(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = value.replace(",", "").replace("$", "").strip()
    if cleaned in {"", "-", "N/A", "None", "null"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def pick(d: dict, *keys: str) -> object:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return None


def era_bucket(year_built: float | None) -> str | None:
    if year_built is None:
        return None
    y = int(year_built)
    if y < 1980:
        return "pre1980"
    if y <= 1999:
        return "1980_1999"
    if y <= 2017:
        return "2000_2017"
    return "2018_plus"


def size_bucket(sqft: float | None) -> str | None:
    if sqft is None:
        return None
    if sqft < 1200:
        return "0_1200"
    if sqft < 1800:
        return "1200_1800"
    if sqft < 2600:
        return "1800_2600"
    return "2600_plus"


def canonical_listing_url(raw: dict, source: str) -> str | None:
    candidate = pick(raw, "URL", "url", "detailUrl", "hdpUrl", "WEB_URL", "PROPERTY_URL")
    if not isinstance(candidate, str) or not candidate.strip():
        return None

    candidate = candidate.strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate

    request_url = raw.get("__request_url")
    if isinstance(request_url, str) and request_url.strip():
        parsed = urlparse(request_url)
        if parsed.scheme and parsed.netloc:
            return urljoin(f"{parsed.scheme}://{parsed.netloc}", candidate)

    if source == "har":
        return urljoin("https://www.har.com", candidate)
    if source == "zillow":
        return urljoin("https://www.zillow.com", candidate)
    return candidate


def normalize_record(raw: dict) -> dict:
    source = str(raw.get("__source", "har"))
    snapshot_id = str(raw.get("__snapshot_id", ""))
    dataset = str(raw.get("__dataset", "active"))

    listing_id = pick(raw, "LISTINGID", "listingId", "id", "zpid")
    mlsnum = pick(raw, "MLSNUM", "mlsId", "mlsNumber")
    beds = parse_num(pick(raw, "BEDROOM", "beds", "bedrooms"))
    baths_full = parse_num(pick(raw, "BATHFULL", "fullBathrooms", "bathroomsFull", "bathsFull"))
    baths_half = parse_num(pick(raw, "BATHHALF", "halfBathrooms", "bathroomsHalf", "bathsHalf"))
    sqft = parse_num(pick(raw, "BLDGSQFT", "livingArea", "sqft", "livingAreaValue"))
    year = parse_num(pick(raw, "YEARBUILT", "yearBuilt"))
    lot_sqft = parse_num(pick(raw, "LOTSIZE", "lotSize", "lotAreaValue"))

    list_price = parse_num(pick(raw, "LISTPRICEORI", "LISTPRICEORIGIN", "listPrice", "price"))
    sold_price = parse_num(pick(raw, "SALESPRICE", "soldPrice", "closePrice"))
    dom = parse_num(pick(raw, "DOM", "DAYSONMARKET", "daysOnMarket"))
    tax_amount = parse_num(pick(raw, "TAXAMOUNT", "taxes"))

    address = pick(raw, "FULLSTREETADDRESS", "address", "streetAddress", "line")
    city = pick(raw, "CITY", "city")
    zip_code = pick(raw, "ZIP", "zipcode", "zip")
    proptype = pick(raw, "PROPTYPENAME", "propertyType", "homeType")
    subdivision = pick(raw, "SUBDIVISION", "subdivision", "neighborhood")
    lat = parse_num(pick(raw, "LATITUDE", "latitude", "lat"))
    lng = parse_num(pick(raw, "LONGITUDE", "longitude", "lng"))
    vendor_ppsf = parse_num(pick(raw, "PRICEPERSQFT", "pricePerSquareFoot", "ppsf"))

    status_raw = str(pick(raw, "STATUS", "status", "LISTINGSTATUS", "listingStatus") or "")
    status_lower = status_raw.lower()
    if "sold" in status_lower or dataset == "sold":
        status_group = "sold"
    elif "pend" in status_lower:
        status_group = "pending"
    else:
        status_group = "active"

    new_construction = bool((year or 0) >= 2022 or str(pick(raw, "NEWCONSTRUCTION", "newConstruction") or "").lower() in {"1", "true", "yes", "y"})

    calc_ppsf_list = (list_price / sqft) if list_price and sqft else None
    calc_ppsf_sold = (sold_price / sqft) if sold_price and sqft else None

    e_bucket = era_bucket(year)
    s_bucket = size_bucket(sqft)

    total_baths = (baths_full or 0) + 0.5 * (baths_half or 0)
    flip_flag = bool(
        proptype in {"Single-Family", "Single Family", "single_family"}
        and beds is not None
        and 3 <= beds <= 4
        and 1.5 <= total_baths <= 2.5
        and sqft is not None
        and 1200 <= sqft <= 1800
        and year is not None
        and 1950 <= year <= 1995
    )

    street_name = None
    if isinstance(address, str):
        parts = address.split(",")[0].split()
        street_name = " ".join(parts[1:]) if len(parts) > 1 else address

    return {
        "source": source,
        "snapshot_id": snapshot_id,
        "dataset": dataset,
        "listing_id": listing_id,
        "mlsnum": mlsnum,
        "status_group": status_group,
        "address": address,
        "zip": str(zip_code).zfill(5) if zip_code not in (None, "") else None,
        "city": city,
        "lat": lat,
        "lng": lng,
        "proptype": proptype,
        "beds": beds,
        "baths_full": baths_full,
        "baths_half": baths_half,
        "sqft": sqft,
        "year_built": year,
        "lot_sqft": lot_sqft,
        "list_price_num": list_price,
        "sold_price_num": sold_price,
        "dom": dom,
        "tax_amount": tax_amount,
        "subdivision": subdivision,
        "new_construction_flag": new_construction,
        "vendor_ppsf": vendor_ppsf,
        "url": canonical_listing_url(raw, source),
        "street_name": street_name,
        "calc_ppsf_list": calc_ppsf_list,
        "calc_ppsf_sold": calc_ppsf_sold,
        "era_bucket": e_bucket,
        "size_bucket": s_bucket,
        "flip_box_flag": flip_flag,
        "grid_id": None,
        "grid_row": None,
        "grid_col": None,
        "grid_centroid_lat": None,
        "grid_centroid_lng": None,
        "flood_flag": None,
        "har_file": raw.get("__har_file"),
        "request_url": raw.get("__request_url"),
        "request_timestamp": raw.get("__request_timestamp"),
        "request_params": json.dumps(raw.get("__request_params", {}), separators=(",", ":")),
    }


def find_snapshot(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg)
        if p.exists():
            return p
        raise FileNotFoundError(f"Snapshot not found: {p}")

    root = Path("snapshots")
    candidates = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
    if not candidates:
        raise FileNotFoundError("No snapshots found. Run init-snapshot first.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def write_dataset(df: pd.DataFrame, out_dir: Path, name: str) -> Path:
    out_path = out_dir / f"{name}.csv"
    df.to_csv(out_path, index=False)
    return out_path


def as_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def dedupe_key(row: pd.Series) -> str:
    listing_id = as_text(row.get("listing_id"))
    if listing_id:
        return f"listing_id:{listing_id}"

    mlsnum = as_text(row.get("mlsnum"))
    if mlsnum:
        return f"mlsnum:{mlsnum}"

    address = as_text(row.get("address"))
    zip_code = as_text(row.get("zip"))
    sqft = as_text(row.get("sqft"))
    year_built = as_text(row.get("year_built"))
    parts = [address, zip_code, sqft, year_built]
    if any(parts):
        normalized = [part or "" for part in parts]
        return "address_zip_sqft_year:" + "|".join(normalized)

    return f"row:{int(row.get('_row_order', 0))}"


def dataset_stats(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "unique_listing_ids": 0,
        }

    return {
        "rows": int(len(df)),
        "unique_listing_ids": int(df["listing_id"].nunique(dropna=True)) if "listing_id" in df.columns else 0,
    }


def dedupe_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.empty:
        return df.copy(), {"raw_rows": 0, "deduped_rows": 0, "duplicate_rows_removed": 0}

    work = df.copy()
    work["_row_order"] = range(len(work))
    work["_request_timestamp_dt"] = pd.to_datetime(work["request_timestamp"], errors="coerce", utc=True)
    work["_non_null_fields"] = work[CANONICAL_COLUMNS].notna().sum(axis=1)
    work["_dedupe_key"] = work.apply(dedupe_key, axis=1)

    deduped = (
        work.sort_values(
            by=["_dedupe_key", "_request_timestamp_dt", "_non_null_fields", "_row_order"],
            ascending=[True, False, False, True],
            na_position="last",
        )
        .drop_duplicates(subset=["_dedupe_key"], keep="first")
        .sort_values("_row_order")
        .drop(columns=["_row_order", "_request_timestamp_dt", "_non_null_fields", "_dedupe_key"])
        .reindex(columns=CANONICAL_COLUMNS)
    )

    raw_rows = int(len(df))
    deduped_rows = int(len(deduped))
    return deduped, {
        "raw_rows": raw_rows,
        "deduped_rows": deduped_rows,
        "duplicate_rows_removed": raw_rows - deduped_rows,
    }


def normalize_snapshot(snapshot: Path) -> dict:
    listings_path = snapshot / "out" / "extracted" / "listings_raw.json"
    if not listings_path.exists():
        raise SystemExit(f"Missing extracted listings file: {listings_path}")

    raw_listings = json.loads(listings_path.read_text(encoding="utf-8"))
    rows = [normalize_record(item) for item in raw_listings if isinstance(item, dict)]
    df = pd.DataFrame(rows)
    df = df.reindex(columns=CANONICAL_COLUMNS)
    if not df.empty and df["lat"].notna().any() and df["lng"].notna().any():
        spec = grid_spec_for_listings(df, cell_size_m=DEFAULT_CELL_SIZE_M)
        df = assign_grid_fields(df, spec)

    out_dir = snapshot / "out" / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_datasets = {
        "active": df[df["dataset"] == "active"].copy(),
        "sold": df[df["dataset"] == "sold"].copy(),
        "rental": df[df["dataset"] == "rental"].copy(),
    }

    deduped_datasets: dict[str, pd.DataFrame] = {}
    dataset_reports: dict[str, dict[str, Any]] = {}
    raw_outputs: dict[str, Path] = {}
    deduped_outputs: dict[str, Path] = {}

    for dataset_name, raw_dataset in raw_datasets.items():
        file_name = DATASET_FILE_NAMES[dataset_name]
        raw_outputs[dataset_name] = write_dataset(raw_dataset, out_dir, f"{file_name}_raw")

        deduped_dataset, dedupe_report = dedupe_dataset(raw_dataset)
        deduped_datasets[dataset_name] = deduped_dataset
        deduped_outputs[dataset_name] = write_dataset(deduped_dataset, out_dir, file_name)
        dataset_reports[dataset_name] = {
            **dedupe_report,
            "raw": dataset_stats(raw_dataset),
            "deduped": dataset_stats(deduped_dataset),
        }

    normalize_report = {
        "snapshot_id": snapshot.name,
        "normalized_rows": int(len(df)),
        "datasets": dataset_reports,
    }
    normalize_report_path = out_dir / "normalize_report.json"
    normalize_report_path.write_text(json.dumps(normalize_report, indent=2), encoding="utf-8")
    return {
        "snapshot": snapshot,
        "normalized_rows": len(df),
        "active_out": deduped_outputs["active"],
        "sold_out": deduped_outputs["sold"],
        "rentals_out": deduped_outputs["rental"],
        "active_raw_out": raw_outputs["active"],
        "sold_raw_out": raw_outputs["sold"],
        "rentals_raw_out": raw_outputs["rental"],
        "normalize_report_out": normalize_report_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize extracted listing objects")
    parser.add_argument("--snapshot", help="Snapshot pack path")
    args = parser.parse_args()

    snapshot = find_snapshot(args.snapshot)
    result = normalize_snapshot(snapshot)

    print(f"Snapshot: {result['snapshot']}")
    print(f"Normalized rows: {result['normalized_rows']}")
    print(f"Wrote: {result['active_out']}")
    print(f"Wrote: {result['sold_out']}")
    print(f"Wrote: {result['rentals_out']}")
    print(f"Wrote: {result['normalize_report_out']}")


if __name__ == "__main__":
    main()
