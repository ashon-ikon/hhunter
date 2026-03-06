"""Run QA checks on normalized snapshot data and produce gate artifacts."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_THRESHOLDS = {
    "max_missing_sqft_pct": 35.0,
    "max_missing_year_built_pct": 50.0,
    "max_missing_price_pct": 35.0,
    "max_rental_contamination_count": 0,
}
COMMON_RESULT_CAPS = {100, 120, 150, 200}
PAGINATION_PARAMS = {"page", "pageindex", "currentpage", "offset", "start", "limit"}


def find_snapshot(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg)
        if p.exists():
            return p
        raise FileNotFoundError(f"Snapshot not found: {p}")
    root = Path("snapshots")
    candidates = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
    if not candidates:
        raise FileNotFoundError("No snapshots found.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def as_pct(value: float) -> float:
    return round(float(value) * 100.0, 2)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def dataset_metrics(name: str, df: pd.DataFrame, normalize_dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    normalize_dataset = normalize_dataset or {}
    duplicate_rows = int(
        normalize_dataset.get(
            "duplicate_rows_removed",
            df.duplicated(subset=["dataset", "listing_id", "request_url"], keep="first").sum()
            if set(["dataset", "listing_id", "request_url"]).issubset(df.columns)
            else df.duplicated().sum(),
        )
    )
    raw_rows = int(normalize_dataset.get("raw_rows", len(df)))

    if df.empty:
        return {
            "rows": 0,
            "raw_rows": raw_rows,
            "unique_listings": 0,
            "duplicate_rows": duplicate_rows,
            "missingness": {
                "sqft_pct": 100.0,
                "year_built_pct": 100.0,
                "price_pct": 100.0,
            },
            "status_distribution": {},
            "zip_distribution": {},
            "ppsf_mismatch_pct": 0.0,
            "rental_contamination_count": 0,
        }

    price_col = "sold_price_num" if name == "sold" else "list_price_num"
    calc_col = "calc_ppsf_sold" if name == "sold" else "calc_ppsf_list"

    mismatch = pd.Series(dtype=float)
    if "vendor_ppsf" in df.columns and calc_col in df.columns:
        work = df[(df["vendor_ppsf"].notna()) & (df[calc_col].notna()) & (df[calc_col] != 0)].copy()
        if not work.empty:
            mismatch = ((work["vendor_ppsf"] - work[calc_col]).abs() / work[calc_col].abs()) > 0.10

    rental_contamination = 0
    if name == "rental" and "request_url" in df.columns:
        rental_contamination = int(
            df["request_url"].astype(str).str.contains("for_sale=1|soldperiod=", case=False, regex=True).sum()
        )

    return {
        "rows": int(len(df)),
        "raw_rows": raw_rows,
        "unique_listings": int(df["listing_id"].nunique(dropna=True)) if "listing_id" in df.columns else 0,
        "duplicate_rows": duplicate_rows,
        "missingness": {
            "sqft_pct": as_pct(df["sqft"].isna().mean()) if "sqft" in df.columns else 100.0,
            "year_built_pct": as_pct(df["year_built"].isna().mean()) if "year_built" in df.columns else 100.0,
            "price_pct": as_pct(df[price_col].isna().mean()) if price_col in df.columns else 100.0,
        },
        "status_distribution": (
            df["status_group"].fillna("missing").value_counts(dropna=False).to_dict()
            if "status_group" in df.columns
            else {}
        ),
        "zip_distribution": (
            df["zip"].fillna("missing").astype(str).value_counts(dropna=False).to_dict()
            if "zip" in df.columns
            else {}
        ),
        "ppsf_mismatch_pct": as_pct(mismatch.mean()) if not mismatch.empty else 0.0,
        "rental_contamination_count": rental_contamination,
    }


def evaluate_gate(metrics: dict, thresholds: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []

    for dataset in ["active", "sold"]:
        m = metrics.get(dataset, {})
        if m.get("rows", 0) == 0:
            failures.append(f"{dataset}: no rows")
        missingness = m.get("missingness", {})
        if missingness.get("sqft_pct", 100.0) > thresholds["max_missing_sqft_pct"]:
            failures.append(f"{dataset}: missing sqft too high")
        if missingness.get("year_built_pct", 100.0) > thresholds["max_missing_year_built_pct"]:
            failures.append(f"{dataset}: missing year_built too high")
        if missingness.get("price_pct", 100.0) > thresholds["max_missing_price_pct"]:
            failures.append(f"{dataset}: missing price too high")

    rental = metrics.get("rental", {})
    if rental.get("rental_contamination_count", 0) > thresholds["max_rental_contamination_count"]:
        failures.append("rental: contains for-sale/sold intent rows")

    return (len(failures) == 0, failures)


def sanitize_thresholds(thresholds: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    cleaned = dict(thresholds)
    warnings: list[str] = []

    for key, default in DEFAULT_THRESHOLDS.items():
        value = cleaned.get(key, default)
        invalid = value is None
        if isinstance(default, float):
            invalid = invalid or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0
        else:
            invalid = invalid or not isinstance(value, int) or value < 0

        if invalid:
            cleaned[key] = default
            warnings.append(f"{key} invalid ({value}); defaulted to {default}")
        elif isinstance(default, float):
            cleaned[key] = float(value)

    return cleaned, warnings


def sold_completeness(snapshot: Path, sold_metrics: dict[str, Any]) -> dict[str, Any]:
    ndjson_path = snapshot / "out" / "extracted" / "har_responses.ndjson"
    base = {
        "request_count": 0,
        "response_row_counts": [],
        "raw_rows": int(sold_metrics.get("raw_rows", 0)),
        "paging_detected": False,
        "likely_capped": False,
        "cap_severity": "none",
        "warnings": [],
    }
    if not ndjson_path.exists():
        return base

    request_counts: list[int] = []
    paging_detected = False

    with ndjson_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            payload = row.get("payload") or {}
            request_params = row.get("request_params") or {}
            if not isinstance(payload, dict):
                continue

            is_sold_request = row.get("dataset_hint") == "sold" or isinstance(payload.get("sold_data"), list)
            if not is_sold_request:
                continue

            if PAGINATION_PARAMS.intersection({str(key).lower() for key in request_params.keys()}):
                paging_detected = True

            count = 0
            if isinstance(payload.get("sold_data"), list):
                count = len(payload["sold_data"])
            elif row.get("dataset_hint") == "sold" and isinstance(payload.get("data"), list):
                count = len(payload["data"])

            request_counts.append(int(count))

    warnings: list[str] = []
    likely_capped = False
    raw_rows = int(sold_metrics.get("raw_rows", 0))
    cap_hits = [count for count in request_counts if count in COMMON_RESULT_CAPS]
    if request_counts and cap_hits and not paging_detected:
        if len(request_counts) == 1 and request_counts[0] == raw_rows:
            likely_capped = True
            warnings.append(
                f"sold results came from a single request returning {request_counts[0]} rows without pagination metadata"
            )
        elif raw_rows in COMMON_RESULT_CAPS:
            likely_capped = True
            warnings.append(f"sold raw row count matches a common response cap ({raw_rows})")

    if likely_capped:
        cap_severity = "high"
    elif raw_rows < 80:
        cap_severity = "low"
    elif raw_rows in COMMON_RESULT_CAPS:
        cap_severity = "medium"
    else:
        cap_severity = "none"

    return {
        "request_count": len(request_counts),
        "response_row_counts": request_counts,
        "raw_rows": raw_rows,
        "paging_detected": paging_detected,
        "likely_capped": likely_capped,
        "cap_severity": cap_severity,
        "warnings": warnings,
    }


def render_summary(report: dict[str, Any]) -> list[str]:
    lines = [
        f"Snapshot: {report['snapshot_id']}",
        f"QA status: {'PASS' if report['passed'] else 'FAIL'}",
        "",
    ]
    for name in ["active", "sold", "rental"]:
        m = report["datasets"][name]
        missingness = m["missingness"]
        lines.append(
            f"[{name}] rows={m['rows']} unique={m['unique_listings']} dupes={m['duplicate_rows']}"
        )
        lines.append(
            f"  missing sqft/year/price = {missingness['sqft_pct']}% / {missingness['year_built_pct']}% / {missingness['price_pct']}%"
        )
        lines.append(f"  ppsf mismatch >10%: {m['ppsf_mismatch_pct']}%")
        lines.append(f"  zip distribution: {m['zip_distribution']}")
        if name == "rental":
            lines.append(f"  rental contamination count: {m['rental_contamination_count']}")

    sold_completeness_report = report.get("completeness", {}).get("sold", {})
    if sold_completeness_report:
        lines.extend(
            [
                "",
                "[sold completeness]",
                f"  requests={sold_completeness_report.get('request_count', 0)} rows={sold_completeness_report.get('response_row_counts', [])}",
                f"  likely capped: {sold_completeness_report.get('likely_capped', False)}",
                f"  cap severity: {sold_completeness_report.get('cap_severity', 'none')}",
            ]
        )

    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(["", "Warnings:"] + [f"- {item}" for item in warnings])
    failures = report.get("failures", [])
    if failures:
        lines.extend(["", "Failures:"] + [f"- {item}" for item in failures])
    return lines


def run_qa(
    snapshot: Path,
    max_missing_sqft_pct: float = 35.0,
    max_missing_year_built_pct: float = 50.0,
    max_missing_price_pct: float = 35.0,
    max_rental_contamination_count: int = 0,
) -> dict:
    norm_dir = snapshot / "out" / "normalized"
    qa_dir = snapshot / "out" / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    normalize_report = load_json(norm_dir / "normalize_report.json")

    datasets = {
        "active": load_csv(norm_dir / "active.csv"),
        "sold": load_csv(norm_dir / "sold.csv"),
        "rental": load_csv(norm_dir / "rentals.csv"),
    }

    metrics = {
        name: dataset_metrics(name, df, normalize_report.get("datasets", {}).get(name))
        for name, df in datasets.items()
    }
    thresholds, threshold_warnings = sanitize_thresholds(
        {
        "max_missing_sqft_pct": max_missing_sqft_pct,
        "max_missing_year_built_pct": max_missing_year_built_pct,
        "max_missing_price_pct": max_missing_price_pct,
        "max_rental_contamination_count": max_rental_contamination_count,
        }
    )
    passed, failures = evaluate_gate(metrics, thresholds)
    completeness = {"sold": sold_completeness(snapshot, metrics["sold"])}
    warnings = threshold_warnings + completeness["sold"]["warnings"]

    report = {
        "snapshot_id": snapshot.name,
        "passed": passed,
        "failures": failures,
        "warnings": warnings,
        "thresholds": thresholds,
        "datasets": metrics,
        "completeness": completeness,
    }

    report_path = qa_dir / "qa_report.json"
    summary_path = qa_dir / "qa_summary.txt"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = render_summary(report)
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "snapshot_id": snapshot.name,
        "passed": passed,
        "failures": failures,
        "warnings": warnings,
        "report_path": report_path,
        "summary_path": summary_path,
        "summary_lines": lines,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QA checks for a snapshot")
    parser.add_argument("--snapshot", help="Snapshot pack path")
    parser.add_argument("--max-missing-sqft-pct", type=float, default=35.0)
    parser.add_argument("--max-missing-year-built-pct", type=float, default=50.0)
    parser.add_argument("--max-missing-price-pct", type=float, default=35.0)
    parser.add_argument("--max-rental-contamination-count", type=int, default=0)
    args = parser.parse_args()

    snapshot = find_snapshot(args.snapshot)
    result = run_qa(
        snapshot=snapshot,
        max_missing_sqft_pct=args.max_missing_sqft_pct,
        max_missing_year_built_pct=args.max_missing_year_built_pct,
        max_missing_price_pct=args.max_missing_price_pct,
        max_rental_contamination_count=args.max_rental_contamination_count,
    )

    print("\n".join(result["summary_lines"]))
    print(f"\nWrote: {result['report_path']}")
    print(f"Wrote: {result['summary_path']}")


if __name__ == "__main__":
    main()
