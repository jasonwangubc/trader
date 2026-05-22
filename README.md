# trader

Personal trading discipline tool — a behavioral-enforcement layer over Questrade.
Pre-trade tickets · breakout monitor · auto stops · trailing exits · S&P/TSX/NASDAQ screener · journal · covered-call / cash-secured-put income · multi-user (Clerk).

Working name. Single-binary deployment for now; multi-tenant SaaS path laid out in [SAAS_ROADMAP.md](SAAS_ROADMAP.md).

## What it does

- **Pre-trade tickets** — commit setup, trigger, stop, target, and sized position size *before* execution. Status: draft → armed → triggered → filled → closed.
- **Breakout monitor** — armed tickets watch live quotes; auto-submits entry + GTC stop on trigger.
- **Trailing coach** — milestone-driven exits (+1R/+5R, exit ladder legs) surface as one-click confirmable actions.
- **Screener** — ~7,500 US + Canadian stocks scored on Trend Template, lenient VCP, IBD-style EPS/SMR rank, and pattern buyability. EOD pipeline runs nightly.
- **Options income** — log covered calls and cash-secured puts; track premium, break-even, P/L. Wheel-strategy candidate screener with correlation + sector concentration checks (see [docs/WHEEL.md](docs/WHEEL.md)).
- **Behavioral guardrails** — streak-scaled position sizing, regime gate (SPY vs 200MA), kill switch, audit log of every state change.
- **Per-account paper mode** — real-money execution requires explicit per-account opt-in.

## Stack

- **Backend:** Python 3.10+, FastAPI, SQLAlchemy 2.0 (async), Alembic, asyncpg
- **Frontend:** Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui
- **Auth:** Clerk (multi-user, per-user Questrade tokens)
- **Database:** Postgres 16 (Docker)
- **Broker:** Questrade (REST + WebSocket) — broker layer abstracted for portability
- **Data:** yfinance (EOD + options chains), SEC EDGAR (fundamentals + universe), Wikipedia (curated indices)
- **Notifications:** email + Telegram (optional)

## Layout

```
trader/
├── backend/             FastAPI app, broker layer, monitor, screener, services
│   ├── app/api/         HTTP routes (accounts, tickets, screener, options, wheel, ...)
│   ├── app/services/    Domain logic (sizing, screener, EOD, wheel, correlation, ...)
│   ├── app/db/          ORM models + session
│   ├── alembic/         Migrations
│   └── scripts/         Maintenance scripts (claim_data.py, etc.)
├── frontend/            Next.js 15 dashboard
│   ├── app/             App-Router routes (dashboard, screener, tickets, wheel, ...)
│   ├── components/      Reusable UI
│   └── lib/             API client, typed shapes
├── docker-compose.yml   Postgres only
├── install-service.ps1  Auto-start backend on Windows login
├── SAAS_ROADMAP.md      Path from personal tool → SaaS
├── SETUP.md             Full setup walkthrough
└── README.md            (this file)
```

## Quickstart

See [SETUP.md](SETUP.md) for the full walkthrough (Clerk, env files, LAN access, auto-start).

Short version:

```bash
# 1. Postgres
docker compose up -d postgres

# 2. Backend
cd backend
python -m venv .venv
.venv\Scripts\pip install -e ".[data]"        # Windows
# .venv/bin/pip install -e ".[data]"           # Mac/Linux
cp .env.example .env                          # then edit EDGAR_USER_AGENT
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8002

# 3. Frontend (separate terminal)
cd frontend
npm install
cp .env.example .env.local                    # then add Clerk keys
npm run dev
```

Open http://localhost:3000, sign up via Clerk, paste a Questrade token in Settings, then **Screener → Run scan**.

## Ports

| Service  | Port | Binding     | Notes |
|----------|------|-------------|-------|
| Frontend | 3000 | `0.0.0.0`   | Accessible on LAN |
| Backend  | 8002 | `127.0.0.1` | Proxied by Next.js |
| Postgres | 5433 | `127.0.0.1` | Avoids conflict with native Postgres 5432 |

## Safety

- **Paper mode by default.** Live execution requires per-account opt-in in Settings.
- **Kill switch** disables all auto-execution from the sidebar.
- **Audit log** records every ticket, order, fill, and state change.
- **Refusal to manage unknown positions** — the app won't auto-stop a position that wasn't opened through a ticket (unless retroactively assigned).
- **Multi-tenant isolation** — every query is scoped to the Clerk `user_id`; per-user Questrade tokens.

## Where things live

| Concern | Code |
|---------|------|
| Position sizing | [backend/app/services/sizing_service.py](backend/app/services/sizing_service.py) |
| Breakout monitor | [backend/app/services/monitor_service.py](backend/app/services/monitor_service.py) |
| Pattern + VCP scoring | [backend/app/services/pattern_service.py](backend/app/services/pattern_service.py), [vcp_scorer.py](backend/app/services/vcp_scorer.py) |
| Screener nightly | [backend/app/services/nightly_service.py](backend/app/services/nightly_service.py) |
| Trailing/exit coach | [backend/app/services/coach_service.py](backend/app/services/coach_service.py), [trailing_service.py](backend/app/services/trailing_service.py) |
| Wheel-strategy candidates | [backend/app/services/wheel_service.py](backend/app/services/wheel_service.py) |
| Options chains | [backend/app/services/options_chain_service.py](backend/app/services/options_chain_service.py) |
| Correlation analyzer | [backend/app/services/correlation_service.py](backend/app/services/correlation_service.py) |

## License

Personal use. Not financial advice. Backtest, paper trade, and audit before live use.
