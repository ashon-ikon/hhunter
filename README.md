# House Hunter

**Data-driven Houston real estate analysis: parcel-level deal scoring, heat maps, and market intelligence.**

House Hunter is a Python-based investment analysis platform that combines two complementary data sources:

1. **HCAD (Harris County Appraisal District)** — 1.6 million parcel records. Full ownership history, valuations, permits, and land data. Powers the heat maps and deal screener web app.
2. **HAR (Houston Association of Realtors)** — Live MLS listing data. Active listings, recent sales, rental comps. Powers the snapshot pipeline and comparable analysis.

---

## What It Does

### HCAD Web App (`localhost:5000`)
A Flask-based investment dashboard built on the full HCAD public dataset:

- **6 choropleth heat maps** — choropleth overlays on OpenStreetMap across all Harris County ZIP codes:
  - Price per sqft (median market value / sqft)
  - YOY appreciation (% change vs prior year)
  - Investor activity (entity ownership %)
  - Permit surge (recent renovation activity)
  - Gentrification score (composite momentum)
  - Flip potential (distressed + unrenovated stock)
- **ZIP insert card** — click any polygon to see all 18 metrics for that ZIP across every map
- **Snapshot export** — press `S` to capture the current map + insights panel as PNG
- **Focus ZIPs** — 77021 and 77088 highlighted with blue dashed borders on every map
- **Parcel deal screener** — filter 1.6M parcels by deal type, ZIP, price, year built, and years held; score and rank results; export to CSV
- **Live ZIP market pulse** — HAR.com active listing and sold data fetched live per ZIP
- **Score legend** — collapsible breakdown of each deal type's scoring components and weights

### HAR Snapshot Pipeline (CLI)
A snapshot-based workflow for MLS listing analysis:

- Capture HAR files from browser network traffic on har.com
- Extract, normalize, and analyze listing snapshots
- Comparable cohort analysis and spread computation
- Grid-based scouting with cell scoring
- Terminal visualization of all artifacts

---

## Quick Start

### HCAD Web App

```bash
# 1. Create virtual environment & install deps
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Download HCAD public data (see INSTALL.md for details)
#    Place TSV files in: /mnt/ssd/projects/hcad-land/Real_acct_owner/

# 3. Ingest data into DuckDB (~2 minutes for 1.6M parcels)
hcad-ingest

# 4. Generate heat map HTML files
hcad-maps

# 5. Start the web app
hcad-app
# Open http://localhost:5000
```

### HAR Snapshot Pipeline

```bash
# 1. Create a snapshot pack
init-snapshot --label "South Side 77021"

# 2. Export HAR from har.com browser network tab
#    Place .har files in snapshots/<id>/raw/har/

# 3. Run full pipeline
pipeline --snapshot snapshots/<snapshot_id>

# 4. Visualize results
visualize --snapshot snapshots/<snapshot_id> --all --limit 20
```

---

## Project Structure

```
house-hunter/
├── src/
│   ├── hcad_ingest.py          ← ETL: load 1.6M HCAD parcels into DuckDB
│   ├── hcad_scores.py          ← ZIP-level scoring (6 heat map metrics)
│   ├── hcad_maps.py            ← Folium choropleth map generation
│   ├── hcad_screener.py        ← Parcel deal screener (5 deal types)
│   ├── hcad_app.py             ← Flask web application
│   ├── extract_har.py          ← HAR snapshot extraction
│   ├── normalize_har.py        ← Listing normalization
│   ├── analyze_spreads.py      ← Cohort and spread analysis
│   ├── grid_analysis.py        ← Grid-based scouting
│   ├── pipeline.py             ← One-shot snapshot pipeline
│   └── visualize.py            ← Terminal artifact viewer
├── data/
│   ├── hcad.duckdb             ← 1.6M parcel analytical database
│   └── houston_zcta.geojson    ← Cached Houston ZIP polygon boundaries
├── static/hcad_maps/           ← Generated HTML map files
├── templates/
│   ├── hcad_dashboard.html     ← Map dashboard
│   └── hcad_screener.html      ← Deal screener UI
├── snapshots/                  ← HAR snapshot packs
├── docs/
│   ├── guides/INSTALL.md       ← Setup guide
│   ├── guides/USAGE.md         ← Full usage reference
│   ├── architecture/ARCHITECTURE.md
│   └── roadmap/ROADMAP.md
└── pyproject.toml
```

---

## Deal Screener Overview

The screener scores every parcel in Harris County across five deal types, all computed inside DuckDB SQL for speed:

| Deal Type | What It Finds | Key Signals |
|-----------|--------------|-------------|
| **Fix & Flip** | Distressed / unrenovated stock | Below replacement cost, no permits 10+ yrs, old structure, price discount |
| **BRRR** | Buy-rehab-rent-refinance plays | Buy discount, rehab runway, ZIP appreciation momentum |
| **Buy & Hold** | Long-term appreciation corridors | ZIP YOY growth, entry below median, long-held properties |
| **Land Play** | Economically obsolete structures | Land value > 70% of total, old building on large lot |
| **Wholesale** | Motivated-seller targeting | Deep discount + long hold + neglect signal |

Scores are 0–100. Each component's weight is shown in the collapsible legend on the screener page.

---

## HCAD Data Sources

| File | Rows | Contents |
|------|------|----------|
| `real_acct.txt` | 1.61M | Property valuations, year built, sqft, lot size |
| `owners.txt` | 1.89M | Owner names (used for investor detection) |
| `deeds.txt` | 2.54M | Deed transfer history (ownership lag) |
| `permits.txt` | 1.3M | Building permits (renovation recency) |
| `real_neighborhood_code.txt` | 12K | Neighborhood descriptions |

Download from: https://hcad.org/hcad-online-services/pdata

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/guides/INSTALL.md](./docs/guides/INSTALL.md) | Installation, HCAD data setup, dependencies |
| [docs/guides/USAGE.md](./docs/guides/USAGE.md) | Web app walkthrough, screener, CLI reference |
| [docs/architecture/ARCHITECTURE.md](./docs/architecture/ARCHITECTURE.md) | System design, data models, scoring formulas |
| [docs/roadmap/ROADMAP.md](./docs/roadmap/ROADMAP.md) | What's built, what's next |

---

## Notes

- All HCAD data is public record — no credentials required
- HAR.com's `SearchListings` API is public and requires no authentication
- The HCAD property detail URL changed in early 2026; the screener links to `search.hcad.org/GISMap/?hcad_num={acct}` which is deterministic and always current
- DuckDB processes all 1.6M parcels for scoring in a few seconds
