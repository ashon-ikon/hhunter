# Usage Guide

Complete reference for using House Hunter tools from the command line.

## Quick Reference

```bash
# Initialize snapshot pack
init-snapshot --label "Acres Homes 77091"

# Extract HAR payloads into snapshot outputs
extract-har --snapshot snapshots/<snapshot_id> snapshots/<snapshot_id>/raw/har
extract-har --replay-failures --snapshot snapshots/<snapshot_id> snapshots/<snapshot_id>/raw/har

# Normalize to canonical tables
normalize --snapshot snapshots/<snapshot_id>

# QA gate
qa --snapshot snapshots/<snapshot_id>

# Analyze candidates + segments + streets
analyze --snapshot snapshots/<snapshot_id>

# Visualize snapshot artifacts in the terminal
visualize --snapshot snapshots/<snapshot_id> --artifact ranked
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --artifact qa
visualize --snapshot snapshots/<snapshot_id> --all --limit 30
visualize --snapshot snapshots/<snapshot_id> --artifact qa
visualize --snapshot snapshots/<snapshot_id> --artifact grid_scoreboard

# Grid-based scouting outputs
grid-analysis --snapshot snapshots/<snapshot_id>
grid-analysis --snapshot snapshots/<snapshot_id> --cell-size-m 400 --export-geojson

# One-shot run (extract -> normalize -> qa -> analyze -> grid)
pipeline --snapshot snapshots/<snapshot_id>
pipeline --replay-failures --snapshot snapshots/<snapshot_id>
```

---

## 1. Importing Data

### 1.1 From HAR File (Browser Export)

**What is a HAR file?**
- HTTP Archive exported from browser DevTools
- Contains network requests/responses including API data
- Can extract HAR from Firefox/Chrome

**How to export:**

1. Open HAR.com in browser
2. Press `F12` (DevTools)
3. Go to **Network** tab
4. Search for properties, apply filters
5. Right-click Network tab → **Save all as HAR...**
6. Save file (e.g., `HAR_Export.har`)

**Import the HAR file:**

```bash
init-snapshot --label "Acres Homes 77091"
extract-har --snapshot snapshots/<snapshot_id> ~/Documents/HAR_Export.har
```

**Output:**
```
Snapshot: 2026-03-05_acres_homes_77091
HAR files processed: 1 / 1
Indexed requests: 160
Extracted payloads: 1
Merged listings: 100
Wrote: snapshots/2026-03-05_acres_homes_77091/out/extracted/har_responses.ndjson
Wrote: snapshots/2026-03-05_acres_homes_77091/out/extracted/requests_index.csv
Wrote: snapshots/2026-03-05_acres_homes_77091/out/extracted/listings_raw.json
```

**What if the HAR is truncated?** (1MB response limit)

The HAR file may only contain part of the API response. Use direct fetching instead:

```bash
python -m src.fetch_searchlistings \
  --snapshot snapshots/<snapshot_id> \
  --har ~/Documents/HAR_Export.har \
  --zip 77088 \
  --for-sale 1
```

### 1.2 From JSON Files

If you have previously exported JSON files:

```bash
# Single file
extract-har --snapshot snapshots/<snapshot_id> ~/Documents/data.json

# Directory (all JSON files)
extract-har --snapshot snapshots/<snapshot_id> ~/Documents/MyData/
```

**Supported formats:**
- `.json` - Standard JSON
- `.jsonc` - JSON with `//` comments (auto-cleaned)

### 1.3 Direct API Call (Using HAR Credentials)

If you don't have a HAR file, create one first using the browser (see 1.1).

Once you have a HAR file with auth cookies:

```bash
# Fetch specific ZIP code
python -m src.fetch_searchlistings \
  --snapshot snapshots/<snapshot_id> \
  --har ~/Documents/HAR_Export.har \
  --zip 77088 \
  --for-sale 1
```

**Custom API URL:**

```bash
# For rentals (for_sale=0)
python -m src.fetch_searchlistings \
  --snapshot snapshots/<snapshot_id> \
  --url "https://www.har.com/api/SearchListings?zip_code=77088&for_sale=0&bedroom_min=2" \
  --for-sale 0
```

**Change ZIP code on the fly:**

```bash
python -m src.fetch_searchlistings \
  --snapshot snapshots/<snapshot_id> \
  --har ~/Documents/HAR_Export.har \
  --zip 77018 \  # Override ZIP in HAR file
  --for-sale 1
```

---

## 2. Processing Data

### 2.1 Normalize All Files

Convert extracted listings in a snapshot pack to clean canonical CSV tables:

```bash
normalize --snapshot snapshots/<snapshot_id>
```

**What it does:**
1. Loads `out/extracted/listings_raw.json` from the snapshot
2. Coerces numeric fields and canonicalizes listing URLs
3. Adds derived segment fields such as `era_bucket`, `size_bucket`, and `flip_box_flag`
4. Adds default 400m grid assignment fields
5. Writes deduped `active.csv`, `sold.csv`, `rentals.csv`, plus raw variants and a normalize report

**Output:**
```
Snapshot: snapshots/<snapshot_id>
Normalized rows: 100
Wrote: snapshots/<snapshot_id>/out/normalized/active.csv
Wrote: snapshots/<snapshot_id>/out/normalized/sold.csv
Wrote: snapshots/<snapshot_id>/out/normalized/rentals.csv
Wrote: snapshots/<snapshot_id>/out/normalized/normalize_report.json
```

### 2.2 Normalize Specific File

```bash
normalize --snapshot snapshots/<snapshot_id>
```

`normalize` operates on extracted snapshot artifacts rather than directly on ad hoc raw files.

### 2.3 Check Output

```bash
# List normalized snapshot outputs
ls -lh snapshots/<snapshot_id>/out/normalized/

# Preview CSV (first 10 rows, specific columns)
head -20 snapshots/<snapshot_id>/out/normalized/active.csv | cut -d',' -f1-10
```

---

## 3. Analysis & Reporting

### 3.0 Visualize Artifacts

Use `visualize` to inspect snapshot outputs in the terminal without opening CSV or JSON files manually.

**Basic usage:**

```bash
visualize --snapshot snapshots/<snapshot_id> --artifact ranked
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --artifact qa
visualize --snapshot snapshots/<snapshot_id> --all --limit 30
```

If `--snapshot` is omitted, `visualize` uses the most recently modified snapshot directory.
When `--all` is used, `visualize` renders each available named artifact for the snapshot one after the other and skips any artifact file that does not exist yet.

**Supported artifacts:**
- `ranked` → `out/analysis/ranked_candidates.csv`
- `scoreboard` → `out/analysis/scoreboard_segments.csv`
- `streets` → `out/analysis/streets_top.csv`
- `grid_scoreboard` → `out/analysis/grid_scoreboard.csv`
- `grid_candidates` → `out/analysis/grid_candidates.csv`
- `grid_streets` → `out/analysis/grid_streets.csv`
- `active` → `out/normalized/active.csv`
- `sold` → `out/normalized/sold.csv`
- `rentals` → `out/normalized/rentals.csv`
- `requests` → `out/extracted/requests_index.csv`
- `qa` → `out/qa/qa_report.json`
- `normalize` → `out/normalized/normalize_report.json`

**Common operator workflows:**

```bash
# Review ranked candidates
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --limit 15

# Review segment scoreboard
visualize --snapshot snapshots/<snapshot_id> --artifact scoreboard --limit 20

# Review QA summary before analysis
visualize --snapshot snapshots/<snapshot_id> --artifact qa

# Inspect grid scouting output
visualize --snapshot snapshots/<snapshot_id> --artifact grid_scoreboard --limit 20
visualize --snapshot snapshots/<snapshot_id> --artifact grid_candidates --limit 20
visualize --snapshot snapshots/<snapshot_id> --artifact grid_streets --limit 20
```

**Useful flags:**
- `--artifact <name>` can be repeated to render multiple named artifacts in one run
- `--all` renders every available named artifact for the snapshot in sequence
- `--limit <n>` limits displayed rows for CSV artifacts
- `--columns col1,col2,...` shows only specific CSV columns
- `--all-columns` shows every column in the artifact
- `--width <n>` overrides terminal width detection
- `--path <file>` renders a direct CSV or JSON path instead of a named artifact

**Examples with custom columns:**

```bash
# Tight view for ranked candidates
visualize \
  --snapshot snapshots/<snapshot_id> \
  --artifact ranked \
  --columns address,zip,list_price,upside_to_p70,rank_score,confidence_grade \
  --limit 10

# Tight view for top grid cells
visualize \
  --snapshot snapshots/<snapshot_id> \
  --artifact grid_scoreboard \
  --columns grid_id,sold_count,active_count,renovation_spread,hunt_score,cell_label \
  --limit 12

# Review multiple outputs in one pass
visualize \
  --snapshot snapshots/<snapshot_id> \
  --artifact qa \
  --artifact ranked \
  --artifact grid_scoreboard \
  --limit 15
```

**Direct path examples:**

```bash
visualize --path snapshots/<snapshot_id>/out/analysis/ranked_candidates.csv --limit 10
visualize --path snapshots/<snapshot_id>/out/qa/qa_report.json
```

### 3.1 Submarket Scoreboard

**Market metrics by ZIP / segment**

```bash
analyze --snapshot snapshots/<snapshot_id>
visualize --snapshot snapshots/<snapshot_id> --artifact scoreboard
```

**Output:**
```
Loaded 120 active listings, 120 sold listings

=== Submarket Scoreboard (by ZIP) ===
  ZIP  sold_count  sold_median_ppsf  sold_median_dom  active_count  active_median_ppsf  ppsf_gap
77008        25.0             262.0             15.0            13               298.0      36.0
77018        21.0             297.0             46.0            27               306.0       9.0
77088        18.0             143.0             59.0            13               184.0      41.0

Saved: snapshots/<snapshot_id>/out/analysis/scoreboard_segments.csv
```

**Interpretation:**
- `sold_count`: Number of recent sales (≥3 = reliable)
- `sold_median_ppsf`: Market price level ($/sqft)
- `sold_median_dom`: Market velocity (days on market)
- `active_median_ppsf`: Current asking prices
- `ppsf_gap`: Direction (+20 = overpriced, -20 = underpriced)

**Use case:**
> "Which ZIPs are best value?" → Look for negative `ppsf_gap` (active below sold)

### 3.2 Top Deals (Ranked by Spread)

**Find properties priced below recent comps**

```bash
# Top 20 underpriced properties
python -m src.analyze_spreads --rank --top 20
```

**Output:**
```
=== Top 20 Active Listings by Spread ===
(Lower spread = better potential value)

  MLSNUM                    ADDRESS   ZIP  PROPTYPENAME  BEDROOM  PPSF_SPREAD
75866936             1117 W 17th St 77008 Single-Family      3.0        -67.0
91212481           1309 W 24th St B 77008 Single-Family      3.0        -56.0
32674968           1515 Thornton Rd 77018 Single-Family      5.0        -54.0
```

**Interpretation:**
- `MLSNUM`: Click on HAR.com to view property
- `PPSF_SPREAD`: (Active ask) - (Sold median)
  - Negative = underpriced relative to comps
  - Positive = overpriced
- `BEDROOM`: Property size reference

**Use cases:**
- "Show me flip opportunities" → `--rank --top 50`
- "What are the 5 best values?" → `--rank --top 5`

**Filter the results manually:**

```bash
# Best deals: large homes, well-below market
# (Sort CSV yourself or use Python below)
python << 'EOF'
import pandas as pd

df = pd.read_csv('snapshots/<snapshot_id>/out/analysis/ranked_candidates.csv')

# Filter
filtered = df[
    (df['PPSF_SPREAD'] < -25) &      # >$25 below market
    (df['BLDGSQFT'] > 1500) &        # At least 1500 sqft
    (df['BEDROOM'] >= 3)              # 3+ bedrooms
]

print(filtered.sort_values('PPSF_SPREAD').head(10))
EOF
```

### 3.3 Property Detail (Subject Analysis)

**Analyze one specific property against comps**

```bash
python -m src.analyze_spreads --mlsnum 75866936
```

**Output:**
```
=== Subject Analysis ===
  MLSNUM                ADDRESS   ZIP  PROPTYPENAME  BEDROOM  PRICEPERSQFT  SOLD_MEDIAN_PPSF  PPSF_SPREAD
75866936  1117 W 17th St 77008  Single-Family      3.0        189.0                256.0         -67.0

Cohort size: 7 comps
Saved ranked outputs under: snapshots/<snapshot_id>/out/analysis/
```

**Find MLSNUM:**
1. Visit HAR.com, find property
2. Copy number from URL or listing details
3. Paste in command

**What is the cohort?**
- 7 comparable sales that match the subject
- Same ZIP, property type, beds/baths, sqft range, year built

**View the comps:**
```bash
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --limit 20
```

**Use cases:**
- "Is this property a good deal?" → Check the spread
- "Build a comp set for a property" → Use the cohort CSV in Excel
- "What similar properties sold for?" → View comps

---

## 4. Advanced Usage

### 4.1 Analyzing Multiple ZIPs

**Fetch and analyze multiple ZIPs**

```bash
# Create a snapshot pack for this scouting run
init-snapshot --label "Multi ZIP scout"

# Fetch sales for 4 ZIPs into the same snapshot
for zip in 77008 77018 77088 77091; do
  echo "Fetching ZIP $zip..."
  python -m src.fetch_searchlistings \
    --snapshot snapshots/<snapshot_id> \
    --har ~/Documents/HAR_Export.har \
    --zip $zip \
    --for-sale 1
done

# Extract/normalize/analyze the snapshot artifacts
normalize --snapshot snapshots/<snapshot_id>

# Compare markets
analyze --snapshot snapshots/<snapshot_id>
visualize --snapshot snapshots/<snapshot_id> --artifact scoreboard
```

### 4.2 Rental Analysis

**Compare sales to rental prices (cap rate, price-to-rent)**

```bash
# Fetch sales
python -m src.fetch_searchlistings \
  --snapshot snapshots/<snapshot_id> \
  --url "https://www.har.com/api/SearchListings?zip_code=77088&for_sale=1" \
  --for-sale 1

# Fetch rentals (for_sale=0)
python -m src.fetch_searchlistings \
  --snapshot snapshots/<snapshot_id> \
  --url "https://www.har.com/api/SearchListings?zip_code=77088&for_sale=0&bedroom_min=2" \
  --for-sale 0

# Normalize both
normalize --snapshot snapshots/<snapshot_id>

# Manually compute metrics (in Python)
python << 'EOF'
import pandas as pd

sales = pd.read_csv('snapshots/<snapshot_id>/out/normalized/active.csv')
rentals = pd.read_csv('snapshots/<snapshot_id>/out/normalized/rentals.csv')

# Single-family homes
sf_sales = sales[sales['PROPTYPENAME'] == 'Single-Family']
sf_rentals = rentals[rentals['PROPTYPENAME'].str.contains('Single Family', na=False)]

med_price = sf_sales['LISTPRICEORI'].median()
med_rent = sf_rentals['LISTPRICEORI'].median()

annual_rent = med_rent * 12
cap_rate = (annual_rent / med_price) * 100

print(f"Median Sale Price: ${med_price:,.0f}")
print(f"Median Monthly Rent: ${med_rent:,.0f}")
print(f"Gross Cap Rate: {cap_rate:.2f}%")
print(f"Price-to-Rent Ratio: {med_price/med_rent:.1f}x")
EOF
```

### 4.3 Custom Filtering

**Create your own deal criteria using Python**

```python
# custom_analysis.py
import pandas as pd

# Load data
active = pd.read_csv('snapshots/<snapshot_id>/out/normalized/active.csv')
ranked = pd.read_csv('snapshots/<snapshot_id>/out/analysis/ranked_candidates.csv')

# Your deal criteria
MY_CRITERIA = {
    'price_min': 200000,
    'price_max': 400000,
    'beds_min': 3,
    'sqft_min': 1500,
    'spread_max': -20,  # At least $20/sqft below market
    'dom_max': 30,      # Not sitting too long
}

# Apply filters
deals = ranked[
    (ranked['LIST_PRICE'] >= MY_CRITERIA['price_min']) &
    (ranked['LIST_PRICE'] <= MY_CRITERIA['price_max']) &
    (ranked['BEDROOM'] >= MY_CRITERIA['beds_min']) &
    (ranked['BLDGSQFT'] >= MY_CRITERIA['sqft_min']) &
    (ranked['PPSF_SPREAD'] <= MY_CRITERIA['spread_max'])
]

print(f"Found {len(deals)} matching deals:")
print(deals[['MLSNUM', 'ADDRESS', 'LIST_PRICE', 'PPSF_SPREAD']])

# Save to CSV
deals.to_csv('my_deals.csv', index=False)
print("\nSaved to my_deals.csv")
```

Run it:
```bash
python custom_analysis.py
```

### 4.4 Exporting for Excel

**Generate reports for spreadsheet analysis**

```bash
# Get best 50 deals
analyze --snapshot snapshots/<snapshot_id>

# Now you have:
# - snapshots/<snapshot_id>/out/analysis/ranked_candidates.csv (main table)
# - snapshots/<snapshot_id>/out/analysis/scoreboard_segments.csv (market metrics)
# - snapshots/<snapshot_id>/out/analysis/streets_top.csv (street worksheet)

# Open in Excel
open snapshots/<snapshot_id>/out/analysis/ranked_candidates.csv
```

---

## 5. Troubleshooting

### Problem: "No listings found in HAR file"

**Cause**: HAR file doesn't contain API responses

**Solution**:
1. Make sure you exported while properties loaded
2. Search/filter on HAR.com to trigger API calls
3. Try a fresh HAR export with the browser DevTools open

### Problem: "ModuleNotFoundError: No module named 'pandas'"

**Cause**: Virtual environment not activated

**Solution**:
```bash
source .venv/bin/activate  # macOS/Linux
# or
.\.venv\Scripts\activate   # Windows

pip list  # Verify pandas is listed
```

### Problem: "Insufficient comparables" for a property

**Cause**: Cohort filtering too strict

**Solution**:
- Property is rare (unique type, very new/old)
- Zip code has few sales
- Try broader search (neighboring ZIPs)

**Example**:
```python
# Manually loosen filters
cohort = build_cohort(
    sold,
    zip_code='77008',
    sqft_band=0.25,      # ±25% instead of ±15%
    year_band=15,        # ±15 years instead of ±10
)
```

### Problem: "CSV seems corrupted" (can't open)

**Cause**: Unusual characters in property names

**Solution**:
```bash
# Check encoding
file snapshots/<snapshot_id>/out/normalized/active.csv

# Re-encode if needed
iconv -f UTF-8 -t ASCII//TRANSLIT -o fixed.csv original.csv
```

---

## 6. Tips & Best Practices

### ✅ Do's

- **Screenshot your results** - Save spread analysis for reference
- **Export to Excel** - Easier to share with partners/lenders
- **Use multiple ZIPs** - Compare markets, find best value areas
- **Check cohort size** - Require ≥3 comps for reliability
- **Track over time** - Run weekly to spot new deals

### ❌ Don'ts

- **Don't trust 1-comp cohorts** - Outliers skew analysis
- **Don't ignore DOM** - High DOM = likely problem property
- **Don't apply to lots** - PPSF doesn't make sense (no building)
- **Don't forget OpEx** - Gross cap rate needs to subtract taxes, insurance, maintenance
- **Don't rely only on comps** - Use subject property inspection, local knowledge

---

## 7. Example Workflows

### Workflow A: Find Flip Deals

**Goal**: Find single-family homes good for renovation flips

```bash
# 1. Fetch data for target area
init-snapshot --label "Flip scout 77008"
python -m src.fetch_searchlistings \
  --snapshot snapshots/<snapshot_id> \
  --har ~/Documents/HAR.har \
  --zip 77008 \
  --for-sale 1

# 2. Normalize
normalize --snapshot snapshots/<snapshot_id>

# 3. Get top deals
analyze --snapshot snapshots/<snapshot_id>
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --limit 30

# 4. Filter manually
# - Look for: PPSF_SPREAD < -$25, BLDGSQFT > 1500, DOM < 60
# - Properties below market indicate possible problems or deals
# - Check neighborhood safety, school ratings, flood zone

# 5. Export to Excel for team review
cp snapshots/<snapshot_id>/out/analysis/ranked_candidates.csv flip_deals_77008.csv
```

### Workflow B: Build Investor Portfolio

**Goal**: Compare rental yields across multiple ZIPs

```bash
# 1. Create a snapshot pack and fetch sales + rentals for 5 ZIPs
init-snapshot --label "Investor portfolio scout"
for zip in 77008 77018 77055 77088 77091; do
  # Sales
  python -m src.fetch_searchlistings \
    --snapshot snapshots/<snapshot_id> \
    --har ~/HAR.har \
    --zip $zip \
    --for-sale 1

  # Rentals
  python -m src.fetch_searchlistings \
    --snapshot snapshots/<snapshot_id> \
    --url "https://www.har.com/api/SearchListings?zip_code=${zip}&for_sale=0&bedroom_min=2" \
    --for-sale 0
done

# 2. Normalize all
normalize --snapshot snapshots/<snapshot_id>

# 3. Generate scoreboard
analyze --snapshot snapshots/<snapshot_id>
visualize --snapshot snapshots/<snapshot_id> --artifact scoreboard

# 4. Analyze cap rates (manual Python)
# (See Advanced Usage 4.2 above)

# 5. Create investment scorecard comparing ZIPs
```

### Workflow C: Value a Single Property

**Goal**: Get market valuation using comps

```bash
# 1. Have HAR data processed (from previous imports)
normalize --snapshot snapshots/<snapshot_id>  # (if not already done)

# 2. Analyze your subject property
analyze --snapshot snapshots/<snapshot_id>
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --limit 20

# 3. Review comps
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --limit 20

# 4. Manual steps
# - Verify cohort makes sense (same neighborhood, similar condition)
# - Adjust comps for differences (pool, corner lot, etc.)
# - Estimate subject's likely sale price
# - Compare to asking price for offer strategy
```

---

## 8. Common Commands Reference

```bash
# Set up (one-time)
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Regular workflow
init-snapshot --label "Acres Homes 77091"
extract-har --snapshot snapshots/<snapshot_id> HAR_Export.har  # OR
python -m src.fetch_searchlistings --snapshot snapshots/<snapshot_id> --har HAR.har --zip 77088 --for-sale 1

normalize --snapshot snapshots/<snapshot_id>

analyze --snapshot snapshots/<snapshot_id>
visualize --snapshot snapshots/<snapshot_id> --artifact scoreboard
visualize --snapshot snapshots/<snapshot_id> --artifact ranked --limit 20

# View results
ls -lh snapshots/<snapshot_id>/out/analysis/        # List output files
head snapshots/<snapshot_id>/out/normalized/*.csv   # Preview normalized CSVs
```

---

**Need more help?** See [INSTALL.md](./INSTALL.md) or [../../README.md](../../README.md)
