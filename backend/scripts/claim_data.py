"""Claim existing data for a specific Clerk user ID.

Run this ONCE after:
  1. Creating your Clerk account
  2. Logging in to the app once (so Clerk assigns you a user ID)
  3. Finding your Clerk user ID at https://dashboard.clerk.com → Users

Usage:
  cd backend
  .venv/Scripts/python.exe scripts/claim_data.py <your-clerk-user-id>

Example:
  .venv/Scripts/python.exe scripts/claim_data.py user_2abc123def456

This updates all 'user_default' rows in the database to belong to your
Clerk user ID, so your existing tickets, journal, accounts, etc. are
accessible after logging in.
"""
import sys
import asyncio
from sqlalchemy import text

# Ensure the backend package is on the path
sys.path.insert(0, ".")

from app.db.base import Base
from app.db.session import SessionLocal


TABLES = [
    "accounts",
    "tickets",
    "orders",
    "option_tickets",
    "streak_state",
    "audit_log",
    "screener_symbols",
]


async def claim(user_id: str) -> None:
    print(f"Claiming all 'user_default' data for user: {user_id}")
    async with SessionLocal() as session:
        for table in TABLES:
            result = await session.execute(
                text(f"UPDATE {table} SET user_id = :uid WHERE user_id = 'user_default'"),
                {"uid": user_id},
            )
            print(f"  {table}: {result.rowcount} rows updated")
        await session.commit()
    print("\nDone. All existing data now belongs to your account.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(claim(sys.argv[1]))
