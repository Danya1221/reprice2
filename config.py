import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

SUPPLIER_BOT = os.getenv("SUPPLIER_BOT", "").strip()
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "").strip()
REQUEST_TEXT = os.getenv("REQUEST_TEXT", "/start").strip()
BUTTON_PATH = [x.strip() for x in os.getenv("BUTTON_PATH", "").split(">") if x.strip()]

CONTROL_BOT_TOKEN = os.getenv("CONTROL_BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DEFAULT_INTERVAL = int(os.getenv("POLL_SECONDS", "1800"))
AFTER_ACTION_DELAY = float(os.getenv("AFTER_ACTION_DELAY", "2.0"))
RESPONSE_TIMEOUT = int(os.getenv("RESPONSE_TIMEOUT", "30"))

CLOSED_TEXT = os.getenv(
    "CLOSED_TEXT",
    "В данный момент мы закрыты. Пожалуйста, дождитесь старта продаж."
).strip()

STATE_DIR = Path(os.getenv("STATE_DIR", "/data"))
if not STATE_DIR.exists():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        STATE_DIR = Path(".")

STATE_FILE = STATE_DIR / "price_manager_state.json"

SETUP_MODE = os.getenv("SETUP_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
SETUP_KEY = os.getenv("SETUP_KEY", "").strip()
PORT = int(os.getenv("PORT", "8080"))
