# tests/test_model.py
from homed.model import Control


def test_control_to_dict_includes_all_fields():
    c = Control(
        domain="fans",
        id="all",
        name="All Fans",
        kind="speed",
        on=True,
        value=2,
        range=(1, 6),
        status="2 of 4",
        online=True,
        options=["a", "b"],
        mode="b",
        offline=1,
    )
    assert c.to_dict() == {
        "domain": "fans",
        "id": "all",
        "name": "All Fans",
        "kind": "speed",
        "on": True,
        "value": 2,
        "range": [1, 6],
        "options": ["a", "b"],
        "mode": "b",
        "status": "2 of 4",
        "online": True,
        "offline": 1,
    }


def test_control_defaults():
    c = Control(domain="gate", id="front", name="Front", kind="momentary")
    d = c.to_dict()
    assert d["on"] is None and d["value"] is None and d["range"] is None
    assert d["status"] is None and d["online"] is True
    assert d["options"] is None
    assert d["mode"] is None
    assert d["offline"] == 0


def test_offline_round_trips():
    c = Control(domain="fans", id="fans", name="Fans", kind="speed", offline=2)
    assert c.offline == 2
    assert c.to_dict()["offline"] == 2


def test_mode_round_trips():
    c = Control(domain="pool", id="spa", name="Spa", kind="segmented", mode="jets")
    assert c.mode == "jets"
    assert c.to_dict()["mode"] == "jets"
