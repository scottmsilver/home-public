import pytest

from homed.aggregator import Aggregator
from homed.model import Control
from homed.server import create_app


@pytest.fixture(autouse=True)
def _broker_handoff_secret(monkeypatch):
    # In real deployment AuthGate reads the broker HMAC secret from ~/.home/.broker_handoff
    # (or BROKER_HANDOFF_SECRET); provide it here so a configured remote_domain yields a
    # fully_configured gate and remote requests reach the 401 (not the 503) path.
    monkeypatch.setenv("BROKER_HANDOFF_SECRET", "test-handoff-secret")


class FakeAdapter:
    domain = "fans"

    def __init__(self):
        self.commands = []

    def snapshot(self):
        return [Control("fans", "fans", "All Fans", "speed", on=True)]

    def command(self, cid, payload):
        self.commands.append((cid, payload))

    def start(self, on_change):
        pass


def make_client(home_rows=None, web=None):
    adapter = FakeAdapter()
    agg = Aggregator({"fans": adapter})
    agg.refresh_all()
    app = create_app(agg, home_rows=home_rows or [{"domain": "fans", "groups": ["fans"]}], web=web or {})
    return app.test_client(), adapter


def test_state_endpoint():
    client, _ = make_client()
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.get_json()["controls"][0]["id"] == "fans"


def test_home_endpoint_filters_by_rows():
    client, _ = make_client(home_rows=[{"domain": "fans", "groups": ["fans"]}])
    r = client.get("/api/home")
    ids = [c["id"] for c in r.get_json()["controls"]]
    assert ids == ["fans"]


def test_command_endpoint_dispatches():
    client, adapter = make_client()
    r = client.post("/api/command", json={"domain": "fans", "id": "fans", "payload": {"on": True}})
    assert r.status_code == 200
    assert adapter.commands == [("fans", {"on": True})]


def test_command_unknown_domain_returns_400():
    client, _ = make_client()
    r = client.post("/api/command", json={"domain": "nope", "id": "x", "payload": {}})
    assert r.status_code == 400


def test_lan_request_open_when_remote_domain_set():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    # Host is the test client default (localhost) → LAN → open
    assert client.get("/api/state").status_code == 200


def test_remote_request_without_cookie_blocked():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    r = client.get("/api/state", headers={"Host": "home.example.com"})
    assert r.status_code == 401
