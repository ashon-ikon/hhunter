# Architecture

House Hunter has two independent analysis systems sharing a codebase and a Flask web server.

---

## System Map

```
┌──────────────────────────────────────────────────────────────────┐
│  HCAD PUBLIC DATA                                                │
│  real_acct.txt  owners.txt  deeds.txt  permits.txt              │
│  (TSV, ~1.6M–2.5M rows each)                                    │
└─────────────────────┬────────────────────────────────────────────┘
                      │  hcad_ingest.py (ETL, ~2 min)
                      ▼
┌──────────────────────────────────────────────────────────────────┐
│  DuckDB  (data/hcad.duckdb, ~1 GB)                              │
│                                                                  │
│  tables:  properties  owners  deeds  permits  neighborhood_codes │
│  views:   sfr  sfr_enriched                                      │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ├─── hcad_scores.py ──► ZIP-level DataFrames (6 metrics)
       │                              │
       │         hcad_maps.py ◄───────┘
       │              │  Folium choropleth HTML
       │              ▼
       │      static/hcad_maps/*.html
       │
       └─── hcad_screener.py ──► Parcel-level ranked DataFrame
                                         │
                                         ▼
                              hcad_app.py (Flask)
                              ├── /                → dashboard
                              ├── /map/<key>       → choropleth map
                              ├── /screener        → deal screener UI
                              ├── /regen[/<key>]   → regenerate maps
                              └── /api/zip-insight → HAR.com live data

┌──────────────────────────────────────────────────────────────────┐
│  HAR.COM  (live API, no auth)                                    │
│  https://www.har.com/api/SearchListings                          │
└──────────────────────┬───────────────────────────────────────────┘
                       │  Browser HAR export  OR  direct API call
                       ▼
               snapshots/<id>/raw/har/
                       │
                       ├── extract_har.py     ← parse HAR / JSON
                       ├── normalize_har.py   ← clean CSV tables
                       ├── analyze_spreads.py ← cohort + spread
                       ├── grid_analysis.py   ← 400m grid scoring
                       └── visualize.py       ← terminal viewer
```

---

## HCAD Pipeline

### ETL — `hcad_ingest.py`

Loads all five HCAD TSV files into DuckDB with `all_varchar=true` to prevent automatic type inference (which breaks `TRIM()` on date and numeric columns):

```python
read_csv(path, sep='\t', header=true, quote='',
         null_padding=true, all_varchar=true, ignore_errors=true)
```

**Key views created:**

`sfr` — Single-family residential parcels:
- `state_class IN ('A1', 'A2')`
- `bld_ar > 200` (has a real structure)
- Valid 5-digit ZIP
- `value_status NOT LIKE '%Pending%'`

`sfr_enriched` — Adds four derived metrics:
| Column | Formula |
|--------|---------|
| `yoy_pct` | `100 × (tot_mkt_val - prior_tot_mkt_val) / prior_tot_mkt_val` |
| `price_per_sqft` | `tot_mkt_val / bld_ar` |
| `mkt_to_rcn_ratio` | `tot_mkt_val / tot_rcn_val` (< 1 = buying below replacement cost) |
| `building_age` | `2026 - yr_impr` |

---

### ZIP Scoring — `hcad_scores.py`

Six functions, each returning a ZIP-level DataFrame:

| Function | Output columns | Notes |
|----------|---------------|-------|
| `price_heatmap()` | `median_price_per_sqft`, `median_value`, `median_sqft`, `num_properties` | |
| `yoy_heatmap()` | `median_yoy_pct`, `pct_appreciating`, `pct_surging` | `pct_surging` = YOY > 10% |
| `investor_heatmap()` | `investor_pct`, `investor_owned` | Entity detection: LLC, LP, Trust, Inc, Corp, Invest, Holdings, Realty, Partners |
| `permit_surge_heatmap()` | `recent_permit_rate`, `permits_since_2023`, `new_construction` | |
| `gentrification_score()` | `gentrification_score` | 40% YOY + 30% permit rate + 30% investor pct, normalized 0–100 |
| `flip_potential_heatmap()` | `flip_score`, `pct_below_rcn`, `pct_unrenovated`, `median_building_age`, `median_years_held` | 35% below-RCN + 25% unrenovated + 25% age + 15% years held |

`all_scores()` returns all six DataFrames as a dict.

---

### Map Generation — `hcad_maps.py`

Uses Folium (`folium.Choropleth`) to render Leaflet.js maps over CartoDB Positron tiles.

**GeoJSON source**: `https://raw.githubusercontent.com/OpenDataDE/State-zip-code-GeoJSON/master/tx_texas_zip_codes_geo.min.json`
Cached to `data/houston_zcta.geojson` after first fetch. Filtered to 138 Houston-area ZIPs.

**ZIP polygon key**: The GeoJSON uses `ZCTA5CE10`; the code normalizes it to `ZCTA5CE` to match the score DataFrames.

**Focus ZIPs** (77021, 77088): rendered with a blue dashed overlay layer (`#1a1aff`, dashArray `6 3`, weight 3).

**Cross-metric ZIP data blob**: `build_all_zip_json(scores)` merges selected columns from all 6 DataFrames by ZIP into a single JSON object embedded in every map's `<script>` tag. This is what powers the ZIP insert card — any map can show all 18 metrics because the full dataset is baked into the HTML.

**Snapshot UI** (`_build_snapshot_ui()`): injected as raw HTML/CSS/JS via `folium.Element`. Contains:
- Right panel: focus ZIP insights + top 5 ZIPs + Houston median
- Left card: ZIP insert card (hidden until a polygon is clicked)
- Snapshot button with `[S]` keyboard shortcut
- `html2canvas` (CDN) for client-side PNG capture

---

### Deal Screener — `hcad_screener.py`

All scoring is computed in a single DuckDB SQL query using CTEs. No Python loops over rows.

**CTE chain:**
```
zip_stats        ← ZIP-level median ppsf, value, YOY, mkt-to-rcn
latest_deed      ← Most recent deed date per acct
latest_permit    ← Permit count and recency per acct
primary_owner    ← Owner name + entity/individual classification
base             ← JOIN all of the above + compute derived fields
SELECT *         ← Add all 5 score columns inline
ORDER BY <score_col> DESC
LIMIT <n * 3>    ← Over-fetch, then filter by min_score in Python
```

**Score formulas** (all 0–100, weights sum to 100%):

```sql
-- Flip Score
LEAST(GREATEST(0, 1 - mkt_to_rcn_ratio), 1.0) * 100 * 0.25   -- RCN gap
+ unrenovated * 100 * 0.20                                      -- no permits since 2015
+ LEAST(building_age / 80.0, 1.0) * 100 * 0.20                -- age
+ LEAST(GREATEST(0, -ppsf_vs_median), 0.5) * 100 * 0.20       -- discount to ZIP median
+ LEAST(years_held / 20.0, 1.0) * 100 * 0.15                  -- ownership lag
```

`ppsf_vs_median = (parcel_ppsf - zip_median_ppsf) / zip_median_ppsf`
— negative = trading at a discount.

**Signal generation** (`deal_signals()`): Takes a row Series and returns 2–4 human-readable bullet strings based on thresholds (e.g., > 15% below RCN, held ≥ 10 years, > 10% below ZIP median $/sqft).

---

### Flask App — `hcad_app.py`

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Dashboard with 6 map cards and screener CTA |
| `/map/<key>` | GET | Serve pre-generated HTML map (price/yoy/investor/permits/gentrification/flip) |
| `/regen` | GET | Regenerate all 6 maps |
| `/regen/<key>` | GET | Regenerate one map |
| `/screener` | GET | Deal screener UI; supports all filter params + `fmt=csv` |
| `/api/zip-insight/<zip>` | GET | Live HAR.com market data for one ZIP (JSON) |

`/api/zip-insight/<zip>` fetches `https://www.har.com/api/SearchListings` with `view=map` and computes: active count, median list price, median $/sqft, median DOM, sold count, median sold price.

---

## HAR Snapshot Pipeline

### Data Flow

```
Browser DevTools export  ─→  extract_har.py
  (.har, .json, .jsonc)            │
                                   ▼
                        out/extracted/listings_raw.json
                                   │
                          normalize_har.py
                                   │
                   ┌───────────────┼──────────────────┐
                   ▼               ▼                  ▼
             active.csv        sold.csv          rentals.csv
                   │               │
              analyze_spreads.py ──┘
                   │
          ┌────────┼──────────────┐
          ▼        ▼              ▼
   ranked_candidates  scoreboard  streets_top
          │
   grid_analysis.py
          │
   grid_scoreboard  grid_candidates  grid_streets
```

### Cohort Algorithm

For a subject property, find comparable sold listings with:
- Exact ZIP match
- Same `PROPTYPENAME`
- Beds ±1, baths ±1
- Sqft ±15%
- Year built ±10 (or ±8 for infill built ≥ 2018)
- DOM ≤ 120

**Spread**: `active_ppsf - median(cohort_ppsf)`

Negative spread = priced below comparable recent sales.

---

## External APIs

| API | Auth | Usage |
|-----|------|-------|
| `www.har.com/api/SearchListings` | None | Live listing data (active, sold, rentals) per ZIP |
| `raw.githubusercontent.com/OpenDataDE/...` | None | Texas ZIP code GeoJSON polygons |
| `search.hcad.org/GISMap/?hcad_num=...` | None (api_key in URL) | HCAD property detail page (per-account link) |
| `arcweb.hcad.org/server/rest/services/public/public_query/MapServer/0/query` | None | HCAD parcel polygons + centroids by HCAD_NUM (ArcGIS REST) |

The ArcGIS REST API (`arcweb.hcad.org`) returns parcel geometry and centroid lat/lon by account number. This is the foundation for Phase 3 H3 grid maps without requiring external geocoding.

---

## Data Schema

### DuckDB tables

**`properties`** (from `real_acct.txt`):

| Column | Type | Notes |
|--------|------|-------|
| `acct` | VARCHAR | HCAD account number (PK) |
| `site_addr_1` | VARCHAR | Street address |
| `zip` | VARCHAR | 5-digit ZIP |
| `state_class` | VARCHAR | `A1` = SFR, `A2` = residential condo |
| `bld_ar` | DOUBLE | Living area (sqft) |
| `land_ar` | DOUBLE | Lot area (sqft) |
| `acreage` | DOUBLE | Lot size in acres |
| `yr_impr` | INTEGER | Year structure built |
| `tot_mkt_val` | DOUBLE | Total market value |
| `tot_rcn_val` | DOUBLE | Replacement cost new |
| `land_val` | DOUBLE | Land value only |
| `bld_val` | DOUBLE | Improvement value |
| `prior_tot_mkt_val` | DOUBLE | Prior year market value |
| `neighborhood_code` | VARCHAR | HCAD neighborhood code |
| `market_area_1_dscr` | VARCHAR | Market area description |

**`owners`** (from `owners.txt`):
- `acct`, `ln_num` (line number, 1 = primary owner), `name`

**`deeds`** (from `deeds.txt`):
- `acct`, `deed_date`, `instrument_num`

**`permits`** (from `permits.txt`):
- `acct`, `issue_date`, `permit_type`, `description`

---

## Performance Notes

- DuckDB processes all 1.6M parcels for a scored screener query in ~2–4 seconds (in-process, no network)
- Map generation (all 6 maps) takes ~30 seconds
- The `sfr_enriched` view is computed on every query — no materialization needed given DuckDB's speed
- GeoJSON is fetched once and cached locally; subsequent `hcad-maps` runs use the cache
