import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

SUPPLIER_BOT = os.getenv("SUPPLIER_BOT", "").strip()
_TARGET_CHANNEL_RAW = os.getenv("TARGET_CHANNEL", "").strip()
if _TARGET_CHANNEL_RAW.lstrip("-").isdigit():
    TARGET_CHANNEL = int(_TARGET_CHANNEL_RAW)
else:
    TARGET_CHANNEL = _TARGET_CHANNEL_RAW
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
PERSIST_DIR = Path(
    os.getenv(
        "PERSIST_DIR",
        str(STATE_DIR / "pricebot"),
    )
)
if not STATE_DIR.exists():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        STATE_DIR = Path(".")

try:
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    PERSIST_DIR = Path(".")

STATE_FILE = PERSIST_DIR / "price_manager_state.json"

SESSION_FILE = os.getenv("SESSION_FILE", str(STATE_DIR / "telegram_user")).strip()

PORT = int(os.getenv("PORT", "8080"))
