# Roadmap

---

## Phase 1 — CLI Snapshot Pipeline ✅ Complete

**Goal**: Build a usable CLI workflow for MLS deal sourcing from HAR.com data.

- ✅ HAR file extraction (JSON / JSONC / HAR format)
- ✅ Data normalization (type coercion, ZIP formatting, dedup)
- ✅ Snapshot pack system (`init-snapshot`, organized directory structure)
- ✅ Cohort-based spread analysis (ZIP, proptype, beds, sqft, year filters)
- ✅ Submarket scoreboard (ZIP-level metrics table)
- ✅ Deal ranking (sorted by active PPSF vs sold median)
- ✅ Rental data support (`for_sale=0` endpoint)
- ✅ Grid-based scouting (400m cells, `grid_analysis.py`)
- ✅ Terminal visualization (`visualize` with named artifact support)
- ✅ Pipeline wrapper command (one-shot extract → normalize → qa → analyze → grid)

---

## Phase 2 — HCAD Web App ✅ Complete

**Goal**: Build a full-county parcel analysis platform on HCAD public data with a Flask-based web UI.

### Data Layer
- ✅ ETL pipeline (`hcad_ingest.py`) loading 5 HCAD TSV files into DuckDB
  - 1.61M properties, 1.89M owner records, 2.54M deeds, 1.3M permits
  - `sfr` and `sfr_enriched` views with derived metrics (YOY%, PPSF, RCN ratio, building age)
- ✅ DuckDB as the analytical engine — all scoring in SQL, no Python loops

### Heat Maps
- ✅ 6 choropleth maps covering all 138 Harris County ZIP codes:
  - Price per sqft, YOY change, investor activity, permit surge, gentrification score, flip potential
- ✅ Folium + CartoDB Positron tiles + OpenDataDE GeoJSON polygons
- ✅ Focus ZIP highlighting (77021, 77088 with blue dashed borders)
- ✅ ZIP insert card — click any polygon, see all 18 metrics across every map
- ✅ Snapshot export (`S` key → PNG download via html2canvas)
- ✅ Map regeneration via `/regen` route

### Deal Screener
- ✅ 5 deal types: Fix & Flip, BRRR, Buy & Hold, Land Play, Wholesale
- ✅ All scoring computed in DuckDB SQL (single CTE query, ~2-4s for 1.6M parcels)
- ✅ Filter by ZIP, price, year built, years held, min score
- ✅ Results table with score bars, signals, HCAD property links, owner badges
- ✅ Lot size shown in both acres and sqft
- ✅ Score legend (collapsible, shows component weights as proportional bars)
- ✅ CSV export
- ✅ CLI interface (`hcad-screen`)

### Live Market Data
- ✅ `/api/zip-insight/<zip>` — live HAR.com data per ZIP (active count, median list price, DOM, sold)
- ✅ ZIP market pulse panel in screener UI (auto-fetched when ZIPs are entered)

### Infrastructure
- ✅ Flask app with dashboard, map viewer, screener, regen routes
- ✅ Updated HCAD property links (new `search.hcad.org/GISMap` URL pattern discovered via HAR analysis)
- ✅ ArcGIS REST API documented (`arcweb.hcad.org`) — public, no auth, returns parcel geometry by HCAD_NUM

---

## Phase 3 — Parcel-Level Geo & Grid Maps (In Progress)

**Goal**: Add geographic precision to the analysis — parcel polygons on maps, H3 hex grids, and neighborhood-level choropleths.

### Parcel Geometry (ArcGIS REST API)
- [ ] Fetch parcel polygon + centroid lat/lon from `arcweb.hcad.org` by HCAD_NUM
  - API: `GET /server/rest/services/public/public_query/MapServer/0/query?where=HCAD_NUM='...'&returnCentroid=true&outSR=4326`
  - Public, no authentication required
  - Returns `esriGeometryPolygon` + centroid
- [ ] Add parcel map view: click a screener result → show parcel outline on Leaflet map
- [ ] Batch centroid lookup for screener results (lat/lon for top N results)

### H3 Hex Grid Maps
- [ ] Assign parcels to H3 hex cells (resolution 8 or 9, ~0.4–1.5 km²)
  - Requires lat/lon per parcel (from ArcGIS API above)
- [ ] Score hex cells by aggregate flip score, investor pct, YOY
- [ ] Render as `folium.GeoJson` choropleth — more granular than ZIP polygons

### Neighborhood Choropleth
- [ ] Obtain HCAD GIS shapefiles for neighborhood boundary polygons
  - Available as ESRI Shapefile download from hcad.org/GIS
- [ ] Add neighborhood toggle to each heat map (ZIP → Neighborhood layer switch)

---

## Phase 4 — Alerts & Time-Series Tracking (Planned)

**Goal**: Move from manual pull-based analysis to automated push-based deal discovery.

- [ ] Daily HCAD data refresh (detect when HCAD publishes new annual file)
- [ ] Price change detection: flag parcels that dropped in value year-over-year
- [ ] Permit surge alerts: notify when a ZIP crosses a permit rate threshold
- [ ] New screener result alerts: "3 new flip candidates in 77021 since your last session"
- [ ] Historical parcel tracking: was this parcel scored last month? How did the score change?

---

## Phase 5 — Portfolio & Deal Economics (Planned)

**Goal**: Connect deal scoring to deal economics — underwriting, financing, and portfolio tracking.

- [ ] ARV estimator: use HAR.com sold comps + HCAD data to estimate after-repair value
- [ ] Cash flow model: purchase price, rehab budget, rent estimate → monthly cash flow, CoC return
- [ ] DSCR calculator: qualify deals against lender DSCR thresholds
- [ ] Owned property tracker: log acquired properties, track vs original underwriting
- [ ] Portfolio dashboard: aggregate metrics across owned properties

---

## Technology Stack

### Current
| Component | Technology |
|-----------|-----------|
| Analytical DB | DuckDB 1.0+ |
| Maps | Folium 0.18 + Leaflet.js |
| Web server | Flask 3.0 |
| Data processing | pandas 2.0 |
| Frontend (maps) | Vanilla JS + html2canvas |
| Tile layer | CartoDB Positron |
| ZIP boundaries | OpenDataDE GeoJSON |

### Planned (Phase 3+)
| Component | Technology |
|-----------|-----------|
| H3 indexing | `h3-py` library |
| Parcel geometry | HCAD ArcGIS REST API |
| Background jobs | APScheduler or simple cron |
| Notifications | Email via SMTP or Slack webhook |
