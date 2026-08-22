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
    def __init__(self, path: Path, default_interval: int):
        self.path = Path(path)
        self.data = {
            "sync_enabled": True,
            "interval": default_interval,
            "blocks": DEFAULT_BLOCKS.copy(),
            "block_order": DEFAULT_BLOCK_ORDER.copy(),
            "message_ids": {},
            "nav_message_id": None,
            "closed_message_id": None,
            "last_supplier_message_id": None,
            "last_supplier_message_ids": [],
            "last_check_ts": None,
            "next_check_ts": None,
            "last_result": "ещё не запускался",
            "supplier_closed": False,
            "supplier_status": "unknown",
            "schedule_closed": False,
            "last_full_price": "",
            "markup_amount": 0,
        }
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data.update(raw)
                blocks = DEFAULT_BLOCKS.copy()
                blocks.update(raw.get("blocks") or {})
                self.data["blocks"] = blocks

                saved_order = raw.get("block_order") or []
                valid_order = []

                for key in saved_order:
                    if key in DEFAULT_BLOCK_ORDER and key not in valid_order:
                        valid_order.append(key)

                for key in DEFAULT_BLOCK_ORDER:
                    if key not in valid_order:
                        valid_order.append(key)

                self.data["block_order"] = valid_order
        except Exception:
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def update(self, **kwargs):
        self.data.update(kwargs)
        self.save()

    def toggle_block(self, key):
        blocks = self.data["blocks"]
        blocks[key] = not blocks.get(key, True)
        self.save()
        return blocks[key]
