import pytest
import requests

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


class FailingAdapter:
    domain = "gate"

    def snapshot(self):
        return [Control("gate", "front", "Front Door", "lock", on=True)]

    def command(self, cid, payload):
        raise requests.HTTPError("500 Server Error: backend daemon failed")

    def start(self, on_change):
        pass


def make_client(home_rows=None, web=None):
    adapter = FakeAdapter()
    agg = Aggregator({"fans": adapter})
    agg.refresh_all()
    app = create_app(agg, home_rows=home_rows or [{"domain": "fans", "groups": ["fans"]}], web=web or {})
    return app.test_client(), adapter


class FakePoolAdapter:
    domain = "pool"

    def __init__(self, raw=None):
        self._raw = raw if raw is not None else {"pool": {"on": True, "temperature": 82, "setpoint": 85}}
        self.raw_commands = []

    def snapshot(self):
        return [Control("pool", "pool", "Pool", "toggle", on=True)]

    def command(self, cid, payload):
        pass

    def raw(self):
        return self._raw

    def raw_command(self, path, body=None):
        # Delegate to the real adapter validation so the endpoint test exercises
        # the actual path-boundary checks rather than a duplicate.
        from homed.adapters.pool import PoolAdapter

        PoolAdapter.raw_command.__wrapped__ if False else None  # noqa
        PoolAdapter._validate_raw_path(path)
        self.raw_commands.append((path, body or {}))
        return {"ok": True}

    def start(self, on_change):
        pass


def make_pool_client(raw=None):
    adapter = FakePoolAdapter(raw=raw)
    agg = Aggregator({"pool": adapter})
    agg.refresh_all()
    app = create_app(agg, home_rows=[], web={})
    return app.test_client(), adapter


def test_raw_pool_returns_backend_state():
    sample = {
        "pool": {"on": True, "temperature": 82, "setpoint": 85},
        "spa": {"on": False, "setpoint": 102, "accessories": {"jets": False}},
        "lights": {"on": True, "mode": "blue", "available_modes": ["blue", "green"]},
        "auxiliaries": [{"id": "aux1", "name": "Waterfall", "on": False}],
        "pump": {"pump_type": "VSF", "running": True, "rpm": 2400, "watts": 900, "gpm": 60},
        "system": {"air_temperature": 70},
    }
    client, _ = make_pool_client(raw=sample)
    r = client.get("/api/raw/pool")
    assert r.status_code == 200
    assert r.get_json() == sample


class FakeGateAdapter:
    domain = "gate"

    def __init__(self, raw=None, image=None):
        self._raw = (
            raw
            if raw is not None
            else [
                {
                    "id": "door1",
                    "name": "Front Gate",
                    "is_online": True,
                    "status": "locked",
                    "imageUrl": "/door-image/door1",
                    "is_held": False,
                    "hold_state": None,
                    "expires_at": None,
                }
            ]
        )
        self._image = image
        self.image_requests = []

    def snapshot(self):
        return [Control("gate", "door1", "Front Gate", "tristate", on=False)]

    def command(self, cid, payload):
        pass

    def raw(self):
        return self._raw

    def door_image(self, door_id):
        self.image_requests.append(door_id)
        if self._image is None:
            raise requests.HTTPError("404 Not Found")
        return self._image

    def start(self, on_change):
        pass


def make_gate_client(raw=None, image=None):
    adapter = FakeGateAdapter(raw=raw, image=image)
    agg = Aggregator({"gate": adapter})
    agg.refresh_all()
    app = create_app(agg, home_rows=[], web={})
    return app.test_client(), adapter


def test_raw_gate_returns_backend_doors():
    sample = [
        {
            "id": "door1",
            "name": "Front Gate",
            "is_online": True,
            "status": "open",
            "imageUrl": "/door-image/door1",
            "is_held": True,
            "hold_state": "hold_forever",
            "expires_at": None,
        }
    ]
    client, _ = make_gate_client(raw=sample)
    r = client.get("/api/raw/gate")
    assert r.status_code == 200
    assert r.get_json() == sample


def test_raw_gate_image_proxies_bytes():
    client, adapter = make_gate_client(image=(b"\xff\xd8jpegbytes", "image/jpeg"))
    r = client.get("/api/raw/gate/image/door1")
    assert r.status_code == 200
    assert r.data == b"\xff\xd8jpegbytes"
    assert r.mimetype == "image/jpeg"
    assert adapter.image_requests == ["door1"]


def test_raw_gate_image_missing_returns_404():
    client, _ = make_gate_client(image=None)
    r = client.get("/api/raw/gate/image/door1")
    assert r.status_code == 404


def test_raw_pool_cmd_passes_through():
    client, adapter = make_pool_client()
    r = client.post("/api/raw/pool/cmd", json={"path": "/api/pool/heat", "body": {"setpoint": 88}})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert adapter.raw_commands == [("/api/pool/heat", {"setpoint": 88})]


def test_raw_pool_cmd_rejects_bad_path():
    client, adapter = make_pool_client()
    for bad in ("/evil", "http://evil/api/x", "/api/x?u=http://evil"):
        r = client.post("/api/raw/pool/cmd", json={"path": bad, "body": {}})
        assert r.status_code == 400, bad
    assert adapter.raw_commands == []


class FakeFansAdapter:
    domain = "fans"

    def __init__(self, raw=None):
        self._raw = (
            raw
            if raw is not None
            else {
                "fans": [
                    {
                        "id": "fan1",
                        "name": "Patio Fan",
                        "online": True,
                        "state": {"fanOn": True, "fanSpeed": 3, "lightOn": False, "lightBrightness": 0, "wind": False},
                        "sleepRemaining": None,
                    }
                ],
                "heaters": [
                    {
                        "id": "h1",
                        "name": "Heater",
                        "online": True,
                        "state": {"on": False, "level": 0},
                        "sleepRemaining": None,
                    }
                ],
            }
        )
        self.raw_commands = []

    def snapshot(self):
        return [Control("fans", "fans", "All Fans", "speed", on=True)]

    def command(self, cid, payload):
        pass

    def raw(self):
        return self._raw

    def raw_command(self, path, body=None):
        # Delegate to the real adapter validation so the endpoint test exercises
        # the actual path-boundary checks rather than a duplicate.
        from homed.adapters.fans import FansAdapter

        FansAdapter._validate_raw_path(path)
        self.raw_commands.append((path, body or {}))
        return {"ok": True}

    def start(self, on_change):
        pass


def make_fans_client(raw=None):
    adapter = FakeFansAdapter(raw=raw)
    agg = Aggregator({"fans": adapter})
    agg.refresh_all()
    app = create_app(agg, home_rows=[], web={})
    return app.test_client(), adapter


def test_raw_fans_returns_backend_state():
    sample = {
        "fans": [
            {
                "id": "fan1",
                "name": "Patio Fan",
                "online": True,
                "state": {"fanOn": True, "fanSpeed": 4, "lightOn": True, "lightBrightness": 60, "wind": False},
                "sleepRemaining": None,
            }
        ],
        "heaters": [
            {"id": "h1", "name": "Heater", "online": True, "state": {"on": True, "level": 50}, "sleepRemaining": None}
        ],
    }
    client, _ = make_fans_client(raw=sample)
    r = client.get("/api/raw/fans")
    assert r.status_code == 200
    assert r.get_json() == sample


def test_raw_fans_cmd_passes_through():
    client, adapter = make_fans_client()
    r = client.post("/api/raw/fans/cmd", json={"path": "/api/fans/fan1", "body": {"fanOn": True, "fanSpeed": 3}})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert adapter.raw_commands == [("/api/fans/fan1", {"fanOn": True, "fanSpeed": 3})]


def test_raw_fans_cmd_rejects_bad_path():
    client, adapter = make_fans_client()
    for bad in ("/evil", "http://evil/api/x", "/api/../admin", "/api//x"):
        r = client.post("/api/raw/fans/cmd", json={"path": bad, "body": {}})
        assert r.status_code == 400, bad
    assert adapter.raw_commands == []


def test_raw_fans_cmd_non_object_body_is_safe():
    # A non-dict JSON body (list/string/null) must not 500; it maps to an empty
    # request → missing path → 400 from validation.
    client, adapter = make_fans_client()
    for bad_body in ([], "x", 5):
        r = client.post("/api/raw/fans/cmd", json=bad_body)
        assert r.status_code == 400, bad_body
    assert adapter.raw_commands == []


def test_raw_fans_no_backend_returns_404():
    agg = Aggregator({"pool": FakePoolAdapter()})
    agg.refresh_all()
    app = create_app(agg, home_rows=[], web={})
    client = app.test_client()
    assert client.get("/api/raw/fans").status_code == 404
    assert client.post("/api/raw/fans/cmd", json={"path": "/api/all", "body": {}}).status_code == 404


def make_client_with_failing_adapter():
    agg = Aggregator({"gate": FailingAdapter()})
    agg.refresh_all()
    app = create_app(agg, home_rows=[], web={})
    return app.test_client()


def test_state_endpoint():
    client, _ = make_client()
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.get_json()["controls"][0]["id"] == "fans"


def test_home_endpoint_filters_by_rows():
    client, _ = make_client(home_rows=[{"domain": "fans", "groups": ["fans"]}])
    r = client.get("/api/home")
    sections = r.get_json()["sections"]
    assert len(sections) == 1
    assert sections[0]["title"] == "Fans"
    ids = [c["id"] for c in sections[0]["controls"]]
    assert ids == ["fans"]


def test_home_endpoint_gate_doors_by_name():
    class GateAdapter:
        domain = "gate"

        def snapshot(self):
            return [
                Control("gate", "door-vehicle", "Gate", "momentary", status="Open"),
                Control("gate", "door-ped", "Side Door", "momentary", status="Locked"),
                # synthetic aggregate: id == domain, collides on name "Gate"
                Control("gate", "gate", "Gate", "momentary", status="1 locked"),
            ]

        def command(self, cid, payload):
            pass

        def start(self, on_change):
            pass

    agg = Aggregator({"gate": GateAdapter()})
    agg.refresh_all()
    app = create_app(agg, home_rows=[{"domain": "gate", "doors": ["Gate"]}], web={})
    client = app.test_client()
    sections = client.get("/api/home").get_json()["sections"]
    assert len(sections) == 1
    assert sections[0]["title"] == "Gate"
    controls = sections[0]["controls"]
    # must resolve to the real door, NOT the synthetic aggregate
    assert [c["id"] for c in controls] == ["door-vehicle"]
    assert controls[0]["status"] == "Open"


def test_home_sections_use_row_title():
    client, _ = make_client(home_rows=[{"domain": "fans", "groups": ["fans"], "title": "Living Room"}])
    sections = client.get("/api/home").get_json()["sections"]
    assert len(sections) == 1
    assert sections[0]["title"] == "Living Room"
    assert [c["id"] for c in sections[0]["controls"]] == ["fans"]


def test_home_sections_skip_empty():
    client, _ = make_client(
        home_rows=[
            {"domain": "fans", "groups": ["fans"]},
            {"domain": "fans", "groups": ["nonexistent"]},
        ]
    )
    sections = client.get("/api/home").get_json()["sections"]
    # the row matching nothing is dropped
    assert len(sections) == 1
    assert sections[0]["title"] == "Fans"
    assert [c["id"] for c in sections[0]["controls"]] == ["fans"]


def test_command_endpoint_dispatches():
    client, adapter = make_client()
    r = client.post("/api/command", json={"domain": "fans", "id": "fans", "payload": {"on": True}})
    assert r.status_code == 200
    assert adapter.commands == [("fans", {"on": True})]


def test_command_unknown_domain_returns_400():
    client, _ = make_client()
    r = client.post("/api/command", json={"domain": "nope", "id": "x", "payload": {}})
    assert r.status_code == 400


def test_command_backend_failure_returns_502():
    client = make_client_with_failing_adapter()
    r = client.post("/api/command", json={"domain": "gate", "id": "front", "payload": {}})
    assert r.status_code == 502
    assert "error" in r.get_json()


def test_lan_request_open_when_remote_domain_set():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    # Host is the test client default (localhost) → LAN → open
    assert client.get("/api/state").status_code == 200


def test_grant_start_lan_issues_ticket_remote_forbidden():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    # LAN host (not the remote domain) → ticket issued
    r = client.post("/api/auth/grant-start", headers={"Host": "192.168.1.15:8099"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ticket"]
    assert body["login_url"].startswith("https://home.example.com/api/auth/login?grant=")
    # Remote host → refused (only on-network may self-approve)
    r2 = client.post("/api/auth/grant-start", headers={"Host": "home.example.com"})
    assert r2.status_code == 403


def test_remote_request_without_cookie_blocked():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    r = client.get("/api/state", headers={"Host": "home.example.com"})
    assert r.status_code == 401


def test_static_path_traversal_blocked():
    client, _ = make_client()
    for path in ("/../homed/server.py", "/..%2Fhomed%2Fserver.py"):
        r = client.get(path)
        # Must never leak the server source. Either falls through to SPA index/stub (200)
        # or 404 — but the body must not contain the source.
        assert b"def create_app" not in r.get_data()
        assert r.status_code in (200, 404)


def test_auth_login_on_remote_domain_redirects_to_broker():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    r = client.get("/api/auth/login", headers={"Host": "home.example.com"})
    assert r.status_code == 302
    # The callback return_url must be built from the CONFIGURED remote domain.
    assert "home.example.com%2Fapi%2Fauth%2Fcallback" in r.headers["Location"]


def test_auth_login_callback_host_ignores_request_host():
    # Even if the Host is a subdomain of the remote domain (still "remote"),
    # the callback must point at the configured apex remote domain, not the
    # attacker-influenced Host header.
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    r = client.get("/api/auth/login", headers={"Host": "evil.home.example.com"})
    assert r.status_code == 302
    assert "https%3A%2F%2Fhome.example.com%2Fapi%2Fauth%2Fcallback" in r.headers["Location"]
    assert "evil" not in r.headers["Location"]


def test_auth_login_on_foreign_host_forbidden():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    r = client.get("/api/auth/login", headers={"Host": "attacker.com"})
    assert r.status_code == 403


def test_auth_callback_rejects_bad_state():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    # Remote host + no matching state cookie → 400 regardless of handoff token.
    r = client.get(
        "/api/auth/callback?state=x&silver_oauth=anything",
        headers={"Host": "home.example.com"},
    )
    assert r.status_code == 400


class GoodnightAdapter:
    """Records goodnight() calls; optionally raises to test domain isolation."""

    def __init__(self, domain, fail=False):
        self.domain = domain
        self._fail = fail
        self.slept = False

    def snapshot(self):
        return [Control(self.domain, self.domain, self.domain, "toggle", on=True)]

    def command(self, cid, payload):
        pass

    def goodnight(self):
        if self._fail:
            raise RuntimeError("backend down")
        self.slept = True

    def start(self, on_change):
        pass


class NoGoodnightAdapter:
    """An adapter (like gate) with no goodnight() — must be skipped, not error."""

    domain = "gate"

    def snapshot(self):
        return [Control("gate", "gate", "Gate", "lock", on=True)]

    def command(self, cid, payload):
        pass

    def start(self, on_change):
        pass


def test_goodnight_fans_out_to_every_adapter_with_goodnight():
    fans, pool, gate = GoodnightAdapter("fans"), GoodnightAdapter("pool"), NoGoodnightAdapter()
    agg = Aggregator({"fans": fans, "pool": pool, "gate": gate})
    agg.refresh_all()
    client = create_app(agg, home_rows=[], web={}).test_client()
    r = client.post("/api/goodnight")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["domains"] == {"fans": "ok", "pool": "ok"}  # gate skipped (no goodnight)
    assert fans.slept and pool.slept


def test_goodnight_one_domain_failing_does_not_block_others():
    fans, pool = GoodnightAdapter("fans", fail=True), GoodnightAdapter("pool")
    agg = Aggregator({"fans": fans, "pool": pool})
    agg.refresh_all()
    client = create_app(agg, home_rows=[], web={}).test_client()
    r = client.post("/api/goodnight")
    assert r.status_code == 200
    body = r.get_json()
    assert body["domains"] == {"fans": "error", "pool": "ok"}
    assert body["ok"] is False  # any domain failing → ok:false
    assert pool.slept  # pool still ran despite fans raising
