# Development Roadmap

Strategic phases for House Hunter's evolution from CLI tool to hosted SaaS platform.

---

## Phase 1: MVP CLI (Current)

**Status**: ✅ Complete

**Objectives**: Build core data pipeline for market analysis

### Completed Features
- ✅ HAR file extraction (JSON/JSONC import)
- ✅ Data normalization (type coercion, ZIP formatting)
- ✅ Cohort building (ZIP, proptype, beds, sqft, year filters)
- ✅ Spread computation (active PPSF vs sold median)
- ✅ Report generation (scoreboard, rankings, subject analyses)
- ✅ Rental market support (for_sale=0 API endpoint)
- ✅ Direct API fetching (SearchListings with HAR credentials)
- ✅ CLI interface (4 main commands)

### Example Usage
```bash
python -m src.fetch_searchlistings --har export.har --zip 77088
python -m src.normalize_har
python -m src.analyze_spreads --scoreboard
python -m src.analyze_spreads --rank --top 20
python -m src.analyze_spreads --mlsnum 75866936
```

---

## Phase 2: Web API & Dashboard (Q2-Q3 2026)

**Objectives**: Make tool accessible via web; add visualization & persistence

### Features to Build

#### 2.1 REST API Service (FastAPI)
- [ ] **Endpoints**:
  - `POST /api/import` - Upload HAR file or JSON
  - `GET /api/listings/{zip}` - Get active listings for ZIP
  - `GET /api/comps/{mlsnum}` - Get cohort for property
  - `GET /api/scoreboard` - Market metrics by ZIP
  - `GET /api/opportunities` - Ranked deals
  - `GET /api/health` - Service status

- [ ] **Authentication**: API keys (bearer tokens)

- [ ] **Error Handling**: Standardized error responses

```python
# Example response
{
  "success": true,
  "data": {
    "zip": "77088",
    "active_count": 120,
    "median_price": 279990,
    "spread": -25.5
  },
  "meta": {
    "timestamp": "2026-03-05T12:00:00Z",
    "execution_time_ms": 245
  }
}
```

#### 2.2 Web Dashboard (React)
- [ ] **Views**:
  - Market overview (selected ZIPs)
  - Submarket scorecards (heat maps by ZIP)
  - Deal ranking (filterable table)
  - Property detail (comps, spread analysis)
  - Saved searches (user favorites)

- [ ] **Features**:
  - Filter by price range, bed/bath, year built, DOM
  - Toggle rental vs sales analysis
  - Export to CSV/Excel
  - Responsive design (mobile-friendly)

#### 2.3 Data Persistence (SQLite / PostgreSQL)
- [ ] **Database schema**:
  ```sql
  listings (
    id, mlsnum, zip, proptype, beds, baths, sqft, year,
    list_price, ppsf, dom, status, updated_at
  )

  snapshots (
    id, zip, source (har/api), count_active, count_sold, created_at
  )

  analysis_cache (
    subject_mlsnum, cohort_json, spread, computed_at
  )
  ```

- [ ] **Cache strategies**:
  - Cache cohort results (expires daily)
  - Cache scorecards (expires daily)
  - Avoid recomputing same comparisons

#### 2.4 Docker & Deployment
- [ ] **Dockerize**:
  - Data pipeline service
  - API service
  - React frontend

- [ ] **docker-compose.yml**:
  ```yaml
  version: '3.8'
  services:
    data:
      build: ./services/data-pipeline
      environment:
        - DATA_DIR=/data
    api:
      build: ./services/api
      ports:
        - "8000:8000"
      depends_on:
        - data
    web:
      build: ./services/web
      ports:
        - "3000:3000"
      depends_on:
        - api
    db:
      image: postgres:15
      volumes:
        - ./data/db:/var/lib/postgresql/data
  ```

### Deliverables
- REST API (OpenAPI/Swagger docs)
- Web dashboard (localhost:3000)
- Docker Compose setup
- Deployment guide

---

## Phase 3: Scheduled Jobs & Alerts (Q4 2026)

**Objectives**: Enable recurring data pulls and automated notifications

### Features to Build

#### 3.1 Job Scheduler (APScheduler / Airflow)
- [ ] **Daily snapshots**:
  - Fetch HAR data for saved ZIPs (hourly)
  - Normalize & cache cohorts
  - Detect new deals (spread < -$25)
  - Update scorecards

```python
# Example DAG
@scheduler.scheduled_job('cron', hour=9, minute=0)
def daily_snapshot():
    for zip_code in config.WATCH_ZIPS:
        fetch_searchlistings(zip_code)
        normalize_har()
        find_new_deals(threshold=-25)
        send_alerts()
```

#### 3.2 Alert System
- [ ] **Email/Slack notifications**:
  - "New deal found in 77088: $XYZ below market"
  - Daily scorecards (top changes by ZIP)
  - Market velocity alerts (DOM jumped 50%)

- [ ] **User preferences**:
  - Watched ZIPs
  - Deal criteria (min spread, max price, etc.)
  - Notification frequency

#### 3.3 Historical Tracking
- [ ] **Time-series data**:
  - Track same property PPSF over weeks/months
  - Detect price reductions (motivation signal)
  - Compute market trends (is 77088 appreciating?)

```python
# Schema addition
price_history (
  mlsnum, zip, date, pricepersqft, days_on_market, status
)
```

#### 3.4 User Accounts & Saved Searches
- [ ] **Authentication**:
  - Sign up / login
  - API key management
  - Email verification

- [ ] **Saved searches**:
  - Store filter criteria (e.g., "3BR under $300k in 77008")
  - Auto-run saved search daily
  - Show "new matches" since last login

### Deliverables
- Job scheduler service
- Alert engine (email/Slack)
- Enhanced API (saved searches, alerts)
- Updated web dashboard

---

## Phase 4: Advanced Analytics (2027)

**Objectives**: Move beyond raw comps; add ML-based insights

### Features to Build

#### 4.1 Hedonic Regression
- [ ] **Automatic adjustment factors**:
  - Pool: +$15k
  - Corner lot: +$5k
  - Recent renovation: +$20k
  - Distance from schools: -$2k/mile

```python
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression

# Train on sold listings
X = sold_df[['BLDGSQFT', 'YEARBUILT', 'BEDROOM', 'POOL', ...]]
y = sold_df['SALESPRICE']

model = LinearRegression().fit(X, y)

# Predict subject value with adjustments
predicted_price = model.predict(subject_features)
adjustment = predicted_price - subject_list_price
```

#### 4.2 Price Prediction
- [ ] **Estimate likely sale price** from active listing characteristics
- [ ] **Predict Days on Market** (quick movers vs stale listings)
- [ ] **Estimate ARV** (After-Repair Value) using comps

#### 4.3 Market Momentum
- [ ] **Detect market shifts**:
  - Inventory up 20% → buyer's market
  - DOM down 10 days → seller's market
  - Price appreciation by ZIP → investment opportunity

#### 4.4 Investment Scoring
- [ ] **Composite deal score** combining:
  - Price-to-rent ratio (rental yield)
  - Spread to comps (equity capture)
  - Market momentum (appreciation potential)
  - Lease-up risk (vacancy rates by area)

```python
deal_score = (
  0.3 * (200 / price_to_rent_ratio) +  # Rent yield
  0.4 * (abs(ppsf_spread) / 50) +      # Price gap
  0.2 * (dom_trend / 30) +              # Velocity
  0.1 * (market_appreciation / 10)      # Appreciation
)
```

### Deliverables
- Hedonic pricing model
- ML-based predictions (price, DOM, ARV)
- Investment scoring system
- Enhanced web dashboard

---

## Phase 5: Enterprise Features (2027+)

**Objectives**: Expand to rental analysis, portfolio tracking, financing integration

### Features to Build

#### 5.1 Rental Analysis Enhancements
- [ ] **Cap rate computation** (rental yield)
- [ ] **Cash flow models** (with financing scenarios)
- [ ] **Portfolio-level metrics** (aggregate ROI across deals)

```python
# Cap Rate Example
annual_rent = monthly_rent * 12
cap_rate = annual_rent / purchase_price * 100  # e.g., 8.5%

# Financing model
loan_amount = purchase_price * 0.75  # 25% down
monthly_payment = mortgage_payment(loan_amount, rate=6.5, years=30)
cash_flow = monthly_rent - monthly_payment - taxes - insurance - maintenance
```

#### 5.2 MLS Direct Integration
- [ ] **Replace HAR API** with official MLS feed
- [ ] **Access full historical data** (not just 30-day window)
- [ ] **Better data quality** (fewer data entry errors)

#### 5.3 Portfolio Tracking
- [ ] **Track owned properties**:
  - Purchase price, date, renovation budget
  - Current estimated value (from comps)
  - Rental income, expenses, cap rate
  - Compare to original underwriting

- [ ] **Performance dashboards**:
  - Year-to-date returns by property
  - Aggregate portfolio metrics
  - Cash flow waterfall

#### 5.4 Financing Integration
- [ ] **Partner with lenders** for:
  - Automated pre-qualification
  - Loan term quotes
  - Underwriting support

- [ ] **Include financing costs** in investment models:
  - Debt service coverage ratio (DSCR)
  - Cash-on-cash return (with financing)
  - Stress tests (rate shock, vacancy)

### Deliverables
- Expanded rental analysis
- Portfolio management dashboard
- MLS feed integration
- Financing integration layer

---

## Technology Stack

### Current (Phase 1)
- **Language**: Python 3.11+
- **Data**: pandas, numpy
- **CLI**: argparse

### Planned (Phase 2+)
- **Backend**: FastAPI, PostgreSQL, Redis (cache)
- **Frontend**: React, TypeScript, Tailwind CSS
- **Job Scheduler**: APScheduler or Airflow
- **Deployment**: Docker, Kubernetes, GitHub Actions
- **Analytics**: scikit-learn, statsmodels
- **Cloud**: AWS (EC2, RDS, S3) or similar

### Infrastructure (Monorepo)
```
house-hunter-monorepo/
├── services/
│   ├── data-pipeline/    (Python)
│   ├── api/              (FastAPI)
│   ├── web/              (React)
│   └── scheduler/        (APScheduler)
├── infra/                (Docker, K8s)
├── docs/
└── scripts/
```

---

## Timeline & Priorities

| Phase | Target | Priority | Effort | Impact |
|-------|--------|----------|--------|--------|
| **1. MVP CLI** | ✅ Done | High | 40 hrs | Enables core analysis |
| **2. Web API** | Q2 2026 | High | 120 hrs | 10x user base |
| **2. Dashboard** | Q3 2026 | High | 80 hrs | Visualization |
| **3. Scheduler** | Q4 2026 | Medium | 60 hrs | Recurring insights |
| **3. Alerts** | Q4 2026 | Medium | 40 hrs | Time-sensitive deals |
| **4. ML Models** | 2027 Q1 | Medium | 100 hrs | Advanced analytics |
| **5. Enterprise** | 2027 Q2+ | Low | TBD | Market expansion |

---

## Success Metrics

### Phase 1 (Current)
- [ ] 50+ HAR datasets processable without error
- [ ] Spread analysis matches manual comping (+/- 5%)
- [ ] CLI usable by non-technical users

### Phase 2
- [ ] API uptime > 99%
- [ ] Dashboard load time < 2 seconds
- [ ] 100 concurrent users supported

### Phase 3
- [ ] Daily jobs run without manual intervention
- [ ] Alert accuracy > 95% (deal actually exists in market)
- [ ] User engagement: 50% of users return weekly

### Phase 4
- [ ] ML model price predictions within 5% of actual
- [ ] Investment scoring correlates with real deal performance

### Phase 5
- [ ] 1000+ active users
- [ ] 10+ properties tracked in portfolio management
- [ ] Financing partnership approved by lenders

---

## Open Questions / Decisions Needed

1. **Data source priority**: Continue HAR API or move to MLS direct access immediately?
   - Pro HAR: Faster MVP, works now
   - Pro MLS: Better data quality, historical access
   - **Decision**: Phase 1 HAR, Phase 5 MLS

2. **Monorepo vs separate repos**: Split services or single repo?
   - Pro monorepo: Easier cross-service changes
   - Pro separate: Independent scaling, ownership
   - **Decision**: Single monorepo (easier for MVP, split later if needed)

3. **SaaS vs enterprise**: Target individuals or corporations?
   - Pro individual: Larger TAM, self-serve
   - Pro enterprise: Higher ACV, support revenue
   - **Decision**: Start individual, upsell to teams in Phase 3

4. **Free vs paid**: Free tier with limits or premium-only?
   - Pro freemium: User acquisition, network effects
   - Pro premium: Revenue from day 1
   - **Decision**: Freemium (3 ZIPs free, unlimited in paid)

---

## Blocked Issues / Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| HAR API rate limits | Medium | Cache aggressively; move to MLS |
| Data quality issues | Low | Validate against actual sales; user feedback |
| User adoption | Medium | Marketing; integration with popular tools |
| Financing regulations | High | Consult legal; partner with compliant lenders |
| Real estate market downturn | Low | Tool more valuable in soft markets (deals scarce) |

---

## Contributing to the Roadmap

Have ideas? See [CONTRIBUTING.md](../../CONTRIBUTING.md) for:
- How to propose new features
- How to vote on priorities
- How to contribute code

---

## Related Documents

- [ARCHITECTURE.md](../architecture/ARCHITECTURE.md) - Technical design
- [README.md](../../README.md) - Project overview
- [INSTALL.md](../guides/INSTALL.md) - Setup guide
