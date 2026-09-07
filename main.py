import asyncio
from contextlib import suppress

from config import (
    ADMIN_ID,
    API_HASH,
    API_ID,
    CONTROL_BOT_TOKEN,
    DATABASE_URL,
    DEFAULT_INTERVAL,
    STATE_FILE,
    SUPPLIER_BOT,
    TARGET_CHANNEL,
)
from control_bot import run_control_bot
from database import DatabaseStore
from state import StateStore
from telegram_runtime import TelegramRuntime


def validate():
    missing = []

    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
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

    database = DatabaseStore(DATABASE_URL)
    state = StateStore(
        STATE_FILE,
        DEFAULT_INTERVAL,
        database,
    )

    runtime = TelegramRuntime(state, database)

    # Start Bot API polling immediately.
    # Telegram/Telethon runtime may still be restoring a session or waiting
    # for the previous Railway instance lock, but /start and /login must stay alive.
    runtime_task = asyncio.create_task(runtime.start())

    try:
        await run_control_bot(
            CONTROL_BOT_TOKEN,
            ADMIN_ID,
            state,
            runtime,
        )
    finally:
        if not runtime_task.done():
            runtime_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await runtime_task
        else:
            with suppress(Exception):
                runtime_task.result()

        with suppress(Exception):
            await runtime.close()
        with suppress(Exception):
            database.close()


if __name__ == "__main__":
    asyncio.run(main())
