# trader — Setup guide

Minervini SEPA methodology trading discipline tool.
Pre-trade tickets · breakout monitor · auto stops · S&P 500/400/600 screener · Questrade integration.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.10+ | [python.org](https://python.org) |
| Node.js | 20+ | [nodejs.org](https://nodejs.org) |
| Docker Desktop | latest | Runs Postgres |
| Git | any | |
| Questrade account | — | API access required |
| Clerk account (free) | — | Login/auth — [clerk.com](https://clerk.com) |

---

## First-time setup

### 1. Clone

```bash
git clone https://github.com/jasonwangubc/trader.git
cd trader
```

### 2. Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\pip install -e ".[data]"

# Mac/Linux
.venv/bin/pip install -e ".[data]"
```

### 3. Frontend

```bash
cd frontend
npm install
```

### 4. Environment files

**Backend** — copy `backend/.env.example` → `backend/.env` and fill in:

```env
DATABASE_URL=postgresql+asyncpg://trader:trader@localhost:5433/trader
DATABASE_URL_SYNC=postgresql+psycopg2://trader:trader@localhost:5433/trader
EDGAR_USER_AGENT=trader-screener/1.0 your@email.com
# Questrade token is set via the app Settings page after you log in.
# You can leave QUESTRADE_REFRESH_TOKEN empty here.
```

**Frontend** — copy `frontend/.env.example` → `frontend/.env.local` and fill in:

```env
# URL of the FastAPI backend (same machine as the frontend)
BACKEND_URL=http://localhost:8002

# Your Clerk keys from https://dashboard.clerk.com
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...

# Clerk route config (leave these as-is)
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL=/
NEXT_PUBLIC_CLERK_AFTER_SIGN_UP_URL=/
```

### 5. Database

```bash
docker compose up -d postgres
```

### 6. Migrations

```bash
cd backend
.venv\Scripts\python.exe -m alembic upgrade head   # Windows
# .venv/bin/python -m alembic upgrade head          # Mac/Linux
```

### 7. Start

Open two terminals:

```bash
# Terminal 1 — backend
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8002

# Terminal 2 — frontend
cd frontend
npm run dev
```

Open http://localhost:3000

---

## First run (new account)

1. **Sign up** at http://localhost:3000/sign-up
2. **Settings** → paste your Questrade token → Connect
3. **Accounts → Sync now** → loads balances
4. **Screener → Run scan** → downloads 1,500+ stocks (takes 5-10 min)
5. **Watchlist** → charts of top-scored setups
6. **New ticket** → arm a breakout with pre-filled trigger + stop

### Claim existing data (after adding Clerk to an existing install)

If you had data before setting up Clerk:

```bash
cd backend
.venv\Scripts\python.exe scripts\claim_data.py user_2...   # your Clerk user ID from dashboard.clerk.com
```

---

## Accessing from another device on the same network

Run the app on one machine (laptop/desktop) and access it from any other device on the same Wi-Fi/LAN.

### Step 1 — Find the host machine's LAN IP

On the machine running the app:
```
# Windows
ipconfig
# Look for "IPv4 Address" under your Wi-Fi adapter: e.g. 192.168.1.94

# Mac/Linux
ip addr   # or ifconfig
```

For stability, assign a **static IP or DHCP reservation** for this machine in your router settings so the IP doesn't change.

### Step 2 — Allow port 3000 through Windows Firewall

Run this **once** as Administrator in PowerShell:

```powershell
New-NetFirewallRule -DisplayName "trader frontend" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
```

The backend (port 8002) stays on localhost — only the frontend needs to be reachable.

### Step 3 — Update Clerk allowed origins

1. Go to [dashboard.clerk.com](https://dashboard.clerk.com)
2. Your app → **Configure** → **Domains** → add `http://192.168.1.94:3000`
   (replace with your actual LAN IP)

### Step 4 — Update frontend .env.local

Add/update these lines in `frontend/.env.local` on the host machine:

```env
# Tell Next.js its own public URL (needed for Clerk redirects on LAN)
NEXTAUTH_URL=http://192.168.1.94:3000
```

Restart the frontend after editing.

### Step 5 — Access from other devices

Open `http://192.168.1.94:3000` (replace with your LAN IP) in any browser on the same network.

> **Tip:** Use your computer's **hostname** instead of the IP if you want something easier to remember.
> On Windows: `http://DESKTOP-XYZ:3000` works on most home networks.

---

## Auto-start on Windows (keep it running)

Run `install-service.ps1` as **Administrator** once — registers the backend as a Task Scheduler job that starts at every login and restarts on crash:

```powershell
# Right-click PowerShell → Run as Administrator
cd C:\path\to\trader
.\install-service.ps1
```

For the frontend, either:
- Keep a terminal open with `npm run dev`
- Or use PM2: `npm install -g pm2 && pm2 start "npm run dev" --name trader-fe`

---

## Questrade token management

Tokens are stored **per user** in the database and rotate automatically on each API call.

**If you get a 401 / connection error:**
1. Go to https://login.questrade.com/APIAccess/UserApps.aspx
2. Generate a new token
3. In the app: **Settings** → paste new token → Connect

---

## Ports

| Service  | Port | Binding | Notes |
|----------|------|---------|-------|
| Frontend | 3000 | `0.0.0.0` | Accessible on LAN by default |
| Backend  | 8002 | `127.0.0.1` | localhost only — proxied by Next.js |
| Postgres | 5433 | `127.0.0.1` | Docker — avoids conflict with native Postgres on 5432 |

---

## Daily startup (after auto-start is configured)

The backend starts automatically at login. For the frontend:

```bash
cd frontend && npm run dev
```

Or just open http://localhost:3000 — if the backend isn't running, the health check on the dashboard will show red.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Blank page / 500 error | Check backend is running: `curl http://localhost:8002/health` |
| "Questrade not connected" | Settings → paste fresh token |
| Auth loop / can't log in | Check Clerk keys in `.env.local`, verify domain in Clerk dashboard |
| Stale price data | Screener → Run scan (auto-runs at 5:30 PM ET weekdays) |
| Port 8002 already in use | Kill old process: PowerShell → `Stop-Process -Id (Get-NetTCPConnection -LocalPort 8002).OwningProcess -Force` |
| Docker not found | Start Docker Desktop first |
