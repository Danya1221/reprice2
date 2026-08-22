import asyncio
from contextlib import suppress

from config import (
    ADMIN_ID,
    API_HASH,
    API_ID,
    CONTROL_BOT_TOKEN,
    DEFAULT_INTERVAL,
    STATE_FILE,
    SUPPLIER_BOT,
    TARGET_CHANNEL,
)
from control_bot import run_control_bot
from state import StateStore
from telegram_runtime import TelegramRuntime


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
        raise RuntimeError(
            "Не заполнены Variables: " + ", ".join(missing)
        )


async def main():
    validate()

    state = StateStore(
        STATE_FILE,
        DEFAULT_INTERVAL,
    )

    runtime = TelegramRuntime(state)

    # Вначале берём file-lock. Это не даёт двум Railway deployments
    # одновременно использовать один Telegram auth key.
    await runtime.start()

    try:
        # Bot API работает всегда, даже если Telethon требует QR-вход.
        await run_control_bot(
            CONTROL_BOT_TOKEN,
            ADMIN_ID,
            state,
            runtime,
        )
    finally:
        with suppress(Exception):
            await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
