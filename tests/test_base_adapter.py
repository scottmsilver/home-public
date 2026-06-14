# tests/test_base_adapter.py
import responses

from homed.adapters.base import Adapter


class Dummy(Adapter):
    domain = "dummy"

    def snapshot(self):
        return []

    def command(self, control_id, payload):
        pass


@responses.activate
def test_get_json_uses_base_url_and_headers():
    responses.add(responses.GET, "http://h/api/x", json={"ok": True}, status=200)
    a = Dummy("http://h", headers={"X-Verified-User": "svc@local"})
    assert a.get_json("/api/x") == {"ok": True}
    assert responses.calls[0].request.headers["X-Verified-User"] == "svc@local"


@responses.activate
def test_post_json_sets_content_type():
    responses.add(responses.POST, "http://h/api/y", json={"ok": True}, status=200)
    a = Dummy("http://h")
    a.post_json("/api/y", {"on": True})
    assert responses.calls[0].request.headers["Content-Type"] == "application/json"
