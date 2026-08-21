import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from parser import BLOCK_TITLES


def main_menu(state):
    enabled = state.get("sync_enabled", True)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏸ Остановить" if enabled else "▶️ Запустить",
                    callback_data="toggle_sync",
                ),
                InlineKeyboardButton(
                    text="🔄 Запросить сейчас",
                    callback_data="sync_now",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱ Интервал",
                    callback_data="interval_menu",
                ),
                InlineKeyboardButton(
                    text="⚙️ Блоки",
                    callback_data="blocks_menu",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статус",
                    callback_data="status",
                )
            ],
        ]
    )


def interval_menu(state):
    current = int(state.get("interval", 1800))
    values = [
        (300, "5 мин"),
        (900, "15 мин"),
        (1800, "30 мин"),
        (3600, "1 час"),
        (7200, "2 часа"),
    ]
    rows = []
    for seconds, label in values:
        mark = "✅ " if current == seconds else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{mark}{label}",
                callback_data=f"interval:{seconds}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="✍️ Свой интервал", callback_data="custom_interval")
    ])
    rows.append([
        InlineKeyboardButton(text="← Назад", callback_data="home")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def blocks_menu(state):
    blocks = state.get("blocks") or {}
    rows = []
    for gen in ("15", "16", "17"):
        row = []
        for sim_type, label in (("sim", "SIM"), ("esim", "eSIM")):
            key = f"{gen}_{sim_type}"
            on = blocks.get(key, True)
            row.append(
                InlineKeyboardButton(
                    text=f"{'✅' if on else '❌'} {gen} {label}",
                    callback_data=f"block:{key}",
                )
            )
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="✅ Включить все", callback_data="blocks_all_on"),
        InlineKeyboardButton(text="❌ Выключить все", callback_data="blocks_all_off"),
    ])
    rows.append([
        InlineKeyboardButton(text="← Назад", callback_data="home")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fmt_time(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts)).strftime("%d.%m %H:%M:%S")


def status_text(state):
    enabled = state.get("sync_enabled", True)
    interval = int(state.get("interval", 1800))
    minutes = interval // 60
    closed = state.get("supplier_closed", False)

    return (
        "📊 <b>Статус прайса</b>\n\n"
        f"{'🟢' if enabled else '🔴'} Синхронизация: "
        f"<b>{'включена' if enabled else 'остановлена'}</b>\n"
        f"⏱ Интервал: <b>{minutes} мин.</b>\n"
        f"🏪 Поставщик: <b>{'закрыт' if closed else 'открыт / неизвестно'}</b>\n"
        f"🕒 Последняя проверка: <b>{fmt_time(state.get('last_check_ts'))}</b>\n"
        f"🔜 Следующая проверка: <b>{fmt_time(state.get('next_check_ts'))}</b>\n"
        f"ℹ️ Последний результат: <b>{state.get('last_result', '—')}</b>"
    )


async def run_control_bot(token, admin_id, state, engine):
    bot = Bot(token)
    dp = Dispatcher()

    awaiting_custom = set()

    def allowed(user_id):
        return int(user_id) == int(admin_id)

    async def deny(target):
        if isinstance(target, CallbackQuery):
            await target.answer("Нет доступа", show_alert=True)
        else:
            await target.answer("Нет доступа.")

    @dp.message(CommandStart())
    async def start(message: Message):
        if not allowed(message.from_user.id):
            return await deny(message)

        await message.answer(
            "🛠 <b>Управление прайсом</b>\n\n"
            "Здесь можно управлять синхронизацией без Railway.",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )

    @dp.callback_query(F.data == "home")
    async def home(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        awaiting_custom.discard(callback.from_user.id)
        await callback.message.edit_text(
            "🛠 <b>Управление прайсом</b>",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "toggle_sync")
    async def toggle_sync(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        new_value = not state.get("sync_enabled", True)
        state.set("sync_enabled", new_value)
        engine.wake()

        await callback.message.edit_text(
            "🛠 <b>Управление прайсом</b>\n\n"
            f"{'▶️ Синхронизация запущена' if new_value else '⏸ Синхронизация остановлена'}",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "sync_now")
    async def sync_now(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.answer("Запрашиваю прайс…")
        await callback.message.edit_text(
            "🔄 <b>Запрашиваю прайс у поставщика…</b>",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )

        try:
            await engine.sync_once(forced=True)
            text = "✅ <b>Готово</b>\n\n" + status_text(state)
        except Exception as e:
            text = f"❌ <b>Ошибка</b>\n\n{e}"

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )

    @dp.callback_query(F.data == "interval_menu")
    async def open_interval(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.message.edit_text(
            "⏱ <b>Интервал запроса прайса</b>",
            parse_mode="HTML",
            reply_markup=interval_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("interval:"))
    async def set_interval(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        seconds = int(callback.data.split(":", 1)[1])
        state.set("interval", seconds)
        engine.wake()

        await callback.message.edit_text(
            f"✅ Интервал установлен: <b>{seconds // 60} мин.</b>",
            parse_mode="HTML",
            reply_markup=interval_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "custom_interval")
    async def custom_interval(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        awaiting_custom.add(callback.from_user.id)
        await callback.message.edit_text(
            "✍️ Пришли одним сообщением количество минут.\n\n"
            "Например: <code>45</code>",
            parse_mode="HTML",
        )
        await callback.answer()

    @dp.message()
    async def custom_interval_message(message: Message):
        if not allowed(message.from_user.id):
            return
        if message.from_user.id not in awaiting_custom:
            return

        raw = (message.text or "").strip()
        try:
            minutes = int(raw)
            if minutes < 1 or minutes > 1440:
                raise ValueError
        except ValueError:
            await message.answer("Введи число от 1 до 1440 минут.")
            return

        awaiting_custom.discard(message.from_user.id)
        state.set("interval", minutes * 60)
        engine.wake()

        await message.answer(
            f"✅ Интервал установлен: <b>{minutes} мин.</b>",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )

    @dp.callback_query(F.data == "blocks_menu")
    async def open_blocks(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.message.edit_text(
            "⚙️ <b>Блоки прайса</b>\n\n"
            "Выключенный блок остаётся на своём месте, но без цен.",
            parse_mode="HTML",
            reply_markup=blocks_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("block:"))
    async def toggle_block(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        key = callback.data.split(":", 1)[1]
        state.toggle_block(key)

        # Перерисуем структуру из текущего состояния при следующей синхронизации.
        # Навигацию можно обновить сразу.
        await engine.ensure_navigation()

        await callback.message.edit_text(
            "⚙️ <b>Блоки прайса</b>",
            parse_mode="HTML",
            reply_markup=blocks_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data.in_({"blocks_all_on", "blocks_all_off"}))
    async def blocks_all(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        value = callback.data == "blocks_all_on"
        blocks = state.get("blocks") or {}
        for key in list(blocks):
            blocks[key] = value
        state.set("blocks", blocks)
        await engine.ensure_navigation()

        await callback.message.edit_text(
            "⚙️ <b>Блоки прайса</b>",
            parse_mode="HTML",
            reply_markup=blocks_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "status")
    async def status(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.message.edit_text(
            status_text(state),
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )
        await callback.answer()

    await dp.start_polling(bot)
