# Usage Guide

Complete reference for using House Hunter tools from the command line.

## Quick Reference

```bash
# Initialize snapshot pack
init-snapshot --label "Acres Homes 77091"

# Extract HAR payloads into snapshot outputs
extract-har --snapshot snapshots/<snapshot_id> snapshots/<snapshot_id>/raw/har

# Normalize to canonical tables
normalize --snapshot snapshots/<snapshot_id>

# QA gate
qa --snapshot snapshots/<snapshot_id>

# Analyze candidates + segments + streets
analyze --snapshot snapshots/<snapshot_id>

# One-shot run (extract -> normalize -> qa -> analyze)
pipeline --snapshot snapshots/<snapshot_id>
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
python -m src.extract_har ~/Documents/HAR_Export.har
```

**Output:**
```
Processing HAR file: /Users/me/Documents/HAR_Export.har
  Extracted: data/raw/searchlistings_20260305_120000_0.json
  Extracted: data/raw/searchlistings_20260305_120001_2.json

Processed 2 file(s) -> data/raw/
```

**What if the HAR is truncated?** (1MB response limit)

The HAR file may only contain part of the API response. Use direct fetching instead:

```bash
python -m src.fetch_searchlistings --har ~/Documents/HAR_Export.har --zip 77088 \
  --output data/raw/sales_77088_full.json
```

### 1.2 From JSON Files

If you have previously exported JSON files:

```bash
# Single file
python -m src.extract_har ~/Documents/data.json

# Directory (all JSON files)
python -m src.extract_har ~/Documents/MyData/
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
  --har ~/Documents/HAR_Export.har \
  --zip 77088 \
  --output data/raw/sales_77088.json
```

**Custom API URL:**

```bash
# For rentals (for_sale=0)
python -m src.fetch_searchlistings \
  --url "https://www.har.com/api/SearchListings?zip_code=77088&for_sale=0&bedroom_min=2" \
  --output data/raw/rentals_77088.json
```

**Change ZIP code on the fly:**

```bash
python -m src.fetch_searchlistings \
  --har ~/Documents/HAR_Export.har \
  --zip 77018 \  # Override ZIP in HAR file
  --output data/raw/sales_77018.json
```

---

## 2. Processing Data

### 2.1 Normalize All Files

Convert raw JSON to clean CSV tables:

```bash
python -m src.normalize_har
```

**What it does:**
1. Finds newest JSON in `data/raw/`
2. Parses JSON (handles JSONC comments)
3. Coerces numeric fields (handles errors gracefully)
4. Normalizes ZIP codes to 5 digits
5. Splits into `active` and `sold` CSVs

**Output:**
```
Processing: data/raw/sales_77088.json
Output:
  Active: data/processed/sales_77088_active.csv
  Sold:   data/processed/sales_77088_sold.csv
```

### 2.2 Normalize Specific File

```bash
python -m src.normalize_har data/raw/rentals_77088.json
```

### 2.3 Check Output

```bash
# List processed files
ls -lh data/processed/

# Preview CSV (first 10 rows, specific columns)
head -20 data/processed/sales_77088_active.csv | cut -d',' -f1-10
```

---

## 3. Analysis & Reporting

### 3.1 Submarket Scoreboard

**Market metrics by ZIP code**

```bash
python -m src.analyze_spreads --scoreboard
```

**Output:**
```
Loaded 120 active listings, 120 sold listings

=== Submarket Scoreboard (by ZIP) ===
  ZIP  sold_count  sold_median_ppsf  sold_median_dom  active_count  active_median_ppsf  ppsf_gap
77008        25.0             262.0             15.0            13               298.0      36.0
77018        21.0             297.0             46.0            27               306.0       9.0
77088        18.0             143.0             59.0            13               184.0      41.0

Saved: data/processed/scoreboard_zip.csv
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

df = pd.read_csv('data/processed/ranked_by_spread.csv')

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
Saved cohort: data/processed/cohort_75866936.csv
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
cat data/processed/cohort_75866936.csv
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
# Fetch sales for 4 ZIPs
for zip in 77008 77018 77088 77091; do
  echo "Fetching ZIP $zip..."
  python -m src.fetch_searchlistings \
    --har ~/Documents/HAR_Export.har \
    --zip $zip \
    --output data/raw/sales_${zip}.json
done

# Normalize all
python -m src.normalize_har

# Compare markets
python -m src.analyze_spreads --scoreboard
```

### 4.2 Rental Analysis

**Compare sales to rental prices (cap rate, price-to-rent)**

```bash
# Fetch sales
python -m src.fetch_searchlistings \
  --url "https://www.har.com/api/SearchListings?zip_code=77088&for_sale=1" \
  --output data/raw/sales_77088.json

# Fetch rentals (for_sale=0)
python -m src.fetch_searchlistings \
  --url "https://www.har.com/api/SearchListings?zip_code=77088&for_sale=0&bedroom_min=2" \
  --output data/raw/rentals_77088.json

# Normalize both
python -m src.normalize_har

# Manually compute metrics (in Python)
python << 'EOF'
import pandas as pd

sales = pd.read_csv('data/processed/sales_77088_active.csv')
rentals = pd.read_csv('data/processed/rentals_77088_active.csv')

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
active = pd.read_csv('data/processed/sales_77088_active.csv')
ranked = pd.read_csv('data/processed/ranked_by_spread.csv')

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
python -m src.analyze_spreads --rank --top 50

# Now you have:
# - data/processed/ranked_by_spread.csv (main table)
# - data/processed/scoreboard_zip.csv (market metrics)
# - data/processed/cohort_<MLSNUM>.csv (comp sets)

# Open in Excel
open data/processed/ranked_by_spread.csv
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
file data/processed/sales_77088_active.csv

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
python -m src.fetch_searchlistings \
  --har ~/Documents/HAR.har \
  --zip 77008 \
  --output data/raw/sales_77008.json

# 2. Normalize
python -m src.normalize_har

# 3. Get top deals
python -m src.analyze_spreads --rank --top 30

# 4. Filter manually
# - Look for: PPSF_SPREAD < -$25, BLDGSQFT > 1500, DOM < 60
# - Properties below market indicate possible problems or deals
# - Check neighborhood safety, school ratings, flood zone

# 5. Export to Excel for team review
cp data/processed/ranked_by_spread.csv flip_deals_77008.csv
```

### Workflow B: Build Investor Portfolio

**Goal**: Compare rental yields across multiple ZIPs

```bash
# 1. Fetch sales + rentals for 5 ZIPs
for zip in 77008 77018 77055 77088 77091; do
  # Sales
  python -m src.fetch_searchlistings \
    --har ~/HAR.har \
    --zip $zip \
    --output data/raw/sales_${zip}.json

  # Rentals
  python -m src.fetch_searchlistings \
    --url "https://www.har.com/api/SearchListings?zip_code=${zip}&for_sale=0&bedroom_min=2" \
    --output data/raw/rentals_${zip}.json
done

# 2. Normalize all
python -m src.normalize_har

# 3. Generate scoreboard
python -m src.analyze_spreads --scoreboard

# 4. Analyze cap rates (manual Python)
# (See Advanced Usage 4.2 above)

# 5. Create investment scorecard comparing ZIPs
```

### Workflow C: Value a Single Property

**Goal**: Get market valuation using comps

```bash
# 1. Have HAR data processed (from previous imports)
python -m src.normalize_har  # (if not already done)

# 2. Analyze your subject property
python -m src.analyze_spreads --mlsnum 75866936

# 3. Review comps
cat data/processed/cohort_75866936.csv

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
python -m src.extract_har HAR_Export.har              # OR
python -m src.fetch_searchlistings --har HAR.har --zip 77088

python -m src.normalize_har

python -m src.analyze_spreads --scoreboard
python -m src.analyze_spreads --rank --top 20
python -m src.analyze_spreads --mlsnum 12345

# View results
ls -lh data/processed/                    # List output files
head data/processed/*.csv                 # Preview CSVs
```

---

**Need more help?** See [INSTALL.md](./INSTALL.md) or [../../README.md](../../README.md)
