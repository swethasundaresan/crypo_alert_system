"""Simple Telegram command bot to start or query the trading loop.

Usage:
    python telegram_control_bot.py

Telegram commands:
    /start      - starts the strategy loop in a background thread
    /status     - shows the latest 24h range snapshot for SOL and BTC
    /help       - shows available commands

The bot reads the same values from .env as telegram_bot.py and trading_bot.py.
"""
import threading
import time

import requests
from dotenv import load_dotenv

import trading_bot

load_dotenv()

BOT_TOKEN = trading_bot.__dict__.get("CONFIG")

# We intentionally avoid depending on a third-party Telegram library.
# The bot uses Telegram's HTTP API directly with the same credentials already in .env.
BASE_URL = f"https://api.telegram.org/bot{trading_bot.__dict__.get('os', __import__('os')).getenv('TELEGRAM_BOT_TOKEN')}"


class TelegramController:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()

    def start_loop(self):
        if self.thread and self.thread.is_alive():
            return "Trading bot already running."
        self.stop_event.clear()
        self.thread = threading.Thread(target=trading_bot.main_loop, daemon=True)
        self.thread.start()
        return "Trading bot started."

    def get_status(self):
        status = trading_bot.get_market_status()
        sol_range = status["sol_range"]
        btc_range = status["btc_range"]
        return (
            f"SOL: {status['sol_price']:.2f} | BTC: {status['btc_price']:.2f}\n"
            f"SOL 24h: {sol_range.low:.2f}-{sol_range.high:.2f}\n"
            f"BTC 24h: {btc_range.low:.2f}-{btc_range.high:.2f}\n"
            f"Signal: {status['signal'].signal_type if status['signal'] else 'None'}"
        )

    def send_message(self, chat_id, text):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        return requests.post(f"{BASE_URL}/sendMessage", data=payload, timeout=10)

    def poll(self):
        offset = None
        while True:
            params = {"timeout": 10}
            if offset is not None:
                params["offset"] = offset + 1
            resp = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=20)
            data = resp.json()
            if not data.get("ok"):
                print(f"Telegram error: {data.get('description')}")
                time.sleep(5)
                continue
            for update in data.get("result", []):
                offset = update.get("update_id")
                message = update.get("message") or {}
                text = (message.get("text") or "").strip()
                chat_id = message.get("chat", {}).get("id")
                if not text or not chat_id:
                    continue
                cmd = text.lower()
                if cmd == "/start":
                    self.send_message(chat_id, self.start_loop())
                elif cmd == "/status":
                    self.send_message(chat_id, self.get_status())
                elif cmd in ["/help", "help"]:
                    self.send_message(chat_id, "Commands: /start, /status, /help")


if __name__ == "__main__":
    controller = TelegramController()
    print("Telegram control bot started. Waiting for commands...")
    controller.poll()
