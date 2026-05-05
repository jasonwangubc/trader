"""Notifications — email (SMTP) and Telegram. Both silently skip if not configured."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from decimal import Decimal
from email.mime.text import MIMEText

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


def _send(subject: str, body: str) -> None:
    s = get_settings()
    if not all([s.smtp_host, s.smtp_username, s.smtp_password, s.smtp_from]):
        return  # not configured — skip silently

    to_addr = getattr(s, "alert_email", None) or s.smtp_from
    msg = MIMEText(body, "plain")
    msg["Subject"] = f"[trader] {subject}"
    msg["From"] = s.smtp_from
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(s.smtp_username, s.smtp_password)
            smtp.sendmail(s.smtp_from, [to_addr], msg.as_string())
        log.info("Alert email sent: %s", subject)
    except Exception:
        log.exception("Failed to send alert email: %s", subject)


def _telegram(message: str) -> None:
    """Fire-and-forget Telegram message. Skipped if bot token/chat not configured."""
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    try:
        import requests as _req  # stdlib fallback via httpx
    except ImportError:
        _req = None

    async def _post():
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json={"chat_id": s.telegram_chat_id, "text": message, "parse_mode": "Markdown"})
        except Exception as exc:
            log.warning("Telegram send failed: %s", exc)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_post())
        else:
            loop.run_until_complete(_post())
    except Exception as exc:
        log.warning("Telegram dispatch failed: %s", exc)


def alert_triggered(symbol: str, trigger_price: Decimal, last_price: Decimal,
                    is_paper: bool, shares: int) -> None:
    label = "PAPER" if is_paper else "LIVE"
    body = (
        f"*[{label}] {symbol} TRIGGERED*\n"
        f"Trigger: {trigger_price}  |  Last: {last_price}  |  Shares: {shares}\n"
        "Entry buy placed. Stop will arm on fill."
    )
    _send(f"[{label}] {symbol} TRIGGERED @ {last_price}", body.replace("*", "").replace("_", ""))
    _telegram(body)


def alert_filled(symbol: str, fill_price: Decimal, stop_price: Decimal,
                 shares: int, is_paper: bool) -> None:
    label = "PAPER" if is_paper else "LIVE"
    body = (
        f"*[{label}] {symbol} FILLED*\n"
        f"Fill: {fill_price}  |  Stop armed: {stop_price}  |  Shares: {shares}\n"
        "GTC stop-loss is live. Do NOT move the stop."
    )
    _send(f"[{label}] {symbol} FILLED @ {fill_price}", body.replace("*", "").replace("_", ""))
    _telegram(body)


def alert_stopped_out(symbol: str, stop_price: Decimal, is_paper: bool) -> None:
    label = "PAPER" if is_paper else "LIVE"
    body = f"*[{label}] {symbol} STOPPED OUT* @ {stop_price}\nReview the trade before opening new risk."
    _send(f"[{label}] {symbol} STOPPED OUT", body.replace("*", ""))
    _telegram(body)


def alert_target_hit(symbol: str, target_label: str, target_price: Decimal, is_paper: bool) -> None:
    label = "PAPER" if is_paper else "LIVE"
    body = f"*[{label}] {symbol} TARGET HIT* — {target_label} @ {target_price}\nConsider scaling out."
    _send(f"[{label}] {symbol} TARGET: {target_label}", body.replace("*", ""))
    _telegram(body)
