import asyncio
import re
from contextlib import suppress

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionPasswordNeededError,
    SessionRevokedError,
)
from telethon.sessions import StringSession

from config import API_ID, API_HASH, RESPONSE_TIMEOUT, AFTER_ACTION_DELAY
from parser import PRICE_END_RE, closed_match


DEAD_SESSION_ERRORS = (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
)

MAX_EXTRA_SOURCES = 2  # slot 1 = main HI bot; slots 2/3 = bot or chat/group sources.


def _clean_spaces(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def _price_from_line(line):
    match = PRICE_END_RE.search(_clean_spaces(line))
    if not match:
        return None
    try:
        return int(re.sub(r"\s+", "", match.group("price")))
    except Exception:
        return None


def _line_without_price(line):
    text = _clean_spaces(line)
    match = PRICE_END_RE.search(text)
    if not match:
        return text
    return text[:match.start("prefix")].strip()


def _canonical_product_key(line):
    """
    Conservative product identity key.

    Important product differences remain in the key:
    memory, colour, SIM/eSIM, country/region, size, CPO/ASIS, generation/model.

    We remove only presentation noise and supplier-specific formatting.
    """
    text = _line_without_price(line).casefold()

    # Visual bullets / markdown.
    text = re.sub(r"[*_`~]+", " ", text)
    text = re.sub(r"^[•▪▫◦·\-–—]+\s*", "", text)

    # Normalize common names/spacing without destroying variants.
    replacements = {
        "айфон": "iphone",
        "эпл": "apple",
        "про макс": "pro max",
        "промакс": "pro max",
        "про макс": "pro max",
        "е-сим": "esim",
        "e-sim": "esim",
        "еsim": "esim",
        "сим": "sim",
        "гб": "gb",
        "тб": "tb",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Normalize memory spellings: 256 gb -> 256gb etc.
    text = re.sub(r"\b(\d+)\s*(gb|tb)\b", r"\1\2", text)

    # Supplier SKU/model codes are presentation noise in retail output.
    text = re.sub(r"\bsm-[a-z0-9/+_-]+\b", " ", text)
    text = re.sub(r"\bsku[:#]?\s*[a-z0-9._/-]+\b", " ", text)

    # Normalize punctuation only.
    text = re.sub(r"[|,:;()\[\]{}]+", " ", text)
    text = re.sub(r"\s*[/\\]\s*", "/", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def merge_supplier_prices(raw_prices):
    """
    Merge supplier price texts into one clean price.

    For a real duplicate, keep exactly one row with the lowest purchase price.
    Distinct variants remain distinct because the key preserves attributes.
    Non-product/header lines are intentionally not copied; the existing parser
    recreates clean class blocks from product rows.
    """
    best = {}
    unpriced = {}

    for source_label, raw in raw_prices:
        for raw_line in (raw or "").splitlines():
            line = _clean_spaces(raw_line)
            if not line:
                continue

            price = _price_from_line(line)
            if price is None:
                continue

            key = _canonical_product_key(line)
            if not key:
                continue

            current = best.get(key)
            if current is None or price < current["price"]:
                best[key] = {
                    "price": price,
                    "line": line,
                    "source": source_label,
                }

    # Stable readable order; parser will split into semantic class blocks.
    rows = sorted(
        best.values(),
        key=lambda item: (_canonical_product_key(item["line"]), item["price"]),
    )

    return "\n".join(item["line"] for item in rows), rows


def _normalize_chat_ref(value):
    value = (value or "").strip()
    if not value:
        return value

    # Telegram private group/channel message link:
    # https://t.me/c/1860042299/123 -> -1001860042299
    m = re.search(
        r"(?:https?://)?(?:t\.me|telegram\.me)/c/(\d+)(?:/\d+)?",
        value,
        flags=re.I,
    )
    if m:
        return int("-100" + m.group(1))

    value = re.sub(r"^https?://", "", value, flags=re.I)
    value = re.sub(r"^t\.me/", "", value, flags=re.I)
    value = re.sub(r"^telegram\.me/", "", value, flags=re.I)
    value = value.strip("/")

    if value.startswith("c/"):
        parts = value.split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            return int("-100" + parts[1])

    if value.startswith("+") or value.startswith("joinchat/"):
        return value

    if value.lstrip("-").isdigit():
        return int(value)

    if not value.startswith("@"):
        value = "@" + value

    return value


def _count_price_rows(text):
    return sum(
        1
        for line in (text or "").splitlines()
        if PRICE_END_RE.search(_clean_spaces(line))
    )


class ExtraSourceManager:
    """
    Additional Telegram supplier sources.

    Slot 1 stays the existing primary HI bot.
    Slots 2 and 3 each have their own Telethon StringSession in PostgreSQL.
    A slot may be a supplier bot or a normal group/channel.
    """

    def __init__(self, state, database):
        self.state = state
        self.database = database
        self.clients = {}
        self.login_clients = {}
        self.login_phone = {}
        self.login_hash = {}
        self._locks = {
            2: asyncio.Lock(),
            3: asyncio.Lock(),
        }

        # Group/channel sources do NOT need a dedicated third Telegram login.
        # They may reuse any already-authorized account that can see the chat.
        self.primary_client = None

    def set_primary_client(self, client):
        self.primary_client = client

    def _session_key(self, slot):
        return f"telethon_source_session_v1:{int(slot)}"

    def _sources(self):
        data = self.state.get("extra_sources") or {}
        if not isinstance(data, dict):
            data = {}
        return data

    def get_source(self, slot):
        slot = str(int(slot))
        return dict((self._sources().get(slot) or {}))

    def set_source(self, slot, **values):
        slot = str(int(slot))
        data = self._sources()
        current = dict(data.get(slot) or {})
        current.update(values)
        data[slot] = current
        self.state.set("extra_sources", data)
        return current

    def remove_source(self, slot):
        slot_i = int(slot)
        data = self._sources()
        data.pop(str(slot_i), None)
        self.state.set("extra_sources", data)
        self.database.delete(self._session_key(slot_i))
        client = self.clients.pop(slot_i, None)
        if client:
            asyncio.create_task(client.disconnect())

    def source_status(self, slot):
        slot_i = int(slot)
        cfg = self.get_source(slot_i)
        if not cfg:
            return "не настроен"

        source_type = (cfg.get("type") or "bot").strip().lower()

        if source_type == "chat":
            target = cfg.get("chat") or "группа/канал"

            # Chat source reuses existing authorized accounts.
            reusable = []
            if self.primary_client is not None:
                reusable.append(self.primary_client)
            reusable.extend(
                client
                for client in self.clients.values()
                if client is not None
            )

            if reusable:
                return f"✅ {target} — через подключённый аккаунт"

            return f"🟡 {target} — ждёт основной аккаунт"

        target = cfg.get("bot") or "поставщик"
        client = self.clients.get(slot_i)

        if client and client.is_connected():
            return f"✅ {target}"

        if self.database.get(self._session_key(slot_i)):
            return f"🟡 {target} — переподключается"

        return f"🔐 {target} — нужен вход"


    async def start(self):
        for slot in (2, 3):
            if self.get_source(slot) and self.database.get(self._session_key(slot)):
                with suppress(Exception):
                    await self._connect_saved(slot)

    async def close(self):
        for client in list(self.clients.values()) + list(self.login_clients.values()):
            with suppress(Exception):
                await client.disconnect()
        self.clients.clear()
        self.login_clients.clear()

    async def _connect_saved(self, slot):
        slot = int(slot)
        session_value = self.database.get(self._session_key(slot)) or ""
        if not session_value:
            return False

        client = TelegramClient(StringSession(session_value), API_ID, API_HASH)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                self.database.delete(self._session_key(slot))
                await client.disconnect()
                return False
            self.clients[slot] = client
            return True
        except DEAD_SESSION_ERRORS:
            self.database.delete(self._session_key(slot))
            with suppress(Exception):
                await client.disconnect()
            self.clients.pop(slot, None)
            return False

    async def begin_login(self, slot, phone):
        slot = int(slot)
        phone = (phone or "").replace(" ", "").strip()
        if slot not in (2, 3):
            raise ValueError("Поддерживаются слоты 2 и 3")
        if not phone.startswith("+"):
            raise ValueError("Номер нужен в международном формате, например +31612345678")

        old = self.login_clients.pop(slot, None)
        if old:
            with suppress(Exception):
                await old.disconnect()

        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        sent = await client.send_code_request(phone)

        self.login_clients[slot] = client
        self.login_phone[slot] = phone
        self.login_hash[slot] = sent.phone_code_hash
        return True

    async def submit_code(self, slot, code):
        slot = int(slot)
        client = self.login_clients.get(slot)
        if not client:
            raise RuntimeError("Сначала запроси новый код для этого аккаунта")

        try:
            await client.sign_in(
                phone=self.login_phone.get(slot),
                code=(code or "").replace(" ", "").replace("-", ""),
                phone_code_hash=self.login_hash.get(slot),
            )
        except SessionPasswordNeededError:
            return "password_required"

        await self._finish_login(slot)
        return "ready"

    async def submit_2fa(self, slot, password):
        slot = int(slot)
        client = self.login_clients.get(slot)
        if not client:
            raise RuntimeError("Сессия входа потеряна. Начни вход заново.")
        await client.sign_in(password=password)
        await self._finish_login(slot)
        return "ready"

    async def _finish_login(self, slot):
        client = self.login_clients.pop(slot)
        session_value = client.session.save()
        if not session_value:
            raise RuntimeError("Не удалось сохранить Telegram StringSession")

        self.database.set(self._session_key(slot), session_value)

        old = self.clients.pop(slot, None)
        if old:
            with suppress(Exception):
                await old.disconnect()

        self.clients[slot] = client
        self.login_phone.pop(slot, None)
        self.login_hash.pop(slot, None)

    async def _ensure_client(self, slot):
        slot = int(slot)
        client = self.clients.get(slot)

        if client and client.is_connected():
            try:
                if await client.is_user_authorized():
                    return client
            except DEAD_SESSION_ERRORS:
                pass

        self.clients.pop(slot, None)
        if await self._connect_saved(slot):
            return self.clients[slot]

        raise RuntimeError(f"Аккаунт {slot}: нужна повторная авторизация")

    async def _snapshot_dialog(self, client, bot_name, limit=100):
        """
        Snapshot supplier dialog before a request.
        Needed because some supplier bots EDIT an old price message instead of
        sending a brand new message.
        """
        messages = await client.get_messages(bot_name, limit=limit)
        snapshot = {}

        for msg in messages:
            raw = (msg.raw_text or "").strip()
            snapshot[msg.id] = {
                "text": raw,
                "edit_date": str(getattr(msg, "edit_date", None) or ""),
            }

        return snapshot


    async def _collect(self, client, bot_name, after_id, timeout, before_snapshot=None):
        loop = asyncio.get_running_loop()
        started = loop.time()
        last_new = None
        found = {}
        before_snapshot = before_snapshot or {}

        while True:
            messages = await client.get_messages(bot_name, limit=100)
            changed = False

            for msg in messages:
                if msg.out:
                    continue

                raw = (msg.raw_text or "").strip()
                if not raw:
                    continue

                # Case 1: supplier sent a new incoming message after our request.
                is_new_message = msg.id > after_id

                # Case 2: supplier edited an old persistent price message.
                old = before_snapshot.get(msg.id)
                is_edited_old = False

                if old is not None:
                    old_text = old.get("text", "")
                    old_edit_date = old.get("edit_date", "")
                    current_edit_date = str(getattr(msg, "edit_date", None) or "")

                    if raw != old_text or current_edit_date != old_edit_date:
                        is_edited_old = True

                if not is_new_message and not is_edited_old:
                    continue

                if msg.id not in found or found[msg.id].raw_text != msg.raw_text:
                    changed = True

                found[msg.id] = msg

            now = loop.time()

            if changed:
                last_new = now

            if found and last_new is not None and now - last_new >= 2.0:
                break

            if not found and now - started >= timeout:
                break

            if now - started >= max(timeout + 10, 40):
                break

            await asyncio.sleep(0.7)

        return [found[mid] for mid in sorted(found)]


    async def request_bot_price(self, slot):
        slot = int(slot)
        cfg = self.get_source(slot)
        if not cfg or not cfg.get("enabled", True):
            return None

        bot_name = (cfg.get("bot") or "").strip()
        request_text = (cfg.get("request") or "/prices").strip()
        button_path = cfg.get("button_path") or []

        if isinstance(button_path, str):
            button_path = [x.strip() for x in button_path.split(">") if x.strip()]

        if not bot_name:
            return None

        async with self._locks[slot]:
            client = await self._ensure_client(slot)

            try:
                # Snapshot BEFORE the request. Some supplier bots update an old
                # price message instead of sending a new reply.
                before_snapshot = await self._snapshot_dialog(client, bot_name)

                print(
                    f"📡 Источник {slot}: отправляю {request_text!r} → {bot_name}",
                    flush=True,
                )
                self.state.set(
                    f"source_{slot}_last_action",
                    f"отправляю {request_text} → {bot_name}",
                )

                entity = await client.get_entity(bot_name)
                sent = await client.send_message(entity, request_text)

                print(
                    f"✅ Источник {slot}: запрос отправлен, message_id={sent.id}",
                    flush=True,
                )
                self.state.set(
                    f"source_{slot}_last_action",
                    f"запрос отправлен → {bot_name}",
                )

                if button_path:
                    batch = await self._collect(
                        client,
                        bot_name,
                        sent.id,
                        RESPONSE_TIMEOUT,
                        before_snapshot=before_snapshot,
                    )
                    if not batch:
                        raise RuntimeError("поставщик не ответил")

                    response = batch[-1]

                    for button_name in button_path:
                        clicked = await response.click(text=button_name)
                        if clicked is None:
                            raise RuntimeError(f"не найдена кнопка: {button_name}")

                        await asyncio.sleep(AFTER_ACTION_DELAY)
                        latest = await client.get_messages(bot_name, limit=1)
                        if latest:
                            response = latest[0]

                messages = await self._collect(
                    client,
                    bot_name,
                    sent.id,
                    RESPONSE_TIMEOUT,
                    before_snapshot=before_snapshot,
                )

                parts = [
                    (msg.raw_text or "").strip()
                    for msg in messages
                    if (msg.raw_text or "").strip()
                ]

                if not parts:
                    self.state.set(
                        f"source_{slot}_last_action",
                        f"⚠️ {bot_name}: команда отправлена, но ответ не найден",
                    )
                    raise RuntimeError("поставщик не прислал прайс")

                result = "\n".join(parts)
                self.state.set(
                    f"source_{slot}_last_action",
                    f"✅ {bot_name}: прайс получен ({len(parts)} сообщ.)",
                )
                return result

            except DEAD_SESSION_ERRORS as e:
                self.database.delete(self._session_key(slot))
                old = self.clients.pop(slot, None)
                if old:
                    with suppress(Exception):
                        await old.disconnect()

                raise RuntimeError(
                    f"Аккаунт {slot}: Telegram-сессия слетела — войди заново"
                ) from e


    async def _chat_client_candidates(self, preferred_slot):
        """
        Return already-authorized Telegram clients for reading a group/channel.
        No new login is required for a chat source.
        """
        candidates = []
        seen = set()

        # Prefer the primary HI account first.
        possible = [self.primary_client]

        # Then the configured bot account for slot 2/3, and any other connected source.
        possible.append(self.clients.get(int(preferred_slot)))
        possible.extend(self.clients.get(slot) for slot in (2, 3))

        for client in possible:
            if client is None:
                continue
            ident = id(client)
            if ident in seen:
                continue
            seen.add(ident)

            try:
                if not client.is_connected():
                    await client.connect()
                if await client.is_user_authorized():
                    candidates.append(client)
            except DEAD_SESSION_ERRORS:
                continue
            except Exception:
                continue

        return candidates


    async def read_chat_price(self, slot):
        """
        Read the newest price batch from a normal Telegram group/channel.

        IMPORTANT:
        - sends NOTHING to the group;
        - does NOT require a third Telegram login;
        - reuses the already connected HI account / second supplier account;
        - automatically tries available authorized accounts until one has access.
        """
        slot = int(slot)
        cfg = self.get_source(slot)

        if not cfg or not cfg.get("enabled", True):
            return None

        chat_ref_raw = (cfg.get("chat") or "").strip()
        if not chat_ref_raw:
            return None

        async with self._locks[slot]:
            chat_ref = _normalize_chat_ref(chat_ref_raw)
            candidates = await self._chat_client_candidates(slot)

            if not candidates:
                raise RuntimeError(
                    "нет подключённого Telegram-аккаунта для чтения группы"
                )

            last_error = None

            for client in candidates:
                try:
                    entity = await client.get_entity(chat_ref)

                    messages = await client.get_messages(
                        entity,
                        limit=int(cfg.get("chat_scan_limit") or 100),
                    )

                    price_messages = []

                    for msg in messages:
                        raw = (msg.raw_text or "").strip()
                        if not raw:
                            continue

                        rows = _count_price_rows(raw)
                        if rows <= 0:
                            continue

                        price_messages.append((msg, raw, rows))

                    if not price_messages:
                        last_error = RuntimeError(
                            f"в {chat_ref_raw} среди последних сообщений прайс не найден"
                        )
                        continue

                    newest_msg = price_messages[0][0]
                    newest_date = getattr(newest_msg, "date", None)

                    selected = []

                    for msg, raw, rows in price_messages:
                        msg_date = getattr(msg, "date", None)

                        if newest_date is not None and msg_date is not None:
                            age_seconds = (newest_date - msg_date).total_seconds()
                            if age_seconds > 15 * 60:
                                break

                        selected.append((msg, raw))

                        if len(selected) >= 30:
                            break

                    selected.sort(key=lambda item: item[0].id)
                    result = "\n".join(raw for _, raw in selected).strip()

                    if result:
                        return result

                except DEAD_SESSION_ERRORS as e:
                    last_error = e
                    continue
                except Exception as e:
                    last_error = e
                    continue

            raise RuntimeError(
                f"ни один подключённый аккаунт не может прочитать {chat_ref_raw}: "
                f"{last_error or 'нет доступа'}"
            )


    async def request_source_price(self, slot):
        cfg = self.get_source(slot)
        source_type = (cfg.get("type") or "bot").strip().lower()

        if source_type == "chat":
            return await self.read_chat_price(slot)

        return await self.request_bot_price(slot)


    async def collect_extra_prices(self):
        """
        Collect configured source 2/3 in parallel.

        This is important for supplier bots: a slow main/other supplier must not
        delay sending /prices to another bot.
        """
        configured_slots = []

        for slot in (2, 3):
            cfg = self.get_source(slot)
            if not cfg or not cfg.get("enabled", True):
                continue

            source_type = (cfg.get("type") or "bot").strip().lower()
            configured = (
                bool(cfg.get("chat"))
                if source_type == "chat"
                else bool(cfg.get("bot"))
            )

            if configured:
                configured_slots.append(slot)

        async def one(slot):
            cfg = self.get_source(slot)
            source_type = (cfg.get("type") or "bot").strip().lower()

            try:
                raw = await self.request_source_price(slot)
                if not raw:
                    return None, None

                label = (
                    f"Группа {slot}"
                    if source_type == "chat"
                    else f"Бот {slot}"
                )
                return (label, raw), None

            except Exception as e:
                return None, f"Источник {slot}: {e}"

        if not configured_slots:
            return [], []

        pairs = await asyncio.gather(
            *(one(slot) for slot in configured_slots),
            return_exceptions=False,
        )

        results = []
        errors = []

        for result, error in pairs:
            if result:
                results.append(result)
            if error:
                errors.append(error)

        return results, errors

