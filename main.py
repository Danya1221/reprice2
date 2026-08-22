import asyncio
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
        await client.start()
    except AuthKeyDuplicatedError:
        print(
            "\n❌ Telegram session-файл аннулирован.\n"
            "Переключи SETUP_MODE=true и один раз войди через веб-страницу.\n"
            "Ничего копировать больше не нужно.\n"
            "После успешного входа верни SETUP_MODE=false.\n",
            flush=True,
        )

        while True:
            await asyncio.sleep(3600)

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
