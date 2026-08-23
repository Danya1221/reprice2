import asyncio
from contextlib import suppress
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from parser import BLOCK_TITLES

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def main_menu(state):
    enabled = state.get("sync_enabled", True)
    auth_status = state.get("tg_auth_status", "starting")

    rows = []

    if auth_status != "ready":
        labels = {
            "starting": "⏳ Telegram подключается…",
            "waiting_instance": "⏳ Жду предыдущий Railway…",
            "login_required": "🔐 Войти в Telegram",
            "phone_required": "📞 Жду номер телефона",
            "code_required": "🔢 Жду код Telegram",
            "password_required": "🔑 Нужен пароль 2FA",
            "error": "🔐 Переподключить Telegram",
        }

        rows.append([
            InlineKeyboardButton(
                text=labels.get(auth_status, "🔐 Войти в Telegram"),
                callback_data=(
                    "tg_login_qr"
                    if auth_status not in {"starting", "waiting_instance", "phone_required", "code_required", "password_required"}
                    else "tg_auth_info"
                ),
            )
        ])

    rows.extend([
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
                text=f"💰 Наценка +{int(state.get('markup_amount', 0) or 0)}",
                callback_data="markup_menu",
            ),
            InlineKeyboardButton(
                text="📊 Статус",
                callback_data="status",
            ),
        ],
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=rows
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


def markup_menu(state):
    current = int(state.get("markup_amount", 0) or 0)

    values = (
        (0, "Без наценки"),
        (500, "+500"),
        (1000, "+1000"),
        (1500, "+1500"),
        (2000, "+2000"),
        (3000, "+3000"),
        (5000, "+5000"),
    )

    rows = []

    for amount, label in values:
        mark = "✅ " if current == amount else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{mark}{label}",
                callback_data=f"markup:{amount}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="✍️ Своя сумма",
            callback_data="custom_markup",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data="home",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def blocks_menu(state):
    blocks = state.get("blocks") or {}
    discovered = state.get("discovered_blocks") or {}

    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if blocks.get('12', False) else '❌'} 12",
                callback_data="block:12",
            ),
            InlineKeyboardButton(
                text=f"{'✅' if blocks.get('13', False) else '❌'} 13",
                callback_data="block:13",
            ),
            InlineKeyboardButton(
                text=f"{'✅' if blocks.get('14', False) else '❌'} 14",
                callback_data="block:14",
            ),
        ],
    ]

    for gen in ("15", "16", "17"):
        rows.append([
            InlineKeyboardButton(
                text=f"{'✅' if blocks.get(f'{gen}_sim', True) else '❌'} {gen} SIM",
                callback_data=f"block:{gen}_sim",
            ),
            InlineKeyboardButton(
                text=f"{'✅' if blocks.get(f'{gen}_esim', True) else '❌'} {gen} eSIM",
                callback_data=f"block:{gen}_esim",
            ),
        ])

    # Real automatically discovered blocks.
    if discovered:
        for key in state.get("block_order") or []:
            if key not in discovered:
                continue

            title = (
                str(discovered.get(key, key))
                .replace("📦", "")
                .strip()
            )

            rows.append([
                InlineKeyboardButton(
                    text=f"{'✅' if blocks.get(key, True) else '❌'} {title}",
                    callback_data=f"block:{key}",
                )
            ])

    # Unknown products still have a fallback block.
    rows.append([
        InlineKeyboardButton(
            text=f"{'✅' if blocks.get('other', False) else '❌'} Другое",
            callback_data="block:other",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            text="✅ Включить все",
            callback_data="blocks_all_on",
        ),
        InlineKeyboardButton(
            text="❌ Выключить все",
            callback_data="blocks_all_off",
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            text="↕️ Порядок блоков",
            callback_data="order_menu",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data="home",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


ORDER_LABELS = {
    "12": "iPhone 12",
    "13": "iPhone 13",
    "14": "iPhone 14",
    "15_sim": "15 SIM",
    "15_esim": "15 eSIM",
    "16_sim": "16 SIM",
    "16_esim": "16 eSIM",
    "17_sim": "17 SIM",
    "17_esim": "17 eSIM",
    "other": "Остальное",
}


def order_menu(state):
    discovered = state.get("discovered_blocks") or {}

    labels = dict(ORDER_LABELS)
    labels.update({
        key: (
            str(title)
            .replace("📦", "")
            .replace("🍏", "")
            .strip()
        )
        for key, title in discovered.items()
    })

    order = state.get("block_order") or list(labels)

    # Keep only valid keys, while preserving the user's saved order.
    valid_keys = set(ORDER_LABELS) | set(discovered)
    order = [
        key
        for key in order
        if key in valid_keys
    ]

    for key in labels:
        if key not in order:
            order.append(key)

    rows = []

    for index, key in enumerate(order):
        rows.append([
            InlineKeyboardButton(
                text="⬆️",
                callback_data=f"order_up:{key}",
            ),
            InlineKeyboardButton(
                text=f"{index + 1}. {labels.get(key, key)}",
                callback_data="order_noop",
            ),
            InlineKeyboardButton(
                text="⬇️",
                callback_data=f"order_down:{key}",
            ),
        ])

    rows.append([
        InlineKeyboardButton(
            text="✅ Применить порядок",
            callback_data="apply_order",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="↩️ Сбросить порядок",
            callback_data="reset_order",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="← К блокам",
            callback_data="blocks_menu",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def fmt_time(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), MOSCOW_TZ).strftime("%d.%m %H:%M:%S МСК")


def status_text(state):
    enabled = state.get("sync_enabled", True)
    interval = int(state.get("interval", 1800))
    minutes = interval // 60

    supplier_status = state.get("supplier_status", "unknown")
    schedule_closed = state.get("schedule_closed", False)

    supplier_labels = {
        "open": "🟢 открыт",
        "closed": "🔴 закрыт",
        "error": "⚠️ ошибка связи",
        "unknown": "⚪ ещё не проверен",
    }

    now = datetime.now(MOSCOW_TZ)
    work_open = 10 <= now.hour < 20

    if work_open:
        schedule_label = "🟢 работаем до 20:00 МСК"
    else:
        schedule_label = "🌙 закрыто до 10:00 МСК"

    auth_status = state.get("tg_auth_status", "starting")
    auth_labels = {
        "ready": f"✅ подключен @{state.get('tg_auth_user')}" if state.get("tg_auth_user") else "✅ подключен",
        "starting": "⏳ подключается",
        "waiting_instance": "⏳ ждёт предыдущий Railway",
        "login_required": "❌ нужен вход",
        "phone_required": "📞 жду номер телефона",
        "code_required": "🔢 жду код Telegram",
        "password_required": "🔑 нужен пароль 2FA",
        "error": "⚠️ ошибка подключения",
    }
    auth_label = auth_labels.get(auth_status, auth_status)

    return (
        "📊 <b>Статус прайса</b>\n\n"
        f"{'🟢' if enabled else '🔴'} Синхронизация: "
        f"<b>{'включена' if enabled else 'остановлена'}</b>\n"
        f"⏱ Интервал: <b>{minutes} мин.</b>\n"
        f"💰 Наценка: <b>+{int(state.get('markup_amount', 0) or 0)}</b>\n"
        f"🔐 Telegram: <b>{auth_label}</b>\n"
        f"🕒 Наш график: <b>{schedule_label}</b>\n"
        f"🏪 Поставщик: <b>{supplier_labels.get(supplier_status, '⚪ ещё не проверен')}</b>\n"
        f"🕒 Последняя проверка: <b>{fmt_time(state.get('last_check_ts'))}</b>\n"
        f"🔜 Следующая проверка: <b>{fmt_time(state.get('next_check_ts'))}</b>\n"
        f"ℹ️ Последний результат: <b>{state.get('last_result', '—')}</b>"
    )


async def run_control_bot(token, admin_id, state, engine):
    bot = Bot(token)
    dp = Dispatcher()

    awaiting_custom = set()
    awaiting_custom_markup = set()
    awaiting_2fa = set()
    awaiting_phone = set()
    awaiting_code = set()

    def allowed(user_id):
        return int(user_id) == int(admin_id)

    async def deny(target):
        if isinstance(target, CallbackQuery):
            await target.answer("Нет доступа", show_alert=True)
        else:
            await target.answer("Нет доступа.")

    @dp.callback_query(F.data == "tg_auth_info")
    async def tg_auth_info(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        status = state.get("tg_auth_status", "starting")

        messages = {
            "starting": "Telegram ещё подключается.",
            "waiting_instance": (
                "Жду завершения предыдущего Railway-инстанса. "
                "Это защита Telegram-сессии."
            ),
            "phone_required": "Пришли номер телефона одним сообщением.",
            "code_required": "Пришли код Telegram одним сообщением.",
            "password_required": "Пришли пароль 2FA одним сообщением.",
        }

        await callback.answer(
            messages.get(status, "Подожди несколько секунд."),
            show_alert=True,
        )

    @dp.callback_query(F.data == "tg_login_qr")
    async def tg_login_start(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        if state.get("tg_auth_status") == "ready":
            await callback.answer("Telegram уже подключен.")
            return

        awaiting_phone.add(callback.from_user.id)
        awaiting_code.discard(callback.from_user.id)
        awaiting_2fa.discard(callback.from_user.id)

        state.update(
            tg_auth_status="phone_required",
            last_result="Жду номер телефона для входа в Telegram.",
        )

        await callback.message.edit_text(
            "🔐 <b>Вход в Telegram</b>\n\n"
            "Пришли сюда номер телефона Telegram одним сообщением.\n"
            "Например: <code>+31612345678</code>",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )

        await callback.answer()

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
        awaiting_custom_markup.discard(callback.from_user.id)
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

    @dp.callback_query(F.data == "markup_menu")
    async def open_markup(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.message.edit_text(
            "💰 <b>Наценка к цене поставщика</b>\n\n"
            "Например: поставщик 80500, наценка +2000 → в канале 82500.",
            parse_mode="HTML",
            reply_markup=markup_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("markup:"))
    async def set_markup(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        amount = int(callback.data.split(":", 1)[1])

        await engine.set_markup(amount)

        await callback.message.edit_text(
            f"✅ Наценка установлена: <b>+{amount}</b>",
            parse_mode="HTML",
            reply_markup=markup_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "custom_markup")
    async def custom_markup(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        awaiting_custom_markup.add(callback.from_user.id)
        awaiting_custom.discard(callback.from_user.id)

        await callback.message.edit_text(
            "✍️ Пришли сумму наценки одним числом.\n\n"
            "Например: <code>2500</code>",
            parse_mode="HTML",
        )
        await callback.answer()

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
    async def custom_value_message(message: Message):
        if not allowed(message.from_user.id):
            return

        user_id = message.from_user.id
        raw = (message.text or "").strip()

        # Telegram login: phone
        if user_id in awaiting_phone:
            phone = raw.replace(" ", "")

            if not phone.startswith("+") or len(phone) < 8:
                await message.answer(
                    "Номер должен быть в международном формате.\n"
                    "Например: <code>+31612345678</code>",
                    parse_mode="HTML",
                )
                return

            try:
                await engine.begin_phone_login(phone)

                awaiting_phone.discard(user_id)
                awaiting_code.add(user_id)

                with suppress(Exception):
                    await message.delete()

                await bot.send_message(
                    user_id,
                    "✅ Код отправлен Telegram.\n\n"
                    "Пришли сюда <b>код из Telegram</b> одним сообщением.",
                    parse_mode="HTML",
                    reply_markup=main_menu(state),
                )

            except Exception as e:
                state.update(
                    tg_auth_status="login_required",
                    last_result=f"Ошибка входа: {e}",
                )
                awaiting_phone.discard(user_id)

                await message.answer(
                    f"❌ Не удалось отправить код:\n<code>{e}</code>",
                    parse_mode="HTML",
                    reply_markup=main_menu(state),
                )
            return

        # Telegram login: code
        if user_id in awaiting_code:
            code = raw.replace(" ", "").replace("-", "")

            if not code.isdigit():
                await message.answer("Пришли только цифры кода Telegram.")
                return

            with suppress(Exception):
                await message.delete()

            try:
                result = await engine.submit_login_code(code)

                awaiting_code.discard(user_id)

                if result == "password_required":
                    awaiting_2fa.add(user_id)

                    await bot.send_message(
                        user_id,
                        "🔑 На аккаунте включена двухэтапная защита.\n\n"
                        "Пришли сюда <b>пароль 2FA</b> одним сообщением. "
                        "Сообщение будет удалено после чтения.",
                        parse_mode="HTML",
                        reply_markup=main_menu(state),
                    )
                else:
                    await bot.send_message(
                        user_id,
                        "✅ <b>Telegram подключен.</b>\n"
                        "Синхронизация запущена автоматически.",
                        parse_mode="HTML",
                        reply_markup=main_menu(state),
                    )

            except Exception as e:
                state.update(
                    tg_auth_status="login_required",
                    last_result=f"Ошибка кода Telegram: {e}",
                )
                awaiting_code.discard(user_id)

                await bot.send_message(
                    user_id,
                    f"❌ Код не принят:\n<code>{e}</code>\n\n"
                    "Нажми «🔐 Войти в Telegram» и начни ещё раз.",
                    parse_mode="HTML",
                    reply_markup=main_menu(state),
                )
            return

        # Telegram login: 2FA
        if user_id in awaiting_2fa:
            password = raw

            with suppress(Exception):
                await message.delete()

            try:
                await engine.submit_2fa(password)
                awaiting_2fa.discard(user_id)

                await bot.send_message(
                    user_id,
                    "✅ <b>Telegram подключен.</b>\n"
                    "Синхронизация запущена автоматически.",
                    parse_mode="HTML",
                    reply_markup=main_menu(state),
                )

            except Exception as e:
                await bot.send_message(
                    user_id,
                    f"❌ Пароль 2FA не принят:\n<code>{e}</code>",
                    parse_mode="HTML",
                    reply_markup=main_menu(state),
                )
            return

        # Custom markup
        if user_id in awaiting_custom_markup:
            try:
                amount = int(raw.replace(" ", ""))
                if amount < 0 or amount > 1_000_000:
                    raise ValueError
            except ValueError:
                await message.answer(
                    "Введи сумму числом от 0 до 1000000."
                )
                return

            awaiting_custom_markup.discard(user_id)

            await engine.set_markup(amount)

            await message.answer(
                f"✅ Наценка установлена: <b>+{amount}</b>",
                parse_mode="HTML",
                reply_markup=main_menu(state),
            )
            return

        # Custom interval
        if user_id in awaiting_custom:
            try:
                minutes = int(raw)
                if minutes < 1 or minutes > 1440:
                    raise ValueError
            except ValueError:
                await message.answer(
                    "Введи число от 1 до 1440 минут."
                )
                return

            awaiting_custom.discard(user_id)
            state.set("interval", minutes * 60)
            engine.wake()

            await message.answer(
                f"✅ Интервал установлен: <b>{minutes} мин.</b>",
                parse_mode="HTML",
                reply_markup=main_menu(state),
            )
            return


    @dp.callback_query(F.data == "blocks_menu")
    async def open_blocks(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.message.edit_text(
            "⚙️ <b>Блоки прайса</b>\n\n"
            "Весь прайс всегда загружается. Здесь выбираешь только то, что показывать в канале.",
            parse_mode="HTML",
            reply_markup=blocks_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("block:"))
    async def toggle_block(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        key = callback.data.split(":", 1)[1]

        current = (state.get("blocks") or {}).get(key, False)
        new_value = not current

        await engine.set_block_enabled(
            key,
            new_value,
        )

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

        await engine.set_all_blocks(value)

        await callback.message.edit_text(
            "⚙️ <b>Блоки прайса</b>",
            parse_mode="HTML",
            reply_markup=blocks_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "order_menu")
    async def open_order_menu(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.message.edit_text(
            "↕️ <b>Порядок блоков</b>\n\n"
            "Стрелками выставь порядок, затем нажми «Применить порядок».",
            parse_mode="HTML",
            reply_markup=order_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "order_noop")
    async def order_noop(callback: CallbackQuery):
        await callback.answer()

    @dp.callback_query(F.data.startswith("order_up:"))
    async def order_up(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        key = callback.data.split(":", 1)[1]
        engine.move_block(key, -1)

        await callback.message.edit_reply_markup(
            reply_markup=order_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("order_down:"))
    async def order_down(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        key = callback.data.split(":", 1)[1]
        engine.move_block(key, 1)

        await callback.message.edit_reply_markup(
            reply_markup=order_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "reset_order")
    async def reset_order(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        engine.reset_block_order()

        await callback.message.edit_reply_markup(
            reply_markup=order_menu(state),
        )
        await callback.answer("Порядок сброшен")

    @dp.callback_query(F.data == "apply_order")
    async def apply_order(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.answer("Применяю порядок…")

        await callback.message.edit_text(
            "🔃 <b>Перестраиваю сообщения в канале…</b>",
            parse_mode="HTML",
        )

        try:
            await engine.rebuild_block_order()

            await callback.message.edit_text(
                "✅ <b>Новый порядок применён</b>",
                parse_mode="HTML",
                reply_markup=order_menu(state),
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ <b>Ошибка перестройки</b>\n\n{e}",
                parse_mode="HTML",
                reply_markup=order_menu(state),
            )

    @dp.callback_query(F.data == "rebuild_order")
    async def rebuild_order(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)

        await callback.answer("Перестраиваю блоки…")

        await callback.message.edit_text(
            "🔃 <b>Перестраиваю порядок блоков…</b>",
            parse_mode="HTML",
        )

        try:
            await engine.rebuild_block_order()

            await callback.message.edit_text(
                "✅ <b>Порядок перестроен</b>",
                parse_mode="HTML",
                reply_markup=blocks_menu(state),
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ <b>Ошибка перестройки</b>\n\n{e}",
                parse_mode="HTML",
                reply_markup=blocks_menu(state),
            )

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
