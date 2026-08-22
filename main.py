import asyncio
import os
import sys
from pathlib import Path
from contextlib import suppress

from telethon import TelegramClient
from telethon.errors import AuthKeyDuplicatedError

from config import (
    ADMIN_ID,
    API_HASH,
    API_ID,
    CONTROL_BOT_TOKEN,
    DEFAULT_INTERVAL,
    SESSION_FILE,
    STATE_FILE,
    SUPPLIER_BOT,
    TARGET_CHANNEL,
)
from control_bot import run_control_bot
from state import StateStore
from sync_engine import PriceSyncEngine



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


def remove_dead_session():
    for path in session_paths():
        try:
            path.unlink(missing_ok=True)
        except Exception as e:
            print(
                f"⚠️ Не удалось удалить {path}: {e}",
                flush=True,
            )


def switch_to_login():
    print(
        "🔐 Переключаю сервис на одноразовый Telegram Login…",
        flush=True,
    )
    os.execv(
        sys.executable,
        [sys.executable, "session_web.py"],
    )



def validate():
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not SUPPLIER_BOT:
        missing.append("SUPPLIER_BOT")
    if not TARGET_CHANNEL:
        missing.append("TARGET_CHANNEL")
    if not CONTROL_BOT_TOKEN:
        missing.append("CONTROL_BOT_TOKEN")
    if not ADMIN_ID:
        missing.append("ADMIN_ID")

    if missing:
        raise RuntimeError("Не заполнены Variables: " + ", ".join(missing))


async def main():
    validate()

    state = StateStore(STATE_FILE, DEFAULT_INTERVAL)

    client = TelegramClient(
        SESSION_FILE,
        API_ID,
        API_HASH,
    )

    try:
        # Никогда не вызываем client.start(): Railway не имеет stdin.
        await client.connect()

        if not await client.is_user_authorized():
            print(
                "🔐 Session-файл есть, но аккаунт не авторизован.",
                flush=True,
            )

            with suppress(Exception):
                await client.disconnect()

            remove_dead_session()
            switch_to_login()

    except AuthKeyDuplicatedError:
        print(
            "❌ Старый Telegram auth key аннулирован Telegram.",
            flush=True,
        )

        with suppress(Exception):
            await client.disconnect()

        remove_dead_session()
        switch_to_login()

    except Exception as e:
        # Не превращаем обычный сетевой сбой в новый логин.
        # Но если Telethon явно сообщает, что session/auth key непригоден,
        # очищаем только session-файл и включаем login.
        text = str(e).casefold()

        auth_broken = any(
            phrase in text
            for phrase in (
                "auth key",
                "authorization key",
                "session revoked",
                "session expired",
                "user deactivated",
            )
        )

        if auth_broken:
            print(
                f"❌ Telegram-сессия непригодна: {e}",
                flush=True,
            )

            with suppress(Exception):
                await client.disconnect()

            remove_dead_session()
            switch_to_login()

        raise

    me = await client.get_me()
    print(f"✅ Telethon: {me.username or me.id}", flush=True)
    print(f"📦 {SUPPLIER_BOT} → {TARGET_CHANNEL}", flush=True)

    # Для приватного канала по ID вида -100... нужен access_hash.
    # Надёжно получаем entity из диалогов авторизованного аккаунта.
    if isinstance(TARGET_CHANNEL, int):
        target_entity = None
        dialogs = await client.get_dialogs(limit=None)

        for dialog in dialogs:
            if dialog.id == TARGET_CHANNEL:
                target_entity = dialog.entity
                break

        if target_entity is None:
            raise RuntimeError(
                f"Канал {TARGET_CHANNEL} не найден среди диалогов этого Telegram-аккаунта. "
                "Добавь аккаунт, которым авторизован Telethon, в целевой канал."
            )
    else:
        target_entity = await client.get_entity(TARGET_CHANNEL)

    print(
        f"✅ TARGET_CHANNEL найден: "
        f"{getattr(target_entity, 'title', TARGET_CHANNEL)}",
        flush=True,
    )

    engine = PriceSyncEngine(client, state, target_entity)
    await engine.start()

    # На старте делаем одну проверку, если синхронизация включена.
    if state.get("sync_enabled", True):
        try:
            await engine.sync_once()
        except Exception as e:
            print(f"⚠️ Первый запрос не удался: {e}", flush=True)

    periodic_task = asyncio.create_task(engine.periodic_loop())
    control_task = asyncio.create_task(
        run_control_bot(
            CONTROL_BOT_TOKEN,
            ADMIN_ID,
            state,
            engine,
        )
    )

    try:
        await asyncio.gather(periodic_task, control_task)
    finally:
        periodic_task.cancel()
        control_task.cancel()
        with suppress(Exception):
            await engine.close()
        with suppress(Exception):
            await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
