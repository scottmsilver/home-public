# homed/adapters/base.py
from abc import ABC, abstractmethod

import requests


class Adapter(ABC):
    """One backend daemon, normalized. Subclasses set `domain`."""

    domain: str = ""

    def __init__(self, base_url: str, headers: dict | None = None, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout

    # ── shared HTTP ──────────────────────────────────────────────
    def get_json(self, path: str) -> dict:
        r = requests.get(self.base_url + path, headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def post_json(self, path: str, body: dict | None = None) -> dict:
        h = {**self.headers, "Content-Type": "application/json"}
        r = requests.post(self.base_url + path, json=body or {}, headers=h, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ── contract ─────────────────────────────────────────────────
    @abstractmethod
    def snapshot(self) -> list:
        """Return list[Control] for this domain."""

    @abstractmethod
    def command(self, control_id: str, payload: dict) -> None:
        """Translate a normalized command to native backend calls."""

    def start(self, on_change):
        """Optional: begin background updates, calling on_change() per update.
        Default no-op; overridden by WS/polling adapters."""
        return None
