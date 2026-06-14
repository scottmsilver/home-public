# tests/test_gate_adapter.py
import responses

from homed.adapters.gate import GateAdapter

DEVICES = [
    {
        "id": "front",
        "name": "Front",
        "status": "locked",
        "is_held": False,
        "hold_state": None,
        "expires_at": None,
        "is_online": True,
    },
    {
        "id": "side",
        "name": "Side",
        "status": "unlocked",
        "is_held": True,
        "hold_state": "hold_forever",
        "expires_at": None,
        "is_online": True,
    },
]


@responses.activate
def test_snapshot_one_control_per_door_plus_aggregate():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    c = {x.id: x for x in GateAdapter("http://g", headers={"X-Verified-User": "svc@local"}).snapshot()}

    assert c["front"].kind == "momentary" and c["front"].on is False
    assert c["front"].status == "Locked"
    assert c["side"].on is True and c["side"].status == "Held open"

    agg = c["gate"]
    assert agg.kind == "momentary" and agg.status == "1 locked"


@responses.activate
def test_snapshot_injects_service_user_header():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    GateAdapter("http://g", headers={"X-Verified-User": "svc@local"}).snapshot()
    assert responses.calls[0].request.headers["X-Verified-User"] == "svc@local"


def test_start_spawns_thread_without_error(monkeypatch):
    import homed.adapters.gate as gatemod

    monkeypatch.setattr(gatemod.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    t = GateAdapter("http://g").start(lambda: None)
    assert t is not None
