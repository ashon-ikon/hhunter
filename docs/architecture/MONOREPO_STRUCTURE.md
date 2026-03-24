# Project Structure

Current layout of the repository as of March 2026.

---

## Directory Tree

```
house-hunter/
│
├── src/                            ← Python package
│   ├── __init__.py
│   │
│   │   # ── HCAD web app ─────────────────────────────────────────
│   ├── hcad_ingest.py              ← ETL: load HCAD TSV files → DuckDB
│   ├── hcad_scores.py              ← ZIP-level scoring (6 heat map metrics)
│   ├── hcad_maps.py                ← Folium choropleth map generation
│   ├── hcad_screener.py            ← Parcel deal screener (5 deal types)
│   ├── hcad_app.py                 ← Flask application (routes + ZIP insight API)
│   │
│   │   # ── HAR snapshot pipeline ──────────────────────────────────
│   ├── extract_har.py              ← Parse HAR/JSON/JSONC files
│   ├── normalize_har.py            ← Normalize to clean CSV tables
│   ├── analyze_spreads.py          ← Cohort building + PPSF spread analysis
│   ├── grid_analysis.py            ← 400m grid cell scoring
│   ├── pipeline.py                 ← One-shot pipeline wrapper
│   ├── visualize.py                ← Terminal artifact viewer
│   └── fetch_searchlistings.py     ← Direct HAR.com API fetching
│
├── templates/                      ← Flask HTML templates
│   ├── hcad_dashboard.html         ← Map dashboard (6 map cards + screener CTA)
│   └── hcad_screener.html          ← Deal screener UI
│
├── static/
│   └── hcad_maps/                  ← Generated Folium HTML files
│       ├── price_per_sqft.html
│       ├── yoy_change.html
│       ├── investor_activity.html
│       ├── permit_surge.html
│       ├── gentrification_score.html
│       └── flip_potential.html
│
├── data/
│   ├── hcad.duckdb                 ← Analytical database (~1 GB, git-ignored)
│   └── houston_zcta.geojson        ← Cached Houston ZIP polygons (git-ignored)
│
├── snapshots/                      ← HAR snapshot packs (git-ignored)
│   └── YYYY-MM-DD_<label>/
│       ├── raw/
│       │   └── har/                ← Place .har or .json files here
│       └── out/
│           ├── extracted/          ← listings_raw.json, requests_index.csv
│           ├── normalized/         ← active.csv, sold.csv, rentals.csv
│           ├── qa/                 ← qa_report.json
│           └── analysis/           ← ranked_candidates.csv, scoreboard_segments.csv, ...
│
├── docs/
│   ├── guides/
│   │   ├── INSTALL.md              ← Setup, dependencies, HCAD data download
│   │   └── USAGE.md                ← Web app + CLI reference
│   ├── architecture/
│   │   ├── ARCHITECTURE.md         ← System design, data models, scoring formulas
│   │   └── MONOREPO_STRUCTURE.md   ← This file
│   └── roadmap/
│       └── ROADMAP.md              ← What's built, what's next
│
├── tests/                          ← Unit tests (in progress)
│
├── .vscode/
│   └── settings.json               ← Python interpreter path for VS Code
│
├── pyproject.toml                  ← Package config + CLI entry points
├── README.md                       ← Project overview
└── CONTRIBUTING.md
```

---

## Source Module Summary

### HCAD Pipeline

| Module | Responsibility |
|--------|---------------|
| `hcad_ingest.py` | Reads 5 HCAD TSV files, creates DuckDB tables, builds `sfr` and `sfr_enriched` views |
| `hcad_scores.py` | Runs DuckDB aggregation queries, returns ZIP-level DataFrames for each of 6 metrics |
| `hcad_maps.py` | Renders Folium choropleths, injects snapshot UI JavaScript, writes HTML to `static/` |
| `hcad_screener.py` | Builds the multi-CTE scoring SQL, defines `DEAL_TYPES`, provides `screen()` and `deal_signals()` |
| `hcad_app.py` | Flask routes, wires together ingest → scores → maps → screener, serves HAR.com ZIP insight API |

### HAR Snapshot Pipeline

| Module | Responsibility |
|--------|---------------|
| `extract_har.py` | Parses `.har`, `.json`, `.jsonc` files; extracts SearchListings payloads |
| `normalize_har.py` | Type coercion, ZIP normalization, active/sold/rental split |
| `analyze_spreads.py` | Cohort building, PPSF spread computation, scoreboard and ranking generation |
| `grid_analysis.py` | Bins listings into 400m grid cells, scores cells by deal density and spread |
| `pipeline.py` | Orchestrates extract → normalize → qa → analyze → grid as a single command |
| `visualize.py` | Terminal table renderer for all named snapshot artifacts |
| `fetch_searchlistings.py` | Calls `har.com/api/SearchListings` directly, writes to snapshot pack |

---

## CLI Entry Points (`pyproject.toml`)

```
hcad-ingest    → src.hcad_ingest:main
hcad-maps      → src.hcad_maps:main
hcad-app       → src.hcad_app:main
hcad-screen    → src.hcad_screener:main

init-snapshot  → src.extract_har:init_snapshot_main
extract-har    → src.extract_har:main
normalize      → src.normalize_har:main
qa             → src.normalize_har:qa_main
analyze        → src.analyze_spreads:main
grid-analysis  → src.grid_analysis:main
pipeline       → src.pipeline:main
visualize      → src.visualize:main
```

---

## Data Files (git-ignored)

| File | Source | Size |
|------|--------|------|
| `data/hcad.duckdb` | Created by `hcad-ingest` | ~1 GB |
| `data/houston_zcta.geojson` | Fetched from OpenDataDE on first `hcad-maps` run | ~5 MB |
| `static/hcad_maps/*.html` | Created by `hcad-maps` | ~2–5 MB each |
| `snapshots/*/` | Created by `init-snapshot` + pipeline | Varies |

HCAD source TSV files live outside this repo at `/mnt/ssd/projects/hcad-land/Real_acct_owner/` (configurable in `hcad_ingest.py`).
