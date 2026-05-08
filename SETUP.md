# Trader — Setup guide

Personal trading discipline tool (Minervini SEPA methodology).

## Prerequisites

- Python 3.10+
- Node.js 20+
- Docker Desktop (for Postgres)
- Questrade account with API access

## First-time setup

```bash
# 1. Clone
git clone <repo-url>
cd trader

# 2. Backend Python environment
cd backend
python -m venv .venv
.venv\Scripts\pip install -e ".[data]"

# 3. Frontend
cd ../frontend
npm install

# 4. Environment files
cd ../backend
copy .env.example .env
# Edit .env — fill in:
#   QUESTRADE_REFRESH_TOKEN  (from Questrade: Login > MyApps > Generate token)
#   EDGAR_USER_AGENT         (your email: "trader-screener/1.0 you@example.com")
#   DATABASE_URL             (update port if needed — default 5433)

cd ../frontend
copy .env.example .env.local   (Linux: cp)
# Edit .env.local:
#   NEXT_PUBLIC_API_URL=http://localhost:8002

# 5. Database
docker compose up -d postgres

# 6. Migrations
cd backend
.venv\Scripts\python.exe -m alembic upgrade head

# 7. Start services (two terminals)
# Terminal 1:
cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --port 8002

# Terminal 2:
cd frontend && npm run dev
```

Open http://localhost:3000

## First run

1. **Accounts → Sync now** — pulls your Questrade balances
2. **Screener → Run scan** — downloads 1,500+ stock universe + scores (takes ~5 min first time)
3. **Watchlist** — review top candidates from screener
4. **/chart/{symbol}** — charts with stop/target recommendations
5. **New ticket** — arm a ticket with pre-filled trigger + stop

## Auto-start (Windows)

Run `install-service.ps1` as Administrator **once**. The backend will start automatically at every login.

Or disable sleep and keep a terminal open with `run-backend.bat`.

## Questrade token

Tokens expire every 30 days if not used. The app auto-rotates tokens on each use (stored in DB).

If you get a 401 error:
1. Go to https://login.questrade.com/APIAccess/UserApps.aspx
2. Generate new token → paste into `backend/.env` as `QUESTRADE_REFRESH_TOKEN`
3. Delete stale token from DB: `docker exec trader-postgres psql -U trader -d trader -c "DELETE FROM settings WHERE key='questrade_refresh_token';"`
4. Restart backend

## Ports

| Service  | Port | Notes |
|----------|------|-------|
| Backend  | 8002 | uvicorn |
| Frontend | 3000 | Next.js dev |
| Postgres | 5433 | Docker (avoids conflict with any local Postgres on 5432) |
