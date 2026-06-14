# tests/test_gate_adapter.py
import responses

from homed.adapters.gate import GateAdapter

DEVICES = [
    {
        "id": "front",
        "name": "Front",
        # Real-world bad-DPS case: door_position reports "open" but the lock
        # is actually engaged. lock_state must win.
        "status": "open",
        "lock_state": "lock",
        "door_position": "open",
        "is_held": False,
        "hold_state": None,
        "expires_at": None,
        "is_online": True,
    },
    {
        "id": "side",
        "name": "Side",
        "status": "unlocked",
        "lock_state": "unlock",
        "door_position": "close",
        "is_held": True,
        "hold_state": "hold_forever",
        "expires_at": None,
        "is_online": True,
    },
    {
        "id": "back",
        "name": "Back",
        "status": "unlocked",
        "lock_state": "unlock",
        "door_position": "close",
        "is_held": True,
        "hold_state": "hold_today",
        "expires_at": 1_700_000_000,
        "is_online": True,
    },
    {
        "id": "garage",
        "name": "Garage",
        # Unlocked via lock_state.
        "status": "open",
        "lock_state": "unlock",
        "door_position": "open",
        "is_held": False,
        "hold_state": None,
        "expires_at": None,
        "is_online": True,
    },
    {
        "id": "legacy",
        "name": "Legacy",
        # Older daemon / synthetic aggregate: no lock_state -> fall back to status.
        "status": "locked",
        "is_held": False,
        "hold_state": None,
        "expires_at": None,
        "is_online": True,
    },
]


@responses.activate
def test_snapshot_one_control_per_door_plus_aggregate():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    c = {
        x.id: x
        for x in GateAdapter(
            "http://g", headers={"X-Verified-User": "svc@local"}
        ).snapshot()
    }

    assert c["front"].kind == "tristate"
    assert c["front"].options == ["once", "timed", "forever"]
    assert c["front"].on is False
    # Real-world bad-DPS case: status/door_position say "open" but lock_state
    # says "lock" -> we must report Closed and ignore the bogus position.
    assert c["front"].mode is None
    assert c["front"].status == "Closed"

    # Open derived from lock_state (even though door_position is "open").
    assert c["garage"].mode is None and c["garage"].status == "Open"

    assert (
        c["side"].on is True
        and c["side"].mode == "forever"
        and c["side"].status == "Held open"
    )

    assert c["back"].mode == "timed" and c["back"].status.startswith("Held until")

    # Fallback: no lock_state -> derive from flattened status.
    assert c["legacy"].mode is None and c["legacy"].status == "Locked"

    agg = c["gate"]
    assert agg.kind == "momentary" and agg.status == "1 locked"


@responses.activate
def test_snapshot_injects_service_user_header():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    GateAdapter("http://g", headers={"X-Verified-User": "svc@local"}).snapshot()
    assert responses.calls[0].request.headers["X-Verified-User"] == "svc@local"


def test_start_spawns_thread_without_error(monkeypatch):
    import homed.adapters.gate as gatemod

    monkeypatch.setattr(
        gatemod.threading,
        "Thread",
        lambda *a, **k: type("T", (), {"start": lambda self: None})(),
    )
    t = GateAdapter("http://g").start(lambda: None)
    assert t is not None


@responses.activate
def test_command_unlock_once():
    responses.add(
        responses.POST, "http://g/unlock/front", json={"status": "success"}, status=200
    )
    GateAdapter("http://g").command("front", {"action": "unlock"})
    assert responses.calls[0].request.url.endswith("/unlock/front")


@responses.activate
def test_command_default_action_is_unlock():
    responses.add(
        responses.POST, "http://g/unlock/front", json={"status": "success"}, status=200
    )
    GateAdapter("http://g").command("front", {})
    assert responses.calls[0].request.url.endswith("/unlock/front")


@responses.activate
def test_command_door_id_is_url_quoted():
    responses.add(
        responses.POST,
        "http://g/unlock/..%2Fadmin",
        json={"status": "success"},
        status=200,
    )
    GateAdapter("http://g").command("../admin", {"action": "unlock"})
    url = responses.calls[0].request.url
    assert "..%2Fadmin" in url
    assert "/unlock/../admin" not in url


@responses.activate
def test_command_hold_today_passes_end_time():
    responses.add(
        responses.POST,
        "http://g/hold/today/front",
        json={"status": "success"},
        status=200,
    )
    GateAdapter("http://g").command(
        "front", {"action": "hold_today", "end_time": "20:30"}
    )
    import json

    assert json.loads(responses.calls[0].request.body) == {"end_time": "20:30"}


@responses.activate
def test_command_aggregate_unlocks_all_doors():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    for door in ("front", "side", "back", "garage", "legacy"):
        responses.add(
            responses.POST,
            f"http://g/unlock/{door}",
            json={"status": "success"},
            status=200,
        )
    GateAdapter("http://g").command("gate", {"action": "unlock"})
    unlocked = {
        c.request.url.rsplit("/", 1)[1]
        for c in responses.calls
        if "/unlock/" in c.request.url
    }
    assert unlocked == {"front", "side", "back", "garage", "legacy"}
