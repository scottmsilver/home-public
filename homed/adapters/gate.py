# homed/adapters/gate.py
import threading
import time
from urllib.parse import quote

import requests

from homed.adapters.base import Adapter
from homed.model import Control


def _door_view(d):
    hs = d.get("hold_state")
    if hs == "hold_forever":
        return "forever", "Held open"
    if hs == "hold_today":
        exp = d.get("expires_at")
        if exp:
            t = time.strftime("%-I:%M %p", time.localtime(exp))
            return "timed", f"Held until {t}"
        return "timed", "Held (timed)"
    if d.get("lock_state") == "lock":
        return None, "Closed"
    if d.get("lock_state") == "unlock":
        return None, "Open"
    # Older daemons / synthetic aggregate have no lock_state: fall back to the
    # flattened position-based status.
    lock = {"locked": "Locked", "unlocked": "Unlocked", "open": "Open"}.get(d.get("status"), "Unknown")
    return None, lock


class GateAdapter(Adapter):
    domain = "gate"

    def raw(self):
        """Return the full, unnormalized unifi-gate door list (GET /devices).

        Used by the faithful unifi-gate-style Gate tab, which renders the door
        cards directly rather than the home-normalized Controls. Carries the
        same injected X-Verified-User header used by every other request.
        """
        return self.get_json("/devices")

    def door_image(self, door_id):
        """Fetch a door's snapshot/cover image from unifi-gate.

        Proxies ``GET /door-image/<door_id>`` and returns (content_bytes,
        content_type). The door_id is URL-quoted. Raises on HTTP error so the
        server route can map failures to 404.
        """
        url = self.base_url + "/door-image/" + quote(door_id, safe="")
        r = requests.get(url, headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "image/jpeg")

    def snapshot(self):
        doors = self.get_json("/devices")
        out = []
        for d in doors:
            mode, status = _door_view(d)
            out.append(
                Control(
                    domain="gate",
                    id=d["id"],
                    name=d.get("name", d["id"]),
                    kind="tristate",
                    options=["once", "timed", "forever"],
                    mode=mode,
                    on=bool(d.get("is_held")),
                    status=status,
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

    def command(self, control_id, payload):
        action = payload.get("action", "unlock")
        if control_id == "gate":
            for d in self.get_json("/devices"):
                self._door_action(d["id"], action, payload)
        else:
            self._door_action(control_id, action, payload)

    def _door_action(self, door_id, action, payload):
        door_id = quote(door_id, safe="")
        if action == "unlock":
            self.post_json(f"/unlock/{door_id}", {})
        elif action == "hold_today":
            body = {}
            if payload.get("end_time"):
                body["end_time"] = payload["end_time"]
            self.post_json(f"/hold/today/{door_id}", body)
        elif action == "hold_forever":
            self.post_json(f"/hold/forever/{door_id}", {})
        elif action == "stop":
            self.post_json(f"/hold/stop/{door_id}", {})
        else:
            raise ValueError(f"unknown gate action: {action}")

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
