"""
hcad_scores.py — Compute ZIP-level analytical scores from HCAD DuckDB.

Each function returns a pandas DataFrame with `zip` as the join key.
Composite scores are normalized 0–100 where 100 = highest opportunity.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path("data/hcad.duckdb")


def connect(db_path: Path = DB_PATH, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=read_only)


# ---------------------------------------------------------------------------
# Individual score DataFrames
# ---------------------------------------------------------------------------


def price_heatmap(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Median price/sqft and home value by ZIP — the baseline affordability map."""
    return con.execute("""
        SELECT
            zip,
            COUNT(*)                                    AS num_properties,
            ROUND(MEDIAN(price_per_sqft), 2)            AS median_price_per_sqft,
            ROUND(MEDIAN(tot_mkt_val), 0)::BIGINT        AS median_value,
            ROUND(MEDIAN(bld_ar), 0)::INTEGER            AS median_sqft,
            ROUND(MEDIAN(acreage), 3)                   AS median_acreage,
            ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY tot_mkt_val), 0)::BIGINT AS p25_value,
            ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tot_mkt_val), 0)::BIGINT AS p75_value,
        FROM sfr_enriched
        WHERE price_per_sqft BETWEEN 20 AND 2000
        GROUP BY zip
        HAVING COUNT(*) >= 10
        ORDER BY median_price_per_sqft DESC
    """).df()


def yoy_heatmap(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Year-over-year value change % by ZIP (prior year → current appraisal year)."""
    return con.execute("""
        SELECT
            zip,
            COUNT(*)                                                                    AS num_properties,
            ROUND(MEDIAN(yoy_pct), 2)                                                  AS median_yoy_pct,
            ROUND(AVG(yoy_pct), 2)                                                     AS avg_yoy_pct,
            ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY yoy_pct), 2)            AS p75_yoy_pct,
            ROUND(100.0 * SUM(CASE WHEN yoy_pct > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_appreciating,
            ROUND(100.0 * SUM(CASE WHEN yoy_pct > 10 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_surging,
        FROM sfr_enriched
        WHERE yoy_pct BETWEEN -50 AND 200
        GROUP BY zip
        HAVING COUNT(*) >= 10
        ORDER BY median_yoy_pct DESC
    """).df()


def investor_heatmap(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """LLC / institutional ownership concentration by ZIP.
    High % = investor-saturated (competitive); rising % = early gentrification signal.
    """
    return con.execute("""
        WITH primary_owners AS (
            SELECT acct, name
            FROM owners
            WHERE ln_num = 1
        ),
        tagged AS (
            SELECT
                p.zip,
                p.acct,
                CASE WHEN
                    po.name ILIKE '%LLC%'
                    OR po.name ILIKE '% LP%'
                    OR po.name ILIKE '%TRUST%'
                    OR po.name ILIKE '% INC%'
                    OR po.name ILIKE '%CORP%'
                    OR po.name ILIKE '% LTD%'
                    OR po.name ILIKE '%INVEST%'
                    OR po.name ILIKE '%REALTY%'
                    OR po.name ILIKE '%HOLDINGS%'
                    OR po.name ILIKE '%PROPERTIES%'
                    OR po.name ILIKE '%PARTNERS%'
                THEN 1 ELSE 0 END AS is_investor
            FROM sfr p
            JOIN primary_owners po ON p.acct = po.acct
        )
        SELECT
            zip,
            COUNT(*)                                                        AS num_properties,
            SUM(is_investor)                                               AS investor_owned,
            ROUND(100.0 * SUM(is_investor) / COUNT(*), 2)                  AS investor_pct,
            ROUND(100.0 * (COUNT(*) - SUM(is_investor)) / COUNT(*), 2)     AS owner_occupied_pct,
        FROM tagged
        GROUP BY zip
        HAVING COUNT(*) >= 10
        ORDER BY investor_pct DESC
    """).df()


def permit_surge_heatmap(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Permit activity rate by ZIP.
    High recent permits = active renovation / new construction wave = gentrification signal.
    """
    return con.execute("""
        WITH zip_base AS (
            SELECT zip, COUNT(*) AS total_props
            FROM sfr
            GROUP BY zip
            HAVING total_props >= 10
        ),
        zip_permits AS (
            SELECT
                p.zip,
                COUNT(DISTINCT pe.acct)                                             AS props_with_permits,
                COUNT(pe.permit_id)                                                 AS total_permits,
                SUM(CASE WHEN pe.issue_date >= '2020-01-01' THEN 1 ELSE 0 END)     AS permits_since_2020,
                SUM(CASE WHEN pe.issue_date >= '2023-01-01' THEN 1 ELSE 0 END)     AS permits_since_2023,
                SUM(CASE WHEN pe.permit_type IN ('1','2','3') THEN 1 ELSE 0 END)   AS new_construction,
                SUM(CASE WHEN pe.permit_type IN ('31','32') THEN 1 ELSE 0 END)     AS remodel_permits,
            FROM sfr p
            JOIN permits pe ON p.acct = pe.acct
            GROUP BY p.zip
        )
        SELECT
            zb.zip,
            zb.total_props,
            COALESCE(zp.props_with_permits, 0)      AS props_with_permits,
            COALESCE(zp.total_permits, 0)           AS total_permits,
            COALESCE(zp.permits_since_2020, 0)      AS permits_since_2020,
            COALESCE(zp.permits_since_2023, 0)      AS permits_since_2023,
            COALESCE(zp.new_construction, 0)        AS new_construction,
            COALESCE(zp.remodel_permits, 0)         AS remodel_permits,
            ROUND(100.0 * COALESCE(zp.permits_since_2023, 0) / zb.total_props, 2) AS recent_permit_rate,
            ROUND(100.0 * COALESCE(zp.props_with_permits, 0) / zb.total_props, 2) AS permit_coverage_pct,
        FROM zip_base zb
        LEFT JOIN zip_permits zp ON zb.zip = zp.zip
        ORDER BY recent_permit_rate DESC
    """).df()


def flip_potential_heatmap(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """ZIP-level flip potential signals.
    Looks for: below-replacement-cost properties, aged unrenovated stock,
    long holding periods, and price spread below neighborhood median.
    """
    # Most recent deed per property
    con.execute("""
        CREATE OR REPLACE TEMP VIEW latest_deed AS
        SELECT acct, MAX(deed_date) AS last_deed_date
        FROM deeds
        GROUP BY acct
    """)
    # Most recent permit per property
    con.execute("""
        CREATE OR REPLACE TEMP VIEW latest_permit AS
        SELECT acct, MAX(issue_date) AS last_permit_date
        FROM permits
        GROUP BY acct
    """)

    return con.execute("""
        WITH enriched AS (
            SELECT
                p.zip,
                p.acct,
                p.tot_mkt_val,
                p.tot_rcn_val,
                p.bld_ar,
                p.building_age,
                p.price_per_sqft,
                p.mkt_to_rcn_ratio,
                ld.last_deed_date,
                lp.last_permit_date,
                -- Years held by current owner
                CASE
                    WHEN ld.last_deed_date IS NOT NULL
                    THEN DATEDIFF('year', ld.last_deed_date, CURRENT_DATE)
                    ELSE NULL
                END AS years_held,
                -- No meaningful renovation in 10+ years
                CASE
                    WHEN lp.last_permit_date IS NULL
                      OR lp.last_permit_date < '2015-01-01'
                    THEN 1 ELSE 0
                END AS unrenovated,
                -- Below replacement cost (good buy signal)
                CASE WHEN mkt_to_rcn_ratio < 0.85 THEN 1 ELSE 0 END AS below_rcn,
            FROM sfr_enriched p
            LEFT JOIN latest_deed  ld ON p.acct = ld.acct
            LEFT JOIN latest_permit lp ON p.acct = lp.acct
            WHERE p.tot_rcn_val > 20000
        ),
        zip_medians AS (
            SELECT zip, MEDIAN(price_per_sqft) AS zip_median_ppsf
            FROM enriched
            WHERE price_per_sqft IS NOT NULL
            GROUP BY zip
        )
        SELECT
            e.zip,
            COUNT(*)                                                                AS num_properties,
            ROUND(MEDIAN(e.building_age), 0)::INTEGER                              AS median_building_age,
            ROUND(MEDIAN(e.years_held), 1)                                         AS median_years_held,
            ROUND(MEDIAN(e.mkt_to_rcn_ratio), 3)                                   AS median_mkt_to_rcn,
            ROUND(100.0 * SUM(e.unrenovated) / COUNT(*), 1)                        AS pct_unrenovated,
            ROUND(100.0 * SUM(e.below_rcn) / COUNT(*), 1)                          AS pct_below_rcn,
            ROUND(zm.zip_median_ppsf, 2)                                           AS median_price_per_sqft,
        FROM enriched e
        JOIN zip_medians zm ON e.zip = zm.zip
        GROUP BY e.zip, zm.zip_median_ppsf
        HAVING COUNT(*) >= 10
        ORDER BY pct_below_rcn DESC
    """).df()


# ---------------------------------------------------------------------------
# Composite scores
# ---------------------------------------------------------------------------


def gentrification_score(
    yoy: pd.DataFrame,
    permits: pd.DataFrame,
    investors: pd.DataFrame,
) -> pd.DataFrame:
    """Composite 0–100 gentrification score per ZIP.

    Weights:
      40% — YOY value appreciation (median %)
      30% — Recent permit rate (permits since 2023 / total props)
      30% — Investor ownership % (LLC / trust concentration)
    """
    df = (
        yoy[["zip", "median_yoy_pct"]]
        .merge(permits[["zip", "recent_permit_rate"]], on="zip", how="outer")
        .merge(investors[["zip", "investor_pct"]], on="zip", how="outer")
        .fillna(0)
    )

    def norm(col: pd.Series) -> pd.Series:
        mn, mx = col.min(), col.max()
        return (col - mn) / (mx - mn + 1e-9) * 100

    df["gentrification_score"] = (
        norm(df["median_yoy_pct"]) * 0.40
        + norm(df["recent_permit_rate"]) * 0.30
        + norm(df["investor_pct"]) * 0.30
    ).round(1)

    return df.sort_values("gentrification_score", ascending=False)


def flip_score(flip: pd.DataFrame) -> pd.DataFrame:
    """Composite 0–100 flip potential score per ZIP.

    Weights:
      35% — % properties below replacement cost
      25% — % unrenovated stock
      25% — Median building age
      15% — Median years held (longer = potentially more motivated sellers)
    """
    df = flip.copy()

    def norm(col: pd.Series) -> pd.Series:
        mn, mx = col.min(), col.max()
        return (col - mn) / (mx - mn + 1e-9) * 100

    df["flip_score"] = (
        norm(df["pct_below_rcn"]) * 0.35
        + norm(df["pct_unrenovated"]) * 0.25
        + norm(df["median_building_age"].fillna(0)) * 0.25
        + norm(df["median_years_held"].fillna(0)) * 0.15
    ).round(1)

    return df.sort_values("flip_score", ascending=False)


# ---------------------------------------------------------------------------
# Convenience: compute all scores at once
# ---------------------------------------------------------------------------


def all_scores(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    price   = price_heatmap(con)
    yoy     = yoy_heatmap(con)
    inv     = investor_heatmap(con)
    permits = permit_surge_heatmap(con)
    flip    = flip_potential_heatmap(con)

    gent    = gentrification_score(yoy, permits, inv)
    flipsco = flip_score(flip)

    return {
        "price":           price,
        "yoy":             yoy,
        "investor":        inv,
        "permits":         permits,
        "gentrification":  gent,
        "flip":            flipsco,
    }
