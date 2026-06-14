import pytest

from homed.aggregator import Aggregator
from homed.model import Control


class FakeAdapter:
    def __init__(self, domain, controls):
        self.domain = domain
        self._controls = controls
        self.commands = []
        self.started = False

    def snapshot(self):
        return self._controls

    def command(self, cid, payload):
        self.commands.append((cid, payload))

    def start(self, on_change):
        self.started = True


def test_state_merges_all_domains():
    a = FakeAdapter("fans", [Control("fans", "fans", "All Fans", "speed", on=True)])
    b = FakeAdapter("pool", [Control("pool", "spa", "Spa", "toggle", on=False)])
    agg = Aggregator({"fans": a, "pool": b})
    agg.refresh_all()
    state = agg.state()
    assert {c["domain"] for c in state["controls"]} == {"fans", "pool"}


def test_dispatch_routes_to_correct_adapter():
    a = FakeAdapter("fans", [])
    agg = Aggregator({"fans": a})
    agg.dispatch("fans", "fans", {"on": True})
    assert a.commands == [("fans", {"on": True})]


def test_dispatch_unknown_domain_raises():
    import pytest

    agg = Aggregator({})
    with pytest.raises(KeyError):
        agg.dispatch("nope", "x", {})


def test_subscribe_receives_notification_on_change():
    a = FakeAdapter("fans", [Control("fans", "fans", "All Fans", "speed", on=True)])
    agg = Aggregator({"fans": a})
    q = agg.subscribe()
    agg.refresh_domain("fans")  # simulate an update
    payload = q.get_nowait()  # a state payload was queued
    assert payload["controls"][0]["domain"] == "fans"


def test_start_begins_all_adapters():
    a = FakeAdapter("fans", [])
    agg = Aggregator({"fans": a})
    agg.start()
    assert a.started is True


def test_full_queue_drops_without_raising():
    a = FakeAdapter("fans", [Control("fans", "fans", "All Fans", "speed", on=True)])
    agg = Aggregator({"fans": a})
    agg.subscribe()  # never drained; maxsize is 8
    # Notify well past the queue capacity; queue.Full must be swallowed.
    for _ in range(20):
        agg.refresh_domain("fans")  # returns normally, no exception


def test_unsubscribe_stops_notifications():
    import queue

    a = FakeAdapter("fans", [Control("fans", "fans", "All Fans", "speed", on=True)])
    agg = Aggregator({"fans": a})
    q = agg.subscribe()
    agg.unsubscribe(q)
    agg.refresh_domain("fans")
    assert q.empty()
    with pytest.raises(queue.Empty):
        q.get_nowait()
