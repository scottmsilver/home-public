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
    "lights": {"on": False},
    "auxiliaries": [{"id": "water_feature", "name": "Water Feature", "on": False}],
}


@responses.activate
def test_snapshot_maps_pool_spa_lights_aux():
    responses.add(responses.GET, "http://p/api/pool", json=SNAP, status=200)
    c = {x.id: x for x in PoolAdapter("http://p").snapshot()}

    assert c["spa"].kind == "toggle" and c["spa"].on is True
    assert c["spa"].value == 88
    assert "102" in c["spa"].status  # heating → target

    assert c["pool"].kind == "toggle" and c["pool"].on is False and c["pool"].value == 78
    assert c["lights"].kind == "toggle" and c["lights"].on is False
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
