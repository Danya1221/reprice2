import asyncio
from contextlib import suppress

from telethon import TelegramClient
from telethon.errors import AuthKeyDuplicatedError, SessionPasswordNeededError
from telethon.sessions import StringSession

from config import (
    API_HASH,
    API_ID,
    SUPPLIER_BOT,
    TARGET_CHANNEL,
)
from sync_engine import DISPLAY_BLOCK_ORDER, PriceSyncEngine


class TelegramRuntime:
    """
    Telethon runtime backed by PostgreSQL StringSession.

    No Telegram authorization is stored in the Railway container filesystem.
    A PostgreSQL advisory lock prevents rolling deployments from using the
    same auth key simultaneously.
    """

    SESSION_DB_KEY = "telethon_session_v1"

    def __init__(self, state, database):
        self.state = state
        self.database = database
        self.client = None
        self.engine = None
        self.periodic_task = None
        self.auth_lock = asyncio.Lock()
        self.login_phone = None
        self.phone_code_hash = None
        self._closing = False

    @property
    def ready(self):
        return self.engine is not None and self.state.get("tg_auth_status") == "ready"

    def _on_lock_wait(self):
        self.state.update(
            tg_auth_status="waiting_instance",
            last_result=(
                "Жду завершения предыдущего Railway deployment. "
                "PostgreSQL защищает Telegram-сессию от двойного запуска."
            ),
        )
        print("⏳ Жду PostgreSQL runtime lock…", flush=True)

    async def acquire_instance_lock(self):
        await asyncio.to_thread(
            self.database.acquire_runtime_lock,
            self._on_lock_wait,
        )
        print("🔒 PostgreSQL runtime lock acquired", flush=True)

    async def start(self):
        self.state.set("tg_auth_status", "starting")

        await self.acquire_instance_lock()
        await self._new_client()

        try:
            await self.client.connect()

            if await self.client.is_user_authorized():
                await self._activate()
            else:
                self.state.update(
                    tg_auth_status="login_required",
                    tg_auth_user="",
                    supplier_status="unknown",
                    last_result=(
                        "Нужен один вход в Telegram. "
                        "Нажми «🔐 Войти в Telegram»."
                    ),
                )
                print("🔐 В PostgreSQL пока нет авторизованной Telegram-сессии", flush=True)

        except AuthKeyDuplicatedError as e:
            # A duplicated key cannot be revived. Remove it from DB so it never
            # causes an endless reconnect loop.
            print(f"❌ Telegram аннулировал старый auth key: {e}", flush=True)
            self.database.delete(self.SESSION_DB_KEY)
            await self._prepare_login(reset_session=False)

        except Exception as e:
            self.state.update(
                tg_auth_status="error",
                supplier_status="error",
                last_result=f"Ошибка подключения Telegram: {e}",
            )
            print(f"❌ Telegram runtime start: {e}", flush=True)

    async def _new_client(self, empty=False):
        if self.client:
            with suppress(Exception):
                await self.client.disconnect()

        session_string = "" if empty else (self.database.get(self.SESSION_DB_KEY) or "")

        try:
            session = StringSession(session_string)
        except Exception:
            # Corrupted DB value: clear it rather than crash forever.
            self.database.delete(self.SESSION_DB_KEY)
            session = StringSession()

        self.client = TelegramClient(
            session,
            API_ID,
            API_HASH,
        )

    def _save_session_to_db(self):
        if not self.client:
            return False

        try:
            value = self.client.session.save()
        except Exception as e:
            raise RuntimeError(f"Не удалось сериализовать Telegram session: {e}")

        if not value:
            return False

        self.database.set(self.SESSION_DB_KEY, value)
        print("💾 Telegram session сохранена в PostgreSQL", flush=True)
        return True

    async def _prepare_login(self, reset_session=False):
        await self._stop_engine()

        if self.client:
            with suppress(Exception):
                await self.client.disconnect()

        if reset_session:
            self.database.delete(self.SESSION_DB_KEY)

        await self._new_client(empty=True)
        await self.client.connect()

        self.state.update(
            tg_auth_status="login_required",
            tg_auth_user="",
            supplier_status="unknown",
            last_result="Нужен вход в Telegram.",
        )

    async def _resolve_target(self):
        if isinstance(TARGET_CHANNEL, int):
            dialogs = await self.client.get_dialogs(limit=None)

            for dialog in dialogs:
                if dialog.id == TARGET_CHANNEL:
                    return dialog.entity

            raise RuntimeError(
                f"Канал {TARGET_CHANNEL} не найден у Telegram-аккаунта."
            )

        return await self.client.get_entity(TARGET_CHANNEL)

    async def _activate(self):
        if not self.client.is_connected():
            await self.client.connect()

        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram user-session не авторизована")

        # Persist before any supplier/channel work. Once sign-in succeeds,
        # the auth key is safe in PostgreSQL even if this container dies.
        self._save_session_to_db()

        me = await self.client.get_me()
        target_entity = await self._resolve_target()

        await self._stop_engine()

        self.engine = PriceSyncEngine(
            self.client,
            self.state,
            target_entity,
            auth_failure_callback=self.handle_auth_failure,
        )

        await self.engine.start()

        self.state.update(
            tg_auth_status="ready",
            tg_auth_user=(me.username or str(me.id)),
            supplier_status=self.state.get("supplier_status", "unknown"),
            last_result="Telegram подключен. Прайс готов к синхронизации.",
        )

        print(f"✅ Telethon: {me.username or me.id}", flush=True)
        print(f"📦 {SUPPLIER_BOT} → {TARGET_CHANNEL}", flush=True)

        if self.state.get("sync_enabled", True):
            try:
                await self.engine.sync_once()
            except Exception as e:
                print(f"⚠️ Первый запрос не удался: {e}", flush=True)

        self.periodic_task = asyncio.create_task(
            self.engine.periodic_loop()
        )

    async def _stop_engine(self):
        current = asyncio.current_task()

        if self.periodic_task:
            task = self.periodic_task
            self.periodic_task = None

            if task is not current:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

        if self.engine:
            old_engine = self.engine
            self.engine = None
            with suppress(Exception):
                await old_engine.close()

    async def handle_auth_failure(self, error=None):
        async with self.auth_lock:
            await self._stop_engine()

            if self.client:
                with suppress(Exception):
                    await self.client.disconnect()

            # AuthKeyDuplicatedError means this specific auth key is dead.
            if isinstance(error, AuthKeyDuplicatedError):
                self.database.delete(self.SESSION_DB_KEY)

            self.state.update(
                tg_auth_status="login_required",
                tg_auth_user="",
                supplier_status="error",
                last_result=(
                    "Telegram-сессия недействительна. "
                    "Нажми «🔐 Войти в Telegram»."
                ),
            )

    async def begin_phone_login(self, phone):
        async with self.auth_lock:
            if self.ready:
                return "ready"

            # Always start a fresh auth key for an explicit login.
            await self._prepare_login(reset_session=True)

            phone = (phone or "").strip()
            if not phone:
                raise ValueError("Номер телефона пустой")

            sent = await self.client.send_code_request(phone)
            self.login_phone = phone
            self.phone_code_hash = sent.phone_code_hash

            self.state.update(
                tg_auth_status="code_required",
                last_result="Код Telegram отправлен. Пришли код в управляющий бот.",
            )
            return "code_required"

    async def submit_login_code(self, code):
        if self.state.get("tg_auth_status") != "code_required":
            raise RuntimeError("Код Telegram сейчас не запрашивается")

        code = (code or "").replace(" ", "").strip()
        if not code:
            raise ValueError("Код пустой")

        try:
            await self.client.sign_in(
                phone=self.login_phone,
                code=code,
                phone_code_hash=self.phone_code_hash,
            )
        except SessionPasswordNeededError:
            self.state.update(
                tg_auth_status="password_required",
                last_result="Нужен пароль двухэтапной защиты Telegram.",
            )
            return "password_required"

        self._save_session_to_db()
        self.state.set("sync_enabled", True)
        await self._activate()

        self.login_phone = None
        self.phone_code_hash = None
        return "ready"

    async def submit_2fa(self, password):
        if self.state.get("tg_auth_status") != "password_required":
            raise RuntimeError("Пароль 2FA сейчас не запрашивается")

        await self.client.sign_in(password=password)
        self._save_session_to_db()
        self.state.set("sync_enabled", True)
        await self._activate()

        self.login_phone = None
        self.phone_code_hash = None
        return True

    def _require_engine(self):
        if not self.engine:
            raise RuntimeError(
                "Telegram не подключен. Нажми «🔐 Войти в Telegram»."
            )
        return self.engine

    async def sync_once(self, *args, **kwargs):
        return await self._require_engine().sync_once(*args, **kwargs)

    async def set_markup(self, amount):
        if not self.engine:
            amount = int(amount)
            if amount < 0:
                raise ValueError("Наценка не может быть отрицательной")
            self.state.set("markup_amount", amount)
            return
        return await self.engine.set_markup(amount)

    async def set_block_enabled(self, key, enabled):
        return await self._require_engine().set_block_enabled(key, enabled)

    async def set_all_blocks(self, enabled):
        return await self._require_engine().set_all_blocks(enabled)

    async def rebuild_block_order(self):
        return await self._require_engine().rebuild_block_order()

    def move_block(self, key, direction):
        if self.engine:
            return self.engine.move_block(key, direction)

        order = list(self.state.get("block_order") or [])
        discovered = self.state.get("discovered_blocks") or {}
        allowed = set(DISPLAY_BLOCK_ORDER) | set(discovered)
        order = [x for x in order if x in allowed]

        for x in DISPLAY_BLOCK_ORDER:
            if x not in order:
                order.append(x)
        for x in discovered:
            if x not in order:
                order.append(x)

        if key not in order:
            return order

        old_index = order.index(key)
        new_index = old_index + int(direction)

        if 0 <= new_index < len(order):
            order[old_index], order[new_index] = order[new_index], order[old_index]
            self.state.set("block_order", order)

        return order

    def reset_block_order(self):
        if self.engine:
            return self.engine.reset_block_order()

        order = [x for x in DISPLAY_BLOCK_ORDER if x != "other"]
        for x in (self.state.get("discovered_blocks") or {}):
            if x not in order:
                order.append(x)
        order.append("other")
        self.state.set("block_order", order)
        return order

    def wake(self):
        if self.engine:
            self.engine.wake()

    async def close(self):
        self._closing = True
        await self._stop_engine()

        if self.client:
            with suppress(Exception):
                # Capture any final session updates before disconnect.
                if await self.client.is_user_authorized():
                    self._save_session_to_db()
            with suppress(Exception):
                await self.client.disconnect()

        await asyncio.to_thread(self.database.release_runtime_lock)
