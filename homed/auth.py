# homed/auth.py
import os
import secrets
import time
from pathlib import Path

import jwt
from flask import request

SESSION_COOKIE = "home_session"
STATE_COOKIE = "home_oauth_state"
HANDOFF_PARAM = "silver_oauth"
SESSION_TTL = 30 * 86400
PUBLIC_PATHS = {"/", "/api/auth/login", "/api/auth/callback", "/api/auth/me", "/api/auth/logout"}


def _host(h):
    h = (h or "").strip()
    if h.startswith("["):
        return h[1 : h.find("]")] if "]" in h else h
    if h.count(":") == 1:
        return h.rsplit(":", 1)[0]
    return h


class AuthGate:
    def __init__(self, web_cfg, state_dir=None):
        self.remote_domain = (web_cfg.get("remote_domain") or "").strip().lower()
        self.broker_url = (web_cfg.get("broker_url") or "").rstrip("/")
        self.allowed = {e.strip().lower() for e in web_cfg.get("allowed_emails", []) if e.strip()}
        self.state_dir = Path(state_dir or Path("~/.home").expanduser())
        self.handoff_secret = self._read("BROKER_HANDOFF_SECRET", ".broker_handoff")
        self.session_secret = self._session_secret()

    def _read(self, env, name):
        v = os.environ.get(env, "").strip()
        if v:
            return v
        try:
            return (self.state_dir / name).read_text().strip()
        except FileNotFoundError:
            return ""

    def _session_secret(self):
        v = os.environ.get("HOME_SESSION_SECRET", "").strip()
        if v:
            return v
        p = self.state_dir / ".session_secret"
        try:
            return p.read_text().strip()
        except FileNotFoundError:
            s = secrets.token_hex(32)
            try:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                p.write_text(s)
                p.chmod(0o600)
            except OSError:
                pass
            return s

    @property
    def fully_configured(self):
        return bool(self.handoff_secret and self.session_secret and self.allowed)

    @property
    def active(self):
        return bool(self.remote_domain)

    def is_remote(self, host_header):
        if not self.remote_domain:
            return False
        host = _host(host_header).lower()
        return host == self.remote_domain or host.endswith("." + self.remote_domain)

    def email_allowed(self, email):
        return bool(email) and email.lower() in self.allowed

    def make_session(self, email):
        now = int(time.time())
        return jwt.encode(
            {"email": email, "iat": now, "exp": now + SESSION_TTL}, self.session_secret, algorithm="HS256"
        )

    def verify_session(self, value):
        try:
            return jwt.decode(value, self.session_secret, algorithms=["HS256"], options={"require": ["exp"]}).get(
                "email"
            )
        except jwt.PyJWTError:
            return None

    def verify_handoff(self, token):
        try:
            return jwt.decode(token, self.handoff_secret, algorithms=["HS256"]).get("email")
        except jwt.PyJWTError:
            return None

    def current_user(self):
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            return None
        email = self.verify_session(cookie)
        return email if self.email_allowed(email) else None
