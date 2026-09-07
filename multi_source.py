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

MAX_EXTRA_SOURCES = 2  # slots 2 and 3; slot 1 is the existing HI account.


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


class ExtraSourceManager:
    """
    Additional Telegram supplier accounts.

    Slot 1 stays the existing primary HI account.
    Slots 2 and 3 are independent Telethon StringSessions stored in PostgreSQL.
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
        client = self.clients.get(slot_i)
        if client and client.is_connected():
            return f"✅ {cfg.get('bot') or 'поставщик'}"
        if self.database.get(self._session_key(slot_i)):
            return f"🟡 {cfg.get('bot') or 'поставщик'} — переподключается"
        return f"🔐 {cfg.get('bot') or 'поставщик'} — нужен вход"

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

    async def _collect(self, client, bot_name, after_id, timeout):
        loop = asyncio.get_running_loop()
        started = loop.time()
        last_new = None
        found = {}

        while True:
            messages = await client.get_messages(bot_name, limit=100)
            changed = False

            for msg in messages:
                if msg.out or msg.id <= after_id:
                    continue
                if msg.id not in found:
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

    async def request_price(self, slot):
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
                sent = await client.send_message(bot_name, request_text)

                if button_path:
                    batch = await self._collect(
                        client, bot_name, sent.id, RESPONSE_TIMEOUT
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
                    client, bot_name, sent.id, RESPONSE_TIMEOUT
                )

                parts = [
                    (msg.raw_text or "").strip()
                    for msg in messages
                    if (msg.raw_text or "").strip()
                ]
                if not parts:
                    raise RuntimeError("поставщик не прислал прайс")

                return "\n".join(parts)

            except DEAD_SESSION_ERRORS as e:
                self.database.delete(self._session_key(slot))
                old = self.clients.pop(slot, None)
                if old:
                    with suppress(Exception):
                        await old.disconnect()
                raise RuntimeError(
                    f"Аккаунт {slot}: Telegram-сессия слетела — войди заново"
                ) from e

    async def collect_extra_prices(self):
        results = []
        errors = []

        for slot in (2, 3):
            cfg = self.get_source(slot)
            if not cfg or not cfg.get("enabled", True) or not cfg.get("bot"):
                continue

            try:
                raw = await self.request_price(slot)
                if raw:
                    results.append((f"Аккаунт {slot}", raw))
            except Exception as e:
                errors.append(str(e))

        return results, errors
