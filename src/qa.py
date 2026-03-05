"""Run QA checks on normalized snapshot data and produce gate artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


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


def dataset_metrics(name: str, df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "rows": 0,
            "unique_listing_ids": 0,
            "duplicate_rows": 0,
            "missing_sqft_pct": 100.0,
            "missing_year_built_pct": 100.0,
            "missing_price_pct": 100.0,
            "status_distribution": {},
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
        "unique_listing_ids": int(df["listing_id"].nunique(dropna=True)) if "listing_id" in df.columns else 0,
        "duplicate_rows": int(df.duplicated(subset=["dataset", "listing_id", "request_url"], keep="first").sum())
        if set(["dataset", "listing_id", "request_url"]).issubset(df.columns)
        else int(df.duplicated().sum()),
        "missing_sqft_pct": as_pct(df["sqft"].isna().mean()) if "sqft" in df.columns else 100.0,
        "missing_year_built_pct": as_pct(df["year_built"].isna().mean()) if "year_built" in df.columns else 100.0,
        "missing_price_pct": as_pct(df[price_col].isna().mean()) if price_col in df.columns else 100.0,
        "status_distribution": (
            df["status_group"].fillna("missing").value_counts(dropna=False).to_dict()
            if "status_group" in df.columns
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
        if m.get("missing_sqft_pct", 100.0) > thresholds["max_missing_sqft_pct"]:
            failures.append(f"{dataset}: missing sqft too high")
        if m.get("missing_year_built_pct", 100.0) > thresholds["max_missing_year_built_pct"]:
            failures.append(f"{dataset}: missing year_built too high")
        if m.get("missing_price_pct", 100.0) > thresholds["max_missing_price_pct"]:
            failures.append(f"{dataset}: missing price too high")

    rental = metrics.get("rental", {})
    if rental.get("rental_contamination_count", 0) > thresholds["max_rental_contamination_count"]:
        failures.append("rental: contains for-sale/sold intent rows")

    return (len(failures) == 0, failures)


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

    datasets = {
        "active": load_csv(norm_dir / "active.csv"),
        "sold": load_csv(norm_dir / "sold.csv"),
        "rental": load_csv(norm_dir / "rentals.csv"),
    }

    metrics = {name: dataset_metrics(name, df) for name, df in datasets.items()}
    thresholds = {
        "max_missing_sqft_pct": max_missing_sqft_pct,
        "max_missing_year_built_pct": max_missing_year_built_pct,
        "max_missing_price_pct": max_missing_price_pct,
        "max_rental_contamination_count": max_rental_contamination_count,
    }
    passed, failures = evaluate_gate(metrics, thresholds)

    report = {
        "snapshot_id": snapshot.name,
        "passed": passed,
        "failures": failures,
        "thresholds": thresholds,
        "datasets": metrics,
    }

    report_path = qa_dir / "qa_report.json"
    summary_path = qa_dir / "qa_summary.txt"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        f"Snapshot: {snapshot.name}",
        f"QA status: {'PASS' if passed else 'FAIL'}",
        "",
    ]
    for name in ["active", "sold", "rental"]:
        m = metrics[name]
        lines.append(f"[{name}] rows={m['rows']} unique={m['unique_listing_ids']} dupes={m['duplicate_rows']}")
        lines.append(
            f"  missing sqft/year/price = {m['missing_sqft_pct']}% / {m['missing_year_built_pct']}% / {m['missing_price_pct']}%"
        )
        lines.append(f"  ppsf mismatch >10%: {m['ppsf_mismatch_pct']}%")
        if name == "rental":
            lines.append(f"  rental contamination count: {m['rental_contamination_count']}")
    if failures:
        lines.extend(["", "Failures:"] + [f"- {item}" for item in failures])
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "snapshot_id": snapshot.name,
        "passed": passed,
        "failures": failures,
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
