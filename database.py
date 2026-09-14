import threading
import time

import psycopg


STORE_TABLE = "pricebot_store"
RUNTIME_LOCK_ID = 734281906251


class DatabaseStore:
    """
    Small PostgreSQL key/value store for the bot.

    Two things are persisted here:
    - the entire control-bot state as JSON text;
    - the Telethon StringSession.

    A dedicated PostgreSQL advisory lock prevents two Railway deployments
    from using the same Telegram authorization key at the same time.
    """

    def __init__(self, database_url: str):
        self.database_url = (database_url or "").strip()
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL не задан. Подключи Railway PostgreSQL к сервису."
            )

        self._conn = None
        self._mutex = threading.RLock()
        self._lock_conn = None
        self.init_schema()

    def _connect(self):
        return psycopg.connect(
            self.database_url,
            autocommit=True,
            connect_timeout=10,
        )

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = self._connect()
        return self._conn

    def _reset_conn(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _execute(self, sql, params=(), fetchone=False):
        last_error = None

        with self._mutex:
            for attempt in range(2):
                try:
                    conn = self._get_conn()
                    cur = conn.execute(sql, params)
                    return cur.fetchone() if fetchone else None
                except Exception as e:
                    last_error = e
                    self._reset_conn()
                    if attempt == 0:
                        time.sleep(0.25)

        raise last_error

    def init_schema(self):
        self._execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STORE_TABLE} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def get(self, key, default=None):
        row = self._execute(
            f"SELECT value FROM {STORE_TABLE} WHERE key = %s",
            (str(key),),
            fetchone=True,
        )
        return row[0] if row else default

    def set(self, key, value):
        self._execute(
            f"""
            INSERT INTO {STORE_TABLE} (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (str(key), str(value)),
        )

    def delete(self, key):
        self._execute(
            f"DELETE FROM {STORE_TABLE} WHERE key = %s",
            (str(key),),
        )

    def acquire_runtime_lock(self, on_wait=None):
        """
        Hold a session-level PostgreSQL advisory lock for the full process.
        Railway rolling deploys therefore cannot connect the same Telethon
        session from two containers simultaneously.
        """
        if self._lock_conn is not None and not self._lock_conn.closed:
            return True

        conn = self._connect()
        waited = False

        while True:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(%s)",
                (RUNTIME_LOCK_ID,),
            ).fetchone()

            if row and row[0]:
                self._lock_conn = conn
                return True

            if not waited and on_wait:
                try:
                    on_wait()
                except Exception:
                    pass
                waited = True

            time.sleep(2)

    def release_runtime_lock(self):
        conn = self._lock_conn
        self._lock_conn = None
        if conn is None:
            return

        try:
            if not conn.closed:
                conn.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (RUNTIME_LOCK_ID,),
                )
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def close(self):
        self.release_runtime_lock()
        self._reset_conn()
