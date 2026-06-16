# homed/adapters/fans.py
import threading
from urllib.parse import quote, unquote

import websocket

from homed.adapters.base import Adapter
from homed.model import Control


def _shared(values):
    """Single agreed value among 'on' devices, else None."""
    s = set(values)
    return values[0] if len(s) == 1 else None


class FansAdapter(Adapter):
    domain = "fans"

    def raw(self):
        """Return the full, unnormalized fans-app state (GET /api/fans).

        Used by the faithful fans-app-style Patio tab, which renders the fans
        app's own per-device card data directly rather than the home-normalized
        Controls.
        """
        return self.get_json("/api/fans")

    def raw_command(self, path: str, body: dict | None = None) -> dict:
        """Pass a command straight through to the fans backend.

        Only paths under ``/api/`` are allowed, and they must not embed a
        scheme (``://``) — the URL is always built as ``base_url + path`` so
        there is no opportunity for SSRF to an arbitrary host. Used for
        per-device fans-app actions home does not model as Controls
        (e.g. ``/api/fans/<id>``, ``/api/heaters/<id>``, ``/api/all``,
        ``/api/sleep``, ``/api/goodnight``).
        """
        self._validate_raw_path(path)
        return self.post_json(path, body or {})

    @staticmethod
    def _validate_raw_path(path: str) -> None:
        """Raise ValueError unless ``path`` is a safe fans backend API path.

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
        data = self.get_json("/api/fans")
        fans = data.get("fans", [])
        heaters = data.get("heaters", [])
        out = []

        # All Fans (speed)
        on_fans = [f for f in fans if f.get("state", {}).get("fanOn")]
        speeds = [f["state"].get("fanSpeed") for f in on_fans if f["state"].get("fanSpeed") is not None]
        out.append(
            Control(
                domain="fans",
                id="fans",
                name="Fans",
                kind="speed",
                on=bool(on_fans),
                value=_shared(speeds) if speeds else None,
                range=(1, 6),
                offline=sum(1 for f in fans if not f.get("online", True)),
                status=f"{len(on_fans)} of {len(fans)}" if fans else None,
                online=any(f.get("online") for f in fans) if fans else False,
            )
        )

        # All Lights (slider)
        on_lights = [f for f in fans if f.get("state", {}).get("lightOn")]
        brights = [
            f["state"].get("lightBrightness") for f in on_lights if f["state"].get("lightBrightness") is not None
        ]
        out.append(
            Control(
                domain="fans",
                id="lights",
                name="Lights",
                kind="slider",
                on=bool(on_lights),
                value=_shared(brights) if brights else None,
                range=(1, 100),
                offline=sum(1 for f in fans if not f.get("online", True)),
                status=f"{len(on_lights)} of {len(fans)}" if fans else None,
                online=any(f.get("online") for f in fans) if fans else False,
            )
        )

        # All Heaters (slider) — only if present
        if heaters:
            on_h = [h for h in heaters if h.get("state", {}).get("on")]
            levels = [h["state"].get("level") for h in on_h if h["state"].get("level") is not None]
            out.append(
                Control(
                    domain="fans",
                    id="heaters",
                    name="Heaters",
                    kind="slider",
                    on=bool(on_h),
                    value=_shared(levels) if levels else None,
                    range=(1, 100),
                    offline=sum(1 for h in heaters if not h.get("online", True)),
                    status=f"{len(on_h)} of {len(heaters)}",
                    online=any(h.get("online") for h in heaters),
                )
            )
        return out

    def command(self, control_id, payload):
        on = payload.get("on")
        value = payload.get("value")
        if control_id == "fans":
            body = {}
            if value is not None:
                body = {"fanOn": True, "fanSpeed": int(value)}
            elif on is not None:
                body = {"fanOn": bool(on)}
            self.post_json("/api/all", body)
        elif control_id == "lights":
            body = {}
            if value is not None:
                body = {"lightOn": True, "lightBrightness": int(value)}
            elif on is not None:
                body = {"lightOn": bool(on)}
            self.post_json("/api/all", body)
        elif control_id == "heaters":
            data = self.get_json("/api/fans")
            for h in data.get("heaters", []):
                if value is not None:
                    self.post_json(f"/api/heaters/{quote(h['id'], safe='')}", {"level": int(value)})
                elif on is not None:
                    self.post_json(f"/api/heaters/{quote(h['id'], safe='')}", {"power": bool(on)})
        else:
            raise ValueError(f"unknown fans control: {control_id}")

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
                app = websocket.WebSocketApp(
                    ws_url,
                    on_message=lambda _ws, m: self._handle_ws_message(m),
                )
                app.run_forever(ping_interval=30)
            except Exception:
                pass
            time.sleep(3)  # reconnect backoff
