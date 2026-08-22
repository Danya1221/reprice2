import asyncio
import html
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import suppress

from telethon import events
from telethon.errors import AuthKeyDuplicatedError, MessageNotModifiedError

from config import (
    AFTER_ACTION_DELAY,
    BUTTON_PATH,
    CLOSED_TEXT,
    CONTROL_BOT_TOKEN,
    REQUEST_TEXT,
    RESPONSE_TIMEOUT,
    SUPPLIER_BOT,
    TARGET_CHANNEL,
)
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from parser import BLOCK_KEYS, BLOCK_TITLES, IPHONE_SECTION_TITLES, add_markup_to_line, closed_match, display_product_line, group_generation_lines, parse_full_price


DISPLAY_BLOCK_ORDER = (
    "12",
    "13",
    "14",
    "15_sim",
    "15_esim",
    "16_sim",
    "16_esim",
    "17_sim",
    "17_esim",
    "other",
)


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
WORK_START_HOUR = 10
WORK_END_HOUR = 20


class PriceSyncEngine:
    def __init__(self, client, state, target_entity):
        self.client = client
        self.state = state
        self.target = target_entity
        self.bot = Bot(CONTROL_BOT_TOKEN) if CONTROL_BOT_TOKEN else None
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()

    def block_order(self):
        saved = self.state.get("block_order") or []
        order = []

        for key in saved:
            if key in DISPLAY_BLOCK_ORDER and key not in order:
                order.append(key)

        for key in DISPLAY_BLOCK_ORDER:
            if key not in order:
                order.append(key)

        return order

    def is_work_hours(self, now=None):
        now = now or datetime.now(MOSCOW_TZ)
        return WORK_START_HOUR <= now.hour < WORK_END_HOUR

    def seconds_until_schedule_boundary(self, now=None):
        now = now or datetime.now(MOSCOW_TZ)

        if self.is_work_hours(now):
            boundary = now.replace(
                hour=WORK_END_HOUR,
                minute=0,
                second=0,
                microsecond=0,
            )
        else:
            if now.hour >= WORK_END_HOUR:
                boundary = (
                    now + timedelta(days=1)
                ).replace(
                    hour=WORK_START_HOUR,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            else:
                boundary = now.replace(
                    hour=WORK_START_HOUR,
                    minute=0,
                    second=0,
                    microsecond=0,
                )

        return max(1, int((boundary - now).total_seconds()))

    async def mark_session_invalid(self, error=None):
        """
        Telegram уже аннулировал SESSION_STRING.
        Останавливаем синхронизацию и не уходим в reconnect-loop.
        """
        message = (
            "SESSION_STRING аннулирован Telegram "
            "(AuthKeyDuplicatedError). Нужна новая Telethon-сессия."
        )

        if error:
            message = f"{message} {error}"

        self.state.update(
            sync_enabled=False,
            supplier_status="error",
            last_result=message,
            next_check_ts=None,
        )

        self.wakeup.set()

        print(f"❌ {message}", flush=True)

        return False

    async def ensure_connected(self):
        """
        Гарантирует рабочее соединение Telethon.
        AuthKeyDuplicatedError не ретраим: такой ключ Telegram уже уничтожил.
        """
        if self.client.is_connected():
            return True

        print("🔌 Telethon отключён — переподключаюсь…", flush=True)

        try:
            await self.client.connect()

            if not await self.client.is_user_authorized():
                raise RuntimeError(
                    "Telegram-сессия больше не авторизована. "
                    "Нужно обновить SESSION_STRING."
                )

            print("✅ Telethon снова подключён", flush=True)
            return True

        except AuthKeyDuplicatedError as e:
            await self.mark_session_invalid(e)
            raise RuntimeError(
                "SESSION_STRING аннулирован Telegram. "
                "Создай новый SESSION_STRING."
            ) from e

        except Exception as e:
            self.state.update(
                supplier_status="error",
                last_result=f"ошибка подключения Telegram: {e}",
            )
            print(f"❌ Не удалось переподключить Telethon: {e}", flush=True)
            raise


    async def telegram_call(self, awaitable_factory, operation="Telegram-запрос"):
        """
        Выполняет Telethon-запрос с одной попыткой reconnect.
        AuthKeyDuplicatedError сразу останавливает синхронизацию.
        """
        await self.ensure_connected()

        try:
            return await awaitable_factory()

        except AuthKeyDuplicatedError as e:
            await self.mark_session_invalid(e)
            raise RuntimeError(
                "SESSION_STRING аннулирован Telegram. "
                "Создай новый SESSION_STRING."
            ) from e

        except Exception as e:
            message = str(e).casefold()

            disconnected = (
                "cannot send requests while disconnected" in message
                or "disconnected" in message
                or ("connection" in message and "closed" in message)
                or "0 bytes read" in message
            )

            if not disconnected:
                raise

            print(
                f"🔌 {operation}: соединение потеряно, повторяю после reconnect…",
                flush=True,
            )

            try:
                await self.client.disconnect()
            except Exception:
                pass

            await asyncio.sleep(1)

            try:
                await self.ensure_connected()
                return await awaitable_factory()

            except AuthKeyDuplicatedError as e2:
                await self.mark_session_invalid(e2)
                raise RuntimeError(
                    "SESSION_STRING аннулирован Telegram. "
                    "Создай новый SESSION_STRING."
                ) from e2


    async def start(self):
        await self.ensure_structure()

        # Старую навигацию удаляем только после успешного создания новой.
        await self.ensure_navigation(force_bottom=True)

        self.client.add_event_handler(
            self.on_supplier_edit,
            events.MessageEdited(chats=SUPPLIER_BOT),
        )

    def _message_link(self, message_id: int):
        target = str(TARGET_CHANNEL).strip()

        if target.startswith("@"):
            return f"https://t.me/{target[1:]}/{message_id}"

        if target.startswith("-100"):
            internal = target[4:]
            return f"https://t.me/c/{internal}/{message_id}"

        if target and not target.lstrip("-").isdigit():
            return f"https://t.me/{target}/{message_id}"

        return None

    async def _find_message_by_title(self, title):
        async for msg in self.client.iter_messages(self.target, limit=150):
            if (msg.raw_text or "").startswith(title):
                return msg
        return None

    async def ensure_structure(self):
        """
        Создаёт сообщения только для ВКЛЮЧЁННЫХ блоков.

        Выключенный блок:
        - не должен существовать в канале;
        - его message_id удаляется из state.

        Включённый блок:
        - если сообщение уже есть — используем его;
        - если нет — создаём.
        """
        await self.ensure_connected()
        message_ids = dict(self.state.get("message_ids") or {})
        blocks_enabled = self.state.get("blocks") or {}

        for key in self.block_order():
            enabled = blocks_enabled.get(key, False)
            title = BLOCK_TITLES[key]
            saved_id = message_ids.get(key)

            if not enabled:
                # Если блок выключен — физически удаляем сообщение.
                if saved_id:
                    try:
                        await self.client.delete_messages(
                            self.target,
                            [int(saved_id)],
                        )
                        print(
                            f"🗑 Блок выключен, сообщение удалено: {key} / {saved_id}",
                            flush=True,
                        )
                    except Exception as e:
                        print(
                            f"⚠️ Не удалось удалить выключенный блок {key}: {e}",
                            flush=True,
                        )

                message_ids.pop(key, None)
                continue

            msg = None

            if saved_id:
                with suppress(Exception):
                    msg = await self.client.get_messages(
                        self.target,
                        ids=int(saved_id),
                    )

            if not msg:
                msg = await self._find_message_by_title(title)

            if not msg:
                msg = await self.client.send_message(
                    self.target,
                    f"<b>{html.escape(title)}</b>",
                    parse_mode="html",
                    link_preview=False,
                )
                print(
                    f"➕ Создан блок: {key} / message_id={msg.id}",
                    flush=True,
                )

            message_ids[key] = msg.id

        self.state.set("message_ids", message_ids)
        await self.ensure_navigation()

    async def _delete_navigation_messages(self):
        """
        Удаляет текущую навигацию и старые текстовые дубли.
        """
        await self.ensure_connected()
        ids = []

        saved_id = self.state.get("nav_message_id")
        if saved_id:
            try:
                ids.append(int(saved_id))
            except Exception:
                pass

        try:
            async for msg in self.client.iter_messages(
                self.target,
                limit=150,
            ):
                if (msg.raw_text or "").startswith("🧭 Навигация по прайсу"):
                    if msg.id not in ids:
                        ids.append(msg.id)
        except Exception as e:
            print(
                f"⚠️ Не удалось найти старую навигацию: {e}",
                flush=True,
            )

        if ids:
            try:
                await self.client.delete_messages(
                    self.target,
                    ids,
                )
            except Exception as e:
                print(
                    f"⚠️ Не удалось удалить старую навигацию: {e}",
                    flush=True,
                )

        self.state.set("nav_message_id", None)

    def _navigation_markup(self):
        blocks_enabled = self.state.get("blocks") or {}
        message_ids = self.state.get("message_ids") or {}

        labels = {
            "12": "12",
            "13": "13",
            "14": "14",
            "15_sim": "15 SIM",
            "15_esim": "15 eSIM",
            "16_sim": "16 SIM",
            "16_esim": "16 eSIM",
            "17_sim": "17 SIM",
            "17_esim": "17 eSIM",
            "other": "Остальное",
        }

        buttons = []

        for key in self.block_order():
            if not blocks_enabled.get(key, False):
                continue

            message_id = message_ids.get(key)
            if not message_id:
                continue

            link = self._message_link(int(message_id))
            if not link:
                continue

            buttons.append(
                InlineKeyboardButton(
                    text=labels.get(key, key),
                    url=link,
                )
            )

        if not buttons:
            return None

        rows = [
            buttons[i:i + 2]
            for i in range(0, len(buttons), 2)
        ]

        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def ensure_navigation(self, force_bottom=False):
        """
        Навигация должна существовать как одно сообщение с inline-кнопками.

        Если меняется структура и нужно опустить её вниз:
        - удаляем старые навигации;
        - создаём одну новую последним сообщением.

        Ошибка не замалчивается: она попадает в last_result и лог.
        """
        if not self.bot:
            error = "CONTROL_BOT_TOKEN не указан — навигация невозможна"
            self.state.set("last_result", f"ошибка навигации: {error}")
            print(f"❌ {error}", flush=True)
            return False

        keyboard = self._navigation_markup()

        if keyboard is None:
            # Нет активных блоков — навигация не нужна.
            await self._delete_navigation_messages()
            print("🧭 Нет активных блоков — навигация удалена", flush=True)
            return True

        text = (
            "🧭 <b>Навигация по прайсу</b>\n\n"
            "Выберите нужный раздел:"
        )

        nav_id = self.state.get("nav_message_id")

        if nav_id and not force_bottom:
            try:
                await self.bot.edit_message_text(
                    chat_id=TARGET_CHANNEL,
                    message_id=int(nav_id),
                    text=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return True
            except Exception as e:
                if "message is not modified" in str(e).casefold():
                    return True

                print(
                    f"⚠️ Навигация будет пересоздана: {e}",
                    flush=True,
                )

        # Для структурных изменений навигация должна физически быть внизу.
        await self._delete_navigation_messages()

        try:
            msg = await self.bot.send_message(
                chat_id=TARGET_CHANNEL,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            error = (
                "не удалось отправить inline-навигацию; "
                "управляющий бот должен быть админом канала "
                f"с правом публикации. {e}"
            )
            self.state.update(
                nav_message_id=None,
                last_result=f"ошибка навигации: {error}",
            )
            print(f"❌ {error}", flush=True)
            return False

        self.state.set("nav_message_id", msg.message_id)

        button_count = sum(
            len(row)
            for row in keyboard.inline_keyboard
        )

        print(
            f"✅ Навигация создана внизу: message_id={msg.message_id}; "
            f"кнопок={button_count}",
            flush=True,
        )

        return True

    async def _latest_incoming(self, limit=12):
        messages = await self.client.get_messages(SUPPLIER_BOT, limit=limit)
        for msg in messages:
            if not msg.out:
                return msg
        return None

    async def _click_button(self, message, wanted):
        if not message or not message.buttons:
            raise RuntimeError(f"Не нашёл кнопки для шага: {wanted}")

        wanted_norm = " ".join(wanted.split()).casefold()

        for row in message.buttons:
            for button in row:
                label = " ".join((button.text or "").split()).casefold()
                if label == wanted_norm:
                    await button.click()
                    return

        labels = [
            button.text
            for row in message.buttons
            for button in row
            if getattr(button, "text", None)
        ]
        raise RuntimeError(f"Кнопка «{wanted}» не найдена. Есть: {labels}")

    async def _collect_supplier_messages(
        self,
        after_id: int,
        first_timeout: float = None,
        quiet_seconds: float = 2.5,
    ):
        """
        Собирает ВСЕ сообщения поставщика после нашей команды.

        Большой прайс Telegram разбивает на несколько сообщений.
        Раньше мы брали только последнее, поэтому получалось ~32 позиции.
        Теперь ждём первую часть, затем продолжаем собирать сообщения,
        пока поставщик не замолчит на quiet_seconds.
        """
        if first_timeout is None:
            first_timeout = RESPONSE_TIMEOUT

        collected = {}
        loop = asyncio.get_running_loop()
        started = loop.time()
        last_new_at = None

        while True:
            messages = await self.client.get_messages(
                SUPPLIER_BOT,
                limit=100,
            )

            found_new = False

            for msg in messages:
                if msg.out:
                    continue

                if msg.id <= after_id:
                    continue

                if msg.id not in collected:
                    found_new = True

                collected[msg.id] = msg

            now = loop.time()

            if found_new:
                last_new_at = now

            if collected and last_new_at is not None:
                if now - last_new_at >= quiet_seconds:
                    break

            if not collected and now - started >= first_timeout:
                break

            # Общий страховочный предел.
            if now - started >= max(first_timeout + 10, 40):
                break

            await asyncio.sleep(0.7)

        return [
            collected[mid]
            for mid in sorted(collected)
        ]

    async def request_price(self):
        await self.ensure_connected()
        print(f"📡 Запрашиваю прайс у {SUPPLIER_BOT}", flush=True)

        sent = await self.client.send_message(
            SUPPLIER_BOT,
            REQUEST_TEXT,
        )

        # Если у поставщика есть цепочка кнопок, получаем первое сообщение
        # и проходим по кнопкам. После этого всё равно собираем весь пакет.
        if BUTTON_PATH:
            first_batch = await self._collect_supplier_messages(
                after_id=sent.id,
                first_timeout=RESPONSE_TIMEOUT,
                quiet_seconds=1.0,
            )

            if not first_batch:
                raise RuntimeError("Поставщик не прислал ответ")

            response = first_batch[-1]

            for button_name in BUTTON_PATH:
                await self._click_button(
                    response,
                    button_name,
                )
                await asyncio.sleep(AFTER_ACTION_DELAY)

                latest = await self._latest_incoming()
                if latest:
                    response = latest

        messages = await self._collect_supplier_messages(
            after_id=sent.id,
            first_timeout=RESPONSE_TIMEOUT,
            quiet_seconds=2.5,
        )

        if not messages:
            raise RuntimeError("Поставщик не прислал прайс")

        # Берём текст всех частей в правильном порядке.
        parts = []

        for msg in messages:
            text = msg.raw_text or ""
            if text.strip():
                parts.append(text.strip())

        if not parts:
            raise RuntimeError("Поставщик прислал сообщения без текста")

        raw_text = "\n".join(parts)
        ids = [msg.id for msg in messages]

        self.state.update(
            last_supplier_message_id=ids[-1],
            last_supplier_message_ids=ids,
        )

        print(
            f"📥 Получено частей прайса: {len(messages)}; "
            f"символов: {len(raw_text)}",
            flush=True,
        )

        return raw_text

    async def set_markup(self, amount: int):
        """
        Наценка хранится отдельно от исходного прайса.
        После изменения сразу перерисовываем текущие блоки.
        """
        amount = int(amount)

        if amount < 0:
            raise ValueError("Наценка не может быть отрицательной")

        self.state.set("markup_amount", amount)

        full_price = self.state.get("last_full_price", "")

        if (
            full_price
            and not self.state.get("supplier_closed", False)
            and not self.state.get("schedule_closed", False)
            and self.is_work_hours()
        ):
            await self.apply_price(full_price)

        print(f"💰 Наценка установлена: +{amount}", flush=True)

    def move_block(self, key: str, direction: int):
        """
        Меняет сохранённый порядок, но НЕ трогает канал до кнопки
        'Применить порядок'.
        """
        order = self.block_order()

        if key not in order:
            return order

        old_index = order.index(key)
        new_index = old_index + int(direction)

        if new_index < 0 or new_index >= len(order):
            return order

        order[old_index], order[new_index] = order[new_index], order[old_index]
        self.state.set("block_order", order)
        return order

    def reset_block_order(self):
        order = list(DISPLAY_BLOCK_ORDER)
        self.state.set("block_order", order)
        return order

    async def rebuild_block_order(self):
        """
        Один раз перестраивает сообщения прайса в правильном порядке.

        Telegram не умеет менять порядок уже отправленных сообщений,
        поэтому удаляем только наши ценовые блоки + навигацию и создаём
        их заново в DISPLAY_BLOCK_ORDER.

        Закрытое сообщение удаляем и затем восстанавливаем при необходимости.
        """
        await self.ensure_connected()
        blocks_enabled = self.state.get("blocks") or {}
        old_message_ids = dict(self.state.get("message_ids") or {})

        # Удаляем все известные ценовые сообщения.
        ids_to_delete = []

        for key in self.block_order():
            mid = old_message_ids.get(key)
            if mid:
                try:
                    ids_to_delete.append(int(mid))
                except Exception:
                    pass

        nav_id = self.state.get("nav_message_id")
        if nav_id:
            try:
                ids_to_delete.append(int(nav_id))
            except Exception:
                pass

        closed_id = self.state.get("closed_message_id")
        if closed_id:
            try:
                ids_to_delete.append(int(closed_id))
            except Exception:
                pass

        # Убираем дубли ID.
        ids_to_delete = list(dict.fromkeys(ids_to_delete))

        if ids_to_delete:
            try:
                await self.client.delete_messages(
                    self.target,
                    ids_to_delete,
                )
            except Exception as e:
                print(
                    f"⚠️ Не все старые блоки удалось удалить при перестройке: {e}",
                    flush=True,
                )

        self.state.update(
            message_ids={},
            nav_message_id=None,
            closed_message_id=None,
        )

        # Создаём включённые блоки строго по порядку.
        new_ids = {}

        for key in self.block_order():
            if not blocks_enabled.get(key, False):
                continue

            title = BLOCK_TITLES[key]

            msg = await self.client.send_message(
                self.target,
                f"<b>{html.escape(title)}</b>",
                parse_mode="html",
                link_preview=False,
            )

            new_ids[key] = msg.id

        self.state.set("message_ids", new_ids)

        # Сразу заполняем текущими ценами или очищаем, если поставщик закрыт.
        full_price = self.state.get("last_full_price", "")
        supplier_closed = self.state.get("supplier_closed", False)
        schedule_closed = self.state.get("schedule_closed", False)

        if supplier_closed or schedule_closed:
            for key in self.block_order():
                if blocks_enabled.get(key, False):
                    await self._set_block_text(
                        key,
                        [],
                        closed=True,
                    )
        elif full_price:
            parsed = parse_full_price(full_price)
            price_blocks = parsed["blocks"]

            for key in self.block_order():
                if blocks_enabled.get(key, False):
                    await self._set_block_text(
                        key,
                        price_blocks.get(key, []),
                        closed=False,
                    )

        # Навигация всегда физически последняя.
        await self.ensure_navigation(force_bottom=True)

        # Если поставщик сейчас закрыт, сообщение "Продажи закрыты"
        # должно идти после навигации.
        if schedule_closed:
            await self.set_schedule_closed()
        elif supplier_closed:
            await self.set_closed()

        print("🔃 Порядок блоков перестроен", flush=True)

    async def set_block_enabled(self, key: str, enabled: bool):
        """
        Применяет переключатель блока СРАЗУ.

        OFF:
        - удаляет сообщение блока из канала;
        - убирает message_id;
        - убирает кнопку навигации.

        ON:
        - создаёт сообщение блока;
        - сразу заполняет его из последнего сохранённого прайса.
        """
        await self.ensure_connected()
        blocks = dict(self.state.get("blocks") or {})
        blocks[key] = bool(enabled)
        self.state.set("blocks", blocks)

        message_ids = dict(self.state.get("message_ids") or {})
        message_id = message_ids.get(key)

        if not enabled:
            if message_id:
                try:
                    await self.client.delete_messages(
                        self.target,
                        [int(message_id)],
                    )
                    print(
                        f"🗑 Удалён блок {key}: message_id={message_id}",
                        flush=True,
                    )
                except Exception as e:
                    print(
                        f"⚠️ Не удалось удалить блок {key}: {e}",
                        flush=True,
                    )

            message_ids.pop(key, None)
            self.state.set("message_ids", message_ids)
            await self.ensure_navigation(force_bottom=True)
            return

        # Включение.
        await self.ensure_structure()

        full_price = self.state.get("last_full_price", "")

        if (
            full_price
            and not self.state.get("supplier_closed", False)
            and not self.state.get("schedule_closed", False)
            and self.is_work_hours()
        ):
            parsed = parse_full_price(full_price)
            lines = parsed["blocks"].get(key, [])
            await self._set_block_text(
                key,
                lines,
                closed=False,
            )
        else:
            # Если поставщик закрыт — блок создаётся только с заголовком.
            await self._set_block_text(
                key,
                [],
                closed=True,
            )

        await self.ensure_navigation(force_bottom=True)

    async def set_all_blocks(self, enabled: bool):
        """
        Массовое включение/выключение с немедленным применением в канале.
        """
        await self.ensure_connected()
        for key in self.block_order():
            await self.set_block_enabled(key, enabled)

    async def _set_block_text(self, key, lines, closed=False):
        await self.ensure_connected()
        blocks_enabled = self.state.get("blocks") or {}

        # ВЫКЛЮЧЕННЫЙ блок не должен существовать в канале.
        if not blocks_enabled.get(key, False):
            message_ids = dict(self.state.get("message_ids") or {})
            message_id = message_ids.get(key)

            if message_id:
                try:
                    await self.client.delete_messages(
                        self.target,
                        [int(message_id)],
                    )
                except Exception:
                    pass

                message_ids.pop(key, None)
                self.state.set("message_ids", message_ids)

            return

        message_ids = dict(self.state.get("message_ids") or {})
        message_id = message_ids.get(key)

        if not message_id:
            # Создаём только этот включённый блок.
            title = BLOCK_TITLES[key]
            msg = await self.client.send_message(
                self.target,
                f"<b>{html.escape(title)}</b>",
                parse_mode="html",
                link_preview=False,
            )
            message_id = msg.id
            message_ids[key] = msg.id
            self.state.set("message_ids", message_ids)

        title = BLOCK_TITLES[key]

        # При закрытии поставщика оставляем только название блока.
        if closed:
            body = f"<b>{html.escape(title)}</b>"
        else:
            markup_amount = int(self.state.get("markup_amount", 0) or 0)

            generation = None

            if key in {"12", "13", "14"}:
                generation = key
            elif key.startswith("15_"):
                generation = "15"
            elif key.startswith("16_"):
                generation = "16"
            elif key.startswith("17_"):
                generation = "17"

            if generation:
                rendered_parts = []

                for section_key, section_lines in group_generation_lines(
                    lines,
                    generation,
                ):
                    if section_key == "other":
                        section_title = "Другие модели"
                    else:
                        section_title = IPHONE_SECTION_TITLES.get(
                            section_key,
                            section_key,
                        )

                    rendered_parts.append(
                        f"<b>— {html.escape(section_title)} —</b>"
                    )

                    rendered_parts.extend(
                        html.escape(
                            add_markup_to_line(
                                display_product_line(x),
                                markup_amount,
                            )
                        )
                        for x in section_lines
                    )

                    rendered_parts.append("")

                content = "\n".join(rendered_parts).strip()
            else:
                display_lines = [
                    add_markup_to_line(
                        display_product_line(x),
                        markup_amount,
                    )
                    for x in lines
                ]

                content = "\n".join(
                    html.escape(x)
                    for x in display_lines
                ).strip()

            if content:
                body = (
                    f"<b>{html.escape(title)}</b>\n\n"
                    f"{content}"
                )
            else:
                body = (
                    f"<b>{html.escape(title)}</b>\n\n"
                    f"<i>Нет позиций</i>"
                )

        if len(body) > 4050:
            body = body[:4010] + "\n\n<i>…часть прайса не поместилась</i>"

        try:
            await self.client.edit_message(
                self.target,
                int(message_id),
                body,
                parse_mode="html",
                link_preview=False,
            )
        except MessageNotModifiedError:
            # Нормальная ситуация: текст уже точно такой же.
            # Не считаем это ошибкой.
            pass

    async def _clear_enabled_prices(self):
        await self.ensure_connected()
        blocks_enabled = self.state.get("blocks") or {}

        for key in self.block_order():
            if blocks_enabled.get(key, False):
                await self._set_block_text(
                    key,
                    [],
                    closed=True,
                )

        await self.ensure_navigation()

    async def _show_closed_message(self, text):
        await self.ensure_connected()
        closed_id = self.state.get("closed_message_id")
        closed_msg = None

        if closed_id:
            with suppress(Exception):
                closed_msg = await self.client.get_messages(
                    self.target,
                    ids=int(closed_id),
                )

        if closed_msg:
            try:
                await self.client.edit_message(
                    self.target,
                    closed_msg.id,
                    text,
                    parse_mode="html",
                )
            except MessageNotModifiedError:
                pass
        else:
            closed_msg = await self.client.send_message(
                self.target,
                text,
                parse_mode="html",
            )

        self.state.set("closed_message_id", closed_msg.id)

    async def set_schedule_closed(self):
        """
        Наш рабочий график: 10:00–20:00 МСК.
        Вне графика поставщика не дёргаем, цены скрываем.
        """
        await self._clear_enabled_prices()

        await self._show_closed_message(
            "🌙 <b>Продажи временно закрыты</b>\n\n"
            "Работаем ежедневно с 10:00 до 20:00 МСК."
        )

        self.state.update(
            schedule_closed=True,
            last_result="закрыто по графику 20:00–10:00 МСК",
        )

        print("🌙 Закрыто по графику — цены скрыты", flush=True)

    async def set_closed(self):
        """
        Поставщик ответил, что закрыт.
        """
        await self._clear_enabled_prices()

        await self._show_closed_message(
            "🚫 <b>Поставщик временно закрыт</b>\n\n"
            "Ожидаем открытия продаж."
        )

        self.state.update(
            supplier_closed=True,
            supplier_status="closed",
            schedule_closed=False,
            last_result="поставщик закрыт — цены скрыты",
        )

        print("🚫 Поставщик закрыт — цены скрыты", flush=True)

    async def clear_closed_message(self):
        await self.ensure_connected()
        closed_id = self.state.get("closed_message_id")

        if closed_id:
            with suppress(Exception):
                await self.client.delete_messages(
                    self.target,
                    [int(closed_id)],
                )

        self.state.set("closed_message_id", None)

    async def apply_price(self, raw_text):
        """
        Полный прайс сохраняется всегда.
        Настройки блоков влияют только на публикацию в канале.
        """
        parsed = parse_full_price(raw_text)
        blocks = parsed["blocks"]

        self.state.set("last_full_price", raw_text)

        await self.clear_closed_message()

        self.state.update(
            supplier_closed=False,
            supplier_status="open",
            schedule_closed=False,
        )

        blocks_enabled = self.state.get("blocks") or {}

        for key in self.block_order():
            if blocks_enabled.get(key, False):
                await self._set_block_text(
                    key,
                    blocks.get(key, []),
                    closed=False,
                )
            else:
                # На всякий случай гарантируем, что OFF-блок исчез.
                await self._set_block_text(
                    key,
                    [],
                    closed=False,
                )

        await self.ensure_navigation()

        counts = {
            key: len(value)
            for key, value in blocks.items()
        }
        total_detected = sum(counts.values())

        self.state.set(
            "last_result",
            (
                f"прайс проверен; позиций: {total_detected}; "
                f"15 SIM/eSIM {counts.get('15_sim', 0)}/{counts.get('15_esim', 0)}, "
                f"16 SIM/eSIM {counts.get('16_sim', 0)}/{counts.get('16_esim', 0)}, "
                f"17 SIM/eSIM {counts.get('17_sim', 0)}/{counts.get('17_esim', 0)}; "
                f"наценка +{int(self.state.get('markup_amount', 0) or 0)}"
            ),
        )

        return counts

    async def sync_once(self, forced=False):
        async with self.lock:
            now_ts = int(time.time())

            self.state.update(
                last_check_ts=now_ts,
                next_check_ts=None,
            )

            # Даже ручная кнопка не должна публиковать цены ночью.
            if not self.is_work_hours():
                await self.set_schedule_closed()
                return {
                    "schedule_closed": True,
                    "closed": True,
                }

            self.state.set("schedule_closed", False)
            self.state.set("last_result", "запрашиваю прайс…")

            try:
                raw = await self.request_price()

                if closed_match(raw, CLOSED_TEXT):
                    await self.set_closed()
                    return {
                        "schedule_closed": False,
                        "closed": True,
                    }

                counts = await self.apply_price(raw)

                return {
                    "schedule_closed": False,
                    "closed": False,
                    "counts": counts,
                }

            except Exception as e:
                self.state.update(
                    supplier_status="error",
                    last_result=f"ошибка: {e}",
                )

                if not self.client.is_connected():
                    try:
                        await self.ensure_connected()
                    except Exception:
                        pass

                raise

    async def on_supplier_edit(self, event):
        ids = self.state.get("last_supplier_message_ids") or []

        if event.message.id not in {int(x) for x in ids}:
            last_id = self.state.get("last_supplier_message_id")

            if not last_id or event.message.id != int(last_id):
                return

        if not self.is_work_hours():
            await self.set_schedule_closed()
            return

        await asyncio.sleep(1.5)

        try:
            await self.sync_once(forced=True)
        except Exception as e:
            self.state.set(
                "last_result",
                f"ошибка обновления после редактирования: {e}",
            )

    async def periodic_loop(self):
        """
        Днём: обычный интервал запросов.
        В 20:00 МСК: цены скрываются.
        Ночью: поставщика не запрашиваем.
        В 10:00 МСК: сразу запрашиваем свежий прайс.
        """
        while True:
            now = datetime.now(MOSCOW_TZ)

            if not self.is_work_hours(now):
                if not self.state.get("schedule_closed", False):
                    try:
                        async with self.lock:
                            await self.set_schedule_closed()
                    except Exception as e:
                        self.state.set(
                            "last_result",
                            f"ошибка ночного закрытия: {e}",
                        )

                wait_seconds = self.seconds_until_schedule_boundary(now)
                self.state.set(
                    "next_check_ts",
                    int(time.time()) + wait_seconds,
                )

                self.wakeup.clear()

                try:
                    await asyncio.wait_for(
                        self.wakeup.wait(),
                        timeout=wait_seconds,
                    )
                    continue
                except asyncio.TimeoutError:
                    # Дошли ровно до 10:00.
                    pass

                if self.state.get("sync_enabled", True):
                    try:
                        await self.sync_once()
                    except Exception:
                        pass
                else:
                    # Синхронизация вручную остановлена:
                    # график уже открыт, но старые цены автоматически не показываем.
                    self.state.set("schedule_closed", False)

                continue

            # Рабочее время.
            if self.state.get("schedule_closed", False):
                # Например процесс проснулся после 10:00.
                self.state.set("schedule_closed", False)

                if self.state.get("sync_enabled", True):
                    try:
                        await self.sync_once()
                    except Exception:
                        pass

            if not self.state.get("sync_enabled", True):
                self.state.set("next_check_ts", None)

                # Но всё равно проснёмся на границе 20:00,
                # чтобы гарантированно скрыть цены.
                wait_seconds = self.seconds_until_schedule_boundary(now)
                self.wakeup.clear()

                try:
                    await asyncio.wait_for(
                        self.wakeup.wait(),
                        timeout=wait_seconds,
                    )
                except asyncio.TimeoutError:
                    pass

                continue

            interval = int(self.state.get("interval", 1800))
            boundary_seconds = self.seconds_until_schedule_boundary(now)
            wait_seconds = min(interval, boundary_seconds)

            self.state.set(
                "next_check_ts",
                int(time.time()) + wait_seconds,
            )

            self.wakeup.clear()

            try:
                await asyncio.wait_for(
                    self.wakeup.wait(),
                    timeout=wait_seconds,
                )
                continue
            except asyncio.TimeoutError:
                pass

            # Если таймер попал на 20:00, сначала скрываем цены.
            if not self.is_work_hours():
                try:
                    async with self.lock:
                        await self.set_schedule_closed()
                except Exception as e:
                    self.state.set(
                        "last_result",
                        f"ошибка закрытия по графику: {e}",
                    )
                continue

            try:
                await self.sync_once()
            except Exception as e:
                print(f"⚠️ Ошибка периодической синхронизации: {e}", flush=True)

                if not self.client.is_connected():
                    try:
                        await self.ensure_connected()
                    except Exception:
                        pass

    async def close(self):
        if self.bot:
            await self.bot.session.close()

    def wake(self):
        self.wakeup.set()
