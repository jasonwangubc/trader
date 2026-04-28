# trader

Personal trading discipline tool. Behavioral-enforcement layer over Questrade. Breakout monitor + pre-trade tickets + auto-stops + screener + journal.

Working name; rename for commercialization later.

## Stack

- **Backend:** Python 3.10+, FastAPI, SQLAlchemy 2.0 (async), Alembic, asyncpg
- **Frontend:** Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui
- **Database:** Postgres 16 (via Docker)
- **Broker:** Questrade (REST + WebSocket); broker layer abstracted for portability
- **Data:** yfinance for nightly screener; Questrade WS for intraday quotes on watchlist
- **Notifications:** email + Telegram

## Layout

```
trader/
├── backend/            FastAPI app, broker layer, monitor, screener, services
├── frontend/           Next.js dashboard
├── docker-compose.yml  Postgres only
└── README.md
```

## Setup (development)

Prereqs: Python 3.10+, Node 20+, Docker Desktop, git.

### 1. Start Postgres

```bash
docker compose up -d postgres
```

Postgres is exposed on host port **5433** (container port 5432). We use 5433 to avoid colliding with a system-native Postgres install that may already be on 5432.

### 2. Backend

```bash
cd backend
python -m venv .venv
. .venv/Scripts/activate    # Windows bash
# or: .venv\Scripts\activate.bat   (cmd)
# or: . .venv/bin/activate         (Linux/macOS)
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Backend is at http://localhost:8000 ; OpenAPI docs at http://localhost:8000/docs.

### 3. Frontend

(Coming in next sprint scaffold step.)

## Sprint plan

- **Sprint 1 (current):** scaffolding, schema, Questrade OAuth, accounts/positions display, pre-trade ticket end-to-end, audit log, paper-mode toggle.
- **Sprint 2:** WebSocket quote streamer, breakout monitor, auto sell-stop on fill, manual-trade detector, email + Telegram alerts, kill switch.
- **Sprint 3:** yfinance EOD pipeline, scored Trend Template, lenient VCP scorer, free-API fundamentals, screener UI, trade journal.
- **Phases 4+:** market regime gate, behavioral guardrails, exit ladder, options module, performance analytics, daily routine + streaks.

## Safety

- Paper mode is the default. Real-money execution requires explicit per-account opt-in in settings.
- Kill switch disables all auto-execution from the dashboard.
- Every order, ticket, and state change is recorded in the audit log.
- The app refuses to manage positions that weren't opened through it unless retroactively assigned a ticket.
