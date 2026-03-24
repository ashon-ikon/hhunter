# Installation Guide

## Prerequisites

- Python 3.11+
- Git
- ~3 GB free disk space (for HCAD DuckDB database)

---

## 1. Clone & Set Up Environment

```bash
git clone https://github.com/your-org/house-hunter.git
cd house-hunter

python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .\.venv\Scripts\activate       # Windows

pip install -e .
```

### Dependencies installed

| Package | Purpose |
|---------|---------|
| `duckdb>=1.0` | In-process analytical database (powers all HCAD queries) |
| `folium>=0.18` | Python wrapper for Leaflet.js choropleth maps |
| `flask>=3.0` | Web application server |
| `branca>=0.7` | Folium HTML/JS helper library |
| `pandas>=2.0` | DataFrame processing |
| `requests>=2.31` | HTTP client (HAR.com API, GeoJSON fetch) |
| `python-dateutil>=2.9` | Date parsing |

---

## 2. HCAD Data Setup

### 2.1 Download HCAD public data files

Go to https://hcad.org/hcad-online-services/pdata and download the current year's data export. The relevant files are in the **Real Accounts (Residential)** bundle:

| File | Description |
|------|-------------|
| `real_acct.txt` | Property valuations, sqft, year built, lot size |
| `owners.txt` | Owner name records |
| `deeds.txt` | Deed transfer history |
| `permits.txt` | Building permits |
| `real_neighborhood_code.txt` | Neighborhood codes |

Place them at:
```
/mnt/ssd/projects/hcad-land/Real_acct_owner/
├── real_acct.txt
├── owners.txt
├── deeds.txt
├── permits.txt
├── real_neighborhood_code.txt
└── parcel_tieback.txt          (optional)
```

> If you want to use a different path, edit `DB_PATH` and `HCAD_DIR` in `src/hcad_ingest.py`.

### 2.2 Ingest into DuckDB

```bash
hcad-ingest
```

This takes about 2 minutes and creates `data/hcad.duckdb` (~1 GB). It:
1. Loads all five TSV files with `all_varchar=true` to prevent type inference issues
2. Creates `sfr` view (A1/A2 state class, building > 200 sqft, valid ZIP)
3. Creates `sfr_enriched` view (adds `yoy_pct`, `price_per_sqft`, `mkt_to_rcn_ratio`, `building_age`)

### 2.3 Generate heat maps

```bash
hcad-maps
```

Fetches the Houston ZIP polygon GeoJSON (cached to `data/houston_zcta.geojson` after first run) and writes 6 HTML files to `static/hcad_maps/`.

---

## 3. Start the Web App

```bash
hcad-app
```

Open **http://localhost:5000** in your browser.

The dashboard shows all 6 heat maps. Click any map to open it, click any ZIP polygon to see the detail insert card, press `S` to snapshot.

---

## 4. CLI Entry Points

After `pip install -e .`, these commands are available:

| Command | Description |
|---------|-------------|
| `hcad-ingest` | Load HCAD TSV files into `data/hcad.duckdb` |
| `hcad-maps` | Regenerate all 6 HTML heat map files |
| `hcad-app` | Start the Flask web server on port 5000 |
| `hcad-screen` | CLI deal screener (see USAGE.md) |
| `init-snapshot` | Create a new HAR snapshot pack |
| `extract-har` | Extract listings from HAR/JSON files |
| `normalize` | Normalize extracted listings |
| `qa` | Run quality checks on a snapshot |
| `analyze` | Run spread and cohort analysis |
| `grid-analysis` | Grid-based scouting |
| `pipeline` | One-shot: extract → normalize → qa → analyze → grid |
| `visualize` | Terminal artifact viewer |

---

## 5. VS Code Setup

`.vscode/settings.json` is already configured to use the `.venv` interpreter:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python"
}
```

If VS Code still picks the wrong interpreter, press `Ctrl+Shift+P` → "Python: Select Interpreter" → choose the `.venv` entry.

---

## 6. Verify Installation

```bash
# Check DB was created
ls -lh data/hcad.duckdb

# Quick sanity check
python -c "
import duckdb
con = duckdb.connect('data/hcad.duckdb', read_only=True)
print(con.execute('SELECT COUNT(*) FROM sfr').fetchone())
"
# Should print roughly (1,000,000+,)

# Check Flask app imports
python -c "from src.hcad_app import app; print('OK')"
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'duckdb'"
Virtual environment is not activated or package install failed.
```bash
source .venv/bin/activate
pip install -e .
```

### "FileNotFoundError: real_acct.txt"
HCAD files are not in the expected path. Check `HCAD_DIR` in `src/hcad_ingest.py` and update to match your actual download location.

### "BinderException: trim(DOUBLE)" during ingest
Old DuckDB version. Update:
```bash
pip install --upgrade duckdb
```
The ingest code uses `all_varchar=true` on all `read_csv()` calls to prevent this.

### Flask port already in use
```bash
# Kill whatever is on port 5000
lsof -i :5000 | awk 'NR>1 {print $2}' | xargs kill -9
hcad-app
```
