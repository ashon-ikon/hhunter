"""
hcad_ingest.py — Load HCAD TSV files into a local DuckDB analytical database.

Run once (or re-run to refresh):
    python -m src.hcad_ingest
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

HCAD_DIR = Path("/mnt/ssd/projects/hcad-land/Real_acct_owner")
DB_PATH = Path("data/hcad.duckdb")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest(hcad_dir: Path = HCAD_DIR, db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))

    _load_properties(con, hcad_dir / "real_acct.txt")
    _load_owners(con, hcad_dir / "owners.txt")
    _load_deeds(con, hcad_dir / "deeds.txt")
    _load_permits(con, hcad_dir / "permits.txt")
    _load_neighborhoods(con, hcad_dir / "real_neighborhood_code.txt")
    _create_views(con)

    return con


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_properties(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    log.info("Loading properties from %s …", path.name)
    con.execute("DROP TABLE IF EXISTS properties")
    con.execute(f"""
        CREATE TABLE properties AS
        SELECT
            TRIM(acct)                              AS acct,
            TRY_CAST(yr AS INTEGER)                 AS yr,
            TRIM(site_addr_1)                       AS site_addr_1,
            TRIM(site_addr_2)                       AS site_addr_2,
            TRIM(site_addr_3)                       AS zip,
            TRIM(state_class)                       AS state_class,
            TRIM(school_dist)                       AS school_dist,
            TRIM(Neighborhood_Code)                 AS neighborhood_code,
            TRIM(Neighborhood_Grp)                  AS neighborhood_grp,
            TRIM(Market_Area_1)                     AS market_area_1,
            TRIM(Market_Area_1_Dscr)                AS market_area_1_dscr,
            TRIM(econ_area)                         AS econ_area,
            TRY_CAST(yr_impr AS INTEGER)            AS yr_impr,
            TRY_CAST(bld_ar AS INTEGER)             AS bld_ar,
            TRY_CAST(land_ar AS INTEGER)            AS land_ar,
            TRY_CAST(acreage AS DOUBLE)             AS acreage,
            TRY_CAST(land_val AS BIGINT)            AS land_val,
            TRY_CAST(bld_val AS BIGINT)             AS bld_val,
            TRY_CAST(tot_appr_val AS BIGINT)        AS tot_appr_val,
            TRY_CAST(tot_mkt_val AS BIGINT)         AS tot_mkt_val,
            TRY_CAST(prior_land_val AS BIGINT)      AS prior_land_val,
            TRY_CAST(prior_bld_val AS BIGINT)       AS prior_bld_val,
            TRY_CAST(prior_tot_appr_val AS BIGINT)  AS prior_tot_appr_val,
            TRY_CAST(prior_tot_mkt_val AS BIGINT)   AS prior_tot_mkt_val,
            TRY_CAST(new_construction_val AS BIGINT) AS new_construction_val,
            TRY_CAST(tot_rcn_val AS BIGINT)         AS tot_rcn_val,
            TRIM(value_status)                      AS value_status,
            TRY_STRPTIME(TRIM(new_own_dt), '%m/%d/%Y') AS new_own_dt,
            TRIM(lgl_1)                             AS lgl_1,
        FROM read_csv(
            '{path}',
            sep='\t',
            header=true,
            quote='',
            null_padding=true,
            all_varchar=true,
            ignore_errors=true
        )
    """)
    n = con.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    log.info("  %s properties loaded", f"{n:,}")


def _load_owners(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    log.info("Loading owners from %s …", path.name)
    con.execute("DROP TABLE IF EXISTS owners")
    con.execute(f"""
        CREATE TABLE owners AS
        SELECT
            TRIM(acct)                  AS acct,
            TRY_CAST(ln_num AS INTEGER) AS ln_num,
            TRIM(name)                  AS name,
            TRY_CAST(pct_own AS DOUBLE) AS pct_own,
        FROM read_csv('{path}', sep='\t', header=true, quote='', null_padding=true, all_varchar=true, ignore_errors=true)
    """)
    n = con.execute("SELECT COUNT(*) FROM owners").fetchone()[0]
    log.info("  %s owner records loaded", f"{n:,}")


def _load_deeds(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    log.info("Loading deeds from %s …", path.name)
    con.execute("DROP TABLE IF EXISTS deeds")
    con.execute(f"""
        CREATE TABLE deeds AS
        SELECT
            TRIM(acct)                                      AS acct,
            TRY_STRPTIME(TRIM(dos), '%m/%d/%Y')            AS deed_date,
            TRY_CAST(clerk_yr AS INTEGER)                  AS clerk_yr,
            TRIM(clerk_id)                                  AS clerk_id,
            TRY_CAST(deed_id AS INTEGER)                   AS deed_id,
        FROM read_csv('{path}', sep='\t', header=true, quote='', null_padding=true, all_varchar=true, ignore_errors=true)
    """)
    n = con.execute("SELECT COUNT(*) FROM deeds").fetchone()[0]
    log.info("  %s deed records loaded", f"{n:,}")


def _load_permits(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    log.info("Loading permits from %s …", path.name)
    con.execute("DROP TABLE IF EXISTS permits")
    con.execute(f"""
        CREATE TABLE permits AS
        SELECT
            TRIM(acct)                                      AS acct,
            id                                             AS permit_id,
            TRIM(agency_id)                                AS agency_id,
            TRIM(status)                                   AS status,
            TRIM(dscr)                                     AS description,
            TRIM(permit_type)                              AS permit_type,
            TRIM(permit_tp_descr)                          AS permit_type_dscr,
            TRY_STRPTIME(TRIM(issue_date), '%m/%d/%Y')     AS issue_date,
            TRY_CAST(yr AS INTEGER)                        AS yr,
        FROM read_csv('{path}', sep='\t', header=true, quote='', null_padding=true, all_varchar=true, ignore_errors=true)
    """)
    n = con.execute("SELECT COUNT(*) FROM permits").fetchone()[0]
    log.info("  %s permit records loaded", f"{n:,}")


def _load_neighborhoods(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    log.info("Loading neighborhood codes …")
    con.execute("DROP TABLE IF EXISTS neighborhood_codes")
    con.execute(f"""
        CREATE TABLE neighborhood_codes AS
        SELECT
            TRIM(cd)                        AS code,
            TRY_CAST(grp_cd AS INTEGER)     AS grp_cd,
            TRIM(dscr)                      AS description,
        FROM read_csv('{path}', sep='\t', header=true, quote='', null_padding=true, all_varchar=true, ignore_errors=true)
    """)
    n = con.execute("SELECT COUNT(*) FROM neighborhood_codes").fetchone()[0]
    log.info("  %s neighborhood codes loaded", f"{n:,}")


# ---------------------------------------------------------------------------
# Analytical views
# ---------------------------------------------------------------------------


def _create_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create filtered views used by all downstream analysis."""

    # Core SFR filter: single-family + condo, valid ZIP, sane size + value
    con.execute("""
        CREATE OR REPLACE VIEW sfr AS
        SELECT *
        FROM properties
        WHERE state_class IN ('A1', 'A2')
          AND bld_ar > 200
          AND tot_mkt_val > 20000
          AND value_status NOT LIKE '%Pending%'
          AND LENGTH(zip) = 5
          AND zip SIMILAR TO '[0-9]{5}'
    """)

    # Enriched view: YOY % change + price/sqft, requires prior year data
    con.execute("""
        CREATE OR REPLACE VIEW sfr_enriched AS
        SELECT *,
            CASE
                WHEN prior_tot_mkt_val > 0
                THEN ROUND(100.0 * (tot_mkt_val - prior_tot_mkt_val) / prior_tot_mkt_val, 2)
                ELSE NULL
            END AS yoy_pct,
            CASE
                WHEN bld_ar > 0 THEN ROUND(tot_mkt_val::DOUBLE / bld_ar, 2)
                ELSE NULL
            END AS price_per_sqft,
            CASE
                WHEN tot_rcn_val > 0 THEN ROUND(tot_mkt_val::DOUBLE / tot_rcn_val, 3)
                ELSE NULL
            END AS mkt_to_rcn_ratio,
            CASE
                WHEN yr_impr IS NOT NULL AND yr_impr > 1800
                THEN (2026 - yr_impr)
                ELSE NULL
            END AS building_age,
        FROM sfr
    """)

    log.info("Views created: sfr, sfr_enriched")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    con = ingest()
    log.info("")
    log.info("Done. Tables: properties, owners, deeds, permits, neighborhood_codes")
    log.info("Views:  sfr, sfr_enriched")
    log.info("DB:     %s", DB_PATH.resolve())
    con.close()


if __name__ == "__main__":
    main()
