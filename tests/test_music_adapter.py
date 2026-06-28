# tests/test_music_adapter.py
import pytest
import responses

from homed.adapters.music import MusicAdapter

SNAP = {
    "players": [
        {
            "id": "leader-1",
            "name": "Whole House",
            "rooms": ["Kitchen", "Living Room"],
            "leader_ip": "192.168.1.50",
            "online": True,
            "status": "play",
            "title": "Track",
            "artist": "Band",
            "album": "Record",
            "art": None,
            "curpos_ms": 0,
            "totlen_ms": 0,
            "vol": 30,
            "mute": False,
            "source": "cast",
        },
        {
            "id": "zone-2",
            "name": "Office",
            "rooms": ["Office"],
            "leader_ip": "192.168.1.51",
            "online": True,
            "status": "pause",
            "title": "",
            "artist": "",
            "album": "",
            "art": None,
            "curpos_ms": 0,
            "totlen_ms": 0,
            "vol": 10,
            "mute": False,
            "source": "airplay",
        },
    ]
}


@responses.activate
def test_raw_passes_through_music_state():
    responses.add(responses.GET, "http://m/api/music", json=SNAP, status=200)
    assert MusicAdapter("http://m").raw() == SNAP


@responses.activate
def test_snapshot_summarizes_playing_count():
    responses.add(responses.GET, "http://m/api/music", json=SNAP, status=200)
    controls = MusicAdapter("http://m").snapshot()
    assert len(controls) == 1
    c = controls[0]
    assert c.domain == "music" and c.id == "music"
    assert c.kind == "readout"
    assert c.on is True  # one player playing
    assert c.status == "1 playing"
    assert c.online is True
    assert c.offline == 0


@responses.activate
def test_snapshot_idle_when_nothing_playing():
    snap = {"players": [{"id": "z", "name": "Z", "online": True, "status": "stop"}]}
    responses.add(responses.GET, "http://m/api/music", json=snap, status=200)
    c = MusicAdapter("http://m").snapshot()[0]
    assert c.on is False
    assert c.status == "Idle"
    assert c.online is True


@responses.activate
def test_snapshot_empty_players():
    responses.add(responses.GET, "http://m/api/music", json={"players": []}, status=200)
    c = MusicAdapter("http://m").snapshot()[0]
    assert c.on is False
    assert c.status is None
    assert c.online is False


@responses.activate
def test_snapshot_counts_offline_players():
    snap = {
        "players": [
            {"id": "a", "name": "A", "online": True, "status": "play"},
            {"id": "b", "name": "B", "online": False, "status": "none"},
        ]
    }
    responses.add(responses.GET, "http://m/api/music", json=snap, status=200)
    c = MusicAdapter("http://m").snapshot()[0]
    assert c.offline == 1
    assert c.on is True


@responses.activate
def test_goodnight_stops_all_players():
    responses.add(responses.POST, "http://m/api/goodnight", json={"ok": True}, status=200)
    MusicAdapter("http://m").goodnight()
    assert responses.calls[0].request.url == "http://m/api/goodnight"


@responses.activate
def test_raw_command_passes_through():
    responses.add(responses.POST, "http://m/api/music/p1/cmd", json={"ok": True}, status=200)
    result = MusicAdapter("http://m").raw_command("/api/music/p1/cmd", {"action": "toggle"})
    assert result == {"ok": True}
    import json

    assert json.loads(responses.calls[0].request.body) == {"action": "toggle"}


def test_raw_command_rejects_bad_path():
    a = MusicAdapter("http://m")
    for bad in ("/evil", "http://evil/api/x", "/api/../admin", "/api//x", "/api/%2e%2e/admin"):
        with pytest.raises(ValueError):
            a.raw_command(bad, {})


def test_command_raises_for_unmodeled_control():
    with pytest.raises(ValueError):
        MusicAdapter("http://m").command("music", {})


def test_start_spawns_thread_without_error(monkeypatch):
    import homed.adapters.music as musicmod

    monkeypatch.setattr(musicmod.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    t = MusicAdapter("http://m").start(lambda: None)
    assert t is not None
