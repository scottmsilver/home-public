# tests/test_pool_adapter.py
import responses

from homed.adapters.pool import PoolAdapter

SNAP = {
    "pool": {"on": False, "temperature": 78, "setpoint": 82, "heating": "off"},
    "spa": {
        "on": True,
        "temperature": 88,
        "setpoint": 102,
        "spa_heat_progress": {"active": True, "minutes_remaining": 12, "target_temp_f": 102},
        "accessories": {"jets": True},
    },
    "lights": {
        "on": False,
        "mode": "american",
        "available_modes": [
            "off",
            "on",
            "set",
            "sync",
            "swim",
            "party",
            "romantic",
            "caribbean",
            "american",
            "sunset",
            "royal",
            "blue",
            "green",
            "red",
            "white",
            "purple",
        ],
    },
    "auxiliaries": [{"id": "water_feature", "name": "Water Feature", "on": False}],
}


@responses.activate
def test_snapshot_maps_pool_spa_lights_aux():
    responses.add(responses.GET, "http://p/api/pool", json=SNAP, status=200)
    c = {x.id: x for x in PoolAdapter("http://p").snapshot()}

    assert c["spa"].kind == "segmented" and c["spa"].on is True
    assert c["spa"].options == ["off", "spa", "jets"]
    assert c["spa"].mode in ("off", "spa", "jets")
    assert c["spa"].value == 88
    assert "102" in c["spa"].status  # heating → target

    assert c["spa_setpoint"].kind == "setpoint"
    assert c["spa_setpoint"].range == (40, 104)
    assert c["spa_setpoint"].value == 102

    assert c["pool"].kind == "toggle" and c["pool"].on is False and c["pool"].value == 78
    assert c["lights"].kind == "modes" and c["lights"].on is False
    assert c["lights"].mode == "american"
    assert "off" not in c["lights"].options and "on" not in c["lights"].options
    assert "blue" in c["lights"].options
    assert "jets" not in c
    assert c["water_feature"].kind == "toggle" and c["water_feature"].name == "Water Feature"


@responses.activate
def test_snapshot_tolerates_null_bodies():
    responses.add(
        responses.GET,
        "http://p/api/pool",
        json={"pool": None, "spa": None, "lights": None, "auxiliaries": []},
        status=200,
    )
    assert PoolAdapter("http://p").snapshot() == []


@responses.activate
def test_command_pool_off():
    responses.add(responses.POST, "http://p/api/pool/off", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("pool", {"on": False})
    assert responses.calls[0].request.url.endswith("/api/pool/off")


@responses.activate
def test_command_spa_segmented_jets():
    responses.add(responses.POST, "http://p/api/spa/jets/on", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("spa", {"state": "jets"})
    assert responses.calls[0].request.url.endswith("/api/spa/jets/on")


@responses.activate
def test_command_spa_segmented_off():
    responses.add(responses.POST, "http://p/api/spa/off", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("spa", {"state": "off"})
    assert responses.calls[0].request.url.endswith("/api/spa/off")


@responses.activate
def test_command_spa_segmented_spa():
    responses.add(responses.POST, "http://p/api/spa/on", json={"ok": True}, status=200)
    responses.add(responses.POST, "http://p/api/spa/jets/off", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("spa", {"state": "spa"})
    assert responses.calls[0].request.url.endswith("/api/spa/on")
    assert responses.calls[1].request.url.endswith("/api/spa/jets/off")


@responses.activate
def test_command_spa_setpoint():
    responses.add(responses.POST, "http://p/api/spa/heat", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("spa_setpoint", {"setpoint": 102})
    req = responses.calls[0].request
    assert req.url.endswith("/api/spa/heat")
    import json as _json

    assert _json.loads(req.body) == {"setpoint": 102}


@responses.activate
def test_command_spa_setpoint_clamps_to_range():
    responses.add(responses.POST, "http://p/api/spa/heat", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("spa_setpoint", {"setpoint": 200})
    import json as _json

    assert _json.loads(responses.calls[0].request.body) == {"setpoint": 104}


def test_command_spa_setpoint_rejects_bad_input():
    import pytest

    with pytest.raises(ValueError):
        PoolAdapter("http://p").command("spa_setpoint", {})


@responses.activate
def test_command_lights_set_mode():
    responses.add(responses.POST, "http://p/api/lights/mode", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("lights", {"mode": "blue"})
    req = responses.calls[0].request
    assert req.url.endswith("/api/lights/mode")
    import json as _json

    assert _json.loads(req.body) == {"mode": "blue"}


@responses.activate
def test_command_lights_on():
    responses.add(responses.POST, "http://p/api/lights/on", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("lights", {"on": True})
    assert responses.calls[0].request.url.endswith("/api/lights/on")


@responses.activate
def test_command_aux_on():
    responses.add(responses.POST, "http://p/api/auxiliary/water_feature/on", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("water_feature", {"on": True})
    assert responses.calls[0].request.url.endswith("/api/auxiliary/water_feature/on")


@responses.activate
def test_command_aux_id_is_url_quoted():
    responses.add(responses.POST, "http://p/api/auxiliary/..%2Fadmin/on", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("../admin", {"on": True})
    url = responses.calls[0].request.url
    assert "..%2Fadmin" in url
    assert "/api/auxiliary/../admin" not in url


def test_ws_message_triggers_on_change():
    a = PoolAdapter("http://p")
    hits = []
    a._on_change = lambda: hits.append(1)
    a._handle_ws_message("{}")
    assert hits == [1]


def test_start_spawns_thread_without_error(monkeypatch):
    import homed.adapters.pool as poolmod

    monkeypatch.setattr(poolmod.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    t = PoolAdapter("http://p").start(lambda: None)
    assert t is not None


# Bedtime state: spa on, pool light on, one aux on, one aux off.
GOODNIGHT_SNAP = {
    "pool": {"on": True, "temperature": 80},
    "spa": {"on": True, "temperature": 88},
    "lights": {"on": True, "mode": "blue"},
    "auxiliaries": [
        {"id": "water_feature", "name": "Water Feature", "on": True},
        {"id": "cleaner", "name": "Cleaner", "on": False},
    ],
}


@responses.activate
def test_goodnight_turns_off_spa_light_and_on_auxes_only():
    responses.add(responses.GET, "http://p/api/pool", json=GOODNIGHT_SNAP, status=200)
    responses.add(responses.POST, "http://p/api/spa/off", json={"ok": True}, status=200)
    responses.add(responses.POST, "http://p/api/lights/off", json={"ok": True}, status=200)
    responses.add(responses.POST, "http://p/api/auxiliary/water_feature/off", json={"ok": True}, status=200)

    PoolAdapter("http://p").goodnight()

    posted = [c.request.url for c in responses.calls if c.request.method == "POST"]
    assert any(u.endswith("/api/spa/off") for u in posted)
    assert any(u.endswith("/api/lights/off") for u in posted)
    assert any(u.endswith("/api/auxiliary/water_feature/off") for u in posted)
    # The main pool pump and the already-off "cleaner" aux are left alone.
    assert not any("/api/pool/off" in u for u in posted)
    assert not any("cleaner" in u for u in posted)


@responses.activate
def test_goodnight_is_best_effort_then_raises():
    # spa-off fails, but lights + aux must still be attempted, and the call
    # must raise afterward so the server marks the pool domain as failed.
    responses.add(responses.GET, "http://p/api/pool", json=GOODNIGHT_SNAP, status=200)
    responses.add(responses.POST, "http://p/api/spa/off", json={"error": "boom"}, status=500)
    responses.add(responses.POST, "http://p/api/lights/off", json={"ok": True}, status=200)
    responses.add(responses.POST, "http://p/api/auxiliary/water_feature/off", json={"ok": True}, status=200)

    import pytest

    with pytest.raises(RuntimeError):
        PoolAdapter("http://p").goodnight()

    posted = [c.request.url for c in responses.calls if c.request.method == "POST"]
    assert any(u.endswith("/api/lights/off") for u in posted), "lights must still be attempted after spa-off fails"
    assert any(u.endswith("/api/auxiliary/water_feature/off") for u in posted), "aux must still be attempted"
