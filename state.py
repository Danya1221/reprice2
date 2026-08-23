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
    Persistent bot state.

    Saves every setting atomically and keeps a .bak copy.
    If the main JSON is damaged, it automatically restores from backup.
    """

    def __init__(self, path, default_interval):
        self.path = Path(path)
        self.backup_path = Path(str(self.path) + ".bak")
        self.tmp_path = Path(str(self.path) + ".tmp")

        self.data = {
            "sync_enabled": True,
            "interval": int(default_interval),
            "blocks": DEFAULT_BLOCKS.copy(),
            "block_order": DEFAULT_BLOCK_ORDER.copy(),
            "discovered_blocks": {},

            # Telegram/channel structure
            "message_ids": {},
            "catalog_message_ids": {},
            "block_extra_message_ids": {},
            "nav_message_id": None,
            "closed_message_id": None,

            # Supplier / cached price
            "supplier_closed": False,
            "supplier_status": "unknown",
            "schedule_closed": False,
            "last_full_price": "",
            "last_supplier_message_id": None,
            "last_supplier_message_ids": [],
            "last_result": "—",

            # Scheduling/status
            "last_check_ts": None,
            "next_check_ts": None,

            # User settings
            "markup_amount": 0,

            # Telegram auth state
            "tg_auth_status": "starting",
            "tg_auth_user": "",
        }

        self.load()

    def _normalized(self, raw):
        if not isinstance(raw, dict):
            return None

        result = dict(self.data)
        result.update(raw)

        # Persist automatically discovered real blocks.
        discovered = raw.get("discovered_blocks") or {}
        if not isinstance(discovered, dict):
            discovered = {}

        result["discovered_blocks"] = {
            str(key): str(title)
            for key, title in discovered.items()
            if str(key).startswith("auto_")
        }

        # Merge built-in + saved + discovered block switches.
        blocks = DEFAULT_BLOCKS.copy()
        saved_blocks = raw.get("blocks") or {}

        if isinstance(saved_blocks, dict):
            blocks.update(saved_blocks)

        for key in result["discovered_blocks"]:
            # New discovered blocks default ON, but never override a saved OFF.
            if key not in saved_blocks:
                blocks[key] = True

        result["blocks"] = blocks

        # Preserve user's custom order including discovered blocks.
        allowed = set(DEFAULT_BLOCK_ORDER) | set(result["discovered_blocks"])
        saved_order = raw.get("block_order") or []
        valid_order = []

        for key in saved_order:
            if key in allowed and key not in valid_order:
                valid_order.append(key)

        # Built-ins that were missing from old state.
        for key in DEFAULT_BLOCK_ORDER:
            if key not in valid_order:
                # Keep "other" as the final fallback until discovered blocks are appended.
                if key == "other":
                    continue
                valid_order.append(key)

        # Discovered blocks survive restarts and are appended if not explicitly ordered.
        for key in result["discovered_blocks"]:
            if key not in valid_order:
                valid_order.append(key)

        if "other" not in valid_order:
            valid_order.append("other")

        result["block_order"] = valid_order

        # Defensive migrations.
        if not isinstance(result.get("message_ids"), dict):
            result["message_ids"] = {}

        if not isinstance(result.get("catalog_message_ids"), dict):
            result["catalog_message_ids"] = {}

        if not isinstance(result.get("block_extra_message_ids"), dict):
            result["block_extra_message_ids"] = {}

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

    def _read_json(self, path):
        if not path.exists():
            return None

        raw = json.loads(
            path.read_text(encoding="utf-8")
        )
        return self._normalized(raw)

    def load(self):
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Main state first.
        try:
            loaded = self._read_json(self.path)

            if loaded is not None:
                self.data = loaded
                return
        except Exception as e:
            print(
                f"⚠️ Основной state повреждён: {e}",
                flush=True,
            )

        # Automatic backup recovery.
        try:
            loaded = self._read_json(self.backup_path)

            if loaded is not None:
                self.data = loaded
                print(
                    "♻️ Настройки восстановлены из backup",
                    flush=True,
                )
                self.save()
                return
        except Exception as e:
            print(
                f"⚠️ Backup state тоже не читается: {e}",
                flush=True,
            )

        print(
            "ℹ️ Сохранённых настроек пока нет — использую defaults",
            flush=True,
        )

    def save(self):
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = json.dumps(
            self.data,
            ensure_ascii=False,
            indent=2,
        )

        # 1. Write complete new file into .tmp.
        self.tmp_path.write_text(
            payload,
            encoding="utf-8",
        )

        # 2. Preserve current valid file as backup.
        if self.path.exists():
            try:
                current = self.path.read_text(
                    encoding="utf-8"
                )
                json.loads(current)

                self.backup_path.write_text(
                    current,
                    encoding="utf-8",
                )
            except Exception:
                pass

        # 3. Atomic replace.
        self.tmp_path.replace(self.path)

        # 4. First save also creates a backup.
        if not self.backup_path.exists():
            try:
                self.backup_path.write_text(
                    payload,
                    encoding="utf-8",
                )
            except Exception:
                pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def update(self, **kwargs):
        self.data.update(kwargs)
        self.save()

