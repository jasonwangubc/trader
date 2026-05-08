# SaaS Roadmap

This document outlines what needs to change to go from a personal single-user tool
to a multi-tenant product that other traders can subscribe to.

## Current architecture (personal use)

- Single Questrade account, credentials in `.env`
- No authentication — anyone with the URL has full access
- All data in one Postgres schema, no user isolation
- Runs locally on one machine

## Phase 1 — Authentication + multi-user data isolation

**Effort: ~2-3 weeks**

### 1a. Add authentication

**Recommended: [Clerk](https://clerk.com)** — fastest path, handles OAuth, MFA, billing integration.
Alternative: NextAuth (self-hosted, free, more config).

```bash
npm install @clerk/nextjs
```

Wrap the root layout with `<ClerkProvider>`, add middleware to protect routes.
Each user gets a `clerk_user_id` string.

### 1b. Add user_id to all DB tables

Every table that stores user data needs `user_id: str` (FK to a `users` table):

```python
# Add to these models:
Account, Ticket, Order, Fill, Position, StreakState,
ScreenerSymbol, ScreenerScore, EarningsDate, OptionTicket
```

Migration: `alembic revision --autogenerate -m "add_user_id"`

### 1c. Row-level isolation in all queries

Every DB query must add `.where(Model.user_id == current_user_id)`.
Add a FastAPI dependency `get_current_user()` that reads from the JWT token.

### 1d. Per-user Questrade credentials

The settings table already stores `questrade_refresh_token`.
Change the key to `{user_id}:questrade_refresh_token`.
Each user generates their own Questrade token via the OAuth flow in the app.

---

## Phase 2 — Questrade OAuth flow in-app

**Effort: ~1 week**

Currently the user pastes a token into `.env`. For a product, users need a click-through OAuth:

1. User clicks "Connect Questrade" in Settings
2. App redirects to `https://login.questrade.com/oauth2/authorize?...`
3. Questrade redirects back to `/api/auth/questrade/callback?code=...`
4. App exchanges code for tokens, stores in DB per user
5. Done — no manual token management

Questrade's OAuth 2.0 supports this flow. You need to register a "partner application"
with Questrade to get a `client_id` and `client_secret`.

---

## Phase 3 — Cloud deployment

**Effort: ~1-2 days for basic, ~1 week for production-grade**

### Recommended stack

| Component | Service | Cost |
|-----------|---------|------|
| Backend (FastAPI) | [Railway](https://railway.app) or [Fly.io](https://fly.io) | $5-20/month |
| Database (Postgres) | Railway managed Postgres | $5/month |
| Frontend (Next.js) | [Vercel](https://vercel.com) | Free / $20/month |
| File storage | Not needed (all in Postgres) | — |

### Railway deployment (backend)

```bash
# Install Railway CLI
npm install -g @railway/cli

railway login
railway init
railway add --database postgresql
railway up
```

Set env vars in Railway dashboard (same as .env but for production).

### Vercel deployment (frontend)

```bash
npm install -g vercel
vercel --prod
```

Set `NEXT_PUBLIC_API_URL` to your Railway backend URL.

### Key production config changes

```env
APP_ENV=production
SECRET_KEY=<strong-random-key>  # openssl rand -hex 32
PAPER_MODE_DEFAULT=true          # keep default paper for safety
DATABASE_URL=<railway-postgres-url>
```

---

## Phase 4 — Billing

**Recommended: [Stripe](https://stripe.com)**

Simple model:
- Free tier: screener read-only, no live trading
- Pro tier ($29/month): full access, live Questrade integration
- Use Stripe Billing + Clerk's billing integration

---

## Phase 5 — Legal / compliance

- **Terms of Service** and **Privacy Policy** — required before launch
- **Not financial advice disclaimer** — prominent on every page
- **Questrade Partner Program** — register to get official API access for production
  (avoid rate limits, get higher quotas)
- **Canadian securities regulations** — consult a lawyer if offering trade signals
  (the app places user-controlled orders, which is different from automated advisory)

---

## Competitive positioning

**Existing tools:**
- MarketSmith ($180/month) — great data, no automation, no discipline enforcement
- TradingView ($15-60/month) — charting, no brokerage integration for Canadians
- Wealthsimple Trade — no screening, no discipline tools

**Differentiators of this app:**
1. **Pre-trade ticket discipline** — forces commitment before entry, unique
2. **Behavioral guardrails** — loss streak block, revenge-trade cooldown, unique
3. **Questrade-native** — Canadian market, TFSA/RRSP awareness, rare
4. **Anti-martingale sizing** — streak-scaled position sizing, unique
5. **Screener → chart → ticket** — seamless workflow, 3 clicks from setup to armed

**Target market:** Canadian growth stock traders using Questrade, ~100k-1M TAM.

---

## Quick wins before launch

- [ ] Add Clerk auth (1 day)
- [ ] Questrade OAuth flow (3 days)
- [ ] Deploy to Railway + Vercel (1 day)
- [ ] Landing page explaining the behavioral enforcement angle
- [ ] Stripe billing ($0 free / $29 pro)
- [ ] Legal pages (ToS, Privacy, Disclaimer)

**Estimated time to launch: 4-6 weeks part-time.**
