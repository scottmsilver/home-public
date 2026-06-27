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
GRANT_TTL = 600  # on-network self-approve ticket lifetime (seconds)
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
        self.approved_path = self.state_dir / "approved_emails.json"
        self._dynamic = self._load_approved()
        self._used_grant_jti = set()

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
        # Secrets are what make remote auth *usable*. An empty allow-list is a
        # valid "no users approved yet" state — the on-network grant flow adds
        # the first user — so it must NOT gate fully_configured.
        return bool(self.handoff_secret and self.session_secret)

    @property
    def active(self):
        return bool(self.remote_domain)

    def is_remote(self, host_header):
        if not self.remote_domain:
            return False
        host = _host(host_header).lower()
        return host == self.remote_domain or host.endswith("." + self.remote_domain)

    def _load_approved(self):
        import json

        try:
            data = json.loads(self.approved_path.read_text())
            return {str(e).strip().lower() for e in data if str(e).strip()}
        except (FileNotFoundError, ValueError, OSError):
            return set()

    def _persist_approved(self):
        import json
        import os

        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.approved_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(sorted(self._dynamic)))
            tmp.chmod(0o600)
            os.replace(tmp, self.approved_path)  # atomic
        except OSError:
            pass

    def approve_email(self, email):
        e = (email or "").strip().lower()
        if not e or e in self._dynamic:
            return
        self._dynamic.add(e)
        self._persist_approved()

    def email_allowed(self, email):
        return bool(email) and email.lower() in (self.allowed | self._dynamic)

    def make_session(self, email):
        now = int(time.time())
        return jwt.encode(
            {"email": email, "iat": now, "exp": now + SESSION_TTL}, self.session_secret, algorithm="HS256"
        )

    def make_grant_ticket(self):
        now = int(time.time())
        return jwt.encode(
            {"typ": "grant", "jti": secrets.token_hex(8), "iat": now, "exp": now + GRANT_TTL},
            self.session_secret,
            algorithm="HS256",
        )

    def consume_grant_ticket(self, token):
        try:
            claims = jwt.decode(token, self.session_secret, algorithms=["HS256"], options={"require": ["exp"]})
        except jwt.PyJWTError:
            return False
        if claims.get("typ") != "grant":
            return False
        jti = claims.get("jti")
        if not jti or jti in self._used_grant_jti:
            return False
        self._used_grant_jti.add(jti)
        return True

    def verify_session(self, value):
        try:
            return jwt.decode(value, self.session_secret, algorithms=["HS256"], options={"require": ["exp"]}).get(
                "email"
            )
        except jwt.PyJWTError:
            return None

    def verify_handoff(self, token):
        try:
            return jwt.decode(token, self.handoff_secret, algorithms=["HS256"], options={"require": ["exp"]}).get(
                "email"
            )
        except jwt.PyJWTError:
            return None

    def current_user(self):
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            return None
        email = self.verify_session(cookie)
        return email if self.email_allowed(email) else None
