import asyncio
import html
import os
import sys
from pathlib import Path

from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
PORT = int(os.getenv("PORT", "8080"))

STATE_DIR = Path(os.getenv("STATE_DIR", "/data"))

try:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

SESSION_FILE = os.getenv(
    "SESSION_FILE",
    str(STATE_DIR / "telegram_user"),
).strip()

if not API_ID or not API_HASH:
    raise RuntimeError("Заполни API_ID и API_HASH")

client = None

login_state = {
    "phone": None,
    "phone_code_hash": None,
}

PAGE = """
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Login</title>
<style>
body {{ font-family:system-ui,sans-serif; max-width:620px; margin:50px auto; padding:0 20px; }}
input,button {{ font-size:16px; padding:12px; margin:7px 0; width:100%; box-sizing:border-box; }}
.card {{ border:1px solid #ddd; border-radius:14px; padding:20px; margin:16px 0; }}
.ok {{ background:#eefbef; }}
.err {{ background:#fff1f1; }}
</style>
</head>
<body>
<h1>Telegram Login</h1>
{body}
</body>
</html>
"""


def esc(value):
    return html.escape(str(value or ""))


def render(body, status=200):
    return web.Response(
        text=PAGE.format(body=body),
        content_type="text/html",
        status=status,
    )


def session_paths():
    base = Path(SESSION_FILE)

    session_path = (
        base
        if base.suffix == ".session"
        else Path(str(base) + ".session")
    )

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

    # Login page is opened only when current session is absent/dead.
    for path in session_paths():
        try:
            path.unlink(missing_ok=True)
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


def start_main_after_response():
    """
    После успешной авторизации не просим пользователя менять Variables
    и не просим делать redeploy. Через 2 секунды этот же процесс
    заменяется на main.py.
    """
    loop = asyncio.get_running_loop()

    def _switch():
        print(
            "✅ Telegram авторизован. Переключаюсь на main.py…",
            flush=True,
        )
        os.execv(
            sys.executable,
            [sys.executable, "main.py"],
        )

    loop.call_later(2.0, _switch)


async def index(request):
    return render("""
    <div class="card">
      <p>
        Telegram удалил старую авторизацию. Нужен один новый вход.
      </p>
      <p>
        После входа бот запустится автоматически. Никаких SESSION_STRING,
        SETUP_MODE и повторных redeploy.
      </p>

      <form method="post" action="/phone">
        <input
          name="phone"
          placeholder="+31612345678"
          autocomplete="tel"
          required
        >
        <button>Получить код Telegram</button>
      </form>
    </div>
    """)


async def phone(request):
    data = await request.post()
    phone_value = (data.get("phone") or "").strip()

    try:
        await fresh_client()

        sent = await client.send_code_request(
            phone_value,
        )
    except Exception as e:
        return render(
            f'<div class="card err">{esc(e)}</div>',
            400,
        )

    login_state["phone"] = phone_value
    login_state["phone_code_hash"] = sent.phone_code_hash

    return render("""
    <div class="card">
      <p>Введи код, который Telegram прислал на аккаунт.</p>

      <form method="post" action="/code">
        <input
          name="code"
          inputmode="numeric"
          placeholder="Код Telegram"
          required
        >
        <button>Войти</button>
      </form>
    </div>
    """)


async def code(request):
    data = await request.post()

    await ensure_client()

    try:
        await client.sign_in(
            phone=login_state["phone"],
            code=(data.get("code") or "").replace(" ", ""),
            phone_code_hash=login_state["phone_code_hash"],
        )

    except SessionPasswordNeededError:
        return render("""
        <div class="card">
          <p>На аккаунте включён пароль двухэтапной защиты.</p>

          <form method="post" action="/password">
            <input
              name="password"
              type="password"
              placeholder="Пароль 2FA"
              required
            >
            <button>Продолжить</button>
          </form>
        </div>
        """)

    except Exception as e:
        return render(
            f'<div class="card err">{esc(e)}</div>',
            400,
        )

    return await done()


async def password(request):
    data = await request.post()

    await ensure_client()

    try:
        await client.sign_in(
            password=data.get("password") or "",
        )
    except Exception as e:
        return render(
            f'<div class="card err">{esc(e)}</div>',
            400,
        )

    return await done()


async def done():
    me = await client.get_me()

    # disconnect() flushes Telethon SQLite session cleanly to /data.
    await client.disconnect()

    session_path = session_paths()[0]

    if not session_path.exists() or session_path.stat().st_size == 0:
        return render(
            '<div class="card err">'
            'Авторизация прошла, но session-файл не сохранился. '
            'Проверь Railway Volume /data.'
            '</div>',
            500,
        )

    start_main_after_response()

    return render(
        '<div class="card ok">'
        '<h2>Готово ✅</h2>'
        f'<p>Аккаунт: <b>{esc(getattr(me, "username", None) or me.id)}</b></p>'
        '<p>Сессия сохранена.</p>'
        '<p><b>Через пару секунд бот запустится сам.</b></p>'
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
    web.run_app(
        app,
        host="0.0.0.0",
        port=PORT,
    )
