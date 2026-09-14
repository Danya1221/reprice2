import json
from pathlib import Path


class StateStore:
    """
    Clean v2 state for the two-price bot.

    We intentionally DO NOT reuse old publication/message-id structures from
    previous experimental builds. Only safe user settings are migrated once.
    Telegram StringSession is stored separately by TelegramRuntime and remains
    reusable.
    """

    DB_KEY = "bot_state_v2_two_prices"
    OLD_DB_KEY = "bot_state_v1"

    def __init__(self, path, default_interval, database):
        self.path = Path(path)
        self.database = database
        self.data = {
            "sync_enabled": True,
            "interval": int(default_interval),
            "markup_amount": 0,

            "price_message_ids": {},        # block_key -> [message_id, ...]
            "block_titles": {},             # block_key -> display title
            "known_block_order": [],        # stable order of seen blocks
            "last_products": [],            # merged purchase-price products

            "supplier1_status": "unknown",
            "supplier2_status": "unknown",
            "supplier1_count": 0,
            "supplier2_count": 0,
            "supplier1_last_error": "",
            "supplier2_last_error": "",
            "supplier1_last_check_ts": None,
            "supplier2_last_check_ts": None,

            "last_check_ts": None,
            "next_check_ts": None,
            "last_result": "—",
            "publication_status": "unknown",  # open / closed / waiting / error

            "tg_auth_status": "starting",
            "tg_auth_user": "",
        }
        self.load()

    def _normalize(self, raw):
        result = dict(self.data)
        if isinstance(raw, dict):
            result.update(raw)

        for key in ("price_message_ids", "block_titles"):
            if not isinstance(result.get(key), dict):
                result[key] = {}

        normalized_ids = {}
        for key, ids in (result.get("price_message_ids") or {}).items():
            if not isinstance(ids, list):
                ids = [ids] if ids else []
            clean = []
            for value in ids:
                try:
                    clean.append(int(value))
                except Exception:
                    pass
            if clean:
                normalized_ids[str(key)] = clean
        result["price_message_ids"] = normalized_ids

        if not isinstance(result.get("known_block_order"), list):
            result["known_block_order"] = []
        result["known_block_order"] = [str(x) for x in result["known_block_order"]]

        if not isinstance(result.get("last_products"), list):
            result["last_products"] = []

        try:
            result["interval"] = max(60, int(result.get("interval") or 1800))
        except Exception:
            result["interval"] = 1800
        try:
            result["markup_amount"] = max(0, int(result.get("markup_amount") or 0))
        except Exception:
            result["markup_amount"] = 0

        for key in ("supplier1_count", "supplier2_count"):
            try:
                result[key] = int(result.get(key) or 0)
            except Exception:
                result[key] = 0

        return result

    def load(self):
        payload = self.database.get(self.DB_KEY)
        if payload:
            self.data = self._normalize(json.loads(payload))
            print("✅ Two-price state загружен из PostgreSQL", flush=True)
            return

        # Safe one-time migration: preserve only settings, never old channel IDs.
        old_payload = self.database.get(self.OLD_DB_KEY)
        if old_payload:
            try:
                old = json.loads(old_payload)
                if isinstance(old, dict):
                    self.data["sync_enabled"] = bool(old.get("sync_enabled", True))
                    self.data["interval"] = int(old.get("interval") or self.data["interval"])
                    self.data["markup_amount"] = int(old.get("markup_amount") or 0)
                    print("♻️ Перенесены только интервал/наценка/старт из старого state", flush=True)
            except Exception:
                pass

        self.data = self._normalize(self.data)
        self.save()
        print("✅ Создан чистый two-price state", flush=True)

    def save(self):
        self.database.set(
            self.DB_KEY,
            json.dumps(self.data, ensure_ascii=False, separators=(",", ":")),
        )

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def update(self, **kwargs):
        self.data.update(kwargs)
        self.save()
