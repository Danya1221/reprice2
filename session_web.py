import html
import os
from pathlib import Path

from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SETUP_KEY = os.getenv("SETUP_KEY", "").strip()
PORT = int(os.getenv("PORT", "8080"))

STATE_DIR = Path(os.getenv("STATE_DIR", "/data"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = os.getenv(
    "SESSION_FILE",
    str(STATE_DIR / "telegram_user"),
).strip()

if not API_ID or not API_HASH:
    raise RuntimeError("Заполни API_ID и API_HASH")

client = None
state = {"phone": None, "phone_code_hash": None}

PAGE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Login</title>
<style>
body {{ font-family:system-ui,sans-serif; max-width:680px; margin:50px auto; padding:0 20px; }}
input,button {{ font-size:16px; padding:12px; margin:6px 0; width:100%; box-sizing:border-box; }}
.card {{ border:1px solid #ddd; border-radius:14px; padding:20px; margin:16px 0; }}
.ok {{ background:#eefbef; }} .err {{ background:#fff1f1; }}
code {{ word-break:break-all; }}
</style>
</head>
<body><h1>Telegram Login</h1>{body}</body>
</html>
"""

def render(body, status=200):
    return web.Response(
        text=PAGE.format(body=body),
        content_type="text/html",
        status=status,
    )

def esc(v):
    return html.escape(str(v or ""))


def key_ok(value):
    # Если SETUP_KEY не задан — пароль не требуется.
    # Если задан — проверяем его как раньше.
    if not SETUP_KEY:
        return True
    return (value or "") == SETUP_KEY


def session_paths():
    base = Path(SESSION_FILE)
    session_path = base if base.suffix == ".session" else Path(str(base) + ".session")
    return [
        session_path,
        Path(str(session_path) + "-journal"),
        Path(str(session_path) + "-shm"),
        Path(str(session_path) + "-wal"),
    ]

async def fresh_client():
    global client

    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass

    for p in session_paths():
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    client = TelegramClient(
        SESSION_FILE,
        API_ID,
        API_HASH,
    )
    await client.connect()

async def ensure_client():
    global client

    if client is None:
        client = TelegramClient(
            SESSION_FILE,
            API_ID,
            API_HASH,
        )

    if not client.is_connected():
        await client.connect()

async def index(request):
    return render("""
    <div class="card">
      <p>Вход сохранится прямо на Railway Volume.</p>
      <p>После этого ничего копировать в Variables не нужно.</p><p>SETUP_KEY можно не указывать — поле пароля можно оставить пустым.</p>
      <form method="post" action="/phone">
        <input name="key" type="password" placeholder="SETUP_KEY (необязательно)">
        <input name="phone" placeholder="+31612345678" required>
        <button>Получить код Telegram</button>
      </form>
    </div>
    """)

async def phone(request):
    data = await request.post()

    if not key_ok(data.get("key", "")):
        return render('<div class="card err">Неверный SETUP_KEY</div>', 403)

    try:
        await fresh_client()
    except Exception as e:
        return render(
            f'<div class="card err">Не удалось начать новый вход: {esc(e)}</div>',
            400,
        )

    phone_value = data.get("phone", "").strip()

    try:
        sent = await client.send_code_request(phone_value)
    except Exception as e:
        return render(f'<div class="card err">{esc(e)}</div>', 400)

    state["phone"] = phone_value
    state["phone_code_hash"] = sent.phone_code_hash

    return render("""
    <div class="card">
      <form method="post" action="/code">
        <input name="key" type="password" placeholder="SETUP_KEY (необязательно)">
        <input name="code" placeholder="Код Telegram" required>
        <button>Подтвердить</button>
      </form>
    </div>
    """)

async def code(request):
    data = await request.post()

    if not key_ok(data.get("key", "")):
        return render('<div class="card err">Неверный SETUP_KEY</div>', 403)

    await ensure_client()

    try:
        await client.sign_in(
            phone=state["phone"],
            code=data.get("code", "").replace(" ", ""),
            phone_code_hash=state["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        return render("""
        <div class="card">
          <form method="post" action="/password">
            <input name="key" type="password" placeholder="SETUP_KEY (необязательно)">
            <input name="password" type="password" placeholder="Пароль 2FA" required>
            <button>Войти</button>
          </form>
        </div>
        """)
    except Exception as e:
        return render(f'<div class="card err">{esc(e)}</div>', 400)

    return await done()

async def password(request):
    data = await request.post()

    if not key_ok(data.get("key", "")):
        return render('<div class="card err">Неверный SETUP_KEY</div>', 403)

    await ensure_client()

    try:
        await client.sign_in(password=data.get("password", ""))
    except Exception as e:
        return render(f'<div class="card err">{esc(e)}</div>', 400)

    return await done()

async def done():
    me = await client.get_me()
    await client.disconnect()

    return render(
        '<div class="card ok">'
        '<h2>Готово ✅</h2>'
        f'<p>Аккаунт: <b>{esc(getattr(me, "username", None) or me.id)}</b></p>'
        f'<p>Сессия сохранена на Volume: <code>{esc(SESSION_FILE)}.session</code></p>'
        '<p>Теперь поставь <code>SETUP_MODE=false</code> и сделай redeploy.</p>'
        '</div>'
    )

app = web.Application()
app.add_routes([
    web.get("/", index),
    web.post("/phone", phone),
    web.post("/code", code),
    web.post("/password", password),
])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
