import os
import sys
from pathlib import Path


state_dir = Path(os.getenv("STATE_DIR", "/data"))

session_base = os.getenv(
    "SESSION_FILE",
    str(state_dir / "telegram_user"),
).strip()

session_path = (
    Path(session_base)
    if str(session_base).endswith(".session")
    else Path(str(session_base) + ".session")
)

if session_path.exists() and session_path.stat().st_size > 0:
    target = "main.py"
else:
    target = "session_web.py"
    print(
        f"🔐 Telegram session not found: {session_path}",
        flush=True,
    )
    print(
        "▶️ Запускаю одноразовый Telegram Login",
        flush=True,
    )

print(f"▶️ Запуск: {target}", flush=True)

os.execv(
    sys.executable,
    [sys.executable, target],
)
