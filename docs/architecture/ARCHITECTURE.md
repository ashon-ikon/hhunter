# Technical Architecture

Detailed technical design, algorithms, and data models for House Hunter.

## System Overview

House Hunter is a **three-stage data pipeline**:

```
┌──────────────────┐
│  RAW DATA        │  HAR API / Browser Export
│  (JSON)          │  SearchListings responses
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  NORMALIZATION   │  Coerce types, clean fields
│  (CSV)           │  Split active/sold, normalize ZIPs
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  ANALYSIS        │  Cohort building, spread computation
│  (Reports)       │  Market scorecards, rankings
└──────────────────┘
```

---

## Stage 1: Raw Data Capture

### Input Sources

**HAR API (SearchListings)**
- Endpoint: `https://www.har.com/api/SearchListings`
- Query params: `zip_code`, `for_sale` (0=rentals, 1=sales), `sort`, `view`
- Response: JSON with `data` (active) and `sold_data` (closed) arrays
- Auth: Browser cookies (via HAR export or direct API)
- Limitations: 1MB response limit per HAR capture

**HAR File Export**
- Browser DevTools Network → Save as HAR
- Contains full HTTP request/response including body (may be truncated)
- Parsed to extract SearchListings responses

**Direct JSON Files**
- JSONC files (JSON with `//` comments) supported
- Manually exported or cached API responses

### Data Schema (Raw)

Each listing record contains ~150 fields:

```json
{
  "MLSNUM": "75866936",
  "LISTINGID": 12345678,
  "FULLSTREETADDRESS": "1117 W 17th St",
  "CITY": "Houston",
  "ZIP": "77008",
  "STATE": "TX",
  "BEDROOM": 3,
  "BATHFULL": 2,
  "BATHHALF": 1,
  "BLDGSQFT": 2322,
  "BLDGSQFTSRC": "Appraisal",
  "LOTSIZE": 2500,
  "YEARBUILT": 2006,
  "PROPTYPENAME": "Single-Family",
  "SUBDIVISION": "Heights District",
  "MARKETAREA": "Heights/Greater Heights",
  "LISTPRICEORI": 440000,
  "PRICEPERSQFT": 189,
  "DAYSONMARKET": 37,
  "DOM": 37,
  "SALESPRICE": null,
  "LISTSTATUS": "Active",
  "AGENTLISTNAME": "Jane Doe",
  "OFFICELISTNAME": "Keller Williams",
  "LATITUDE": 29.800285,
  "LONGITUDE": -95.420281,
  "RESTRICTION": "Deed Restrictions",
  "HASPRIVATEPOOL": false,
  "GARAGENUM": 2,
  ...
}
```

---

## Stage 2: Normalization

### Process: `normalize_har.py`

**Input**: Raw JSON (from HAR or API)

**Steps**:

1. **Parse JSON**
   - Remove JSONC comments if present
   - Handle gzip compression (if any)
   - Extract `data` (active) and `sold_data` (closed) arrays

2. **Type Coercion**
   - Numeric fields: coerce to `float64`, invalid → `NaN`
   - String fields: preserve case, trim whitespace
   - Boolean fields: parse "true"/"false" strings
   - Date fields: parse ISO 8601 or M/D/YYYY

3. **Field Normalization**
   - ZIP: pad to 5 digits (e.g., "8" → "00008")
   - Prices: remove $ and commas if string
   - DOM: ensure numeric (not "N/A")
   - Property type: standardize naming

4. **Output**: Two CSV files
   - `{snapshot}_active.csv` - Active listings
   - `{snapshot}_sold.csv` - Closed listings

### Data Quality Checks

```python
# Numeric field list (auto-coerce)
NUMERIC_FIELDS = [
    "LISTPRICEORI", "SALESPRICE", "PRICEPERSQFT",
    "BLDGSQFT", "LOTSIZE", "ACRES", "BEDROOM", "BATHFULL",
    "DAYSONMARKET", "DOM", "YEARBUILT", "LATITUDE", "LONGITUDE"
]

# For each record:
for field in NUMERIC_FIELDS:
    df[field] = pd.to_numeric(df[field], errors='coerce')
```

**Result**: Clean, machine-readable data

---

## Stage 3: Analysis & Reporting

### Core Algorithm: Cohort Building

Given a subject property (active listing), find comparable sales:

```python
def build_cohort(
    sold_df,           # All closed listings
    zip_code,          # Required: exact match
    proptype,          # Required: e.g., "Single-Family"
    beds,              # Optional: ±1
    bathfull,          # Optional: ±1
    sqft,              # Optional: ±15%
    yearbuilt,         # Optional: ±10 years
    dom_cutoff=120,    # Exclude stale listings
):
    """
    Build comparable sales cohort from sold listings.
    """
    cohort = sold_df.copy()

    # Hard filters
    cohort = cohort[cohort['ZIP'] == str(zip_code).zfill(5)]
    cohort = cohort[cohort['PROPTYPENAME'] == proptype]

    # Soft filters (ranges)
    if beds:
        cohort = cohort[cohort['BEDROOM'].between(beds - 1, beds + 1)]
    if bathfull:
        cohort = cohort[cohort['BATHFULL'].between(bathfull - 1, bathfull + 1)]
    if sqft > 0:
        lo, hi = sqft * 0.85, sqft * 1.15  # ±15%
        cohort = cohort[cohort['BLDGSQFT'].between(lo, hi)]
    if yearbuilt:
        # Tighter band for newer homes (infill)
        band = 8 if yearbuilt >= 2018 else 10
        cohort = cohort[cohort['YEARBUILT'].between(yearbuilt - band, yearbuilt + band)]

    # Quality filter
    cohort = cohort[cohort['DOM'] <= dom_cutoff]
    cohort = cohort[pd.notna(cohort['PRICEPERSQFT'])]

    return cohort
```

### Spread Computation

```python
def compute_spread(subject, cohort):
    """
    Calculate price gap between active listing and sold comps.
    """
    active_ppsf = subject['PRICEPERSQFT']
    sold_median_ppsf = cohort['PRICEPERSQFT'].median()

    spread = active_ppsf - sold_median_ppsf

    return {
        'active_ppsf': active_ppsf,
        'sold_median_ppsf': sold_median_ppsf,
        'spread': spread,
        'interpretation': (
            'Below market 🎯' if spread < -25 else
            'At market' if -25 <= spread <= 25 else
            'Above market ⚠️'
        )
    }
```

**Example**:
- Subject PPSF: $189
- Cohort median: $256
- **Spread: -$67** = Subject is $155k below market on 2,322 sqft property

### Report Types

#### 1. Submarket Scoreboard

Groups listings by ZIP code and computes:

```python
scoreboard = pd.DataFrame({
    'ZIP': [...],
    'sold_count': [...],           # Number of sales
    'sold_median_ppsf': [...],     # Market price level
    'sold_median_dom': [...],      # Market velocity
    'active_count': [...],         # Active inventory
    'active_median_ppsf': [...],   # Ask price level
    'ppsf_gap': [...],             # (active - sold) = market direction
})
```

**Interpretation**:
- `ppsf_gap < -20`: Actives priced below market (buyer's market / overstock)
- `ppsf_gap > +20`: Actives priced above market (seller's market / scarce)
- `sold_count < 5`: Insufficient comps (unreliable)

#### 2. Ranked Opportunities

Ranks active listings by spread (best deals first):

```python
ranked = active_df.groupby('MLSNUM').apply(
    lambda row: subject_vs_cohort(active_df, sold_df, row['MLSNUM'])
).sort_values('PPSF_SPREAD')
```

**Filters applied**:
- Only properties with ≥3 comps (sample size)
- Only valid spreads (not NaN)
- Sorted ascending (negative = deals)

#### 3. Subject Analysis

For a single property, outputs:
- Subject attributes
- Matching cohort (all comps)
- Spread metrics
- Interpretation

---

## Data Model

### Table Structure (CSV Output)

**active.csv / sold.csv columns:**

| Field | Type | Notes |
|-------|------|-------|
| MLSNUM | int | MLS listing number (PK) |
| LISTINGID | int | Internal HAR ID |
| FULLSTREETADDRESS | str | Address |
| ZIP | str | 5-digit ZIP |
| CITY | str | City name |
| PROPTYPENAME | str | "Single-Family", "Duplex", "Lot", etc. |
| BEDROOM | float | May be null for lots |
| BATHFULL | float | Full baths |
| BATHHALF | float | Half baths |
| BLDGSQFT | float | Building sqft (null for lots) |
| LOTSIZE | float | Lot sqft |
| YEARBUILT | float | Year (null for undeveloped lots) |
| LISTPRICEORI | float | List/ask price (or monthly rent if rental) |
| SALESPRICE | float | Sale price (closed listings) |
| PRICEPERSQFT | float | $ per sqft of building |
| DOM | float | Days on market |
| DAYSONMARKET | float | Synonym for DOM |
| SUBDIVISION | str | Subdivision name |
| MARKETAREA | str | Market area (e.g., "Heights/Greater Heights") |
| LISTSTATUS | str | "Active", "Closed", "Under Contract", etc. |
| RESTRICTION | str | "Deed Restrictions", "No Restrictions", etc. |
| AGENTLISTNAME | str | Listing agent name |
| OFFICELISTNAME | str | Listing brokerage |
| LATITUDE | float | GPS latitude |
| LONGITUDE | float | GPS longitude |
| HASPRIVATEPOOL | bool | Has pool? |
| GARAGENUM | float | Number of garage spaces |
| __dataset | str | "active" or "sold" (added during normalization) |

### Key Relationships

- **ZIP → Market Area**: Many ZIPs belong to one market area
- **Property Type → Price**: Single-family ≠ duplex ≠ lot
- **Year Built → Price**: Infill (2015+) commands premium vs legacy
- **DOM → Quality**: High DOM suggests overpriced or problem property
- **PPSF → Valuation**: Primary metric for cohort comparison

---

## Algorithm Limitations & Future Improvements

### Current Limitations

1. **Cohort size bias**: Small cohorts (n<3) are unreliable
   - Solution: Expand filtering to larger geography

2. **List price assumption**: Assumes active PPSF = market price
   - Reality: Active prices are "aspirational"; sold prices are actual
   - Mitigation: Use sold price for actives once they close

3. **Year built granularity**: ±10 year band may mix eras
   - Example: 1995 home vs 2005 home (different construction)
   - Solution: Use infill threshold (2015+) for tighter segments

4. **Market area heterogeneity**: Even within ZIP, huge variation
   - Example: Flood zone vs elevated = 20% price delta
   - Solution: Add flood zone, school district as filters

5. **No adjustment factors**: Treated all comps equally
   - Reality: Corner lot, pool, recent renovation = premium
   - Solution: Use hedonic regression for adjustment

### Roadmap Improvements

- **Phase 2**: Add filters for lot characteristics (pool, corner, etc.)
- **Phase 3**: Hedonic regression for automatic adjustment factors
- **Phase 4**: Machine learning to predict DOM and sale price
- **Phase 5**: Time-series tracking (track same property over months)

---

## Performance Characteristics

### Data Processing Speed

| Operation | Time (120 listings) | Time (1000 listings) |
|-----------|------------------|----------------------|
| Extract from HAR | ~2 sec | ~15 sec |
| Normalize to CSV | ~1 sec | ~3 sec |
| Load CSVs | ~0.5 sec | ~2 sec |
| Build single cohort | ~0.1 sec | ~0.5 sec |
| Rank all actives | ~30 sec | ~300 sec |
| Generate scoreboard | ~1 sec | ~5 sec |

**Bottleneck**: Ranking all properties (N×M comparisons)

**Optimization (Phase 2)**:
- Cache cohort results
- Parallel processing (multiprocessing.Pool)
- Database indexing (SQLite/PostgreSQL)

### Memory Usage

- 120 listings: ~30 MB
- 1000 listings: ~250 MB
- 10000 listings: ~2.5 GB

---

## Error Handling

### Graceful Degradation

```python
# Parse invalid JSON: try JSONC, then raw
try:
    data = json.loads(text)
except json.JSONDecodeError:
    # Try removing comments
    clean = re.sub(r'//.*?\n', '\n', text)
    data = json.loads(clean)

# Numeric field coercion: invalid → NaN (not 0)
df['PRICEPERSQFT'] = pd.to_numeric(df['PRICEPERSQFT'], errors='coerce')

# Cohort building: filter as much as possible
if cohort.empty:
    # Return "no comps available" rather than crash
    return {'error': 'Insufficient comparables', 'n': 0}
```

---

## Security Considerations

### Data Privacy
- ✅ No PII collected (public MLS data only)
- ✅ All data local (no cloud storage)
- ⚠️ HAR exports contain cookies (store securely, don't commit)

### Input Validation
- Validates JSON structure before processing
- Sanitizes file paths
- Type-checks API parameters

### Rate Limiting
- Respects HAR API rate limits (implement backoff, Phase 2)
- Consider MLS direct access for production use

---

## Testing Strategy (Coming)

```python
# tests/test_normalize.py
def test_coerce_numeric():
    df = pd.DataFrame({'price': ['$100,000', 'invalid', 100000]})
    result = to_numeric(df)
    assert result['price'].isna().sum() == 1  # "invalid" → NaN

# tests/test_cohort.py
def test_build_cohort_empty_filter():
    cohort = build_cohort(sold_df, zip='12345', proptype='Nonexistent')
    assert len(cohort) == 0

# tests/test_spread.py
def test_spread_computation():
    spread = compute_spread(subject, cohort)
    assert spread['spread'] == spread['active_ppsf'] - spread['sold_median_ppsf']
```

---

## See Also

- [ROADMAP.md](../roadmap/ROADMAP.md) - Implementation phases
- [README.md](../../README.md) - Project overview
- [USAGE.md](../guides/USAGE.md) - How to use the tool
