# Contributing to House Hunter

We welcome contributions! This document explains how to contribute effectively.

## Getting Started

### 1. Set Up Development Environment

See [docs/guides/INSTALL.md](./docs/guides/INSTALL.md) for full setup instructions:

```bash
git clone https://github.com/your-fork/house-hunter.git
cd house-hunter
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Understand the Codebase

- Read [README.md](./README.md) for project overview
- Read [docs/architecture/ARCHITECTURE.md](./docs/architecture/ARCHITECTURE.md) for technical design
- Explore `src/*.py` - each module is well-documented

### 3. Check the Roadmap

See [docs/roadmap/ROADMAP.md](./docs/roadmap/ROADMAP.md) to understand:
- What features are planned
- What's currently being worked on
- Priorities for the next phase

---

## Development Workflow

### 1. Find or Create an Issue

**Browse open issues**: [GitHub Issues](https://github.com/your-org/house-hunter/issues)

**No suitable issue?** Create one:
- Use the appropriate template (bug, feature request, documentation)
- Provide clear description
- For features, link to [ROADMAP.md](./docs/roadmap/ROADMAP.md)

### 2. Create a Feature Branch

```bash
git checkout -b feature/my-feature
# or for bugs:
git checkout -b fix/bug-description
```

**Branch naming conventions**:
- `feature/short-description` - New functionality
- `fix/short-description` - Bug fixes
- `docs/short-description` - Documentation updates
- `refactor/short-description` - Code cleanup

### 3. Make Your Changes

**Code style**:
```bash
# Format code
ruff format src/

# Lint code
ruff check src/ --fix
```

**File organization**:
- New feature? Add to appropriate `src/*.py` or create new module
- Tests? Add to `tests/` with name `test_*.py`
- Docs? Add to `docs/` with clear naming

### 4. Write Tests (Phase 2+)

For now, tests are optional but encouraged:

```python
# tests/test_normalize.py
import pytest
from src.normalize_har import to_numeric

def test_coerce_numeric_valid():
    df = pd.DataFrame({'price': ['100000', '200000']})
    result = to_numeric(df)
    assert result['price'].dtype == 'float64'

def test_coerce_numeric_invalid():
    df = pd.DataFrame({'price': ['$100k', 'free']})
    result = to_numeric(df)
    assert result['price'].isna().sum() == 2  # Both invalid
```

Run tests:
```bash
pytest tests/ -v
```

### 5. Commit with Clear Messages

```bash
git add src/my_file.py
git commit -m "Add feature: description of what was changed

- Bullet point explaining key changes
- Reference issue if applicable (#123)
- Follow conventional commits style"
```

**Commit message style**:
- First line: imperative mood, < 50 characters
- Blank line
- Detailed explanation (wrapped at 72 chars)
- Reference issues: "Closes #123" or "Fixes #456"

### 6. Push and Create Pull Request

```bash
git push origin feature/my-feature
```

Then go to GitHub and click "Create Pull Request"

**PR template** (coming):
```markdown
## Description
What does this PR do?

## Related Issue
Closes #123

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Refactoring

## Testing
How was this tested?

## Checklist
- [ ] Code follows style guidelines
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No breaking changes
```

### 7. Respond to Feedback

Maintainers will review your code. Be prepared to:
- Explain design decisions
- Make requested changes
- Add tests or documentation as needed

Once approved, your changes will be merged! 🎉

---

## Code Standards

### Python Style

We use **Ruff** for formatting and linting:

```bash
# Auto-format code
ruff format src/ tests/

# Check for errors
ruff check src/ tests/

# Fix auto-fixable errors
ruff check src/ --fix
```

**Style guide** (enforced by Ruff):
- 100 character line length
- 4-space indentation
- Imports organized (standard lib, third-party, local)
- Docstrings for all public functions

### Example Function

```python
def build_cohort(
    sold: pd.DataFrame,
    zip_code: str,
    proptype: str = "Single-Family",
    beds: int | None = None,
    sqft: float | None = None,
) -> pd.DataFrame:
    """
    Build a comparable sales cohort from sold listings.

    Filters applied in order:
    - ZIP code (exact match)
    - Property type (exact match)
    - Bedrooms (±1)
    - Building sqft (±15%)

    Args:
        sold: DataFrame of sold listings
        zip_code: Target ZIP code (e.g., "77008")
        proptype: Property type to match
        beds: Number of bedrooms (optional, ±1)
        sqft: Building sqft (optional, ±15%)

    Returns:
        DataFrame of matching comparables

    Raises:
        ValueError: If zip_code is invalid format

    Example:
        >>> cohort = build_cohort(sold, "77008", beds=3, sqft=2000)
        >>> cohort.shape
        (7, 150)
    """
    # Implementation here
    pass
```

### Type Hints

Use Python 3.11+ type hints:

```python
# Good
def fetch_listings(url: str, timeout: int = 30) -> dict:
    pass

def process_data(df: pd.DataFrame) -> tuple[list[str], list[int]]:
    pass

# Acceptable in Phase 1 (no hints)
def old_function(url, timeout=30):
    pass
```

### Error Handling

Explicit error handling, avoid bare `except`:

```python
# Good
try:
    data = json.loads(text)
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse JSON: {e}")
    return None

# Avoid
try:
    data = json.loads(text)
except:  # ❌ Too broad
    pass
```

---

## Testing Guidelines

### Test Structure

```python
# tests/test_analyze_spreads.py
import pytest
import pandas as pd
from src.analyze_spreads import build_cohort, compute_spread

class TestBuildCohort:
    @pytest.fixture
    def sample_data(self):
        """Sample sold listings for testing."""
        return pd.DataFrame({
            'ZIP': ['77008', '77008', '77008', '77009'],
            'PROPTYPENAME': ['Single-Family'] * 3 + ['Duplex'],
            'BEDROOM': [3, 3, 4, 3],
            'BLDGSQFT': [2000, 2200, 2500, 1800],
            'PRICEPERSQFT': [200, 220, 180, 210],
        })

    def test_exact_match(self, sample_data):
        """Cohort with exact ZIP and type."""
        cohort = build_cohort(sample_data, '77008', 'Single-Family')
        assert len(cohort) == 3

    def test_zip_mismatch(self, sample_data):
        """No results for wrong ZIP."""
        cohort = build_cohort(sample_data, '99999', 'Single-Family')
        assert len(cohort) == 0

    def test_bed_filter(self, sample_data):
        """Filter by bedrooms (±1)."""
        cohort = build_cohort(sample_data, '77008', beds=3)
        assert all(cohort['BEDROOM'].between(2, 4))
```

### Test Coverage (Target)

- **Phase 1**: No coverage requirement (focus on functionality)
- **Phase 2**: 70%+ coverage target
- **Phase 3+**: 80%+ coverage requirement

Measure with:
```bash
pip install pytest-cov
pytest tests/ --cov=src/
```

---

## Documentation

### Docstring Format

Use Google-style docstrings:

```python
def fetch_listings(url: str, headers: dict | None = None) -> dict:
    """
    Fetch SearchListings API data.

    Makes HTTP GET request to HAR SearchListings endpoint and
    returns parsed JSON response.

    Args:
        url: Full SearchListings API URL with query parameters
        headers: Optional HTTP headers (cookies, auth, etc.)

    Returns:
        Dictionary with keys:
        - 'data': List of active listings
        - 'sold_data': List of sold listings
        - 'meta': Metadata about the response

    Raises:
        requests.exceptions.RequestException: If API call fails
        ValueError: If response is not valid JSON

    Example:
        >>> data = fetch_listings("https://www.har.com/api/...")
        >>> len(data['data'])
        120
    """
    pass
```

### README Updates

If your change affects user-facing functionality:
1. Update [README.md](./README.md) feature table
2. Update [docs/guides/USAGE.md](./docs/guides/USAGE.md) with examples
3. Update [docs/architecture/ARCHITECTURE.md](./docs/architecture/ARCHITECTURE.md) if algorithm changes

### Documentation Files

- `README.md` - Project overview (user-facing)
- `docs/guides/USAGE.md` - How to use tools (examples, workflows)
- `docs/guides/INSTALL.md` - Setup instructions
- `docs/architecture/ARCHITECTURE.md` - Technical design (developer-facing)
- `docs/architecture/DATA_SCHEMA.md` - Data model (coming)
- `docs/roadmap/ROADMAP.md` - Future plans

---

## Common Contributions

### Adding a New CLI Command

Example: Add `--filter` option to `analyze_spreads.py`

1. **Update function**:
```python
# src/analyze_spreads.py
def rank_active_by_spread(
    active: pd.DataFrame,
    sold: pd.DataFrame,
    price_min: float | None = None,  # NEW
    price_max: float | None = None,  # NEW
    top_n: int = 20,
) -> pd.DataFrame:
    """Rank active listings by spread with optional price filter."""
    results = []
    for _, row in active.iterrows():
        # ... existing code ...
        if price_min and row['LISTPRICEORI'] < price_min:
            continue
        if price_max and row['LISTPRICEORI'] > price_max:
            continue
        results.append(summary.iloc[0].to_dict())
    return pd.DataFrame(results)
```

2. **Add CLI argument**:
```python
# src/analyze_spreads.py main()
parser.add_argument("--price-min", type=float, help="Min list price")
parser.add_argument("--price-max", type=float, help="Max list price")

# Then:
ranked = rank_active_by_spread(
    active,
    sold,
    price_min=args.price_min,
    price_max=args.price_max,
)
```

3. **Write tests**:
```python
# tests/test_analyze_spreads.py
def test_rank_with_price_filter():
    ranked = rank_active_by_spread(
        active, sold,
        price_min=200000, price_max=400000
    )
    assert all(200000 <= p <= 400000 for p in ranked['LIST_PRICE'])
```

4. **Update docs**:
- [docs/guides/USAGE.md](./docs/guides/USAGE.md) - Add example
- [docs/guides/API.md](./docs/guides/API.md) - Document new flag

### Bug Fix

Example: Fix ZIP normalization not handling null values

1. **Find the bug** (in `normalize_har.py`):
```python
# Current code
df["ZIP"] = df["ZIP"].astype(str).str.zfill(5)  # Fails on null
```

2. **Fix it**:
```python
# Better
df["ZIP"] = df["ZIP"].fillna("00000").astype(str).str.zfill(5)
```

3. **Test it**:
```python
def test_zip_null():
    df = pd.DataFrame({'ZIP': [None, '8', '77008']})
    result = normalize_zip(df)
    assert result['ZIP'].tolist() == ['00000', '00008', '77008']
```

4. **Commit with "Fixes #123"**

### Documentation Update

No need for code review if only updating markdown:

```bash
git checkout -b docs/clarify-cohort-algorithm
# Edit docs/architecture/ARCHITECTURE.md
git add docs/
git commit -m "docs: clarify cohort building algorithm"
git push origin docs/clarify-cohort-algorithm
```

---

## Getting Help

### Before You Start

- **Question about the code?** Check [docs/architecture/ARCHITECTURE.md](./docs/architecture/ARCHITECTURE.md)
- **How to use the tool?** Check [docs/guides/USAGE.md](./docs/guides/USAGE.md)
- **Want to add a feature?** Check [docs/roadmap/ROADMAP.md](./docs/roadmap/ROADMAP.md)

### While Contributing

- **Can't figure out a bug?** Comment on the issue and ask
- **Need feedback on approach?** Open a draft PR early
- **Blocked on something?** Create an issue or ask in PR

### Code Review Process

1. You create a PR
2. Maintainers review (within 3-5 days)
3. You respond to feedback
4. Changes approved → merged
5. Celebrate! 🎉

Typical review cycle: 1-3 rounds of feedback

---

## Code of Conduct

Be respectful, inclusive, and constructive. We're all here to build something great.

---

## Recognition

All contributors will be:
- Added to [CONTRIBUTORS.md](./CONTRIBUTORS.md) (coming)
- Credited in release notes
- Invited to community calls (Phase 2+)

---

## Questions?

- **GitHub Issues**: For bugs, feature requests
- **Email**: (Coming - for private feedback)
- **Discussions**: (Coming - for general questions)

---

**Thank you for contributing! We're excited to have you on the team.** 🚀
