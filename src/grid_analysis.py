"""Grid-cell scouting analysis for snapshot listings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.analyze_spreads import (
    confidence_penalty_ppsf,
    filter_prospective_status,
    filter_zip_whitelist,
    find_snapshot,
    legacy_segment,
    load_norm,
    require_qa_pass,
)
from src.grid_utils import (
    DEFAULT_CELL_SIZE_M,
    assign_grid_fields,
    grid_cell_polygon,
    grid_spec_for_listings,
)


def select_segment(df: pd.DataFrame, segment: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    if segment != "legacy_sfr_flip":
        raise ValueError(f"Unsupported segment: {segment}")
    return legacy_segment(df, require_flip_box=True)


def normalize_series(
    series: pd.Series,
    *,
    invert: bool = False,
    clip_floor: float | None = None,
) -> pd.Series:
    base = series.astype(float)
    if clip_floor is not None:
        base = base.clip(lower=clip_floor)
    valid = base.dropna()
    if valid.empty:
        return pd.Series(0.0, index=series.index)

    minimum = float(valid.min())
    maximum = float(valid.max())
    if maximum == minimum:
        scaled = pd.Series(0.5, index=series.index)
        scaled[base.isna()] = 0.0
        return 1.0 - scaled if invert else scaled

    scaled = (base - minimum) / (maximum - minimum)
    scaled = scaled.fillna(0.0)
    return 1.0 - scaled if invert else scaled


def cell_confidence_grade(sold_count: int, dispersion_iqr: float | None, sold_cap_severity: str) -> str:
    if sold_count >= 20 and (dispersion_iqr or 0) <= 35:
        grade = "A"
    elif sold_count >= 12:
        grade = "B"
    elif sold_count >= 8:
        grade = "C"
    else:
        grade = "D"

    if sold_cap_severity == "severe":
        return {"A": "B", "B": "C", "C": "D", "D": "D"}[grade]
    if sold_cap_severity == "moderate":
        return {"A": "A", "B": "C", "C": "D", "D": "D"}[grade]
    return grade


def classify_cell(row: pd.Series) -> str:
    if row["sold_count"] < 5:
        return "avoid_sparse"
    if row["active_minus_sold"] > 20:
        return "avoid_overheated"
    if (
        row["sold_count"] >= 8
        and row["renovation_spread"] >= 12
        and row["sold_median_dom"] <= 45
        and row["active_minus_sold"] <= 12
    ):
        return "hunt_now"
    return "monitor"


def aggregate_cell_metrics(
    active: pd.DataFrame,
    sold: pd.DataFrame,
    rentals: pd.DataFrame,
    sold_cap_severity: str,
) -> pd.DataFrame:
    all_grid_ids = pd.Index(
        sorted(
            set(active["grid_id"].dropna().tolist())
            | set(sold["grid_id"].dropna().tolist())
            | set(rentals["grid_id"].dropna().tolist())
        )
    )
    if all_grid_ids.empty:
        return pd.DataFrame()

    scoreboard = pd.DataFrame(index=all_grid_ids)

    all_listings = pd.concat(
        [
            sold.assign(_listing_source="sold"),
            active.assign(_listing_source="active"),
            rentals.assign(_listing_source="rental"),
        ],
        ignore_index=True,
    )
    spatial_metrics = all_listings.groupby("grid_id", dropna=False).agg(
        cell_center_lat=("grid_centroid_lat", "first"),
        cell_center_lng=("grid_centroid_lng", "first"),
        grid_row=("grid_row", "first"),
        grid_col=("grid_col", "first"),
    )
    sold_metrics = sold.groupby("grid_id", dropna=False).agg(
        sold_count=("listing_id", "count"),
        sold_median_dom=("dom", "median"),
        sold_median_ppsf=("calc_ppsf_sold", "median"),
        dispersion_iqr=("calc_ppsf_sold", lambda s: s.quantile(0.75) - s.quantile(0.25)),
        sold_ppsf_p30=("calc_ppsf_sold", lambda s: s.quantile(0.30)),
        sold_ppsf_p70=("calc_ppsf_sold", lambda s: s.quantile(0.70)),
        sold_ppsf_p85=("calc_ppsf_sold", lambda s: s.quantile(0.85)),
    )
    active_metrics = active.groupby("grid_id", dropna=False).agg(
        active_count=("listing_id", "count"),
        active_median_ppsf=("calc_ppsf_list", "median"),
    )
    rental_metrics = rentals.groupby("grid_id", dropna=False).agg(
        rental_count=("listing_id", "count"),
    )

    for frame in (spatial_metrics, sold_metrics, active_metrics, rental_metrics):
        scoreboard = scoreboard.join(frame, how="left")
    transition_metrics = all_listings.groupby("grid_id", dropna=False).agg(
        listing_count=("listing_id", "count"),
        new_construction_count=("new_construction_flag", "sum"),
        legacy_sold_count=("status_group", lambda s: int((s == "sold").sum())),
        legacy_active_count=("status_group", lambda s: int((s == "active").sum())),
    )
    scoreboard = scoreboard.join(transition_metrics, how="left")
    scoreboard = scoreboard.reset_index(names="grid_id")

    count_columns = [
        "sold_count",
        "active_count",
        "rental_count",
        "listing_count",
        "new_construction_count",
        "legacy_sold_count",
        "legacy_active_count",
    ]
    for column in count_columns:
        if column not in scoreboard.columns:
            scoreboard[column] = 0
        scoreboard[column] = scoreboard[column].fillna(0).astype(int)

    for column in [
        "sold_median_dom",
        "sold_median_ppsf",
        "dispersion_iqr",
        "sold_ppsf_p30",
        "sold_ppsf_p70",
        "sold_ppsf_p85",
        "active_median_ppsf",
        "cell_center_lat",
        "cell_center_lng",
    ]:
        if column not in scoreboard.columns:
            scoreboard[column] = pd.NA

    scoreboard["new_construction_ratio"] = (
        scoreboard["new_construction_count"] / scoreboard["listing_count"].replace({0: pd.NA})
    ).fillna(0.0)
    scoreboard["renovation_spread"] = scoreboard["sold_ppsf_p70"] - scoreboard["sold_ppsf_p30"]
    scoreboard["top_tier_spread"] = scoreboard["sold_ppsf_p85"] - scoreboard["sold_ppsf_p30"]
    scoreboard["active_minus_sold"] = (
        scoreboard["active_median_ppsf"] - scoreboard["sold_median_ppsf"]
    ).fillna(0.0)
    scoreboard["sold_cap_severity"] = sold_cap_severity
    scoreboard["grid_confidence_grade"] = scoreboard.apply(
        lambda row: cell_confidence_grade(
            int(row["sold_count"]),
            None if pd.isna(row["dispersion_iqr"]) else float(row["dispersion_iqr"]),
            sold_cap_severity,
        ),
        axis=1,
    )

    scoreboard["renovation_spread_norm"] = normalize_series(scoreboard["renovation_spread"], clip_floor=0.0)
    scoreboard["sold_count_norm"] = normalize_series(scoreboard["sold_count"])
    scoreboard["velocity_norm"] = normalize_series(scoreboard["sold_median_dom"], invert=True)
    scoreboard["new_construction_ratio_norm"] = normalize_series(scoreboard["new_construction_ratio"])
    scoreboard["active_overpricing_norm"] = normalize_series(
        scoreboard["active_minus_sold"].clip(lower=0.0)
    )
    scoreboard["hunt_score"] = 100.0 * (
        (0.30 * scoreboard["renovation_spread_norm"])
        + (0.20 * scoreboard["sold_count_norm"])
        + (0.15 * scoreboard["velocity_norm"])
        + (0.15 * scoreboard["new_construction_ratio_norm"])
        - (0.20 * scoreboard["active_overpricing_norm"])
    )
    scoreboard["hunt_score"] = scoreboard["hunt_score"].round(2)
    scoreboard["cell_label"] = scoreboard.apply(classify_cell, axis=1)

    ordered_columns = [
        "grid_id",
        "grid_row",
        "grid_col",
        "cell_center_lat",
        "cell_center_lng",
        "sold_count",
        "active_count",
        "rental_count",
        "sold_median_dom",
        "sold_ppsf_p30",
        "sold_median_ppsf",
        "sold_ppsf_p70",
        "sold_ppsf_p85",
        "active_median_ppsf",
        "active_minus_sold",
        "renovation_spread",
        "top_tier_spread",
        "new_construction_count",
        "new_construction_ratio",
        "legacy_sold_count",
        "legacy_active_count",
        "dispersion_iqr",
        "hunt_score",
        "grid_confidence_grade",
        "cell_label",
        "sold_cap_severity",
    ]
    return scoreboard[ordered_columns].sort_values(
        ["hunt_score", "sold_count", "renovation_spread"],
        ascending=[False, False, False],
    )


def build_candidate_report(
    active: pd.DataFrame,
    scoreboard: pd.DataFrame,
    *,
    min_sold: int,
    min_active: int,
    include_pending: bool,
    rehab_rate_per_sqft: float,
    sell_cost_pct: float,
    holding_cost_est: float,
    target_profit: float,
) -> pd.DataFrame:
    eligible_cells = scoreboard[
        (scoreboard["sold_count"] >= min_sold)
        & (scoreboard["active_count"] >= min_active)
        & (scoreboard["cell_label"].isin(["hunt_now", "monitor"]))
    ][
        [
            "grid_id",
            "hunt_score",
            "grid_confidence_grade",
            "cell_label",
            "sold_ppsf_p30",
            "sold_ppsf_p70",
            "sold_ppsf_p85",
            "sold_median_ppsf",
            "renovation_spread",
        ]
    ]
    if eligible_cells.empty:
        return pd.DataFrame()

    prospective = filter_prospective_status(active, include_pending=include_pending)
    candidates = prospective.merge(eligible_cells, on="grid_id", how="inner")
    if candidates.empty:
        return pd.DataFrame()
    candidates = candidates[candidates["sqft"].notna() & candidates["calc_ppsf_list"].notna()].copy()
    if candidates.empty:
        return pd.DataFrame()

    candidates["upside_to_p70"] = candidates["sold_ppsf_p70"] - candidates["calc_ppsf_list"]
    candidates["upside_to_p85"] = candidates["sold_ppsf_p85"] - candidates["calc_ppsf_list"]
    candidates["arv_p70"] = candidates["sold_ppsf_p70"] * candidates["sqft"]
    candidates["arv_p85"] = candidates["sold_ppsf_p85"] * candidates["sqft"]
    candidates["rehab_budget_est"] = rehab_rate_per_sqft * candidates["sqft"]
    candidates["sell_cost_est"] = candidates["arv_p70"] * sell_cost_pct
    sell_cost_p85 = candidates["arv_p85"] * sell_cost_pct
    candidates["holding_cost_est"] = holding_cost_est
    candidates["max_offer_p70"] = (
        candidates["arv_p70"]
        - candidates["rehab_budget_est"]
        - candidates["sell_cost_est"]
        - holding_cost_est
        - target_profit
    )
    candidates["max_offer_p85"] = (
        candidates["arv_p85"]
        - candidates["rehab_budget_est"]
        - sell_cost_p85
        - holding_cost_est
        - target_profit
    )
    candidates["rank_score"] = candidates["upside_to_p70"] - candidates["grid_confidence_grade"].map(
        confidence_penalty_ppsf
    ).fillna(20.0)
    candidates["confidence_grade"] = candidates["grid_confidence_grade"]
    candidates["underwriting_scope"] = "triage_only"

    columns = [
        "grid_id",
        "address",
        "list_price_num",
        "calc_ppsf_list",
        "sold_ppsf_p30",
        "sold_ppsf_p70",
        "upside_to_p70",
        "upside_to_p85",
        "rank_score",
        "confidence_grade",
        "hunt_score",
        "cell_label",
        "sqft",
        "arv_p70",
        "arv_p85",
        "rehab_budget_est",
        "sell_cost_est",
        "holding_cost_est",
        "max_offer_p70",
        "max_offer_p85",
        "url",
    ]
    renamed = candidates[columns].rename(columns={"list_price_num": "list_price"})
    return renamed.sort_values(
        ["hunt_score", "rank_score", "upside_to_p70"],
        ascending=[False, False, False],
    )


def build_street_report(
    active: pd.DataFrame,
    sold: pd.DataFrame,
    scoreboard: pd.DataFrame,
    *,
    min_sold: int,
) -> pd.DataFrame:
    high_cells = scoreboard[
        (scoreboard["sold_count"] >= min_sold) & (scoreboard["cell_label"].isin(["hunt_now", "monitor"]))
    ]["grid_id"]
    if high_cells.empty:
        return pd.DataFrame()

    sold_streets = (
        sold[sold["grid_id"].isin(high_cells) & sold["street_name"].notna()]
        .groupby(["grid_id", "street_name"], dropna=False)
        .agg(
            sold_count_legacy=("listing_id", "count"),
            sold_median_ppsf_legacy=("calc_ppsf_sold", "median"),
            median_dom_sold_legacy=("dom", "median"),
            new_construction_count=("new_construction_flag", "sum"),
        )
        .reset_index()
    )
    active_streets = (
        active[active["grid_id"].isin(high_cells) & active["street_name"].notna()]
        .groupby(["grid_id", "street_name"], dropna=False)
        .agg(active_count_legacy=("listing_id", "count"))
        .reset_index()
    )
    if sold_streets.empty and active_streets.empty:
        return pd.DataFrame()

    streets = sold_streets.merge(active_streets, on=["grid_id", "street_name"], how="outer")
    streets["sold_count_legacy"] = streets["sold_count_legacy"].fillna(0).astype(int)
    streets["active_count_legacy"] = streets["active_count_legacy"].fillna(0).astype(int)
    streets["new_construction_count"] = streets["new_construction_count"].fillna(0).astype(int)

    streets["street_score"] = 100.0 * (
        (0.45 * normalize_series(streets["sold_count_legacy"]))
        + (0.20 * normalize_series(streets["active_count_legacy"]))
        + (0.20 * normalize_series(streets["new_construction_count"]))
        + (0.15 * normalize_series(streets["median_dom_sold_legacy"], invert=True))
    )
    return streets.sort_values(
        ["street_score", "sold_count_legacy", "new_construction_count"],
        ascending=[False, False, False],
    )


def build_geojson(scoreboard: pd.DataFrame, spec: Any) -> dict[str, Any]:
    features = []
    for row in scoreboard.itertuples(index=False):
        if pd.isna(row.grid_row) or pd.isna(row.grid_col):
            continue
        properties = {
            "grid_id": row.grid_id,
            "hunt_score": row.hunt_score,
            "sold_count": row.sold_count,
            "active_count": row.active_count,
            "renovation_spread": row.renovation_spread,
            "active_minus_sold": row.active_minus_sold,
            "confidence": row.grid_confidence_grade,
            "cell_label": row.cell_label,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [grid_cell_polygon(spec, int(row.grid_row), int(row.grid_col))],
                },
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def run_grid_analysis(
    snapshot: Path,
    *,
    force: bool = False,
    cell_size_m: float = DEFAULT_CELL_SIZE_M,
    min_sold: int = 5,
    min_active: int = 3,
    segment: str = "legacy_sfr_flip",
    export_geojson: bool = False,
    zip_whitelist: set[str] | None = None,
    include_pending: bool = False,
    rehab_rate_per_sqft: float = 45.0,
    sell_cost_pct: float = 0.08,
    holding_cost_est: float = 15_000.0,
    target_profit: float = 30_000.0,
) -> dict[str, Any]:
    require_qa_pass(snapshot, force)
    active, sold, rentals = load_norm(snapshot)
    active = filter_zip_whitelist(active, zip_whitelist)
    sold = filter_zip_whitelist(sold, zip_whitelist)
    rentals = filter_zip_whitelist(rentals, zip_whitelist)

    combined = pd.concat([active, sold, rentals], ignore_index=True)
    if combined.empty:
        raise SystemExit("No normalized listings available for grid analysis.")

    spec = grid_spec_for_listings(combined, cell_size_m=cell_size_m)
    active = assign_grid_fields(active, spec)
    sold = assign_grid_fields(sold, spec)
    rentals = assign_grid_fields(rentals, spec)

    active_segment = select_segment(active, segment)
    sold_segment = select_segment(sold, segment)
    rental_segment = select_segment(rentals, segment)
    if sold_segment.empty and active_segment.empty:
        raise SystemExit(f"Selected segment {segment} produced no active or sold listings.")

    qa_path = snapshot / "out" / "qa" / "qa_report.json"
    sold_cap_severity = "none"
    if qa_path.exists():
        report = json.loads(qa_path.read_text(encoding="utf-8"))
        sold_cap_severity = str(
            report.get("completeness", {}).get("sold", {}).get("cap_severity", "none")
        )

    scoreboard = aggregate_cell_metrics(active_segment, sold_segment, rental_segment, sold_cap_severity)
    candidates = build_candidate_report(
        active_segment,
        scoreboard,
        min_sold=min_sold,
        min_active=min_active,
        include_pending=include_pending,
        rehab_rate_per_sqft=rehab_rate_per_sqft,
        sell_cost_pct=sell_cost_pct,
        holding_cost_est=holding_cost_est,
        target_profit=target_profit,
    )
    streets = build_street_report(active_segment, sold_segment, scoreboard, min_sold=min_sold)

    analysis_dir = snapshot / "out" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    scoreboard_path = analysis_dir / "grid_scoreboard.csv"
    candidates_path = analysis_dir / "grid_candidates.csv"
    streets_path = analysis_dir / "grid_streets.csv"
    scoreboard.to_csv(scoreboard_path, index=False)
    candidates.to_csv(candidates_path, index=False)
    streets.to_csv(streets_path, index=False)

    result = {
        "snapshot_name": snapshot.name,
        "segment": segment,
        "cell_size_m": cell_size_m,
        "scoreboard_path": scoreboard_path,
        "candidates_path": candidates_path,
        "streets_path": streets_path,
        "scoreboard_count": int(len(scoreboard)),
        "candidates_count": int(len(candidates)),
        "streets_count": int(len(streets)),
    }
    if export_geojson:
        geojson_path = analysis_dir / "grid_scoreboard.geojson"
        geojson_path.write_text(json.dumps(build_geojson(scoreboard, spec), indent=2), encoding="utf-8")
        result["geojson_path"] = geojson_path
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run grid-based scouting analysis for a snapshot")
    parser.add_argument("--snapshot", help="Snapshot pack path")
    parser.add_argument("--force", action="store_true", help="Bypass failed/missing QA gate")
    parser.add_argument("--cell-size-m", type=float, default=DEFAULT_CELL_SIZE_M)
    parser.add_argument("--min-sold", type=int, default=5)
    parser.add_argument("--min-active", type=int, default=3)
    parser.add_argument("--segment", default="legacy_sfr_flip")
    parser.add_argument("--zip-whitelist", help="Comma-separated ZIP whitelist for grid outputs")
    parser.add_argument(
        "--include-pending-prospects",
        action="store_true",
        help="Include pending / under-contract listings in grid candidate outputs",
    )
    parser.add_argument("--export-geojson", action="store_true")
    parser.add_argument("--rehab-rate-per-sqft", type=float, default=45.0)
    parser.add_argument("--sell-cost-pct", type=float, default=0.08)
    parser.add_argument("--holding-cost-est", type=float, default=15000.0)
    parser.add_argument("--target-profit", type=float, default=30000.0)
    args = parser.parse_args()

    zip_whitelist = None
    if args.zip_whitelist:
        zip_whitelist = {item.strip().zfill(5) for item in args.zip_whitelist.split(",") if item.strip()}

    snapshot = find_snapshot(args.snapshot)
    result = run_grid_analysis(
        snapshot=snapshot,
        force=args.force,
        cell_size_m=args.cell_size_m,
        min_sold=args.min_sold,
        min_active=args.min_active,
        segment=args.segment,
        export_geojson=args.export_geojson,
        zip_whitelist=zip_whitelist,
        include_pending=args.include_pending_prospects,
        rehab_rate_per_sqft=args.rehab_rate_per_sqft,
        sell_cost_pct=args.sell_cost_pct,
        holding_cost_est=args.holding_cost_est,
        target_profit=args.target_profit,
    )

    print(f"Snapshot: {result['snapshot_name']}")
    print(f"Segment: {result['segment']}")
    print(f"Cell size (m): {result['cell_size_m']}")
    print(f"Grid scoreboard: {result['scoreboard_count']} -> {result['scoreboard_path']}")
    print(f"Grid candidates: {result['candidates_count']} -> {result['candidates_path']}")
    print(f"Grid streets: {result['streets_count']} -> {result['streets_path']}")
    if "geojson_path" in result:
        print(f"Grid geojson: {result['geojson_path']}")


if __name__ == "__main__":
    main()
