# Installation & Development Setup

Complete guide for setting up House Hunter for development and usage.

## Prerequisites

- **Python 3.11+** (check with `python --version`)
- **pip** (usually included with Python)
- **Git** (for cloning the repo)
- **Virtual environment tool** (`venv` is built-in)

## For Users: Quick Install

### 1. Clone Repository

```bash
git clone https://github.com/your-org/house-hunter.git
cd house-hunter
```

### 2. Create Virtual Environment

```bash
python -m venv .venv
```

Activate it:

**macOS / Linux:**
```bash
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
.\.venv\Scripts\activate.bat
```

### 3. Install Dependencies

```bash
pip install -e .
```

This installs the package and its dependencies:
- `pandas>=2.0` - Data manipulation
- `python-dateutil>=2.9` - Date utilities
- `requests>=2.31` - HTTP requests

### 4. Verify Installation

```bash
normalize --help
qa --help
analyze --help
pipeline --help
```

You should see help text for both commands.

---

## For Developers: Full Development Setup

### 1. Clone & Environment Setup

```bash
git clone https://github.com/your-org/house-hunter.git
cd house-hunter
python -m venv .venv
source .venv/bin/activate  # or .\.venv\Scripts\activate on Windows
```

### 2. Install Dev Dependencies

```bash
pip install -e ".[dev]"
```

This adds:
- `pytest>=8.0` - Testing framework
- `ruff>=0.1` - Code linting & formatting

### 3. Configure IDE (VS Code)

Create `.vscode/settings.json`:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  },
  "files.exclude": {
    "**/__pycache__": true,
    "**/*.pyc": true
  }
}
```

### 4. Code Quality Checks

**Lint & Format:**
```bash
ruff check src/ --fix
ruff format src/
```

**Run Tests** (once tests are added):
```bash
pytest tests/ -v
```

### 5. Project Structure Overview

```
src/
├── __init__.py
├── extract_har.py
│   └── extract_from_har()    - Extract JSON from HAR files
│   └── copy_json_file()      - Import JSON/JSONC files
├── normalize_har.py
│   └── load_snapshot()       - Parse HAR JSON to DataFrames
│   └── process_snapshot()    - Write clean CSVs
├── analyze_spreads.py
│   └── build_cohort()        - Filter similar properties
│   └── subject_vs_cohort()   - Compare property to comps
│   └── generate_scoreboard() - Market metrics by ZIP
│   └── rank_active_by_spread()  - Rank opportunities
└── fetch_searchlistings.py
    └── fetch_listings()      - Direct API calls
    └── extract_headers_from_har() - Get auth from HAR
```

---

## Working with Data

### Getting HAR Files

**Option 1: Browser Export (Recommended)**

1. Open HAR.com in Firefox/Chrome
2. Open DevTools (F12)
3. Go to Network tab
4. Search for properties, apply filters
5. Right-click Network tab → Save as HAR file

**Option 2: Direct API** (requires HAR file for cookies first time)

```bash
python -m src.fetch_searchlistings --har exported.har --zip 77088
```

### Data Workflow

```bash
# 1. Import raw data
init-snapshot --label "Acres Homes 77091"
# Place HAR exports in snapshots/<snapshot_id>/raw/har/
extract-har --snapshot snapshots/<snapshot_id> snapshots/<snapshot_id>/raw/har
normalize --snapshot snapshots/<snapshot_id>
qa --snapshot snapshots/<snapshot_id>
analyze --snapshot snapshots/<snapshot_id>
```

### Output Files

After processing, check:

```
data/processed/
├── {snapshot}_active.csv      ← Active listings (normalized)
├── {snapshot}_sold.csv        ← Sold listings (normalized)
├── scoreboard_zip.csv         ← Market metrics by ZIP
├── ranked_by_spread.csv       ← Ranked opportunities
└── cohort_{MLSNUM}.csv        ← Comparable sales for property
```

---

## Troubleshooting

### "Module not found" Error

```bash
# Make sure you're in the right directory and venv is activated
cd /path/to/house-hunter
source .venv/bin/activate
pip list | grep pandas
```

### HAR File Won't Import

```bash
# Check HAR file is valid JSON
python -c "import json; json.load(open('file.har'))"

# HAR may be truncated (>1MB responses). Use fetch_searchlistings instead:
python -m src.fetch_searchlistings --har file.har --zip 77088
```

### Slow CSV Processing

Large datasets (1000+ listings) may take time:
- `normalize_har.py`: ~5-10 seconds per 1000 records
- `analyze_spreads.py`: ~30-60 seconds for --rank on full dataset

This is expected. For performance improvements, see [ROADMAP.md](../roadmap/ROADMAP.md).

### "Permission Denied" on Linux/Mac

```bash
# Make scripts executable
chmod +x .venv/bin/activate
chmod +x src/*.py
```

---

## Advanced Setup

### Installing from Different Locations

**From a fork:**
```bash
git clone https://github.com/your-fork/house-hunter.git
cd house-hunter
pip install -e .
```

**For editing & contributing:**
```bash
# Install in editable mode (changes to src/ are reflected immediately)
pip install -e ".[dev]"
```

### Using in a Jupyter Notebook

```python
import sys
sys.path.insert(0, '/path/to/house-hunter')

from src.normalize_har import load_snapshot, to_numeric
from src.analyze_spreads import load_latest_csvs, build_cohort
from pathlib import Path

# Now you can use functions directly
active, sold = load_latest_csvs()
```

### CI/CD (GitHub Actions)

Example `.github/workflows/test.yml` (coming):

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: ruff check src/
      - run: pytest tests/
```

---

## Docker (For Hosted Service)

**Dockerfile** (planned for Phase 2):

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install -e .

CMD ["python", "-m", "src.analyze_spreads", "--help"]
```

Build & run:
```bash
docker build -t house-hunter .
docker run house-hunter python -m src.fetch_searchlistings --help
```

---

## Next Steps

- Read [docs/guides/USAGE.md](./USAGE.md) for detailed command reference
- Read [docs/architecture/ARCHITECTURE.md](../architecture/ARCHITECTURE.md) for internal design
- Check [docs/roadmap/ROADMAP.md](../roadmap/ROADMAP.md) for what's coming next
- See [CONTRIBUTING.md](../../CONTRIBUTING.md) if you want to contribute

---

## Environment Variables (For Future Use)

Not currently needed, but will support:

```bash
# HAR.COM authentication (future MLS integration)
export HAR_USERNAME="your_username"
export HAR_PASSWORD="your_password"

# AWS/cloud storage (future data warehouse)
export AWS_REGION="us-east-1"
export DATA_BUCKET="house-hunter-data"

# API service configuration (Phase 2)
export API_PORT=8000
export API_DEBUG=false
```

---

## Uninstalling

To remove House Hunter:

```bash
deactivate  # Exit virtual environment
rm -rf house-hunter/  # Delete folder
# Or keep .venv for other projects:
rm -rf house-hunter/{src,data,docs}
```

---

**Having issues?** See [README.md#-resources](../../README.md#-resources) or open an issue on GitHub.
