import asyncio
import html
from contextlib import suppress
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def main_menu(state):
    enabled = bool(state.get("sync_enabled", True))
    rows = []

    if state.get("tg_auth_status") != "ready":
        rows.append([
            InlineKeyboardButton(text="🔐 Войти в Telegram", callback_data="tg_login")
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                text="⏸ Остановить" if enabled else "▶️ Запустить",
                callback_data="toggle_sync",
            ),
            InlineKeyboardButton(text="🔄 Обновить сейчас", callback_data="sync_now"),
        ],
        [
            InlineKeyboardButton(text="⏱ Интервал", callback_data="interval_menu"),
            InlineKeyboardButton(
                text=f"💰 Наценка +{int(state.get('markup_amount', 0) or 0)}",
                callback_data="markup_menu",
            ),
        ],
        [InlineKeyboardButton(text="📊 Статус", callback_data="status")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def interval_menu(state):
    current = int(state.get("interval", 1800) or 1800)
    values = ((300, "5 мин"), (900, "15 мин"), (1800, "30 мин"), (3600, "1 час"), (7200, "2 часа"))
    rows = []
    for seconds, label in values:
        mark = "✅ " if current == seconds else ""
        rows.append([InlineKeyboardButton(text=mark + label, callback_data=f"interval:{seconds}")])
    rows.append([InlineKeyboardButton(text="✍️ Свой интервал", callback_data="custom_interval")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def markup_menu(state):
    current = int(state.get("markup_amount", 0) or 0)
    values = (0, 300, 500, 1000, 1500, 2000, 3000, 5000)
    rows = []
    for amount in values:
        label = "Без наценки" if amount == 0 else f"+{amount}"
        mark = "✅ " if current == amount else ""
        rows.append([InlineKeyboardButton(text=mark + label, callback_data=f"markup:{amount}")])
    rows.append([InlineKeyboardButton(text="✍️ Своя сумма", callback_data="custom_markup")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fmt_time(ts):
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts), MOSCOW_TZ).strftime("%d.%m %H:%M:%S МСК")
    except Exception:
        return "—"


def _source_line(state, slot):
    status = state.get(f"supplier{slot}_status", "unknown")
    labels = {
        "open": "🟢 открыт",
        "closed": "🔴 закрыт",
        "error": "⚠️ ошибка",
        "unknown": "⚪ не проверен",
    }
    count = int(state.get(f"supplier{slot}_count", 0) or 0)
    error = str(state.get(f"supplier{slot}_last_error", "") or "").strip()
    line = f"Поставщик {slot}: <b>{labels.get(status, status)}</b>"
    if status == "open":
        line += f" · {count} позиций"
    if error:
        line += f"\n└ <code>{html.escape(error[:300])}</code>"
    return line


def status_text(state):
    enabled = bool(state.get("sync_enabled", True))
    auth_status = state.get("tg_auth_status", "starting")
    auth_user = state.get("tg_auth_user", "")
    auth_labels = {
        "ready": f"✅ @{auth_user}" if auth_user else "✅ подключен",
        "starting": "⏳ подключается",
        "waiting_instance": "⏳ ждёт предыдущий Railway",
        "login_required": "❌ нужен вход",
        "code_required": "🔢 ждёт код",
        "password_required": "🔑 ждёт 2FA",
        "error": "⚠️ ошибка",
    }

    pub = state.get("publication_status", "unknown")
    pub_labels = {
        "open": "🟢 опубликован",
        "closed": "🔴 продажи закрыты",
        "waiting": "🌙 ждём 10:00 / двух открытых поставщиков",
        "error": "⚠️ оставлен последний корректный прайс",
        "unknown": "⚪ ещё не опубликован",
    }

    interval = int(state.get("interval", 1800) or 1800)
    return (
        "📊 <b>Статус двух прайсов</b>\n\n"
        f"{'🟢' if enabled else '🔴'} Автосинхронизация: <b>{'включена' if enabled else 'остановлена'}</b>\n"
        f"🔐 Telegram: <b>{auth_labels.get(auth_status, auth_status)}</b>\n"
        f"⏱ Интервал: <b>{interval // 60} мин.</b>\n"
        f"💰 Наценка: <b>+{int(state.get('markup_amount', 0) or 0)}</b>\n"
        f"📣 Публикация: <b>{pub_labels.get(pub, pub)}</b>\n\n"
        f"{_source_line(state, 1)}\n"
        f"{_source_line(state, 2)}\n\n"
        f"🕓 Последняя проверка: <b>{fmt_time(state.get('last_check_ts'))}</b>\n"
        f"⏭ Следующая: <b>{fmt_time(state.get('next_check_ts'))}</b>\n\n"
        f"Последний результат:\n<code>{html.escape(str(state.get('last_result', '—'))[:1200])}</code>"
    )


async def run_control_bot(token, admin_id, state, runtime):
    bot = Bot(token)
    dp = Dispatcher()

    awaiting_phone = set()
    awaiting_code = set()
    awaiting_2fa = set()
    awaiting_interval = set()
    awaiting_markup = set()

    def allowed(user_id):
        return int(user_id) == int(admin_id)

    async def deny(event):
        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("Нет доступа")

    @dp.message(CommandStart())
    async def start_cmd(message: Message):
        if not allowed(message.from_user.id):
            return await deny(message)
        await message.answer(
            "⚙️ <b>Управление двумя прайсами</b>",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )

    @dp.callback_query(F.data == "home")
    async def home(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        await callback.message.edit_text(
            "⚙️ <b>Управление двумя прайсами</b>",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )
        await callback.answer()

    @dp.callback_query(F.data == "toggle_sync")
    async def toggle_sync(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        enabled = not bool(state.get("sync_enabled", True))
        state.set("sync_enabled", enabled)
        runtime.wake()
        await callback.answer("Запущено" if enabled else "Остановлено")

        if enabled and runtime.ready:
            try:
                await runtime.sync_once(forced=True)
            except Exception as e:
                state.set("last_result", f"ошибка запуска: {e}")

        await callback.message.edit_text(
            "⚙️ <b>Управление двумя прайсами</b>",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )

    @dp.callback_query(F.data == "sync_now")
    async def sync_now(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        await callback.answer("Запрашиваю оба прайса…")
        try:
            result = await runtime.sync_once(forced=True)
            count = result.get("count") if isinstance(result, dict) else None
            text = "✅ <b>Проверка завершена</b>"
            if count is not None:
                text += f"\nИтоговых позиций: <b>{count}</b>"
        except Exception as e:
            text = f"❌ <b>Ошибка</b>\n<code>{html.escape(str(e))}</code>"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu(state))

    @dp.callback_query(F.data == "status")
    async def status(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        await callback.message.edit_text(status_text(state), parse_mode="HTML", reply_markup=main_menu(state))
        await callback.answer()

    @dp.callback_query(F.data == "interval_menu")
    async def open_interval(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        await callback.message.edit_text("⏱ <b>Интервал обновления</b>", parse_mode="HTML", reply_markup=interval_menu(state))
        await callback.answer()

    @dp.callback_query(F.data.startswith("interval:"))
    async def set_interval(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        seconds = int(callback.data.split(":", 1)[1])
        state.set("interval", seconds)
        runtime.wake()
        await callback.message.edit_text("⏱ <b>Интервал обновления</b>", parse_mode="HTML", reply_markup=interval_menu(state))
        await callback.answer("Сохранено")

    @dp.callback_query(F.data == "custom_interval")
    async def custom_interval(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        awaiting_interval.add(callback.from_user.id)
        await callback.message.edit_text("Пришли интервал в минутах: от 1 до 1440.", reply_markup=main_menu(state))
        await callback.answer()

    @dp.callback_query(F.data == "markup_menu")
    async def open_markup(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        await callback.message.edit_text("💰 <b>Наценка</b>", parse_mode="HTML", reply_markup=markup_menu(state))
        await callback.answer()

    @dp.callback_query(F.data.startswith("markup:"))
    async def set_markup(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        amount = int(callback.data.split(":", 1)[1])
        await runtime.set_markup(amount)
        await callback.message.edit_text("💰 <b>Наценка</b>", parse_mode="HTML", reply_markup=markup_menu(state))
        await callback.answer("Сохранено")

    @dp.callback_query(F.data == "custom_markup")
    async def custom_markup(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        awaiting_markup.add(callback.from_user.id)
        await callback.message.edit_text("Пришли наценку числом, например <code>500</code>.", parse_mode="HTML", reply_markup=main_menu(state))
        await callback.answer()

    @dp.callback_query(F.data == "tg_login")
    async def tg_login(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return await deny(callback)
        awaiting_phone.add(callback.from_user.id)
        await callback.message.edit_text(
            "📞 Пришли номер Telegram в международном формате, например <code>+79991234567</code>.",
            parse_mode="HTML",
            reply_markup=main_menu(state),
        )
        await callback.answer()

    @dp.message()
    async def text_input(message: Message):
        if not allowed(message.from_user.id):
            return await deny(message)
        uid = message.from_user.id
        raw = (message.text or "").strip()

        if uid in awaiting_phone:
            with suppress(Exception):
                await message.delete()
            try:
                result = await runtime.begin_phone_login(raw)
                awaiting_phone.discard(uid)
                if result == "ready":
                    await bot.send_message(uid, "✅ Telegram уже подключен.", reply_markup=main_menu(state))
                else:
                    awaiting_code.add(uid)
                    await bot.send_message(uid, "🔢 Пришли код Telegram цифрами.", reply_markup=main_menu(state))
            except Exception as e:
                awaiting_phone.discard(uid)
                await bot.send_message(uid, f"❌ <code>{html.escape(str(e))}</code>", parse_mode="HTML", reply_markup=main_menu(state))
            return

        if uid in awaiting_code:
            code = raw.replace(" ", "").replace("-", "")
            with suppress(Exception):
                await message.delete()
            try:
                result = await runtime.submit_login_code(code)
                awaiting_code.discard(uid)
                if result == "password_required":
                    awaiting_2fa.add(uid)
                    await bot.send_message(uid, "🔑 Пришли пароль 2FA.", reply_markup=main_menu(state))
                else:
                    await bot.send_message(uid, "✅ Telegram подключен.", reply_markup=main_menu(state))
            except Exception as e:
                awaiting_code.discard(uid)
                await bot.send_message(uid, f"❌ <code>{html.escape(str(e))}</code>", parse_mode="HTML", reply_markup=main_menu(state))
            return

        if uid in awaiting_2fa:
            with suppress(Exception):
                await message.delete()
            try:
                await runtime.submit_2fa(raw)
                awaiting_2fa.discard(uid)
                await bot.send_message(uid, "✅ Telegram подключен.", reply_markup=main_menu(state))
            except Exception as e:
                await bot.send_message(uid, f"❌ <code>{html.escape(str(e))}</code>", parse_mode="HTML", reply_markup=main_menu(state))
            return

        if uid in awaiting_interval:
            try:
                minutes = int(raw)
                if not 1 <= minutes <= 1440:
                    raise ValueError
            except ValueError:
                await message.answer("Нужно число от 1 до 1440.")
                return
            awaiting_interval.discard(uid)
            state.set("interval", minutes * 60)
            runtime.wake()
            await message.answer(f"✅ Интервал: <b>{minutes} мин.</b>", parse_mode="HTML", reply_markup=main_menu(state))
            return

        if uid in awaiting_markup:
            try:
                amount = int(raw.replace(" ", ""))
                if not 0 <= amount <= 1_000_000:
                    raise ValueError
            except ValueError:
                await message.answer("Нужно число от 0 до 1000000.")
                return
            awaiting_markup.discard(uid)
            await runtime.set_markup(amount)
            await message.answer(f"✅ Наценка: <b>+{amount}</b>", parse_mode="HTML", reply_markup=main_menu(state))
            return

    await dp.start_polling(bot)
