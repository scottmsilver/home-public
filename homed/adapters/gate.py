# homed/adapters/gate.py
import threading
import time
from urllib.parse import quote

import requests

from homed.adapters.base import Adapter
from homed.model import Control


def _door_state(d):
    """Canonical derived door state — the single source of truth both the
    normalized snapshot (Home tab) and the raw passthrough (Gate tab) consume,
    so the two can never disagree on locked/open.

    ``open`` means UNLOCKED (or held open). It is derived from ``lock_state``,
    NOT the physical ``door_position``/``status`` — a locked gate can be swung
    physically "open" while its lock is engaged, which must still read Closed.
    """
    hs = d.get("hold_state")
    held = bool(d.get("is_held")) or hs in ("hold_forever", "hold_today")
    ls = d.get("lock_state")
    if ls == "lock":
        open_ = False
    elif ls == "unlock":
        open_ = True
    else:
        # Older daemons / synthetic aggregate have no lock_state: fall back to the
        # flattened status field.
        open_ = d.get("status") in ("unlocked", "open")
    open_ = open_ or held

    if hs == "hold_forever":
        mode, label = "forever", "Held open"
    elif hs == "hold_today":
        exp = d.get("expires_at")
        if exp:
            t = time.strftime("%-I:%M %p", time.localtime(exp))
            mode, label = "timed", f"Held until {t}"
        else:
            mode, label = "timed", "Held (timed)"
    else:
        mode, label = None, ("Open" if open_ else "Closed")
    return {"open": open_, "held": held, "mode": mode, "label": label}


def _door_view(d):
    s = _door_state(d)
    return s["mode"], s["label"]


class GateAdapter(Adapter):
    domain = "gate"

    def raw(self):
        """Return the full, unnormalized unifi-gate door list (GET /devices).

        Used by the faithful unifi-gate-style Gate tab, which renders the door
        cards directly rather than the home-normalized Controls. Carries the
        same injected X-Verified-User header used by every other request.

        Each door is enriched with a ``derived`` object (the same canonical
        locked/open/label/mode the Home tab uses) so the Gate tab consumes one
        source of truth instead of re-deriving status from raw fields.
        """
        doors = self.get_json("/devices")
        for d in doors:
            if isinstance(d, dict):
                d["derived"] = _door_state(d)
        return doors

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
                    expires_at=d.get("expires_at"),
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
