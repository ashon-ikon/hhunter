# Monorepo Structure (Future: Hosted Service)

This document outlines how House Hunter will be restructured as a monorepo when transitioning to a hosted SaaS platform (Phase 2+).

## Current Structure (Phase 1: CLI Only)

```
house-hunter/
├── src/                    ← Single Python package
│   ├── __init__.py
│   ├── extract_har.py
│   ├── normalize_har.py
│   ├── analyze_spreads.py
│   └── fetch_searchlistings.py
├── data/                   ← User data (local)
│   ├── raw/
│   └── processed/
├── docs/                   ← Documentation
├── tests/                  ← Tests (coming)
├── pyproject.toml          ← Python package config
└── README.md
```

**Limitation**: Single CLI tool, no web UI, no API, no persistence

---

## Target Structure (Phase 2-3: Hosted Service)

When we add web UI, API, and scheduling, we'll restructure as a **monorepo**:

```
house-hunter-monorepo/
├── services/               ← Microservices
│   ├── data-pipeline/      ← Current Python CLI (moved here)
│   │   ├── src/
│   │   │   ├── extract_har.py
│   │   │   ├── normalize_har.py
│   │   │   ├── analyze_spreads.py
│   │   │   └── fetch_searchlistings.py
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── README.md       ← Data pipeline docs
│   │
│   ├── api/                ← FastAPI REST service (NEW)
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py     ← FastAPI app
│   │   │   ├── routes/     ← API endpoints
│   │   │   │   ├── __init__.py
│   │   │   │   ├── listings.py     # /api/listings/*
│   │   │   │   ├── analysis.py     # /api/analysis/*
│   │   │   │   └── accounts.py     # /api/accounts/* (Phase 3)
│   │   │   ├── models/     ← Pydantic schemas
│   │   │   │   ├── listing.py
│   │   │   │   ├── cohort.py
│   │   │   │   └── user.py
│   │   │   ├── services/   ← Business logic
│   │   │   │   ├── analysis_service.py
│   │   │   │   └── alert_service.py
│   │   │   └── db/         ← Database access
│   │   │       ├── models.py       # SQLAlchemy ORM
│   │   │       └── connection.py
│   │   ├── tests/
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── README.md       ← API docs
│   │
│   ├── web/                ← React frontend (NEW)
│   │   ├── src/
│   │   │   ├── components/
│   │   │   │   ├── Header.tsx
│   │   │   │   ├── Dashboard.tsx
│   │   │   │   ├── ScoreboardTable.tsx
│   │   │   │   └── PropertyDetail.tsx
│   │   │   ├── pages/
│   │   │   │   ├── Home.tsx
│   │   │   │   ├── Market.tsx
│   │   │   │   ├── Property.tsx
│   │   │   │   └── SavedSearches.tsx
│   │   │   ├── services/
│   │   │   │   └── api.ts         # API client (calls /api/*)
│   │   │   └── App.tsx
│   │   ├── tests/
│   │   ├── package.json
│   │   ├── Dockerfile
│   │   └── README.md       ← Frontend docs
│   │
│   └── scheduler/          ← Job scheduler (NEW - Phase 3)
│       ├── dags/           ← Airflow DAGs (if using Airflow)
│       │   ├── daily_snapshots.py
│       │   └── alerts.py
│       ├── jobs/           ← Or simple APScheduler jobs
│       │   ├── fetch_latest.py
│       │   └── send_alerts.py
│       ├── config.py
│       ├── requirements.txt
│       ├── Dockerfile
│       └── README.md
│
├── infra/                  ← Infrastructure as Code
│   ├── docker-compose.yml  ← Local development
│   ├── Dockerfile.base     ← Shared base image
│   ├── kubernetes/         ← K8s manifests (Phase 3+)
│   │   ├── data-pipeline-deployment.yaml
│   │   ├── api-deployment.yaml
│   │   ├── web-deployment.yaml
│   │   ├── postgres-statefulset.yaml
│   │   └── redis-deployment.yaml
│   ├── terraform/          ← Cloud IaC (AWS/GCP/Azure)
│   │   ├── main.tf
│   │   ├── vpc.tf
│   │   └── rds.tf
│   └── README.md           ← Deployment guide
│
├── .github/                ← GitHub-specific
│   ├── workflows/          ← CI/CD pipelines
│   │   ├── test.yml        ← Run tests on PR
│   │   ├── lint.yml        ← Code quality checks
│   │   ├── build.yml       ← Build Docker images
│   │   └── deploy.yml      ← Deploy to staging/prod
│   └── ISSUE_TEMPLATE/
│       ├── bug.md
│       └── feature.md
│
├── docs/                   ← Central documentation
│   ├── README.md           ← Main docs index
│   ├── ARCHITECTURE.md     ← System architecture
│   ├── DEPLOYMENT.md       ← How to deploy
│   ├── API.md              ← API reference
│   ├── guides/
│   │   ├── setup.md        ← Getting started
│   │   ├── contributing.md
│   │   └── faq.md
│   └── images/             ← Diagrams, screenshots
│
├── scripts/                ← Shared utilities
│   ├── setup.sh            ← Development setup
│   ├── test.sh             ← Run all tests
│   ├── lint.sh             ← Code quality
│   └── migrate-db.py       ← Database migrations
│
├── .docker/                ← Docker build configs
│   ├── nginx.conf          ← Reverse proxy config
│   └── Dockerfile.prod     ← Production image
│
├── .env.example            ← Environment variables template
├── .gitignore              ← Git ignore rules
├── docker-compose.yml      ← Main docker-compose
├── docker-compose.dev.yml  ← Development overrides
├── README.md               ← Project overview
├── CONTRIBUTING.md         ← Contribution guide
└── LICENSE
```

---

## Service Descriptions

### 1. Data Pipeline (`services/data-pipeline/`)

**Purpose**: Raw data capture, normalization, analysis

**Responsibilities**:
- Import HAR files / JSON
- Normalize to CSV
- Build cohorts
- Compute spreads
- Generate reports

**Can run as**:
- CLI tool (current use)
- Cron job (scheduled snapshots)
- API service (async processing)

**Technology**:
- Python 3.11+
- pandas, numpy
- Current codebase

---

### 2. API Service (`services/api/`)

**Purpose**: REST API for data access and analysis

**Endpoints** (examples):
```
POST   /api/import              # Upload HAR/JSON
GET    /api/listings/{zip}      # Get active listings
GET    /api/comps/{mlsnum}      # Get cohort for property
GET    /api/scoreboard          # Market metrics by ZIP
GET    /api/opportunities       # Ranked deals
GET    /api/health              # Service status
POST   /api/saved-searches      # Save search criteria (Phase 3)
GET    /api/alerts              # Get user alerts (Phase 3)
```

**Technology**:
- FastAPI (async Python)
- SQLAlchemy ORM
- PostgreSQL/SQLite
- Pydantic validation

**Deployment**:
- Docker container
- Uvicorn ASGI server
- Behind nginx reverse proxy

---

### 3. Web Frontend (`services/web/`)

**Purpose**: Interactive dashboard and UI

**Pages** (examples):
- Home → Search form
- Dashboard → Market overview
- Market → Scoreboard (ZIP metrics)
- Property → Detail view with comps
- Opportunities → Deal ranking table
- Saved Searches → User's criteria (Phase 3)

**Technology**:
- React 18+
- TypeScript
- Tailwind CSS
- Axios (API client)

**Deployment**:
- Static SPA (Single Page App)
- Served by nginx
- Environment-specific API base URL

---

### 4. Job Scheduler (`services/scheduler/`)

**Purpose**: Automated recurring tasks (Phase 3+)

**Example jobs**:
- Daily snapshots (fetch HAR data for watched ZIPs)
- Analysis caching (pre-compute cohorts)
- Alert generation (detect new deals)
- Notifications (email/Slack)

**Technology**:
- APScheduler (simple) or Airflow (enterprise)
- Python
- Cron scheduling

**Deployment**:
- Docker container
- Runs on schedule
- Logs to central system

---

## Development Workflow

### Local Development (docker-compose)

Everyone runs locally before pushing:

```bash
# 1. Clone monorepo
git clone https://github.com/your-org/house-hunter-monorepo.git
cd house-hunter-monorepo

# 2. Start local stack
docker-compose up

# Services available:
# - API: http://localhost:8000
# - Web: http://localhost:3000
# - Database: localhost:5432
# - Redis cache: localhost:6379
```

**docker-compose.yml**:
```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_PASSWORD: dev
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports:
      - "6379:6379"

  data-pipeline:
    build: ./services/data-pipeline
    environment:
      DATA_DIR: /app/data
    volumes:
      - ./data:/app/data

  api:
    build: ./services/api
    environment:
      DATABASE_URL: postgresql://postgres:dev@postgres:5432/house_hunter
      REDIS_URL: redis://redis:6379
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
      - data-pipeline

  web:
    build: ./services/web
    environment:
      REACT_APP_API_URL: http://localhost:8000/api
    ports:
      - "3000:3000"
    depends_on:
      - api
```

### Testing

```bash
# Test individual service
cd services/api
pytest tests/

# Or test all services
./scripts/test.sh
```

### Deployment

```bash
# 1. Push to main
git push origin feature/xyz

# 2. GitHub Actions CI runs
# - Linting
# - Tests
# - Build Docker images
# - Push to registry

# 3. Deploy to staging
# - K8s applies new manifests
# - Smoke tests run
# - Ready for QA

# 4. Promote to production
# - Manual approval
# - K8s rolling update
# - Zero downtime
```

---

## Database Schema (Planned)

**Core tables**:

```sql
-- Imported listings
listings (
  id, mlsnum, zip, address, proptype, beds, baths, sqft, year,
  list_price, ppsf, dom, status, updated_at
)

-- Cached analyses
cohort_cache (
  subject_mlsnum, cohort_json, spread, computed_at, ttl
)

-- User accounts (Phase 3)
users (
  id, email, password_hash, created_at
)

-- Saved searches (Phase 3)
saved_searches (
  id, user_id, name, filters_json, created_at
)

-- Alerts (Phase 3)
alerts (
  id, user_id, search_id, deal_mlsnum, message, sent_at
)

-- Historical snapshots
snapshots (
  id, zip, source, count_active, count_sold, created_at
)
```

---

## Environment Configuration

**`.env.example`** (template):
```
# API
API_HOST=0.0.0.0
API_PORT=8000
API_DEBUG=false

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/house_hunter
SQLALCHEMY_ECHO=false

# Redis
REDIS_URL=redis://localhost:6379/0

# HAR.com (if using direct API)
HAR_USERNAME=your_username
HAR_PASSWORD=your_password

# Email (alerts)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=alerts@example.com
SMTP_PASSWORD=your_app_password

# AWS/Cloud (for file storage in Phase 3+)
AWS_REGION=us-east-1
S3_BUCKET=house-hunter-data
```

---

## Migration Path: CLI → Monorepo

### Step 1: Extract data pipeline (Phase 2 start)
- Move `src/` → `services/data-pipeline/src/`
- Keep CLI working
- Add tests

### Step 2: Build API (Phase 2 mid)
- Create `services/api/`
- API wraps data pipeline
- Both CLI and API access same normalized data

### Step 3: Build web (Phase 2 end)
- Create `services/web/`
- React frontend calls API
- Users can access via web instead of CLI

### Step 4: Add persistence (Phase 3 start)
- Add PostgreSQL
- Cache cohort results
- Track user preferences

### Step 5: Add scheduler (Phase 3 mid)
- Create `services/scheduler/`
- Automate daily snapshots
- Send alerts to users

### Step 6: Scale & optimize (Phase 3+)
- Add Kubernetes
- Add monitoring/logging
- Multi-region deployment

---

## Key Design Decisions

### ✅ Monorepo Benefits

1. **Easier cross-service changes** - Single PR can update API + web
2. **Shared CI/CD** - One test suite, one deploy pipeline
3. **Easier local dev** - `docker-compose up` for full stack
4. **Clear dependencies** - All code in one place
5. **Easier team coordination** - Less repo navigation

### ⚠️ Monorepo Tradeoffs

- Larger repo (but manageable at this scale)
- Can't scale services independently at start
- Need discipline to avoid tight coupling

**Mitigation**: Use clear service boundaries; avoid shared code

---

## See Also

- [ROADMAP.md](../roadmap/ROADMAP.md) - Implementation phases
- [ARCHITECTURE.md](./ARCHITECTURE.md) - Current technical design
- [DEPLOYMENT.md](../deployment/DEPLOYMENT.md) - Deployment guide (coming)
