"""
Telegram alert helper for the crypto alert system.

Setup:
    pip install requests python-dotenv

.env file (same folder, never share or commit it):
    TELEGRAM_BOT_TOKEN=your_token_from_botfather
    TELEGRAM_CHAT_ID=          (leave empty until step 2)

Usage:
    python telegram_bot.py chatid   -> prints your Chat ID (send your bot "hi" first)
    python telegram_bot.py test     -> sends a test message to your phone

In your main bot, import and use:
    from telegram_bot import send_telegram_message
    send_telegram_message("SOL breakout detected")
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE = f"https://api.telegram.org/bot{TOKEN}"


def _check_token():
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN missing in .env")


def get_chat_id() -> None:
    """Print chat IDs of everyone who recently messaged the bot."""
    _check_token()
    r = requests.get(f"{BASE}/getUpdates", timeout=10)
    data = r.json()
    if not data.get("ok"):
        sys.exit(f"Telegram error: {data.get('description')}")
    results = data.get("result", [])
    if not results:
        sys.exit("No messages found. Open your bot in Telegram, send 'hi', then run again.")
    seen = set()
    for update in results:
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            name = chat.get("first_name") or chat.get("title") or ""
            print(f"Chat ID: {cid}  ({name})")
    print("\nPut this in .env as TELEGRAM_CHAT_ID=<number>")


def send_telegram_message(text: str) -> bool:
    """Send a message to your chat. Returns True on success."""
    _check_token()
    if not CHAT_ID:
        print("TELEGRAM_CHAT_ID missing in .env")
        return False
    try:
        r = requests.post(
            f"{BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
        data = r.json()
    except requests.RequestException as e:
        print(f"Network error sending Telegram message: {e}")
        return False
    if not data.get("ok"):
        print(f"Telegram error: {data.get('description')}")
        return False
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "chatid":
        get_chat_id()
    elif cmd == "test":
        ok = send_telegram_message("✅ Bot is working")
        print("Sent!" if ok else "Failed, see error above.")
    else:
        print(__doc__)
