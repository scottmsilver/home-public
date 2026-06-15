# tests/test_fans_adapter.py
import responses

from homed.adapters.fans import FansAdapter

SNAP = {
    "fans": [
        {
            "id": "a",
            "name": "A",
            "online": True,
            "state": {"fanOn": True, "fanSpeed": 2, "lightOn": True, "lightBrightness": 60},
        },
        {"id": "b", "name": "B", "online": True, "state": {"fanOn": False, "lightOn": False}},
    ],
    "heaters": [
        {"id": "h1", "name": "Patio", "online": True, "state": {"on": True, "level": 40}},
    ],
}


@responses.activate
def test_snapshot_aggregates_fans_lights_heaters():
    responses.add(responses.GET, "http://f/api/fans", json=SNAP, status=200)
    controls = {c.id: c for c in FansAdapter("http://f").snapshot()}

    fans = controls["fans"]
    assert fans.name == "Fans"
    assert fans.kind == "speed" and fans.on is True
    assert fans.range == (1, 6)
    assert fans.status == "1 of 2"  # one of two fans on
    assert fans.value == 2  # shared speed (only A is on)

    lights = controls["lights"]
    assert lights.name == "Lights"
    assert lights.kind == "slider" and lights.on is True
    assert lights.value == 60 and lights.range == (1, 100)

    heaters = controls["heaters"]
    assert heaters.name == "Heaters"
    assert heaters.kind == "slider" and heaters.on is True and heaters.value == 40

    # all devices in SNAP are online → no offline count anywhere
    assert fans.offline == 0
    assert lights.offline == 0
    assert heaters.offline == 0


OFFLINE_SNAP = {
    "fans": [
        {"id": "a", "name": "A", "online": True, "state": {"fanOn": True, "fanSpeed": 2, "lightOn": True}},
        {"id": "b", "name": "B", "online": False, "state": {"fanOn": False, "lightOn": False}},
    ],
    "heaters": [
        {"id": "h1", "name": "Patio", "online": True, "state": {"on": True, "level": 40}},
        {"id": "h2", "name": "Side", "online": False, "state": {"on": False}},
    ],
}


@responses.activate
def test_snapshot_counts_offline_devices():
    responses.add(responses.GET, "http://f/api/fans", json=OFFLINE_SNAP, status=200)
    controls = {c.id: c for c in FansAdapter("http://f").snapshot()}

    # one fan (B) is offline → both fans and lights controls report 1 offline
    assert controls["fans"].offline == 1
    assert controls["lights"].offline == 1
    # one heater (h2) is offline
    assert controls["heaters"].offline == 1


@responses.activate
def test_command_fans_toggle_posts_all():
    responses.add(responses.POST, "http://f/api/all", json={}, status=200)
    FansAdapter("http://f").command("fans", {"on": True})
    import json

    assert json.loads(responses.calls[0].request.body) == {"fanOn": True}


@responses.activate
def test_command_fans_speed_posts_all():
    responses.add(responses.POST, "http://f/api/all", json={}, status=200)
    FansAdapter("http://f").command("fans", {"value": 3})
    import json

    assert json.loads(responses.calls[0].request.body) == {"fanOn": True, "fanSpeed": 3}


@responses.activate
def test_command_lights_brightness_posts_all():
    responses.add(responses.POST, "http://f/api/all", json={}, status=200)
    FansAdapter("http://f").command("lights", {"value": 75})
    import json

    assert json.loads(responses.calls[0].request.body) == {"lightOn": True, "lightBrightness": 75}


@responses.activate
def test_command_heaters_iterates_devices():
    responses.add(responses.GET, "http://f/api/fans", json=SNAP, status=200)
    responses.add(responses.POST, "http://f/api/heaters/h1", json={}, status=200)
    FansAdapter("http://f").command("heaters", {"on": False})
    import json

    assert json.loads(responses.calls[-1].request.body) == {"power": False}


def test_ws_message_triggers_on_change(monkeypatch):
    a = FansAdapter("http://f")
    hits = []
    a._on_change = lambda: hits.append(1)
    a._handle_ws_message("{}")
    assert hits == [1]


def test_start_spawns_thread_without_error(monkeypatch):
    import homed.adapters.fans as fansmod

    monkeypatch.setattr(fansmod.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    t = FansAdapter("http://f").start(lambda: None)
    assert t is not None


MIXED_SPEED_SNAP = {
    "fans": [
        {"id": "a", "name": "A", "online": True, "state": {"fanOn": True, "fanSpeed": 2, "lightOn": False}},
        {"id": "b", "name": "B", "online": True, "state": {"fanOn": True, "fanSpeed": 5, "lightOn": False}},
    ],
    "heaters": [],
}


@responses.activate
def test_snapshot_mixed_speeds_yields_none_value():
    responses.add(responses.GET, "http://f/api/fans", json=MIXED_SPEED_SNAP, status=200)
    controls = {c.id: c for c in FansAdapter("http://f").snapshot()}

    fans = controls["fans"]
    assert fans.on is True
    assert fans.status == "2 of 2"
    assert fans.value is None  # two fans on at different speeds → no shared value
