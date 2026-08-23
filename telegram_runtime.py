import asyncio
import fcntl
import shutil
from contextlib import suppress
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import AuthKeyDuplicatedError, SessionPasswordNeededError

from config import (
    API_HASH,
    API_ID,
    SESSION_FILE,
    SUPPLIER_BOT,
    TARGET_CHANNEL,
)
from sync_engine import DISPLAY_BLOCK_ORDER, PriceSyncEngine


class TelegramRuntime:
    """
    Управляет Telethon независимо от Bot API.

    - Управляющий Telegram-бот работает даже без user-session.
    - Авторизация user-account делается QR-кодом прямо через управляющего бота.
    - Session хранится обычным SQLite-файлом.
    - File lock не даёт двум Railway-инстансам использовать один auth key
      одновременно во время rolling deploy.
    """

    def __init__(self, state):
        self.state = state
        self.client = None
        self.engine = None
        self.periodic_task = None
        self.auth_lock = asyncio.Lock()
        self.login_phone = None
        self.phone_code_hash = None
        self._instance_lock_handle = None
        self._closing = False

    def _session_base_candidates(self):
        """
        Known session locations used by previous bot builds.
        The first existing non-empty session wins.
        """
        configured = Path(SESSION_FILE)

        candidates = [
            configured,
            Path("/data/telegram_user"),
            Path("/data/pricebot/telegram_user"),
            Path("telegram_user"),
        ]

        unique = []
        seen = set()

        for base in candidates:
            key = str(base)
            if key not in seen:
                seen.add(key)
                unique.append(base)

        return unique

    def _session_file_for_base(self, base):
        base = Path(base)

        if base.suffix == ".session":
            return base

        return Path(str(base) + ".session")

    def _find_existing_session_base(self):
        for base in self._session_base_candidates():
            session_file = self._session_file_for_base(base)

            try:
                if session_file.exists() and session_file.stat().st_size > 0:
                    return base
            except Exception:
                pass

        return None

    def _migrate_legacy_session_if_needed(self):
        """
        Migrate any old working session to the canonical SESSION_FILE path.
        This prevents login resets when bot versions changed the session path.
        """
        canonical_base = Path(SESSION_FILE)
        canonical_file = self._session_file_for_base(canonical_base)

        # Canonical already exists -> do nothing.
        try:
            if canonical_file.exists() and canonical_file.stat().st_size > 0:
                return canonical_base
        except Exception:
            pass

        existing_base = self._find_existing_session_base()

        if not existing_base:
            return canonical_base

        existing_file = self._session_file_for_base(existing_base)

        if existing_file == canonical_file:
            return canonical_base

        canonical_file.parent.mkdir(parents=True, exist_ok=True)

        # Copy SQLite session + possible sidecar files.
        suffixes = ["", "-journal", "-shm", "-wal"]

        for suffix in suffixes:
            src = Path(str(existing_file) + suffix)
            dst = Path(str(canonical_file) + suffix)

            try:
                if src.exists():
                    shutil.copy2(src, dst)
            except Exception as e:
                print(
                    f"⚠️ Не удалось перенести session sidecar {src}: {e}",
                    flush=True,
                )

        print(
            f"♻️ Telegram session перенесена: {existing_file} → {canonical_file}",
            flush=True,
        )

        return canonical_base

    @property
    def ready(self):
        return self.engine is not None and self.state.get("tg_auth_status") == "ready"

    def _session_path(self):
        base = Path(SESSION_FILE)
        return base if base.suffix == ".session" else Path(str(base) + ".session")

    def _session_sidecars(self):
        session = self._session_path()
        return [
            session,
            Path(str(session) + "-journal"),
            Path(str(session) + "-shm"),
            Path(str(session) + "-wal"),
        ]

    async def acquire_instance_lock(self):
        """
        Один процесс на один persistent session.
        На Railway это защищает session при перекрывающихся deploy.
        """
        lock_path = Path(str(self._session_path()) + ".instance.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        handle = open(lock_path, "a+")

        while not self._closing:
            try:
                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                self._instance_lock_handle = handle
                print(
                    f"🔒 Instance lock acquired: {lock_path}",
                    flush=True,
                )
                return
            except BlockingIOError:
                self.state.update(
                    tg_auth_status="waiting_instance",
                    last_result=(
                        "Жду завершения предыдущего Railway-инстанса, "
                        "чтобы не дублировать Telegram-сессию."
                    ),
                )
                print(
                    "⏳ Предыдущий Railway-инстанс ещё держит Telegram session. "
                    "Жду…",
                    flush=True,
                )
                await asyncio.sleep(2)

        handle.close()

    def _release_instance_lock(self):
        if not self._instance_lock_handle:
            return

        with suppress(Exception):
            fcntl.flock(
                self._instance_lock_handle.fileno(),
                fcntl.LOCK_UN,
            )
        with suppress(Exception):
            self._instance_lock_handle.close()

        self._instance_lock_handle = None

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
                print("🔐 Telegram user-session не авторизована", flush=True)

        except AuthKeyDuplicatedError as e:
            print(f"❌ Старый auth key уже аннулирован: {e}", flush=True)
            await self._prepare_login(reset_session=True)

        except Exception as e:
            self.state.update(
                tg_auth_status="error",
                supplier_status="error",
                last_result=f"Ошибка подключения Telegram: {e}",
            )
            print(f"❌ Telegram runtime start: {e}", flush=True)

    async def _new_client(self):
        if self.client:
            with suppress(Exception):
                await self.client.disconnect()

        session_base = self._migrate_legacy_session_if_needed()

        self.client = TelegramClient(
            str(session_base),
            API_ID,
            API_HASH,
        )

    async def _prepare_login(self, reset_session=False):
        await self._stop_engine()

        if self.client:
            with suppress(Exception):
                await self.client.disconnect()

        if reset_session:
            for path in self._session_sidecars():
                with suppress(Exception):
                    path.unlink(missing_ok=True)

        await self._new_client()
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

        print(
            f"✅ Telegram user connected: {me.username or me.id}",
            flush=True,
        )
        print(
            f"📦 {SUPPLIER_BOT} → {TARGET_CHANNEL}",
            flush=True,
        )

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
            if self.state.get("tg_auth_status") == "login_required":
                return

            await self._stop_engine()

            if self.client:
                with suppress(Exception):
                    await self.client.disconnect()

            self.state.update(
                tg_auth_status="login_required",
                tg_auth_user="",
                supplier_status="error",
                last_result=(
                    "Telegram-сессия аннулирована. "
                    "Нажми «🔐 Войти в Telegram»."
                ),
            )

    async def begin_phone_login(self, phone):
        """
        Start a normal Telegram login using phone + code.
        """
        async with self.auth_lock:
            if self.ready:
                return "ready"

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
        """
        Confirm the Telegram login code.
        """
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

        self.state.set("sync_enabled", True)

        await self._activate()

        self.login_phone = None
        self.phone_code_hash = None

        return "ready"

    async def submit_2fa(self, password):
        if self.state.get("tg_auth_status") != "password_required":
            raise RuntimeError("Пароль 2FA сейчас не запрашивается")

        await self.client.sign_in(password=password)

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

    # ---- Proxy API used by control_bot.py ----

    async def sync_once(self, *args, **kwargs):
        return await self._require_engine().sync_once(*args, **kwargs)

    async def set_markup(self, amount):
        # Наценку можно сохранить даже пока Telegram не подключен.
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

        order = self.state.get("block_order") or list(DISPLAY_BLOCK_ORDER)
        order = [x for x in order if x in DISPLAY_BLOCK_ORDER]

        for x in DISPLAY_BLOCK_ORDER:
            if x not in order:
                order.append(x)

        if key not in order:
            return order

        old_index = order.index(key)
        new_index = old_index + int(direction)

        if 0 <= new_index < len(order):
            order[old_index], order[new_index] = (
                order[new_index],
                order[old_index],
            )
            self.state.set("block_order", order)

        return order

    def reset_block_order(self):
        if self.engine:
            return self.engine.reset_block_order()

        order = list(DISPLAY_BLOCK_ORDER)
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
                await self.client.disconnect()

        self._release_instance_lock()
