import html
import os

from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SETUP_KEY = os.getenv("SETUP_KEY", "").strip()
PORT = int(os.getenv("PORT", "8080"))

if not API_ID or not API_HASH:
    raise RuntimeError("Заполни API_ID и API_HASH")
if not SETUP_KEY:
    raise RuntimeError("Добавь SETUP_KEY")

client = TelegramClient(StringSession(), API_ID, API_HASH)
state = {"phone": None, "phone_code_hash": None}

PAGE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Session Setup</title>
<style>
body {{ font-family:system-ui,sans-serif; max-width:680px; margin:50px auto; padding:0 20px; }}
input,button {{ font-size:16px; padding:12px; margin:6px 0; width:100%; box-sizing:border-box; }}
.card {{ border:1px solid #ddd; border-radius:14px; padding:20px; margin:16px 0; }}
.ok {{ background:#eefbef; }} .err {{ background:#fff1f1; }}
pre {{ white-space:pre-wrap; word-break:break-all; }}
</style>
</head>
<body><h1>Telegram Session Setup</h1>{body}</body>
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

async def ensure_client():
    if not client.is_connected():
        await client.connect()

async def index(request):
    return render("""
    <div class="card">
    <form method="post" action="/phone">
      <input name="key" type="password" placeholder="SETUP_KEY" required>
      <input name="phone" placeholder="+31612345678" required>
      <button>Отправить код</button>
    </form>
    </div>
    """)

async def phone(request):
    data = await request.post()
    if data.get("key", "") != SETUP_KEY:
        return render('<div class="card err">Неверный SETUP_KEY</div>', 403)

    await ensure_client()
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
      <input name="key" type="password" placeholder="SETUP_KEY" required>
      <input name="code" placeholder="Код Telegram" required>
      <button>Подтвердить</button>
    </form>
    </div>
    """)

async def code(request):
    data = await request.post()
    if data.get("key", "") != SETUP_KEY:
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
          <input name="key" type="password" placeholder="SETUP_KEY" required>
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
    if data.get("key", "") != SETUP_KEY:
        return render('<div class="card err">Неверный SETUP_KEY</div>', 403)

    await ensure_client()
    try:
        await client.sign_in(password=data.get("password", ""))
    except Exception as e:
        return render(f'<div class="card err">{esc(e)}</div>', 400)

    return await done()

async def done():
    value = client.session.save()
    return render(
        f'<div class="card ok"><h2>Готово ✅</h2>'
        f'<p>Скопируй в Railway как SESSION_STRING:</p>'
        f'<pre>{esc(value)}</pre>'
        f'<p>После этого SETUP_MODE=false.</p></div>'
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
