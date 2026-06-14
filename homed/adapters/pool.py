# homed/adapters/pool.py
import threading
from urllib.parse import quote

import websocket

from homed.adapters.base import Adapter
from homed.model import Control

# Plumbing modes that are not user-selectable colors/scenes.
_PLUMBING_MODES = {"off", "on", "set", "sync"}


class PoolAdapter(Adapter):
    domain = "pool"

    def snapshot(self):
        data = self.get_json("/api/pool")
        out = []

        spa = data.get("spa")
        if spa is not None:
            spa_on = bool(spa.get("on"))
            jets_on = bool((spa.get("accessories") or {}).get("jets"))
            spa_mode = "jets" if (spa_on and jets_on) else ("spa" if spa_on else "off")
            temp = spa.get("temperature")
            prog = spa.get("spa_heat_progress") or {}
            if prog.get("active") and prog.get("target_temp_f"):
                mins = prog.get("minutes_remaining")
                heat = f"Heating → {prog['target_temp_f']}°" + (f" ({mins}m)" if mins else "")
            else:
                heat = "On" if spa_on else "Off"
            status = f"{temp}° · {heat}" if temp is not None else heat
            out.append(
                Control(
                    domain="pool",
                    id="spa",
                    name="Spa",
                    kind="segmented",
                    options=["off", "spa", "jets"],
                    mode=spa_mode,
                    on=spa_on,
                    value=temp,
                    status=status,
                )
            )
            out.append(
                Control(
                    domain="pool",
                    id="spa_setpoint",
                    name="Setpoint",
                    kind="setpoint",
                    value=spa.get("setpoint"),
                    range=(40, 104),
                )
            )

        pool = data.get("pool")
        if pool is not None:
            out.append(
                Control(
                    domain="pool",
                    id="pool",
                    name="Pool",
                    kind="toggle",
                    on=bool(pool.get("on")),
                    value=pool.get("temperature"),
                    status="On" if pool.get("on") else "Off",
                )
            )

        lights = data.get("lights")
        if lights is not None:
            color_modes = [m for m in lights.get("available_modes", []) if m not in _PLUMBING_MODES]
            out.append(
                Control(
                    domain="pool",
                    id="lights",
                    name="Pool Light",
                    kind="modes",
                    on=bool(lights.get("on")),
                    status=(lights.get("mode") or "off").title(),
                    options=color_modes,
                    mode=(lights.get("mode") or "off"),
                )
            )

        for aux in data.get("auxiliaries", []):
            out.append(
                Control(
                    domain="pool", id=aux["id"], name=aux.get("name", aux["id"]), kind="toggle", on=bool(aux.get("on"))
                )
            )
        return out

    def command(self, control_id, payload):
        verb = "on" if payload.get("on") else "off"
        if control_id == "spa":
            state = payload.get("state")
            if state == "off":
                self.post_json("/api/spa/off", {})
            elif state == "spa":
                self.post_json("/api/spa/on", {})
                self.post_json("/api/spa/jets/off", {})
            elif state == "jets":
                self.post_json("/api/spa/jets/on", {})
        elif control_id == "spa_setpoint":
            self.post_json("/api/spa/heat", {"setpoint": int(payload["setpoint"])})
        elif control_id == "lights":
            if payload.get("mode"):
                self.post_json("/api/lights/mode", {"mode": payload["mode"]})
            else:
                self.post_json(f"/api/lights/{verb}", {})
        elif control_id == "pool":
            self.post_json(f"/api/pool/{verb}", {})
        else:
            self.post_json(f"/api/auxiliary/{quote(control_id, safe='')}/{verb}", {})

    def start(self, on_change):
        self._on_change = on_change
        ws_url = self.base_url.replace("http", "ws", 1) + "/api/ws"
        t = threading.Thread(target=self._run_ws, args=(ws_url,), daemon=True)
        t.start()
        return t

    def _handle_ws_message(self, _msg):
        if getattr(self, "_on_change", None):
            self._on_change()

    def _run_ws(self, ws_url):
        import time

        while True:
            try:
                app = websocket.WebSocketApp(ws_url, on_message=lambda _ws, m: self._handle_ws_message(m))
                app.run_forever(ping_interval=30)
            except Exception:
                pass
            time.sleep(3)
