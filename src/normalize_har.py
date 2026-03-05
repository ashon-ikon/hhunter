"""Normalize extracted listing objects into canonical CSV outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

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
    "flood_flag",
    "har_file",
    "request_url",
    "request_timestamp",
    "request_params",
]


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
        "url": pick(raw, "URL", "url", "detailUrl", "hdpUrl"),
        "street_name": street_name,
        "calc_ppsf_list": calc_ppsf_list,
        "calc_ppsf_sold": calc_ppsf_sold,
        "era_bucket": e_bucket,
        "size_bucket": s_bucket,
        "flip_box_flag": flip_flag,
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


def normalize_snapshot(snapshot: Path) -> dict:
    listings_path = snapshot / "out" / "extracted" / "listings_raw.json"
    if not listings_path.exists():
        raise SystemExit(f"Missing extracted listings file: {listings_path}")

    raw_listings = json.loads(listings_path.read_text(encoding="utf-8"))
    rows = [normalize_record(item) for item in raw_listings if isinstance(item, dict)]
    df = pd.DataFrame(rows)
    df = df.reindex(columns=CANONICAL_COLUMNS)

    out_dir = snapshot / "out" / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)

    active = df[df["dataset"] == "active"].copy()
    sold = df[df["dataset"] == "sold"].copy()
    rentals = df[df["dataset"] == "rental"].copy()

    active_out = write_dataset(active, out_dir, "active")
    sold_out = write_dataset(sold, out_dir, "sold")
    rentals_out = write_dataset(rentals, out_dir, "rentals")

    qa_stub = {
        "snapshot_id": snapshot.name,
        "row_counts": {
            "active": int(len(active)),
            "sold": int(len(sold)),
            "rental": int(len(rentals)),
        },
        "missingness": {
            "sqft": float(df["sqft"].isna().mean()) if not df.empty else 1.0,
            "year_built": float(df["year_built"].isna().mean()) if not df.empty else 1.0,
            "list_price_num": float(df["list_price_num"].isna().mean()) if not df.empty else 1.0,
            "sold_price_num": float(df["sold_price_num"].isna().mean()) if not df.empty else 1.0,
        },
    }
    (out_dir / "qa_report.json").write_text(json.dumps(qa_stub, indent=2), encoding="utf-8")
    return {
        "snapshot": snapshot,
        "normalized_rows": len(df),
        "active_out": active_out,
        "sold_out": sold_out,
        "rentals_out": rentals_out,
        "qa_stub_out": out_dir / "qa_report.json",
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
    print(f"Wrote: {result['qa_stub_out']}")


if __name__ == "__main__":
    main()
