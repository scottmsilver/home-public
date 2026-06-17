# homed/adapters/pool.py
import threading
from urllib.parse import quote, unquote

import websocket

from homed.adapters.base import Adapter
from homed.model import Control

# Plumbing modes that are not user-selectable colors/scenes.
_PLUMBING_MODES = {"off", "on", "set", "sync"}


class PoolAdapter(Adapter):
    domain = "pool"

    def raw(self):
        """Return the full, unnormalized pentair pool state (GET /api/pool).

        Used by the faithful pentair-style Pool tab, which renders pentair's
        own card data directly rather than the home-normalized Controls.
        """
        return self.get_json("/api/pool")

    def raw_command(self, path: str, body: dict | None = None) -> dict:
        """Pass a command straight through to the pentair backend.

        Only paths under ``/api/`` are allowed, and they must not embed a
        scheme (``://``) — the URL is always built as ``base_url + path`` so
        there is no opportunity for SSRF to an arbitrary host. Used for
        pentair actions home does not model as Controls (e.g. pool heat).
        """
        self._validate_raw_path(path)
        return self.post_json(path, body or {})

    def goodnight(self):
        """Bedtime: turn off the spa, the pool light, and any 'on' auxiliaries.

        Leaves the main pool pump alone — filtration runs on its own schedule,
        so a goodnight tap shouldn't disrupt it. Only switches off what is
        currently on, so it's a no-op for anything already off.

        Best-effort: every action is attempted even if an earlier one fails (a
        failed spa-off must not leave the lights and auxiliaries on). If any
        action failed, raise after attempting them all so the caller can report
        the pool domain as failed.
        """
        data = self.get_json("/api/pool")
        actions = []
        spa = data.get("spa")
        if spa and spa.get("on"):
            actions.append("/api/spa/off")
        lights = data.get("lights")
        if lights and lights.get("on"):
            actions.append("/api/lights/off")
        for aux in data.get("auxiliaries", []):
            if aux.get("on"):
                actions.append(f"/api/auxiliary/{quote(aux['id'], safe='')}/off")

        failures = []
        for path in actions:
            try:
                self.post_json(path, {})
            except Exception as e:
                failures.append(f"{path}: {e}")
        if failures:
            raise RuntimeError("goodnight: " + "; ".join(failures))

    @staticmethod
    def _validate_raw_path(path: str) -> None:
        """Raise ValueError unless ``path`` is a safe pentair backend API path.

        The URL is built as ``base_url + path``, so the host can't change. But
        ``requests`` normalizes dot segments before sending ("/api/../admin" →
        "/admin"), which would escape the ``/api/`` scope and hit non-API backend
        paths. Decode percent-encoding (and normalize backslashes) first so
        "%2e%2e"/"\\.." variants can't sneak a ".." or "//" past the check.
        """
        if not isinstance(path, str) or not path.startswith("/api/") or "://" in path:
            raise ValueError("raw_command path must start with '/api/' and contain no scheme")
        decoded = unquote(path).replace("\\", "/")
        if (
            ".." in decoded.split("/")
            or "//" in decoded
            or "\x00" in decoded
            or any(c in decoded for c in ("\r", "\n"))
        ):
            raise ValueError("raw_command path may not contain '..', '//', or control characters")

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
            try:
                sp = int(payload["setpoint"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("spa_setpoint requires an integer 'setpoint'")
            sp = max(40, min(104, sp))
            self.post_json("/api/spa/heat", {"setpoint": sp})
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
