# homed/adapters/gate.py
import threading
import time

from homed.adapters.base import Adapter
from homed.model import Control


def _door_status(d):
    if d.get("is_held"):
        return "Held open"
    return {"locked": "Locked", "unlocked": "Unlocked", "open": "Open"}.get(d.get("status"), "Unknown")


class GateAdapter(Adapter):
    domain = "gate"

    def snapshot(self):
        doors = self.get_json("/devices")
        out = []
        for d in doors:
            out.append(
                Control(
                    domain="gate",
                    id=d["id"],
                    name=d.get("name", d["id"]),
                    kind="momentary",
                    on=bool(d.get("is_held")),
                    status=_door_status(d),
                    online=bool(d.get("is_online", True)),
                )
            )
        locked = sum(1 for d in doors if d.get("status") == "locked")
        out.append(
            Control(
                domain="gate",
                id="gate",
                name="Gate",
                kind="momentary",
                on=any(d.get("is_held") for d in doors),
                status=f"{locked} locked",
                online=any(d.get("is_online", True) for d in doors),
            )
        )
        return out

    def command(self, control_id, payload):  # filled in Task 12
        raise NotImplementedError

    # polling upstream (no client WS on unifi-gate)
    def start(self, on_change):
        self._on_change = on_change
        t = threading.Thread(target=self._poll, daemon=True)
        t.start()
        return t

    def _poll(self):
        while True:
            try:
                self.snapshot()
                if getattr(self, "_on_change", None):
                    self._on_change()
            except Exception:
                pass
            time.sleep(3)
