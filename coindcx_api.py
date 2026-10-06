"""
CoinDCX data layer for trading_bot.py (READ-ONLY: no order placement).

Endpoints used (from docs.coindcx.com):
  - GET  https://api.coindcx.com/market_data/candles?pair=B-SOL_USDT&interval=15m&limit=96
  - POST https://api.coindcx.com/exchange/v1/users/balances   (signed, optional)

.env (optional, only for live balance):
    COINDCX_API_KEY=...
    COINDCX_SECRET=...
    CAPITAL_INR=5000        # if set, this is used instead of the balance API
"""
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.coindcx.com"
CANDLE_INTERVAL = "15m"
CANDLES_PER_HOUR = 4  # for 15m candles
TIMEOUT = 10


@dataclass
class PriceCandle:
    timestamp: datetime
    price: float  # candle close


def _to_pair(symbol: str) -> str:
    """'SOLUSDT' -> 'B-SOL_USDT' (CoinDCX pair format)."""
    if symbol.endswith("USDT"):
        return f"B-{symbol[:-4]}_USDT"
    raise ValueError(f"Unsupported symbol: {symbol}")


def _get_candles(pair: str, interval: str, limit: int) -> list:
    r = requests.get(
        f"{BASE_URL}/market_data/candles",
        params={"pair": pair, "interval": interval, "limit": limit},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Unexpected candle response for {pair}: {str(data)[:200]}")
    # API returns newest first; sort oldest -> newest
    return sorted(data, key=lambda c: c["time"])


def fetch_current_price(symbol: str) -> float:
    """Latest 1-minute close for the symbol, e.g. 'SOLUSDT'."""
    candles = _get_candles(_to_pair(symbol), "1m", 1)
    return float(candles[-1]["close"])


def fetch_price_history(symbol: str, hours: int) -> list:
    """List of PriceCandle (oldest -> newest) covering the lookback window."""
    limit = min(hours * CANDLES_PER_HOUR, 1000)
    candles = _get_candles(_to_pair(symbol), CANDLE_INTERVAL, limit)
    return [
        PriceCandle(
            timestamp=datetime.fromtimestamp(c["time"] / 1000),
            price=float(c["close"]),
        )
        for c in candles
    ]


def _signed_post(path: str, body: dict) -> object:
    key = os.getenv("COINDCX_API_KEY")
    secret = os.getenv("COINDCX_SECRET")
    if not key or not secret:
        raise RuntimeError("COINDCX_API_KEY / COINDCX_SECRET missing in .env")
    body = {**body, "timestamp": int(round(time.time() * 1000))}
    payload = json.dumps(body, separators=(",", ":"))
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    r = requests.post(
        f"{BASE_URL}{path}",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": key,
            "X-AUTH-SIGNATURE": signature,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_account_capital_inr(usd_inr_rate: float = 95.95) -> float:
    """
    Capital in INR.
    1) If CAPITAL_INR is set in .env, use it (simplest, recommended to start).
    2) Otherwise read SPOT wallet balances: INR + USDT * usd_inr_rate.
       NOTE: this does not include money already moved to the futures wallet.
    """
    manual = os.getenv("CAPITAL_INR")
    if manual:
        return float(manual)

    balances = _signed_post("/exchange/v1/users/balances", {})
    total = 0.0
    for b in balances:
        if b.get("currency") == "INR":
            total += float(b.get("balance", 0))
        elif b.get("currency") == "USDT":
            total += float(b.get("balance", 0)) * usd_inr_rate
    if total <= 0:
        raise RuntimeError("Balance came back as 0. Set CAPITAL_INR in .env instead.")
    return total
