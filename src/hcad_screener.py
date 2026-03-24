"""
hcad_screener.py — Parcel-level deal screener.

All scoring is computed inside DuckDB SQL so it runs fast across 1.6M parcels.

Deal types
----------
flip       Buy distressed / unrenovated, renovate, resell at spread
brrr       Buy-Rehab-Rent-Refinance-Repeat: buy at discount, force equity via rehab
buy_hold   Long-term appreciation play: buy below market in appreciating corridor
land       Economically obsolete structure on land-value-dominant lot (teardown / redev)
wholesale  Deep-discount motivated-seller targeting: long hold + neglect + below median

CLI
---
python -m src.hcad_screener --type flip --zip 77021 77088 --limit 25
python -m src.hcad_screener --type brrr --min-score 55 --limit 50 --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path("data/hcad.duckdb")

# ---------------------------------------------------------------------------
# Score definitions (weights must sum to 1.0)
# ---------------------------------------------------------------------------

DEAL_TYPES = {
    "flip": {
        "label":       "Fix & Flip",
        "description": "Buy distressed / unrenovated, renovate, resell at spread.",
        "score_col":   "flip_score",
        "color":       "#f59e0b",
        "components": [
            ("RCN Gap",        "Below replacement-cost buy-in (structure discount)", 25),
            ("Unrenovated",    "No permits in 10+ years — cosmetic / structural lag", 20),
            ("Building Age",   "Older stock = more upside from renovation",          20),
            ("Price Discount", "Trading below ZIP median $/sqft",                    20),
            ("Ownership Lag",  "Long hold → potential motivation to sell",            15),
        ],
    },
    "brrr": {
        "label":       "BRRR",
        "description": "Buy-Rehab-Rent-Refinance-Repeat: force equity, pull cash out.",
        "score_col":   "brrr_score",
        "color":       "#8b5cf6",
        "components": [
            ("Buy Discount",    "Below replacement cost — room for forced appreciation", 30),
            ("Rehab Upside",    "Unrenovated stock — value can be added via rehab",      25),
            ("Building Age",    "Older structure = more rehab runway",                   20),
            ("Area Momentum",   "ZIP-level YOY appreciation (strong exit / refi base)",  15),
            ("Land Optionality","High land-value fraction → exit flexibility",            10),
        ],
    },
    "buy_hold": {
        "label":       "Buy & Hold",
        "description": "Long-term appreciation in a corridor with strong momentum.",
        "score_col":   "buy_hold_score",
        "color":       "#10b981",
        "components": [
            ("Area Momentum",  "ZIP YOY appreciation rate",                                   35),
            ("Entry Discount", "Below ZIP median $/sqft → buy well",                          25),
            ("Value Vintage",  "Older stock in appreciating area = unpriced upside",          20),
            ("Ownership Lag",  "Long-held properties in rising areas = below-market entry",   20),
        ],
    },
    "land": {
        "label":       "Land Play",
        "description": "Structure is economically obsolete; value is mostly in the land.",
        "score_col":   "land_score",
        "color":       "#ef4444",
        "components": [
            ("Land Dominance",  "Land value as % of total market value",      40),
            ("Structure Age",   "Older building on valuable land → teardown", 30),
            ("Lot Size",        "Larger lot = more development flexibility",   20),
            ("Area Trajectory", "Gentrifying ZIP → land value trend",          10),
        ],
    },
    "wholesale": {
        "label":       "Wholesale",
        "description": "Deep-discount off-market targeting: neglect + long hold + below median.",
        "score_col":   "wholesale_score",
        "color":       "#0ea5e9",
        "components": [
            ("Price Discount", "Deep below ZIP median $/sqft",                        30),
            ("Ownership Lag",  "Long-held by same owner → estate / fatigue signal",   25),
            ("Neglect Signal", "No permits in 10+ years → deferred maintenance",      25),
            ("RCN Gap",        "Below replacement cost → distressed pricing",          20),
        ],
    },
}

# ---------------------------------------------------------------------------
# Core screener query
# ---------------------------------------------------------------------------


def _build_query(
    zips: list[str] | None,
    state_classes: list[str],
    min_price: int | None,
    max_price: int | None,
    max_year_built: int | None,
    min_years_held: int | None,
    limit: int,
    deal_type: str,
) -> str:
    score_col = DEAL_TYPES[deal_type]["score_col"]

    zip_filter  = f"AND p.zip IN ({','.join(repr(z) for z in zips)})" if zips else ""
    cls_filter  = f"AND p.state_class IN ({','.join(repr(c) for c in state_classes)})"
    lo_filter   = f"AND p.tot_mkt_val >= {min_price}" if min_price else ""
    hi_filter   = f"AND p.tot_mkt_val <= {max_price}" if max_price else ""
    yr_filter   = f"AND p.yr_impr <= {max_year_built}" if max_year_built else ""

    return f"""
WITH
zip_stats AS (
    SELECT
        zip,
        MEDIAN(price_per_sqft)  AS zip_median_ppsf,
        MEDIAN(tot_mkt_val)     AS zip_median_val,
        MEDIAN(yoy_pct)         AS zip_yoy_pct,
        MEDIAN(mkt_to_rcn_ratio) AS zip_mkt_rcn,
    FROM sfr_enriched
    WHERE price_per_sqft IS NOT NULL
    GROUP BY zip
),
latest_deed AS (
    SELECT acct, MAX(deed_date) AS last_deed_date
    FROM deeds
    GROUP BY acct
),
latest_permit AS (
    SELECT acct,
        MAX(issue_date)  AS last_permit_date,
        COUNT(*)         AS permit_count,
        SUM(CASE WHEN issue_date >= '2020-01-01' THEN 1 ELSE 0 END) AS recent_permits
    FROM permits
    GROUP BY acct
),
primary_owner AS (
    SELECT acct, name AS owner_name,
        CASE
            WHEN name ILIKE '%LLC%' OR name ILIKE '% LP%' OR name ILIKE '%TRUST%'
              OR name ILIKE '% INC%' OR name ILIKE '%CORP%' OR name ILIKE '% LTD%'
              OR name ILIKE '%INVEST%' OR name ILIKE '%HOLDINGS%' OR name ILIKE '%REALTY%'
            THEN 'Entity'
            ELSE 'Individual'
        END AS owner_type
    FROM owners
    WHERE ln_num = 1
),
base AS (
    SELECT
        p.acct,
        p.site_addr_1                               AS address,
        p.zip,
        p.neighborhood_code,
        p.market_area_1_dscr                        AS market_area,
        p.state_class,
        p.bld_ar                                    AS sqft,
        p.land_ar,
        p.acreage,
        p.yr_impr,
        p.building_age,
        p.tot_mkt_val                               AS mkt_val,
        p.tot_rcn_val                               AS rcn_val,
        p.land_val,
        p.bld_val,
        p.price_per_sqft                            AS ppsf,
        p.mkt_to_rcn_ratio,
        p.yoy_pct,
        p.new_own_dt,
        ld.last_deed_date,
        DATEDIFF('year', ld.last_deed_date, CURRENT_DATE)       AS years_held,
        lp.last_permit_date,
        COALESCE(lp.permit_count, 0)                AS permit_count,
        COALESCE(lp.recent_permits, 0)              AS recent_permits,
        CASE
            WHEN lp.last_permit_date IS NULL
              OR lp.last_permit_date < '2015-01-01' THEN 1
            ELSE 0
        END                                         AS unrenovated,
        po.owner_name,
        po.owner_type,
        zs.zip_median_ppsf,
        zs.zip_median_val,
        zs.zip_yoy_pct,
        -- % above/below ZIP median (negative = discount)
        CASE WHEN zs.zip_median_ppsf > 0
             THEN (p.price_per_sqft - zs.zip_median_ppsf) / zs.zip_median_ppsf
             ELSE NULL END                          AS ppsf_vs_median,
        -- land value as fraction of total
        CASE WHEN p.tot_mkt_val > 0
             THEN p.land_val::DOUBLE / p.tot_mkt_val
             ELSE NULL END                          AS land_value_frac,
    FROM sfr_enriched p
    JOIN zip_stats zs ON p.zip = zs.zip
    LEFT JOIN latest_deed  ld ON p.acct = ld.acct
    LEFT JOIN latest_permit lp ON p.acct = lp.acct
    LEFT JOIN primary_owner po ON p.acct = po.acct
    WHERE 1=1
      {zip_filter}
      {cls_filter}
      {lo_filter}
      {hi_filter}
      {yr_filter}
      AND p.tot_mkt_val  > 10000
      AND p.price_per_sqft IS NOT NULL
      AND p.mkt_to_rcn_ratio IS NOT NULL
)
SELECT *,

    -- ── FLIP SCORE (0-100) ──────────────────────────────────────────────────
    ROUND(
          LEAST(GREATEST(0, (1 - mkt_to_rcn_ratio)), 1.0) * 100 * 0.25
        + unrenovated * 100 * 0.20
        + LEAST(COALESCE(building_age, 0) / 80.0, 1.0)    * 100 * 0.20
        + LEAST(COALESCE(years_held,   0) / 20.0, 1.0)    * 100 * 0.15
        + LEAST(GREATEST(0, COALESCE(-ppsf_vs_median, 0)), 0.5) * 100 * 0.20
    , 1) AS flip_score,

    -- ── BRRR SCORE ──────────────────────────────────────────────────────────
    ROUND(
          LEAST(GREATEST(0, (1 - mkt_to_rcn_ratio)), 1.0) * 100 * 0.30
        + unrenovated * 100 * 0.25
        + LEAST(COALESCE(building_age, 0) / 80.0, 1.0)                          * 100 * 0.20
        + LEAST(GREATEST(0, COALESCE(zip_yoy_pct, 0) / 15.0), 1.0)              * 100 * 0.15
        + LEAST(COALESCE(land_value_frac, 0), 1.0)                               * 100 * 0.10
    , 1) AS brrr_score,

    -- ── BUY & HOLD SCORE ────────────────────────────────────────────────────
    ROUND(
          LEAST(GREATEST(0, COALESCE(zip_yoy_pct, 0) / 15.0), 1.0)              * 100 * 0.35
        + LEAST(GREATEST(0, COALESCE(-ppsf_vs_median, 0)), 0.5)                 * 100 * 0.25
        + LEAST(COALESCE(building_age, 0) / 80.0, 1.0)                          * 100 * 0.20
        + LEAST(COALESCE(years_held,   0) / 15.0, 1.0)                          * 100 * 0.20
    , 1) AS buy_hold_score,

    -- ── LAND PLAY SCORE ─────────────────────────────────────────────────────
    ROUND(
          LEAST(COALESCE(land_value_frac, 0), 1.0)                               * 100 * 0.40
        + LEAST(COALESCE(building_age, 0) / 80.0, 1.0)                          * 100 * 0.30
        + LEAST(land_ar::DOUBLE / 12000.0, 1.0)                                  * 100 * 0.20
        + LEAST(GREATEST(0, COALESCE(zip_yoy_pct, 0) / 15.0), 1.0)              * 100 * 0.10
    , 1) AS land_score,

    -- ── WHOLESALE SCORE ─────────────────────────────────────────────────────
    ROUND(
          LEAST(GREATEST(0, COALESCE(-ppsf_vs_median, 0)), 0.6)                  * 100 * 0.30
        + LEAST(COALESCE(years_held,   0) / 20.0, 1.0)                           * 100 * 0.25
        + unrenovated * 100 * 0.25
        + LEAST(GREATEST(0, (1 - mkt_to_rcn_ratio)), 1.0)                        * 100 * 0.20
    , 1) AS wholesale_score

FROM base
ORDER BY {score_col} DESC
LIMIT {limit}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def screen(
    con: duckdb.DuckDBPyConnection,
    deal_type: str = "flip",
    zips: list[str] | None = None,
    min_score: float = 0.0,
    min_price: int | None = None,
    max_price: int | None = None,
    max_year_built: int | None = None,
    min_years_held: int | None = None,
    state_classes: list[str] | None = None,
    limit: int = 50,
) -> pd.DataFrame:
    """Run the deal screener and return a ranked DataFrame."""
    if deal_type not in DEAL_TYPES:
        raise ValueError(f"Unknown deal type: {deal_type}. Valid: {list(DEAL_TYPES)}")

    classes = state_classes or ["A1", "A2"]
    sql = _build_query(
        zips=zips,
        state_classes=classes,
        min_price=min_price,
        max_price=max_price,
        max_year_built=max_year_built,
        min_years_held=min_years_held,
        limit=limit * 3,   # over-fetch then filter by min_score
        deal_type=deal_type,
    )
    df = con.execute(sql).df()

    score_col = DEAL_TYPES[deal_type]["score_col"]
    if min_years_held:
        df = df[df["years_held"].fillna(0) >= min_years_held]
    df = df[df[score_col] >= min_score].head(limit)

    return df


def connect(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=True)


# ---------------------------------------------------------------------------
# Deal-type signal explanations (human-readable)
# ---------------------------------------------------------------------------


def deal_signals(row: pd.Series, deal_type: str) -> list[str]:
    """Return 2-4 bullet-point signal strings for a single property row."""
    signals: list[str] = []
    score_col = DEAL_TYPES[deal_type]["score_col"]
    score = row.get(score_col, 0)

    def _f(key, default=0):
        v = row.get(key, default)
        try:
            return float(v) if v is not None and v == v else default
        except Exception:
            return default

    mkt     = _f("mkt_val")
    rcn     = _f("rcn_val")
    age     = _f("building_age")
    held    = _f("years_held")
    disc    = _f("ppsf_vs_median")
    unren   = _f("unrenovated")
    lv_frac = _f("land_value_frac")
    yoy     = _f("zip_yoy_pct")
    ppsf    = _f("ppsf")
    med_ppsf= _f("zip_median_ppsf")

    if rcn > 0 and mkt < 0.85 * rcn:
        gap_pct = int((1 - mkt / rcn) * 100)
        signals.append(f"{gap_pct}% below replacement cost (${mkt:,.0f} mkt vs ${rcn:,.0f} RCN)")

    if unren:
        yrs = int(age) if age else "?"
        signals.append(f"No permits since 2015 — {yrs}-year-old unrenovated structure")

    if held and held >= 10:
        signals.append(f"Owned {int(held)} years — long hold may signal motivation")

    if disc < -0.10:
        signals.append(f"${ppsf:.0f}/sqft — {abs(disc)*100:.0f}% below ZIP median (${med_ppsf:.0f}/sqft)")

    if lv_frac > 0.70 and deal_type in ("land", "brrr"):
        signals.append(f"Land = {lv_frac*100:.0f}% of value — structure is economically obsolete")

    if yoy and yoy > 8 and deal_type in ("buy_hold", "brrr"):
        signals.append(f"ZIP appreciating at +{yoy:.1f}% YOY — strong exit / refi base")

    return signals[:4]  # cap at 4


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HCAD Parcel Deal Screener")
    p.add_argument("--type",      default="flip",
                   choices=list(DEAL_TYPES), help="Deal type to score for")
    p.add_argument("--zip",       nargs="+", help="ZIP codes to filter (space-separated)")
    p.add_argument("--limit",     type=int,  default=25,  help="Max results")
    p.add_argument("--min-score", type=float,default=40.0,help="Minimum deal score (0-100)")
    p.add_argument("--max-price", type=int,  help="Max market value ($)")
    p.add_argument("--min-price", type=int,  help="Min market value ($)")
    p.add_argument("--max-year",  type=int,  help="Max year built (e.g. 1970)")
    p.add_argument("--min-held",  type=int,  help="Minimum years held by current owner")
    p.add_argument("--csv",       type=str,  help="Save results to CSV file")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    con  = connect()
    meta = DEAL_TYPES[args.type]
    score_col = meta["score_col"]

    print(f"\n{'─'*70}")
    print(f"  {meta['label']} Screener  —  {meta['description']}")
    print(f"  ZIPs: {args.zip or 'All Harris County'}")
    print(f"{'─'*70}\n")

    df = screen(
        con,
        deal_type=args.type,
        zips=args.zip,
        min_score=args.min_score,
        max_price=args.max_price,
        min_price=args.min_price,
        max_year_built=args.max_year,
        min_years_held=args.min_held,
        limit=args.limit,
    )

    if df.empty:
        print("No results matched your filters.")
        return

    display_cols = ["address", "zip", "mkt_val", "ppsf", "zip_median_ppsf",
                    "yr_impr", "years_held", "unrenovated", "owner_name",
                    "owner_type", score_col]
    display_cols = [c for c in display_cols if c in df.columns]

    for i, (_, row) in enumerate(df.iterrows(), 1):
        score = row[score_col]
        bar = "█" * int(score // 5) + "░" * (20 - int(score // 5))
        def _safe_int(v, default=0):
            try:
                return int(v) if v is not None and v == v else default
            except Exception:
                return default

        print(f"  #{i:>2}  [{bar}] {score:>5.1f}  {row.get('address',''):<30}  {row.get('zip','')}")
        print(f"       ${_safe_int(row.get('mkt_val',0)):>10,}  |  "
              f"${_safe_int(row.get('ppsf',0))}/sqft (ZIP med ${_safe_int(row.get('zip_median_ppsf',0))})  |  "
              f"Built {_safe_int(row.get('yr_impr',0))}  |  "
              f"Held {_safe_int(row.get('years_held',0))} yrs  |  "
              f"{row.get('owner_type','?')}")
        for sig in deal_signals(row, args.type):
            print(f"         → {sig}")
        print()

    print(f"  {len(df)} results  ·  min score {args.min_score}")

    if args.csv:
        df.to_csv(args.csv, index=False, quoting=csv.QUOTE_NONNUMERIC)
        print(f"\n  Saved → {args.csv}")

    con.close()


if __name__ == "__main__":
    main()
