# House Hunter 🏠

**Data-driven real estate market analysis and investment opportunity discovery tool.**

A Python-based system for aggregating raw real estate data from HAR (Houston Association of Realtors), normalizing it into clean datasets, and performing cohort-based spread analysis to identify market inefficiencies and investment opportunities.

---

## 🎯 What This Project Does

House Hunter solves the core problem in real estate investing: **finding apples-to-apples comparables in an asymmetric market**.

### The Problem
- **Raw data fragmentation**: MLS data, HAR listings, rental comps scattered across APIs
- **Inconsistent pricing metrics**: List price ≠ market price ≠ offer price; no unified valuation framework
- **Submarket blindness**: Houston is extremely heterogeneous (year built, deed restrictions, flood zones, school boundaries); naive comparisons fail
- **Time-wasting**: Manual spreadsheet comping takes hours per property
- **Decision paralysis**: "Is this deal good?" requires data from multiple sources stitched together

### The Solution
House Hunter provides a **decision-grade data pipeline** that:

1. **Captures raw snapshots** of active & closed listings from HAR API
2. **Normalizes** raw JSON into clean CSV tables (consistent types, ZIP normalization, etc.)
3. **Builds apples-to-apples cohorts** using intelligent filtering:
   - Same ZIP or nearby (with fallback logic)
   - Matching property type, bed/bath, sqft band, year built, DOM cutoff
4. **Computes "spreads"** (active ask PPSF - sold median PPSF)
5. **Ranks opportunities** by value (negative spreads = potential deals)
6. **Generates reports**:
   - Submarket scorecards (ZIP-level metrics)
   - Deal ranking lists (sorted by spread)
   - Individual subject analyses (with comps)

---

## 🔍 How It Works

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. RAW DATA CAPTURE                                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  HAR API SearchListings       Browser HAR Export                │
│  (for_sale=1, for_sale=0)    (.har files, 1MB+ responses)      │
│         ↓                            ↓                          │
│    curl + cookies         →  extract_har.py                    │
│         │                            │                         │
│         └────────────┬───────────────┘                          │
│                      ↓                                          │
│          data/raw/*.json (snapshots)                           │
│                      │                                          │
└─────────────────────────┼──────────────────────────────────────┘
                          │
┌─────────────────────────┼──────────────────────────────────────┐
│ 2. NORMALIZATION                                               │
├─────────────────────────┼──────────────────────────────────────┤
│                         ↓                                       │
│      normalize_har.py                                          │
│      • Parse JSON (handles JSONC comments)                     │
│      • Coerce numeric fields (handles "N/A", null, etc.)       │
│      • Normalize ZIP to 5-digit strings                        │
│      • Split into [active, sold] CSVs                          │
│                      ↓                                         │
│          data/processed/                                       │
│          ├── {snapshot}_active.csv                             │
│          └── {snapshot}_sold.csv                               │
│                      │                                         │
└─────────────────────────┼──────────────────────────────────────┘
                          │
┌─────────────────────────┼──────────────────────────────────────┐
│ 3. ANALYSIS & REPORTING                                        │
├─────────────────────────┼──────────────────────────────────────┤
│                         ↓                                       │
│      analyze_spreads.py                                        │
│      • Load latest active + sold CSVs                          │
│      • Build cohorts (ZIP, proptype, beds, sqft band, year)    │
│      • Compute spreads (active PPSF - sold median PPSF)        │
│      • Generate scorecards, rankings, analyses                 │
│                      ↓                                         │
│          data/processed/                                       │
│          ├── scoreboard_zip.csv                                │
│          ├── ranked_by_spread.csv                              │
│          └── cohort_<MLSNUM>.csv                               │
│                      │                                         │
└─────────────────────────┼──────────────────────────────────────┘
                          │
                          ↓
                   📊 Decision-Grade Insights
```

### Example: Single Property Analysis

Given active listing MLSNUM 75866936 (1117 W 17th St, 77008):

1. **Extract subject characteristics**: 3BR/2BA, 2,322 sqft, 2006 YoB, $440k asking
2. **Build cohort** from sold listings:
   - ZIP 77008 ✓
   - Single-Family ✓
   - 3 ± 1 beds ✓
   - 2,322 ± 15% sqft ✓
   - 2006 ± 10 years ✓
   - Results: 7 comparable sales
3. **Compute spread**:
   - Subject asking PPSF: $189
   - Cohort median sold PPSF: $256
   - **Spread: -$67 PPSF** (subject is $155k below market 🎯)

---

## 📋 Key Features

| Feature | Status | Description |
|---------|--------|-------------|
| **Raw data capture** | ✅ | Direct API fetch or HAR file extraction |
| **Multi-format import** | ✅ | JSON, JSONC, HAR files supported |
| **Data normalization** | ✅ | Type coercion, ZIP formatting, comment removal |
| **Cohort analysis** | ✅ | Flexible filtering (ZIP, type, beds, sqft, year, DOM) |
| **Spread computation** | ✅ | PPSF-based valuation gap analysis |
| **Report generation** | ✅ | Submarket scorecards, deal rankings, subject analyses |
| **Rental data support** | ✅ | Cap rate, price-to-rent metrics (coming: cash flow models) |
| **CLI interface** | ✅ | Command-line tools for all operations |
| **Web dashboard** | 🔄 | Coming: visualizations, filtering, alerting |
| **API service** | 🔄 | Coming: REST API for downstream integrations |
| **MLS integration** | 🔄 | Coming: direct MLS feed (currently HAR-only) |
| **Automated alerts** | 🔄 | Coming: "new deals matching criteria" notifications |

---

## 🚀 Quick Start

### Installation

```bash
# Clone the repo
git clone https://github.com/your-org/house-hunter.git
cd house-hunter

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

See [INSTALL.md](./docs/guides/INSTALL.md) for detailed setup and development guides.

### Basic Workflow

```bash
# 1. Create a snapshot pack
init-snapshot --label "Acres Homes 77091 sales sold rentals"

# 2. Manually export HAR files into snapshots/<snapshot_id>/raw/har

# 3. Extract + normalize + QA + analyze
extract-har --snapshot snapshots/<snapshot_id> snapshots/<snapshot_id>/raw/har
normalize --snapshot snapshots/<snapshot_id>
qa --snapshot snapshots/<snapshot_id>
analyze --snapshot snapshots/<snapshot_id>

# Optional one-shot wrapper
pipeline --snapshot snapshots/<snapshot_id>
```

See [Usage Guide](./docs/guides/USAGE.md) for detailed examples.

---

## 📁 Project Structure

```
house-hunter/
├── README.md                    ← You are here
├── INSTALL.md                   ← Setup & development
├── CONTRIBUTING.md              ← How to contribute
├── pyproject.toml               ← Python package config
├── src/
│   ├── __init__.py
│   ├── extract_har.py           ← Import JSON/HAR files
│   ├── normalize_har.py         ← Parse to clean CSVs
│   ├── analyze_spreads.py       ← Cohort analysis & reports
│   └── fetch_searchlistings.py  ← Direct API fetching
├── data/
│   ├── raw/                     ← Raw JSON snapshots (git-ignored)
│   └── processed/               ← Clean CSVs (git-ignored)
├── docs/
│   ├── guides/
│   │   ├── INSTALL.md           ← Setup & development
│   │   ├── USAGE.md             ← How to use the tool
│   │   └── API.md               ← CLI reference
│   ├── architecture/
│   │   ├── ARCHITECTURE.md      ← Technical design
│   │   ├── DATA_SCHEMA.md       ← Data model docs
│   │   └── ALGORITHMS.md        ← Cohort analysis algorithm
│   └── roadmap/
│       ├── ROADMAP.md           ← Implementation plan
│       └── PHASES.md            ← Development phases
└── tests/                       ← Unit tests (coming)
```

---

## 💡 Use Cases

### 1. Flip Deal Screening
*"Find single-family homes priced ≥15% below recent comps, good for light-to-heavy rehab"*

```bash
python -m src.analyze_spreads --rank --top 50 | \
  filter: PPSF_SPREAD < -$30 AND BLDGSQFT > 1500
```

### 2. Rental Yield Analysis
*"What ZIP codes have the best price-to-rent ratios?"*

```bash
# Fetch sales + rentals for multiple ZIPs
for zip in 77008 77018 77088 77091; do
  python -m src.fetch_searchlistings --zip $zip
done
python -m src.analyze_spreads --scoreboard  # Pivot by ZIP
```

### 3. Submarket Comp Selection
*"Build a comp set for 77008 to value subject property"*

```bash
python -m src.analyze_spreads --mlsnum 75866936
# Output: cohort_75866936.csv (all matching comps)
```

### 4. Market Velocity Tracking
*"How many days are homes sitting in each ZIP before sale?"*

```bash
# Load processed CSVs, pivot by ZIP on DOM
python -m src.analyze_spreads --scoreboard  # See DOM column
```

---

## 🏗️ Monorepo Vision (Hosted Service)

As this evolves into a hosted service, we'll restructure as a monorepo:

```
house-hunter-monorepo/
├── services/
│   ├── data-pipeline/          ← Current Python code
│   │   ├── src/
│   │   ├── pyproject.toml
│   │   └── tests/
│   ├── api/                    ← REST API service (FastAPI)
│   │   ├── app/
│   │   ├── requirements.txt
│   │   └── tests/
│   ├── web/                    ← React dashboard
│   │   ├── src/
│   │   ├── package.json
│   │   └── tests/
│   └── scheduler/              ← Airflow for scheduled jobs
│       ├── dags/
│       └── requirements.txt
├── infra/                      ← Docker, K8s, Terraform
│   ├── docker-compose.yml
│   ├── Dockerfile.*
│   └── k8s/
├── docs/                       ← Central documentation
│   ├── ARCHITECTURE.md
│   ├── DEPLOYMENT.md
│   └── API_SPEC.md
└── scripts/                    ← Shared utilities
    └── setup.sh
```

**Current Phase**: Standalone CLI tool
**Phase 2**: REST API + simple web UI
**Phase 3**: Hosted SaaS with user accounts, saved searches, alerts

See [ROADMAP.md](./docs/roadmap/ROADMAP.md) for detailed phases.

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| [docs/guides/INSTALL.md](./docs/guides/INSTALL.md) | Setup, dependencies, development environment |
| [docs/guides/USAGE.md](./docs/guides/USAGE.md) | Command reference, examples, workflows |
| [docs/architecture/ARCHITECTURE.md](./docs/architecture/ARCHITECTURE.md) | Technical design, data models, algorithms |
| [docs/roadmap/ROADMAP.md](./docs/roadmap/ROADMAP.md) | Implementation phases, priorities, timeline |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | How to contribute, coding standards, PR process |

---

## 🔐 Important Notes

### Data & Privacy
- **No personal data**: Tool aggregates public MLS data only (addresses, prices, property attributes)
- **Fair use**: Respects HAR rate limits; consider MLS direct access for production use
- **Data caching**: All snapshots are local; no external storage or analytics

### Limitations
- **Current data source**: HAR SearchListings API (limited to browser scraping)
- **Active-only scope**: Doesn't track historical trends (yet)
- **Gross metrics**: Cohort analysis is market-level; doesn't account for property-specific defects
- **No financing model**: Spreads are pricing gaps, not deal economics

---

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](./CONTRIBUTING.md) for:
- Code standards & style guide
- Testing requirements
- Pull request process
- Issue templates

---

## 📝 License

MIT License - see [LICENSE](./LICENSE)

---

## 🔗 Resources

- **HAR API Documentation**: https://www.har.com/
- **Real Estate Data Standards**: MLS RESO standards
- **Python Real Estate Libraries**: `pandas`, `numpy` for analysis

---

## 🎓 Learning Path for New Developers

1. **Read** [docs/guides/INSTALL.md](./docs/guides/INSTALL.md) - set up your environment
2. **Try** the [Quick Start](#-quick-start) with sample data
3. **Read** [docs/guides/USAGE.md](./docs/guides/USAGE.md) - understand workflows
4. **Read** [docs/architecture/ARCHITECTURE.md](./docs/architecture/ARCHITECTURE.md) - understand internals
5. **Explore** the code: `src/*.py` are well-documented
6. **Run tests** (coming): `pytest tests/`
7. **Contribute**: Pick an issue from [docs/roadmap/ROADMAP.md](./docs/roadmap/ROADMAP.md)

---

**Made with ❤️ for real estate investors and data enthusiasts.**
