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
    )
    assert c.to_dict() == {
        "domain": "fans",
        "id": "all",
        "name": "All Fans",
        "kind": "speed",
        "on": True,
        "value": 2,
        "range": [1, 6],
        "status": "2 of 4",
        "online": True,
    }


def test_control_defaults():
    c = Control(domain="gate", id="front", name="Front", kind="momentary")
    d = c.to_dict()
    assert d["on"] is None and d["value"] is None and d["range"] is None
    assert d["status"] is None and d["online"] is True
