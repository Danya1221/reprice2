import asyncio
import html
import time
from contextlib import suppress
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    FloodWaitError,
    MessageNotModifiedError,
    SessionRevokedError,
)

from config import (
    AFTER_ACTION_DELAY,
    BUTTON_PATH_1,
    BUTTON_PATH_2,
    CLOSED_TEXT_1,
    CLOSED_TEXT_2,
    REQUEST_TEXT_1,
    REQUEST_TEXT_2,
    RESPONSE_TIMEOUT,
    SUPPLIER_BOT_1,
    SUPPLIER_BOT_2,
    SUPPLIER_QUIET_SECONDS,
    TARGET_CHANNEL,
    WORK_START_HOUR,
)
from parser import (
    BLOCK_PRIORITY,
    closed_match,
    group_products,
    merge_products,
    parse_supplier_price,
    render_product_line,
)


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DISPLAY_BLOCK_ORDER = tuple(BLOCK_PRIORITY)  # compatibility with TelegramRuntime
DEAD_SESSION_ERRORS = (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
)


class PriceSyncEngine:
    """
    Simple two-source price synchronizer.

    The only data flow is:
      supplier 1 + supplier 2 -> parse retail rows -> exact variant dedupe ->
      lower purchase price -> markup -> edit stable channel messages.
    """

    def __init__(self, client, state, target_entity, auth_failure_callback=None):
        self.client = client
        self.state = state
        self.target = target_entity
        self.auth_failure_callback = auth_failure_callback
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._last_send_at = 0.0

        self.sources = (
            {
                "slot": 1,
                "bot": SUPPLIER_BOT_1,
                "request": REQUEST_TEXT_1,
                "buttons": BUTTON_PATH_1,
                "closed_text": CLOSED_TEXT_1,
            },
            {
                "slot": 2,
                "bot": SUPPLIER_BOT_2,
                "request": REQUEST_TEXT_2,
                "buttons": BUTTON_PATH_2,
                "closed_text": CLOSED_TEXT_2,
            },
        )

    async def start(self):
        await self.ensure_connected()

    async def close(self):
        return None

    def wake(self):
        self.wakeup.set()

    async def ensure_connected(self):
        if self.client.is_connected():
            return
        try:
            await self.client.connect()
        except DEAD_SESSION_ERRORS as e:
            if self.auth_failure_callback:
                await self.auth_failure_callback(e)
            raise

        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram user-session не авторизована")

    async def _safe_send_message(self, entity, *args, **kwargs):
        async with self._send_lock:
            while True:
                elapsed = time.monotonic() - self._last_send_at
                if elapsed < 1.25:
                    await asyncio.sleep(1.25 - elapsed)
                try:
                    msg = await self.client.send_message(entity, *args, **kwargs)
                    self._last_send_at = time.monotonic()
                    return msg
                except FloodWaitError as e:
                    await asyncio.sleep(int(e.seconds) + 1)
                except DEAD_SESSION_ERRORS as e:
                    if self.auth_failure_callback:
                        await self.auth_failure_callback(e)
                    raise

    async def _safe_edit(self, message_id, text):
        try:
            await self.client.edit_message(
                self.target,
                int(message_id),
                text,
                parse_mode="html",
                link_preview=False,
            )
            return True
        except MessageNotModifiedError:
            return True
        except DEAD_SESSION_ERRORS as e:
            if self.auth_failure_callback:
                await self.auth_failure_callback(e)
            raise
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Supplier requests
    # ------------------------------------------------------------------
    async def _snapshot_dialog(self, bot):
        messages = await self.client.get_messages(bot, limit=100)
        snapshot = {}
        for msg in messages:
            if msg.out:
                continue
            snapshot[int(msg.id)] = (msg.raw_text or "").strip()
        return snapshot

    async def _changed_messages(self, bot, snapshot):
        messages = await self.client.get_messages(bot, limit=100)
        changed = {}
        for msg in messages:
            if msg.out:
                continue
            text = (msg.raw_text or "").strip()
            old = snapshot.get(int(msg.id))
            if old is None or old != text:
                changed[int(msg.id)] = msg
        return changed

    async def _wait_for_change(self, bot, snapshot, timeout=None):
        timeout = float(timeout or RESPONSE_TIMEOUT)
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            changed = await self._changed_messages(bot, snapshot)
            if changed:
                return changed[max(changed)]
            await asyncio.sleep(0.6)
        return None

    async def _click_button(self, message, wanted):
        wanted_cf = str(wanted).strip().casefold()
        for row in (message.buttons or []):
            for button in row:
                label = str(getattr(button, "text", "") or "").strip()
                if label.casefold() == wanted_cf:
                    await message.click(text=label)
                    return True
        raise RuntimeError(f"не найдена кнопка «{wanted}»")

    async def _collect_response(self, bot, snapshot):
        collected = {}
        started = time.monotonic()
        last_change_at = None

        while True:
            changed = await self._changed_messages(bot, snapshot)
            now = time.monotonic()

            new_any = False
            for mid, msg in changed.items():
                text = (msg.raw_text or "").strip()
                marker = (mid, text)
                previous = collected.get(mid)
                if previous is None or (previous.raw_text or "").strip() != text:
                    new_any = True
                collected[mid] = msg

            if new_any:
                last_change_at = now

            if collected and last_change_at is not None:
                if now - last_change_at >= float(SUPPLIER_QUIET_SECONDS):
                    break

            if not collected and now - started >= RESPONSE_TIMEOUT:
                break

            if now - started >= max(RESPONSE_TIMEOUT + 15, 50):
                break

            await asyncio.sleep(0.7)

        return [collected[mid] for mid in sorted(collected)]

    async def request_supplier(self, source):
        slot = source["slot"]
        bot = source["bot"]
        result = {
            "slot": slot,
            "bot": bot,
            "status": "error",
            "raw": "",
            "products": [],
            "error": "",
        }

        try:
            await self.ensure_connected()
            snapshot = await self._snapshot_dialog(bot)
            print(f"📡 Источник {slot}: {bot} ← {source['request']}", flush=True)

            await self._safe_send_message(bot, source["request"])

            # Optional menu path; kept only for suppliers that need it.
            wait_snapshot = dict(snapshot)
            for button_name in source["buttons"]:
                response = await self._wait_for_change(bot, wait_snapshot)
                if not response:
                    raise RuntimeError("поставщик не прислал меню")
                # Snapshot the menu BEFORE clicking it. The next path step then
                # waits for a genuinely new/edited response after this click.
                before_click = await self._snapshot_dialog(bot)
                await self._click_button(response, button_name)
                await asyncio.sleep(AFTER_ACTION_DELAY)
                wait_snapshot = before_click
                # Preserve the original pre-command snapshot for final collection.

            messages = await self._collect_response(bot, snapshot)
            parts = [(msg.raw_text or "").strip() for msg in messages if (msg.raw_text or "").strip()]
            if not parts:
                raise RuntimeError("поставщик не прислал текстовый ответ")

            raw = "\n".join(parts)
            result["raw"] = raw

            if closed_match(raw, source["closed_text"]):
                result["status"] = "closed"
                print(f"🚫 Источник {slot}: закрыт", flush=True)
                return result

            products = parse_supplier_price(raw, source=f"source_{slot}")
            if not products:
                raise RuntimeError("ответ получен, но обычные товарные строки с ценой не найдены")

            result["status"] = "open"
            result["products"] = products
            print(f"✅ Источник {slot}: {len(products)} позиций", flush=True)
            return result

        except DEAD_SESSION_ERRORS as e:
            if self.auth_failure_callback:
                await self.auth_failure_callback(e)
            result["error"] = str(e)
            return result
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            print(f"⚠️ Источник {slot}: {e}", flush=True)
            return result

    def _save_source_status(self, result):
        slot = int(result["slot"])
        now_ts = int(time.time())
        self.state.update(**{
            f"supplier{slot}_status": result["status"],
            f"supplier{slot}_count": len(result.get("products") or []),
            f"supplier{slot}_last_error": result.get("error") or "",
            f"supplier{slot}_last_check_ts": now_ts,
        })

    # ------------------------------------------------------------------
    # Stable publication
    # ------------------------------------------------------------------
    def _split_block(self, title, products, markup):
        lines = [render_product_line(p, markup) for p in products]
        chunks = []
        current = []

        def make_body(rows, index):
            heading = title if index == 0 else f"{title} — продолжение"
            return f"<b>{html.escape(heading)}</b>\n\n" + "\n".join(rows)

        for line in lines:
            candidate = current + [line]
            if current and len(make_body(candidate, len(chunks))) > 3850:
                chunks.append(make_body(current, len(chunks)))
                current = [line]
            else:
                current = candidate

        if current:
            chunks.append(make_body(current, len(chunks)))

        return chunks or [f"<b>{html.escape(title)}</b>\n\n<i>Нет в наличии</i>"]

    async def _publish_block(self, key, title, products):
        ids_map = dict(self.state.get("price_message_ids") or {})
        titles = dict(self.state.get("block_titles") or {})
        known_order = list(self.state.get("known_block_order") or [])

        ids = list(ids_map.get(key) or [])
        markup = int(self.state.get("markup_amount", 0) or 0)
        chunks = self._split_block(title, products, markup)
        new_ids = []

        for index, body in enumerate(chunks):
            existing = ids[index] if index < len(ids) else None
            if existing and await self._safe_edit(existing, body):
                new_ids.append(int(existing))
                continue

            msg = await self._safe_send_message(
                self.target,
                body,
                parse_mode="html",
                link_preview=False,
            )
            new_ids.append(int(msg.id))
            print(f"➕ Создан блок {key} part={index + 1} id={msg.id}", flush=True)

        # Telegram has a hard message-size limit. If a block previously needed
        # more continuation messages but no longer does, remove only obsolete
        # continuation parts. The primary message_id never changes in normal sync.
        extras = ids[len(chunks):]
        if extras:
            with suppress(Exception):
                await self.client.delete_messages(self.target, [int(x) for x in extras])

        ids_map[key] = new_ids
        titles[key] = title
        if key not in known_order:
            known_order.append(key)

        self.state.update(
            price_message_ids=ids_map,
            block_titles=titles,
            known_block_order=known_order,
        )

    async def _mark_missing_blocks(self, current_keys):
        ids_map = dict(self.state.get("price_message_ids") or {})
        titles = dict(self.state.get("block_titles") or {})
        for key, ids in ids_map.items():
            if key in current_keys or not ids:
                continue
            title = titles.get(key, key)
            body = f"<b>{html.escape(title)}</b>\n\n<i>Сейчас нет в наличии</i>"
            first = ids[0]
            if not await self._safe_edit(first, body):
                continue
            # Obsolete continuations may be deleted; primary message stays stable.
            if len(ids) > 1:
                with suppress(Exception):
                    await self.client.delete_messages(self.target, [int(x) for x in ids[1:]])
                ids_map[key] = [int(first)]
        self.state.set("price_message_ids", ids_map)

    async def publish_products(self, products):
        groups = group_products(products)
        current_keys = set()

        for key, title, rows in groups:
            current_keys.add(key)
            await self._publish_block(key, title, rows)

        await self._mark_missing_blocks(current_keys)
        self.state.update(
            last_products=products,
            publication_status="open",
        )

    async def _edit_all_existing(self, message_text):
        ids_map = dict(self.state.get("price_message_ids") or {})
        titles = dict(self.state.get("block_titles") or {})
        for key, ids in ids_map.items():
            title = titles.get(key, key)
            for index, mid in enumerate(ids):
                suffix = "" if index == 0 else " — продолжение"
                body = (
                    f"<b>{html.escape(title + suffix)}</b>\n\n"
                    f"<i>{html.escape(message_text)}</i>"
                )
                await self._safe_edit(mid, body)

    async def set_both_closed(self):
        await self._edit_all_existing("🚫 Продажи закрыты. Ожидаем открытия поставщиков.")
        self.state.update(
            publication_status="closed",
            last_result="оба поставщика закрыты — публикация закрыта",
        )

    async def set_waiting_start(self):
        await self._edit_all_existing(
            f"🌙 Ожидаем {WORK_START_HOUR:02d}:00 МСК или открытия обоих поставщиков."
        )
        self.state.update(
            publication_status="waiting",
            last_result=(
                f"до {WORK_START_HOUR:02d}:00 МСК: публикация начнётся раньше только если открыты оба поставщика"
            ),
        )

    def can_publish_now(self, result1, result2):
        now = datetime.now(MOSCOW_TZ)
        both_open = result1["status"] == "open" and result2["status"] == "open"
        return now.hour >= int(WORK_START_HOUR) or both_open

    # ------------------------------------------------------------------
    # Public controls
    # ------------------------------------------------------------------
    async def set_markup(self, amount):
        amount = int(amount)
        if amount < 0:
            raise ValueError("Наценка не может быть отрицательной")
        self.state.set("markup_amount", amount)

        products = self.state.get("last_products") or []
        if products and self.state.get("publication_status") == "open":
            await self.publish_products(products)
        print(f"💰 Наценка: +{amount}", flush=True)

    # Compatibility no-ops: v2 intentionally has no manual block subsystem.
    async def set_block_enabled(self, key, enabled):
        return None

    async def set_all_blocks(self, enabled):
        return None

    async def rebuild_block_order(self):
        products = self.state.get("last_products") or []
        if products:
            await self.publish_products(products)

    def move_block(self, key, direction):
        return self.state.get("known_block_order") or []

    def reset_block_order(self):
        return self.state.get("known_block_order") or []

    async def sync_once(self, forced=False):
        async with self.lock:
            now_ts = int(time.time())
            self.state.update(last_check_ts=now_ts, next_check_ts=None, last_result="запрашиваю два прайса…")

            result1, result2 = await asyncio.gather(
                self.request_supplier(self.sources[0]),
                self.request_supplier(self.sources[1]),
            )
            self._save_source_status(result1)
            self._save_source_status(result2)

            statuses = (result1["status"], result2["status"])

            if statuses == ("closed", "closed"):
                await self.set_both_closed()
                return {"closed": True, "sources": [result1, result2]}

            open_results = [r for r in (result1, result2) if r["status"] == "open"]

            if not open_results:
                # Do not destroy a good existing price on network/parser errors.
                errors = [r["error"] for r in (result1, result2) if r.get("error")]
                self.state.update(
                    publication_status="error",
                    last_result="нет свежего открытого прайса: " + ("; ".join(errors) or "поставщики недоступны"),
                )
                return {"closed": False, "error": True, "sources": [result1, result2]}

            if not self.can_publish_now(result1, result2):
                await self.set_waiting_start()
                return {"waiting": True, "sources": [result1, result2]}

            merged = merge_products(*(r["products"] for r in open_results))
            if not merged:
                self.state.update(publication_status="error", last_result="два прайса прочитаны, но итог пустой")
                return {"error": True, "sources": [result1, result2]}

            await self.publish_products(merged)

            source_note = "/".join(str(len(r["products"])) if r["status"] == "open" else r["status"] for r in (result1, result2))
            self.state.set(
                "last_result",
                f"готово: {len(merged)} итоговых позиций; источники {source_note}; наценка +{int(self.state.get('markup_amount', 0) or 0)}",
            )
            return {
                "closed": False,
                "waiting": False,
                "count": len(merged),
                "sources": [result1, result2],
            }

    async def periodic_loop(self):
        while True:
            if not self.state.get("sync_enabled", True):
                self.state.set("next_check_ts", None)
                self.wakeup.clear()
                await self.wakeup.wait()
                continue

            interval = max(60, int(self.state.get("interval", 1800) or 1800))
            self.state.set("next_check_ts", int(time.time()) + interval)
            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=interval)
                # Menu changes/start already perform their own action when needed.
                # Wake only recalculates the timer; it must not cause a duplicate request.
                continue
            except asyncio.TimeoutError:
                pass

            if not self.state.get("sync_enabled", True):
                continue

            try:
                await self.sync_once()
            except Exception as e:
                self.state.set("last_result", f"ошибка автосинхронизации: {e}")
