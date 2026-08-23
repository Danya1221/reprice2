import json
import time
from pathlib import Path


DEFAULT_BLOCKS = {
    "12": False,
    "13": False,
    "14": False,
    "15_sim": True,
    "15_esim": True,
    "16_sim": True,
    "16_esim": True,
    "17_sim": True,
    "17_esim": True,
    "other": False,
}

DEFAULT_BLOCK_ORDER = [
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
]



class StateStore:
    """
    PostgreSQL-backed persistent state.

    The local JSON file is only a one-time migration/fallback source.
    PostgreSQL is the canonical store, so Railway redeploys do not reset
    blocks, order, markup, cached price, navigation ids, or other settings.
    """

    DB_KEY = "bot_state_v1"

    def __init__(self, path, default_interval, database):
        self.path = Path(path)
        self.database = database

        self.data = {
            "sync_enabled": True,
            "interval": int(default_interval),
            "blocks": DEFAULT_BLOCKS.copy(),
            "block_order": DEFAULT_BLOCK_ORDER.copy(),
            "discovered_blocks": {},

            "message_ids": {},
            "catalog_message_ids": {},
            "block_extra_message_ids": {},
            "nav_message_id": None,
            "closed_message_id": None,

            "supplier_closed": False,
            "supplier_status": "unknown",
            "schedule_closed": False,
            "last_full_price": "",
            "last_supplier_message_id": None,
            "last_supplier_message_ids": [],
            "last_result": "—",

            "last_check_ts": None,
            "next_check_ts": None,
            "markup_amount": 0,

            "tg_auth_status": "starting",
            "tg_auth_user": "",
        }

        self.load()

    def _normalized(self, raw):
        if not isinstance(raw, dict):
            return None

        result = dict(self.data)
        result.update(raw)

        discovered = raw.get("discovered_blocks") or {}
        if not isinstance(discovered, dict):
            discovered = {}

        result["discovered_blocks"] = {
            str(key): str(title)
            for key, title in discovered.items()
            if str(key).startswith("auto_")
        }

        blocks = DEFAULT_BLOCKS.copy()
        saved_blocks = raw.get("blocks") or {}
        if isinstance(saved_blocks, dict):
            blocks.update(saved_blocks)

        for key in result["discovered_blocks"]:
            if key not in saved_blocks:
                blocks[key] = True

        result["blocks"] = blocks

        allowed = set(DEFAULT_BLOCK_ORDER) | set(result["discovered_blocks"])
        saved_order = raw.get("block_order") or []
        valid_order = []

        for key in saved_order:
            if key in allowed and key not in valid_order:
                valid_order.append(key)

        for key in DEFAULT_BLOCK_ORDER:
            if key == "other":
                continue
            if key not in valid_order:
                valid_order.append(key)

        for key in result["discovered_blocks"]:
            if key not in valid_order:
                valid_order.append(key)

        if "other" not in valid_order:
            valid_order.append("other")

        result["block_order"] = valid_order

        for key in (
            "message_ids",
            "catalog_message_ids",
            "block_extra_message_ids",
        ):
            if not isinstance(result.get(key), dict):
                result[key] = {}

        if not isinstance(result.get("last_supplier_message_ids"), list):
            one = result.get("last_supplier_message_id")
            result["last_supplier_message_ids"] = [one] if one else []

        try:
            result["interval"] = int(result.get("interval") or 1800)
        except Exception:
            result["interval"] = 1800

        try:
            result["markup_amount"] = int(result.get("markup_amount") or 0)
        except Exception:
            result["markup_amount"] = 0

        return result

    def _load_local_for_migration(self):
        try:
            if not self.path.exists():
                return None
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._normalized(raw)
        except Exception:
            return None

    def load(self):
        # PostgreSQL is always first and canonical.
        try:
            payload = self.database.get(self.DB_KEY)
            if payload:
                loaded = self._normalized(json.loads(payload))
                if loaded is not None:
                    self.data = loaded
                    print("✅ Настройки загружены из PostgreSQL", flush=True)
                    return
        except Exception as e:
            raise RuntimeError(f"Не удалось прочитать state из PostgreSQL: {e}")

        # One-time migration of an old local state if it happens to exist.
        migrated = self._load_local_for_migration()
        if migrated is not None:
            self.data = migrated
            self.save()
            print("♻️ Старые настройки перенесены в PostgreSQL", flush=True)
            return

        # First DB launch.
        self.save()
        print("✅ Новый state создан в PostgreSQL", flush=True)

    def save(self):
        payload = json.dumps(
            self.data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.database.set(self.DB_KEY, payload)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def update(self, **kwargs):
        self.data.update(kwargs)
        self.save()

