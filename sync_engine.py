import asyncio
import html
import time
from contextlib import suppress

from telethon import events

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
from parser import BLOCK_KEYS, BLOCK_TITLES, closed_match, parse_full_price


class PriceSyncEngine:
    def __init__(self, client, state, target_entity):
        self.client = client
        self.state = state
        self.target = target_entity
        self.bot = Bot(CONTROL_BOT_TOKEN) if CONTROL_BOT_TOKEN else None
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()

    async def start(self):
        await self.ensure_structure()
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
        Создаёт 6 сообщений один раз. После этого они только редактируются.
        Если state потерялся после redeploy, находит сообщения по заголовкам.
        """
        message_ids = dict(self.state.get("message_ids") or {})

        for key in BLOCK_KEYS:
            title = BLOCK_TITLES[key]
            msg = None
            saved_id = message_ids.get(key)

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

            message_ids[key] = msg.id

        self.state.set("message_ids", message_ids)
        await self.ensure_navigation()

    async def ensure_navigation(self):
        """
        Inline-кнопки навигации отправляет управляющий Bot API.
        Ошибка навигации не должна останавливать синхронизацию прайса.
        """
        if not self.bot:
            print("⚠️ CONTROL_BOT_TOKEN не указан — навигация пропущена", flush=True)
            return

        rows = []
        blocks_enabled = self.state.get("blocks") or {}
        message_ids = self.state.get("message_ids") or {}

        for gen in ("15", "16", "17"):
            row = []

            for sim_type, label in (("sim", "SIM"), ("esim", "eSIM")):
                key = f"{gen}_{sim_type}"

                if not blocks_enabled.get(key, True):
                    continue

                message_id = message_ids.get(key)
                if not message_id:
                    continue

                link = self._message_link(int(message_id))
                if link:
                    row.append(
                        InlineKeyboardButton(
                            text=f"{gen} {label}",
                            url=link,
                        )
                    )

            if row:
                rows.append(row)

        keyboard = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        text = "🧭 <b>Навигация по прайсу</b>\n\nВыберите нужный раздел:"

        try:
            nav_id = self.state.get("nav_message_id")

            if nav_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=TARGET_CHANNEL,
                        message_id=int(nav_id),
                        text=text,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                    return
                except Exception as e:
                    print(
                        f"⚠️ Не удалось отредактировать навигацию: {e}",
                        flush=True,
                    )
                    self.state.set("nav_message_id", None)

            msg = await self.bot.send_message(
                chat_id=TARGET_CHANNEL,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            self.state.set("nav_message_id", msg.message_id)

        except Exception as e:
            print(
                "⚠️ Навигация не создана. "
                "Добавь CONTROL_BOT_TOKEN-бота админом в TARGET_CHANNEL. "
                f"Ошибка: {e}",
                flush=True,
            )

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

    async def _set_block_text(self, key, lines, closed=False):
        message_ids = self.state.get("message_ids") or {}
        message_id = message_ids.get(key)
        if not message_id:
            await self.ensure_structure()
            message_id = (self.state.get("message_ids") or {}).get(key)

        title = BLOCK_TITLES[key]
        enabled = (self.state.get("blocks") or {}).get(key, True)

        # При закрытии или выключенном блоке оставляем только шапку.
        if closed or not enabled:
            body = f"<b>{html.escape(title)}</b>"
        else:
            content = "\n".join(html.escape(x) for x in lines).strip()
            if content:
                body = f"<b>{html.escape(title)}</b>\n\n{content}"
            else:
                body = f"<b>{html.escape(title)}</b>\n\n<i>Нет позиций</i>"

        # Telegram ограничивает текст сообщения ~4096 символами.
        if len(body) > 4050:
            body = body[:4010] + "\n\n<i>…часть прайса не поместилась</i>"

        await self.client.edit_message(
            self.target,
            int(message_id),
            body,
            parse_mode="html",
            link_preview=False,
        )

    async def set_closed(self):
        for key in BLOCK_KEYS:
            await self._set_block_text(key, [], closed=True)

        await self.ensure_navigation()

        closed_id = self.state.get("closed_message_id")
        closed_msg = None
        if closed_id:
            with suppress(Exception):
                closed_msg = await self.client.get_messages(
                    self.target,
                    ids=int(closed_id),
                )

        text = "🚫 <b>Продажи временно закрыты</b>\n\nОжидаем открытия продаж."

        if closed_msg:
            await self.client.edit_message(
                self.target,
                closed_msg.id,
                text,
                parse_mode="html",
            )
        else:
            closed_msg = await self.client.send_message(
                self.target,
                text,
                parse_mode="html",
            )

        self.state.update(
            closed_message_id=closed_msg.id,
            supplier_closed=True,
            last_result="продажи закрыты — цены скрыты",
        )

    async def clear_closed_message(self):
        closed_id = self.state.get("closed_message_id")
        if closed_id:
            with suppress(Exception):
                await self.client.delete_messages(
                    self.target,
                    [int(closed_id)],
                )
        self.state.update(
            closed_message_id=None,
            supplier_closed=False,
        )

    async def apply_price(self, raw_text):
        """
        Полный прайс сохраняется всегда.
        Настройки блоков влияют только на публикацию в канале.
        """
        parsed = parse_full_price(raw_text)
        blocks = parsed["blocks"]

        self.state.set("last_full_price", raw_text)

        await self.clear_closed_message()

        for key in BLOCK_KEYS:
            await self._set_block_text(
                key,
                blocks.get(key, []),
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
                f"весь прайс получен; позиций: {total_detected}; "
                f"15 SIM/eSIM {counts.get('15_sim', 0)}/{counts.get('15_esim', 0)}, "
                f"16 SIM/eSIM {counts.get('16_sim', 0)}/{counts.get('16_esim', 0)}, "
                f"17 SIM/eSIM {counts.get('17_sim', 0)}/{counts.get('17_esim', 0)}"
            ),
        )

        return counts

    async def sync_once(self, forced=False):
        async with self.lock:
            now = int(time.time())
            self.state.update(
                last_check_ts=now,
                next_check_ts=None,
                last_result="запрашиваю прайс…",
            )

            try:
                raw = await self.request_price()

                if closed_match(raw, CLOSED_TEXT):
                    await self.set_closed()
                    return {"closed": True}

                counts = await self.apply_price(raw)
                return {"closed": False, "counts": counts}

            except Exception as e:
                self.state.set("last_result", f"ошибка: {e}")
                raise

    async def on_supplier_edit(self, event):
        """
        Если поставщик изменил одну из частей последнего прайса,
        через небольшую паузу заново запрашиваем весь прайс.
        """
        ids = self.state.get("last_supplier_message_ids") or []

        if event.message.id not in {int(x) for x in ids}:
            last_id = self.state.get("last_supplier_message_id")
            if not last_id or event.message.id != int(last_id):
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
        while True:
            if not self.state.get("sync_enabled", True):
                self.state.set("next_check_ts", None)
                self.wakeup.clear()
                await self.wakeup.wait()
                continue

            interval = int(self.state.get("interval", 1800))
            next_ts = int(time.time()) + interval
            self.state.set("next_check_ts", next_ts)

            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=interval)
                continue
            except asyncio.TimeoutError:
                pass

            if self.state.get("sync_enabled", True):
                try:
                    await self.sync_once()
                except Exception:
                    pass

    async def close(self):
        if self.bot:
            await self.bot.session.close()

    def wake(self):
        self.wakeup.set()
