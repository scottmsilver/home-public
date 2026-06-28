# homed/adapters/music.py
import threading
import time
from urllib.parse import unquote

from homed.adapters.base import Adapter
from homed.model import Control


class MusicAdapter(Adapter):
    """Thin adapter to the wiimd multiroom-audio daemon.

    A wiimd "player" is a LOGICAL player: a multiroom group (master+slaves)
    shown as one, or a standalone zone. wiimd runs on the host (default
    127.0.0.1:8096) and talks to the WiiMs on the LAN.
    """

    domain = "music"

    def raw(self):
        """Return the full, unnormalized wiimd state (GET /api/music).

        Used by the faithful wiimd-style Music tab, which renders the daemon's
        own per-player card data directly rather than the home-normalized
        Controls.
        """
        return self.get_json("/api/music")

    def raw_command(self, path: str, body: dict | None = None) -> dict:
        """Pass a command straight through to the wiimd backend.

        Only paths under ``/api/`` are allowed, and they must not embed a
        scheme (``://``) — the URL is always built as ``base_url + path`` so
        there is no opportunity for SSRF to an arbitrary host. Used for
        per-player wiimd actions home does not model as Controls
        (e.g. ``/api/music/<player_id>/cmd``, ``/api/goodnight``).
        """
        self._validate_raw_path(path)
        return self.post_json(path, body or {})

    def goodnight(self):
        """Bedtime: stop every player. Part of the home-wide goodnight scene."""
        self.post_json("/api/goodnight", {})

    @staticmethod
    def _validate_raw_path(path: str) -> None:
        """Raise ValueError unless ``path`` is a safe wiimd backend API path.

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
        data = self.get_json("/api/music")
        players = data.get("players", [])
        playing = [p for p in players if p.get("status") == "play"]
        online = any(p.get("online") for p in players) if players else False
        if playing:
            status = f"{len(playing)} playing"
        elif players:
            status = "Idle"
        else:
            status = None
        return [
            Control(
                domain="music",
                id="music",
                name="Music",
                kind="readout",
                on=bool(playing),
                offline=sum(1 for p in players if not p.get("online", True)),
                status=status,
                online=online,
            )
        ]

    def command(self, control_id, payload):
        # Home does not model per-player transport as normalized Controls; the
        # Music tab drives players via the raw passthrough (/api/raw/music/cmd).
        raise ValueError(f"unknown music control: {control_id}")

    # polling upstream (wiimd WS is optional; home polls like the gate adapter)
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
