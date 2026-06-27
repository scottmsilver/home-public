# tests/test_auth.py
import json
import time
from pathlib import Path

import jwt
from flask import Flask

from homed.auth import AuthGate

CFG = {"remote_domain": "home.example.com", "broker_url": "https://b", "allowed_emails": ["you@gmail.com"]}


def _gate(tmp_path, allowed=()):
    return AuthGate(
        {"remote_domain": "home.example.com", "broker_url": "https://b", "allowed_emails": list(allowed)},
        state_dir=tmp_path,
    )


def test_approve_email_persists_and_unions_with_config(tmp_path):
    g = _gate(tmp_path, allowed=["seed@x.com"])
    assert g.email_allowed("seed@x.com")  # from config
    assert not g.email_allowed("new@x.com")
    g.approve_email("New@x.com")  # case-insensitive
    assert g.email_allowed("new@x.com")
    # persisted to disk and reloaded by a fresh gate
    g2 = _gate(tmp_path, allowed=["seed@x.com"])
    assert g2.email_allowed("new@x.com")
    assert json.loads((Path(tmp_path) / "approved_emails.json").read_text()) == ["new@x.com"]


def test_load_approved_tolerates_missing_and_corrupt(tmp_path):
    (Path(tmp_path) / "approved_emails.json").write_text("{ not json")
    g = _gate(tmp_path)
    assert g.email_allowed("anyone@x.com") is False  # corrupt file → empty set, no crash


def make_gate(tmp_path, handoff="hs", session="ss"):
    g = AuthGate(CFG, state_dir=tmp_path)
    g.handoff_secret = handoff
    g.session_secret = session
    return g


def test_lan_request_is_open(tmp_path):
    g = make_gate(tmp_path)
    assert g.is_remote("192.168.1.9") is False
    assert g.is_remote("home.example.com") is True


def test_session_roundtrip(tmp_path):
    g = make_gate(tmp_path)
    cookie = g.make_session("you@gmail.com")
    assert g.verify_session(cookie) == "you@gmail.com"


def test_disallowed_email_rejected(tmp_path):
    g = make_gate(tmp_path)
    cookie = g.make_session("intruder@evil.com")
    # verify_session returns the email, but current_user enforces the allowlist
    assert g.email_allowed("intruder@evil.com") is False
    assert g.email_allowed("you@gmail.com") is True


def test_verify_handoff_uses_handoff_secret(tmp_path):
    g = make_gate(tmp_path)
    token = jwt.encode({"email": "you@gmail.com", "exp": int(time.time()) + 60}, "hs", algorithm="HS256")
    assert g.verify_handoff(token) == "you@gmail.com"


def test_handoff_without_exp_rejected(tmp_path):
    # The broker mints short-lived handoff JWTs WITH exp; a token lacking exp
    # must be rejected so a stripped/forged token can't be replayed indefinitely.
    g = make_gate(tmp_path)
    token = jwt.encode({"email": "you@gmail.com"}, "hs", algorithm="HS256")
    assert g.verify_handoff(token) is None


def test_fully_configured(tmp_path):
    g = make_gate(tmp_path)
    assert g.fully_configured is True
    g.handoff_secret = ""
    assert g.fully_configured is False


def test_expired_session_rejected(tmp_path):
    g = make_gate(tmp_path)
    token = jwt.encode({"email": "you@gmail.com", "exp": 1}, "ss", algorithm="HS256")
    assert g.verify_session(token) is None


def test_session_without_exp_rejected(tmp_path):
    g = make_gate(tmp_path)
    token = jwt.encode({"email": "you@gmail.com"}, "ss", algorithm="HS256")
    assert g.verify_session(token) is None


def test_session_signed_with_wrong_secret_rejected(tmp_path):
    g = make_gate(tmp_path)
    token = jwt.encode({"email": "you@gmail.com"}, "WRONG", algorithm="HS256")
    assert g.verify_session(token) is None


def test_handoff_token_signed_with_session_secret_fails(tmp_path):
    g = make_gate(tmp_path)
    token = jwt.encode({"email": "you@gmail.com"}, "ss", algorithm="HS256")
    assert g.verify_handoff(token) is None


def test_current_user_rejects_validly_signed_but_disallowed_email(tmp_path):
    g = make_gate(tmp_path)
    app = Flask(__name__)
    cookie = g.make_session("intruder@evil.com")
    with app.test_request_context("/", headers={"Cookie": f"home_session={cookie}"}):
        assert g.current_user() is None
