import asyncio
from contextlib import suppress

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import (
    ADMIN_ID,
    API_HASH,
    API_ID,
    CONTROL_BOT_TOKEN,
    DEFAULT_INTERVAL,
    SESSION_STRING,
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
    if not SESSION_STRING:
        missing.append("SESSION_STRING")
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
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
    )

    await client.start()

    me = await client.get_me()
    print(f"✅ Telethon: {me.username or me.id}", flush=True)
    print(f"📦 {SUPPLIER_BOT} → {TARGET_CHANNEL}", flush=True)

    engine = PriceSyncEngine(client, state)
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
            await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
