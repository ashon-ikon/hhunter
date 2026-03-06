"""Segment-first cohort analysis for legacy SFR flip candidate hunting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

CONFIDENCE_PENALTY_PPSF = {
    "A": 0.0,
    "B": 5.0,
    "C": 12.5,
    "D": 20.0,
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
        raise FileNotFoundError("No snapshots found.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def require_qa_pass(snapshot: Path, force: bool) -> None:
    qa_path = snapshot / "out" / "qa" / "qa_report.json"
    if not qa_path.exists():
        if not force:
            raise RuntimeError("QA report missing. Run: qa --snapshot <snapshot>")
        return
    report = json.loads(qa_path.read_text(encoding="utf-8"))
    if not report.get("passed", False) and not force:
        raise RuntimeError("QA gate failed. Re-run with --force to bypass.")


def load_norm(snapshot: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    norm = snapshot / "out" / "normalized"
    return (
        pd.read_csv(norm / "active.csv") if (norm / "active.csv").exists() else pd.DataFrame(),
        pd.read_csv(norm / "sold.csv") if (norm / "sold.csv").exists() else pd.DataFrame(),
        pd.read_csv(norm / "rentals.csv") if (norm / "rentals.csv").exists() else pd.DataFrame(),
    )


def parse_zip_whitelist(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    values = {item.strip().zfill(5) for item in raw.split(",") if item.strip()}
    return values or None


def filter_zip_whitelist(df: pd.DataFrame, zip_whitelist: set[str] | None) -> pd.DataFrame:
    if df.empty or not zip_whitelist or "zip" not in df.columns:
        return df
    work = df.copy()
    return work[work["zip"].astype(str).str.zfill(5).isin(zip_whitelist)]


def legacy_segment(
    df: pd.DataFrame,
    require_flip_box: bool = False,
    max_list_price: float | None = None,
) -> pd.DataFrame:
    out = df.copy()
    out = out[out["proptype"].isin(["Single-Family", "Single Family", "single_family"])]
    out = out[out["era_bucket"].isin(["pre1980", "1980_1999"])]
    out = out[out["size_bucket"].isin(["1200_1800"])]
    out = out[out["beds"].between(3, 4, inclusive="both")]
    bath_total = out["baths_full"].fillna(0) + (0.5 * out["baths_half"].fillna(0))
    out = out[bath_total.between(1.5, 2.5, inclusive="both")]
    if require_flip_box and "flip_box_flag" in out.columns:
        out = out[out["flip_box_flag"] == True]
    if max_list_price is not None and "list_price_num" in out.columns:
        out = out[out["list_price_num"].notna() & (out["list_price_num"] <= max_list_price)]
    return out


def build_cohort(subject: pd.Series, sold: pd.DataFrame, min_n: int = 10) -> tuple[pd.DataFrame, str]:
    base = legacy_segment(sold)
    base = base[base["zip"].astype(str).str.zfill(5) == str(subject.get("zip", "")).zfill(5)]

    if base.empty:
        return base, "tight"

    # Tight strategy: sqft +/-15%, year +/-20
    tight = base.copy()
    sqft = subject.get("sqft")
    year_built = subject.get("year_built")

    if pd.notna(sqft) and sqft > 0:
        tight = tight[tight["sqft"].between(sqft * 0.85, sqft * 1.15)]
    if pd.notna(year_built):
        tight = tight[tight["year_built"].between(year_built - 20, year_built + 20)]

    if len(tight) >= min_n:
        return tight, "tight"

    # Relaxed strategy: keep hard legacy segment + zip only
    relaxed = base.copy()
    return relaxed, "relaxed"


def confidence_grade(n: int, iqr: float | None, strategy: str) -> str:
    if n >= 20 and strategy == "tight":
        return "A"
    if n >= 10 and strategy == "tight":
        return "B"
    if n >= 10 and strategy == "relaxed":
        return "C"
    return "D"


def confidence_penalty_ppsf(grade: str) -> float:
    return CONFIDENCE_PENALTY_PPSF.get(grade, CONFIDENCE_PENALTY_PPSF["D"])


def percentile_metrics(series: pd.Series) -> dict[str, float]:
    return {
        "sold_ppsf_p30": float(series.quantile(0.30)),
        "sold_ppsf_p50": float(series.quantile(0.50)),
        "sold_ppsf_p70": float(series.quantile(0.70)),
        "sold_ppsf_p85": float(series.quantile(0.85)),
    }


def analyze_candidates(
    active: pd.DataFrame,
    sold: pd.DataFrame,
    min_n: int = 10,
    require_flip_box: bool = True,
    max_list_price: float | None = None,
    min_upside_to_p70: float | None = None,
    sold_data_capped: bool = False,
    sold_cap_severity: str = "none",
) -> pd.DataFrame:
    work = legacy_segment(active, require_flip_box=require_flip_box, max_list_price=max_list_price)
    rows: list[dict] = []

    for _, subject in work.iterrows():
        if pd.isna(subject.get("sqft")) or pd.isna(subject.get("list_price_num")):
            continue
        cohort, strategy = build_cohort(subject, sold, min_n=min_n)
        n = len(cohort)
        if n == 0:
            continue

        sold_ppsf = cohort["calc_ppsf_sold"].dropna()
        if sold_ppsf.empty:
            continue

        percentiles = percentile_metrics(sold_ppsf)
        iqr_ppsf = float(sold_ppsf.quantile(0.75) - sold_ppsf.quantile(0.25))
        subject_ppsf = float(subject["calc_ppsf_list"])
        spread = percentiles["sold_ppsf_p50"] - subject_ppsf
        grade = confidence_grade(n, iqr_ppsf, strategy)
        discount_vs_p30 = percentiles["sold_ppsf_p30"] - subject_ppsf
        upside_to_p70 = percentiles["sold_ppsf_p70"] - subject_ppsf
        upside_to_p85 = percentiles["sold_ppsf_p85"] - subject_ppsf
        rank_score = upside_to_p70 - confidence_penalty_ppsf(grade)

        rows.append(
            {
                "listing_id": subject.get("listing_id"),
                "mlsnum": subject.get("mlsnum"),
                "address": subject.get("address"),
                "zip": subject.get("zip"),
                "beds": subject.get("beds"),
                "baths": (subject.get("baths_full") or 0) + 0.5 * (subject.get("baths_half") or 0),
                "sqft": subject.get("sqft"),
                "yearbuilt": subject.get("year_built"),
                "list_price": subject.get("list_price_num"),
                "flip_box_flag": subject.get("flip_box_flag"),
                "calc_ppsf_list": subject_ppsf,
                "sold_ppsf_p30": percentiles["sold_ppsf_p30"],
                "sold_median_ppsf": percentiles["sold_ppsf_p50"],
                "sold_ppsf_p50": percentiles["sold_ppsf_p50"],
                "sold_ppsf_p70": percentiles["sold_ppsf_p70"],
                "sold_ppsf_p85": percentiles["sold_ppsf_p85"],
                "sold_iqr_ppsf": iqr_ppsf,
                "cohort_n": n,
                "spread": spread,
                "discount_vs_p30": discount_vs_p30,
                "upside_to_p70": upside_to_p70,
                "upside_to_p85": upside_to_p85,
                "confidence_penalty_ppsf": confidence_penalty_ppsf(grade),
                "rank_score": rank_score,
                "confidence_grade": grade,
                "dom": subject.get("dom"),
                "subdivision": subject.get("subdivision"),
                "url": subject.get("url"),
                "cohort_strategy": strategy,
                "snapshot_id": subject.get("snapshot_id"),
                "sold_data_capped": sold_data_capped,
                "sold_cap_severity": sold_cap_severity,
            }
        )

    if not rows:
        return pd.DataFrame()

    ranked = pd.DataFrame(rows)
    ranked = ranked[ranked["confidence_grade"] != "D"]
    ranked = ranked[ranked["cohort_n"] >= min_n]
    ranked = ranked[ranked["sqft"].notna() & ranked["list_price"].notna()]
    if min_upside_to_p70 is not None:
        ranked = ranked[ranked["upside_to_p70"] >= min_upside_to_p70]
    ranked = ranked.sort_values(
        ["rank_score", "upside_to_p70", "discount_vs_p30", "confidence_grade"],
        ascending=[False, False, False, True],
    )
    return ranked


def build_scoreboard(active: pd.DataFrame, sold: pd.DataFrame) -> pd.DataFrame:
    sold_seg = sold.groupby(["zip", "era_bucket", "size_bucket"], dropna=False).agg(
        sold_ppsf_p30=("calc_ppsf_sold", lambda s: s.quantile(0.30)),
        sold_median_ppsf=("calc_ppsf_sold", "median"),
        sold_ppsf_p70=("calc_ppsf_sold", lambda s: s.quantile(0.70)),
        sold_ppsf_p85=("calc_ppsf_sold", lambda s: s.quantile(0.85)),
        sold_median_dom=("dom", "median"),
        count_sold=("listing_id", "count"),
    )
    active_seg = active.groupby(["zip", "era_bucket", "size_bucket"], dropna=False).agg(
        active_median_ppsf=("calc_ppsf_list", "median"),
    )
    board = sold_seg.join(active_seg, how="outer").reset_index()
    board["active_minus_sold"] = board["active_median_ppsf"] - board["sold_median_ppsf"]
    return board.sort_values("count_sold", ascending=False)


def build_streets(active: pd.DataFrame, sold: pd.DataFrame) -> pd.DataFrame:
    sold_legacy = legacy_segment(sold)
    sold_legacy = sold_legacy[sold_legacy["street_name"].notna()].copy()
    active_legacy = legacy_segment(active)
    active_legacy = active_legacy[active_legacy["street_name"].notna()].copy()

    if sold_legacy.empty:
        return pd.DataFrame()

    zip_median = sold_legacy.groupby("zip")["calc_ppsf_sold"].median().rename("zip_median_ppsf")
    sold_legacy = sold_legacy.join(zip_median, on="zip")
    sold_legacy["flip_proxy"] = (sold_legacy["calc_ppsf_sold"] >= sold_legacy["zip_median_ppsf"]) & (
        sold_legacy["dom"] <= 45
    )
    sold_legacy["new_construction"] = sold_legacy["year_built"] >= 2022

    sold_streets = sold_legacy.groupby(["street_name", "zip"], dropna=False).agg(
        count_sold_flips=("flip_proxy", "sum"),
        sold_median_ppsf_legacy=("calc_ppsf_sold", "median"),
        median_dom_sold_legacy=("dom", "median"),
        new_construction_count=("new_construction", "sum"),
        sold_count_legacy=("listing_id", "count"),
    ).reset_index()

    active_streets = active_legacy.groupby(["street_name", "zip"], dropna=False).agg(
        active_count_legacy=("listing_id", "count"),
    ).reset_index()

    streets = sold_streets.merge(active_streets, on=["street_name", "zip"], how="left")
    streets["active_count_legacy"] = streets["active_count_legacy"].fillna(0).astype(int)
    return streets.sort_values(
        ["count_sold_flips", "sold_count_legacy", "sold_median_ppsf_legacy"],
        ascending=[False, False, False],
    )


def run_analysis(
    snapshot: Path,
    force: bool = False,
    min_cohort: int = 10,
    require_flip_box: bool = True,
    max_list_price: float | None = None,
    min_upside_to_p70: float | None = None,
    zip_whitelist: set[str] | None = None,
) -> dict:
    require_qa_pass(snapshot, force)
    active, sold, _ = load_norm(snapshot)
    active = filter_zip_whitelist(active, zip_whitelist)
    sold = filter_zip_whitelist(sold, zip_whitelist)
    if active.empty or sold.empty:
        raise SystemExit("Active or sold normalized dataset is empty.")

    qa_path = snapshot / "out" / "qa" / "qa_report.json"
    sold_data_capped = False
    sold_cap_severity = "none"
    if qa_path.exists():
        report = json.loads(qa_path.read_text(encoding="utf-8"))
        sold_completeness = report.get("completeness", {}).get("sold", {})
        sold_data_capped = bool(sold_completeness.get("likely_capped", False))
        sold_cap_severity = str(sold_completeness.get("cap_severity", "none"))

    analysis_dir = snapshot / "out" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    ranked = analyze_candidates(
        active,
        sold,
        min_n=min_cohort,
        require_flip_box=require_flip_box,
        max_list_price=max_list_price,
        min_upside_to_p70=min_upside_to_p70,
        sold_data_capped=sold_data_capped,
        sold_cap_severity=sold_cap_severity,
    )
    scoreboard = build_scoreboard(active, sold)
    streets = build_streets(active, sold)
    scoreboard["sold_data_capped"] = sold_data_capped
    scoreboard["sold_cap_severity"] = sold_cap_severity
    streets["sold_data_capped"] = sold_data_capped
    streets["sold_cap_severity"] = sold_cap_severity

    ranked_path = analysis_dir / "ranked_candidates.csv"
    scoreboard_path = analysis_dir / "scoreboard_segments.csv"
    streets_path = analysis_dir / "streets_top.csv"

    ranked.to_csv(ranked_path, index=False)
    scoreboard.to_csv(scoreboard_path, index=False)
    streets.to_csv(streets_path, index=False)
    return {
        "snapshot_name": snapshot.name,
        "ranked_count": len(ranked),
        "scoreboard_count": len(scoreboard),
        "streets_count": len(streets),
        "sold_data_capped": sold_data_capped,
        "sold_cap_severity": sold_cap_severity,
        "ranked_path": ranked_path,
        "scoreboard_path": scoreboard_path,
        "streets_path": streets_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze snapshot for legacy flip hunting")
    parser.add_argument("--snapshot", help="Snapshot pack path")
    parser.add_argument("--force", action="store_true", help="Bypass failed/missing QA gate")
    parser.add_argument("--min-cohort", type=int, default=10)
    parser.add_argument(
        "--include-non-flip-box",
        action="store_true",
        help="Do not require flip_box_flag for ranked candidates",
    )
    parser.add_argument(
        "--max-list-price",
        type=float,
        help="Only rank actives at or below this list price",
    )
    parser.add_argument(
        "--min-upside-to-p70",
        type=float,
        help="Only rank candidates with upside to cohort p70 at or above this threshold",
    )
    parser.add_argument(
        "--zip-whitelist",
        help="Comma-separated ZIP whitelist for ranked, scoreboard, and street outputs",
    )
    args = parser.parse_args()

    snapshot = find_snapshot(args.snapshot)
    result = run_analysis(
        snapshot=snapshot,
        force=args.force,
        min_cohort=args.min_cohort,
        require_flip_box=not args.include_non_flip_box,
        max_list_price=args.max_list_price,
        min_upside_to_p70=args.min_upside_to_p70,
        zip_whitelist=parse_zip_whitelist(args.zip_whitelist),
    )

    print(f"Snapshot: {result['snapshot_name']}")
    print(f"Sold data capped: {result['sold_data_capped']}")
    print(f"Sold cap severity: {result['sold_cap_severity']}")
    print(f"Ranked candidates: {result['ranked_count']} -> {result['ranked_path']}")
    print(f"Scoreboard segments: {result['scoreboard_count']} -> {result['scoreboard_path']}")
    print(f"Street worksheet: {result['streets_count']} -> {result['streets_path']}")


if __name__ == "__main__":
    main()
