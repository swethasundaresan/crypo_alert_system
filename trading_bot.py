"""
Personal Crypto Trading Assistant - Core Logic Engine
CoinDCX SOL/USDT & BTC/USDT - Range Detection & Signal Alerts

IMPORTANT: This is ALERT-ONLY logic. No order execution included.
Verify all API endpoints against current CoinDCX developer docs before use.

Telegram alerts: requires telegram_bot.py and .env in the same folder.
"""

import time
from dataclasses import dataclass
from datetime import datetime

from coindcx_api import (
    fetch_account_capital_inr as coindcx_fetch_account_capital_inr,
    fetch_current_price as coindcx_fetch_current_price,
    fetch_price_history as coindcx_fetch_price_history,
)
from telegram_bot import send_telegram_message


def get_market_status() -> dict:
    """Return the latest 24h range snapshot and signal state."""
    sol_price = fetch_current_price("SOLUSDT")
    btc_price = fetch_current_price("BTCUSDT")
    sol_history = fetch_price_history("SOLUSDT", CONFIG["lookback_hours"])
    btc_history = fetch_price_history("BTCUSDT", CONFIG["lookback_hours"])
    sol_range = compute_range(sol_history)
    btc_range = compute_range(btc_history)
    signal = detect_signal(sol_range, sol_price, btc_range, btc_price)
    return {
        "sol_price": sol_price,
        "btc_price": btc_price,
        "sol_range": sol_range,
        "btc_range": btc_range,
        "signal": signal,
        "capital_inr": fetch_account_capital_inr(),
    }


# ═══════════════════════════════════════════════════════
# 1. CONFIGURATION
# ═══════════════════════════════════════════════════════

CONFIG = {
    "lookback_hours": 24,  # rolling window for range detection
    "poll_interval_seconds": 30,
    "alert_cooldown_seconds": 1800,  # don't repeat the same alert within 30 min
    "reversal_buffer_pct": 1.5,  # how close to support/resistance counts as "approaching"
    "max_leverage": 10,  # hard ceiling, never exceed
    "max_risk_pct_per_trade": 15.0,  # hard ceiling on capital at risk
    "daily_loss_limit_pct": 10.0,  # circuit breaker threshold
    "max_consecutive_losses": 3,  # circuit breaker threshold
    "usd_inr_rate": 95.95,  # update periodically or fetch live
}


# ═══════════════════════════════════════════════════════
# 2. DATA STRUCTURES
# ═══════════════════════════════════════════════════════

@dataclass
class PriceCandle:
    timestamp: datetime
    price: float


@dataclass
class RangeState:
    high: float
    low: float
    midpoint: float
    position_in_range_pct: float  # 0% = at low, 100% = at high


@dataclass
class Signal:
    asset: str
    signal_type: str  # "BREAKOUT_LONG", "BREAKOUT_SHORT", "REVERSION_LONG_WATCH", "REVERSION_SHORT_WATCH"
    confirmed: bool  # True if both BTC and SOL agree, False if partial/divergent
    current_price: float
    entry_trigger: float
    stop_loss: float
    target_1: float
    target_2: float
    confidence_note: str


@dataclass
class RiskState:
    consecutive_losses: int = 0
    daily_pnl_inr: float = 0.0
    daily_loss_limit_hit: bool = False
    alerts_paused: bool = False


# ═══════════════════════════════════════════════════════
# 3. DATA FETCHING (API INTEGRATION LAYER)
# ═══════════════════════════════════════════════════════

def fetch_current_price(symbol: str) -> float:
    """Return the current CoinDCX market price for a pair like SOLUSDT."""
    return coindcx_fetch_current_price(symbol)


def fetch_price_history(symbol: str, hours: int) -> list:
    """Return a local PriceCandle list covering the lookback window."""
    history = coindcx_fetch_price_history(symbol, hours)
    return [
        PriceCandle(timestamp=c.timestamp, price=c.price)
        for c in history
    ]


def fetch_account_capital_inr() -> float:
    """Return current available capital in INR using CoinDCX account data if configured."""
    return coindcx_fetch_account_capital_inr(usd_inr_rate=CONFIG["usd_inr_rate"])


# ═══════════════════════════════════════════════════════
# 4. RANGE DETECTION
# ═══════════════════════════════════════════════════════

def compute_range(price_history: list) -> RangeState:
    """Compute rolling high/low/midpoint and current position within range."""
    prices = [c.price for c in price_history]
    high = max(prices)
    low = min(prices)
    midpoint = (high + low) / 2
    current = prices[-1]

    if high == low:
        position_pct = 50.0  # avoid division by zero on flat data
    else:
        position_pct = ((current - low) / (high - low)) * 100

    return RangeState(high=high, low=low, midpoint=midpoint,
                      position_in_range_pct=position_pct)


# ═══════════════════════════════════════════════════════
# 5. SIGNAL / TRIGGER DETECTION
# ═══════════════════════════════════════════════════════

def detect_signal(sol_range: RangeState, sol_price: float,
                  btc_range: RangeState, btc_price: float) -> Signal | None:
    """
    Core trigger logic:
    - Breakout: price closes above/below range extreme
    - Reversion: price approaches extreme (within buffer) - treated as a
      watch condition, not a full signal (needs candle confirmation)
    - Confirmed vs Partial: both assets must agree for "confirmed"
    """
    buffer = CONFIG["reversal_buffer_pct"]

    sol_breakout_long = sol_price >= sol_range.high
    sol_breakout_short = sol_price <= sol_range.low
    btc_breakout_long = btc_price >= btc_range.high
    btc_breakout_short = btc_price <= btc_range.low

    sol_near_low = sol_range.position_in_range_pct <= buffer
    sol_near_high = sol_range.position_in_range_pct >= (100 - buffer)
    btc_near_low = btc_range.position_in_range_pct <= buffer
    btc_near_high = btc_range.position_in_range_pct >= (100 - buffer)

    span = sol_range.high - sol_range.low

    # --- Breakout Long: both confirm ---
    if sol_breakout_long and btc_breakout_long:
        return Signal(
            asset="SOL", signal_type="BREAKOUT_LONG", confirmed=True,
            current_price=sol_price, entry_trigger=sol_range.high,
            stop_loss=sol_range.midpoint,
            target_1=sol_range.high + span * 0.5,
            target_2=sol_range.high + span * 1.2,
            confidence_note="CONFIRMED: Both SOL and BTC broke range high together."
        )

    # --- Breakout Long: SOL only (partial) ---
    if sol_breakout_long and not btc_breakout_long:
        return Signal(
            asset="SOL", signal_type="BREAKOUT_LONG", confirmed=False,
            current_price=sol_price, entry_trigger=sol_range.high,
            stop_loss=sol_range.midpoint,
            target_1=sol_range.high + span * 0.3,
            target_2=sol_range.high + span * 0.6,
            confidence_note="PARTIAL: SOL broke out, BTC has NOT confirmed. "
                            "Lower confidence, use tighter stop and smaller size."
        )

    # --- Breakout Short: both confirm ---
    if sol_breakout_short and btc_breakout_short:
        return Signal(
            asset="SOL", signal_type="BREAKOUT_SHORT", confirmed=True,
            current_price=sol_price, entry_trigger=sol_range.low,
            stop_loss=sol_range.midpoint,
            target_1=sol_range.low - span * 0.5,
            target_2=sol_range.low - span * 1.2,
            confidence_note="CONFIRMED: Both SOL and BTC broke range low together."
        )

    # --- Breakout Short: SOL only (partial) ---
    if sol_breakout_short and not btc_breakout_short:
        return Signal(
            asset="SOL", signal_type="BREAKOUT_SHORT", confirmed=False,
            current_price=sol_price, entry_trigger=sol_range.low,
            stop_loss=sol_range.midpoint,
            target_1=sol_range.low - span * 0.3,
            target_2=sol_range.low - span * 0.6,
            confidence_note="PARTIAL: SOL broke down, BTC has NOT confirmed. "
                            "Lower confidence, use tighter stop and smaller size."
        )

    # --- Mean-reversion watch conditions (informational only) ---
    if sol_near_high and btc_near_high:
        return Signal(
            asset="SOL", signal_type="REVERSION_SHORT_WATCH", confirmed=False,
            current_price=sol_price, entry_trigger=sol_range.high,
            stop_loss=sol_range.high * 1.01,
            target_1=sol_range.midpoint,
            target_2=sol_range.low,
            confidence_note="WATCH: Both SOL and BTC near range high. "
                            "Wait for an actual rejection candle before entering."
        )

    if sol_near_low and btc_near_low:
        return Signal(
            asset="SOL", signal_type="REVERSION_LONG_WATCH", confirmed=False,
            current_price=sol_price, entry_trigger=sol_range.low,
            stop_loss=sol_range.low * 0.99,
            target_1=sol_range.midpoint,
            target_2=sol_range.high,
            confidence_note="WATCH: Both SOL and BTC near range low. "
                            "Wait for an actual bounce candle before entering."
        )

    return None  # no signal - still mid-range


# ═══════════════════════════════════════════════════════
# 6. POSITION SIZING
# ═══════════════════════════════════════════════════════

def calculate_position(capital_inr: float, leverage: int, entry: float,
                       stop: float, direction: str) -> dict:
    """Replicates the manual sizing formula used in this project."""
    leverage = min(leverage, CONFIG["max_leverage"])  # hard ceiling enforced
    capital_usd = capital_inr / CONFIG["usd_inr_rate"]
    notional = capital_usd * leverage
    quantity = notional / entry

    if direction == "LONG":
        liquidation = entry * (1 - 1 / leverage)
    else:  # SHORT
        liquidation = entry * (1 + 1 / leverage)

    risk_usd = quantity * abs(entry - stop)
    risk_inr = risk_usd * CONFIG["usd_inr_rate"]
    risk_pct = (risk_inr / capital_inr) * 100

    return {
        "quantity": round(quantity, 4),
        "notional_usd": round(notional, 2),
        "liquidation_price": round(liquidation, 2),
        "risk_inr": round(risk_inr, 2),
        "risk_pct_of_capital": round(risk_pct, 2),
        "exceeds_risk_ceiling": risk_pct > CONFIG["max_risk_pct_per_trade"],
    }


# ═══════════════════════════════════════════════════════
# 7. RISK SAFEGUARDS / CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════

def check_circuit_breaker(risk_state: RiskState, capital_inr: float) -> bool:
    """Returns True if alerts should be paused due to risk limits being hit."""
    if risk_state.consecutive_losses >= CONFIG["max_consecutive_losses"]:
        risk_state.alerts_paused = True

    daily_loss_pct = abs(min(risk_state.daily_pnl_inr, 0)) / capital_inr * 100
    if daily_loss_pct >= CONFIG["daily_loss_limit_pct"]:
        risk_state.alerts_paused = True
        risk_state.daily_loss_limit_hit = True

    return risk_state.alerts_paused


# ═══════════════════════════════════════════════════════
# 8. NOTIFICATION FORMATTING
# ═══════════════════════════════════════════════════════

def format_alert_message(signal: Signal, sizing: dict) -> str:
    """Produces a human-readable alert."""
    status = "✅ CONFIRMED" if signal.confirmed else "⚠️ PARTIAL - LOWER CONFIDENCE"
    return f"""{status} SIGNAL: {signal.asset} {signal.signal_type}

Current Price: ${signal.current_price}
Entry Trigger: ${signal.entry_trigger}
Stop-Loss: ${signal.stop_loss}
Target 1: ${signal.target_1}
Target 2: ${signal.target_2}

Suggested Quantity: {sizing['quantity']}
Liquidation Price: ${sizing['liquidation_price']}
Risk: ₹{sizing['risk_inr']} ({sizing['risk_pct_of_capital']}% of capital)

{signal.confidence_note}

⚠️ Reminder: Confirm manually before placing any order.
This is an alert, not an automatic trade.
"""


# ═══════════════════════════════════════════════════════
# 9. MAIN LOOP
# ═══════════════════════════════════════════════════════

def main_loop():
    risk_state = RiskState()
    last_alert = {}  # (signal_type, confirmed) -> time of last send
    poll_count = 0

    print("🟢 Trading alert bot started. Polling every 30s...")
    send_telegram_message("🟢 Trading alert bot started. Polling every 30s...")

    while True:
        if risk_state.alerts_paused:
            print("Alerts paused due to risk circuit breaker. Manual reset required.")
            send_telegram_message("⚠️ Alerts paused due to risk circuit breaker. Manual reset required.")
            time.sleep(CONFIG["poll_interval_seconds"])
            continue

        try:
            poll_count += 1
            status = get_market_status()
            sol_price = status["sol_price"]
            btc_price = status["btc_price"]
            sol_range = status["sol_range"]
            btc_range = status["btc_range"]
            capital_inr = status["capital_inr"]
            signal = status["signal"]

            if poll_count % 5 == 0 or signal:
                label = signal.signal_type if signal else "None"
                summary = (
                    f"Poll #{poll_count}: SOL={sol_price:.2f} | BTC={btc_price:.2f} | "
                    f"SOL range={sol_range.low:.2f}-{sol_range.high:.2f} | "
                    f"BTC range={btc_range.low:.2f}-{btc_range.high:.2f} | "
                    f"signal={label} | capital=₹{capital_inr:.0f}"
                )
                print(summary)
                send_telegram_message(summary)

            if signal:
                key = (signal.signal_type, signal.confirmed)
                now = time.time()
                if now - last_alert.get(key, 0) >= CONFIG["alert_cooldown_seconds"]:
                    sizing = calculate_position(
                        capital_inr=capital_inr, leverage=5,
                        entry=signal.entry_trigger, stop=signal.stop_loss,
                        direction="LONG" if "LONG" in signal.signal_type else "SHORT"
                    )
                    message = format_alert_message(signal, sizing)
                    print(message)
                    if send_telegram_message(message):
                        last_alert[key] = now

        except NotImplementedError as e:
            print(f"API integration incomplete: {e}")
            send_telegram_message(f"API integration incomplete: {e}")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")
            send_telegram_message(f"Error in main loop: {e}")

        time.sleep(CONFIG["poll_interval_seconds"])


if __name__ == "__main__":
    main_loop()
