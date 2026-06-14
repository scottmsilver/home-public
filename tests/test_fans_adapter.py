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
    assert fans.kind == "speed" and fans.on is True
    assert fans.range == (1, 6)
    assert fans.status == "1 of 2"  # one of two fans on
    assert fans.value == 2  # shared speed (only A is on)

    lights = controls["lights"]
    assert lights.kind == "slider" and lights.on is True
    assert lights.value == 60 and lights.range == (1, 100)

    heaters = controls["heaters"]
    assert heaters.kind == "slider" and heaters.on is True and heaters.value == 40
