# Home Gateway Backend Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the headless `home` gateway — a Flask service that aggregates the fans, pentair, and unifi-gate daemons behind one normalized API, one merged real-time stream, and one auth front door. Fully testable via `curl` with no UI.

**Architecture:** A Flask app loads three per-domain *adapters* (fans, pool, gate). Each adapter maps its backend's REST API into one normalized `Control` shape, translates normalized commands back to native calls, and feeds a background updater. fans/pentair updates arrive over upstream WebSockets (background threads); unifi-gate is polled. A central `Aggregator` caches the latest per-domain snapshot and pushes the merged `{gate,pool,fans}` state to browsers over **SSE** (`/api/stream`). Commands go via `POST /api/command`. A silver-oauth gate (ported from `fans/auth.py`) leaves LAN open and gates remote requests.

**Tech Stack:** Python 3.12, Flask, `requests` (REST to backends), `websocket-client` (upstream WS), `flask` SSE via a generator, `PyJWT` (silver-oauth session), `tomllib` (config), `pytest` + `responses` (HTTP mocking).

**Deviations from the spec (deliberate, see brainstorm spec §4):**
- Downstream push is **SSE**, not WebSocket — Flask serves one-directional push trivially and commands already use REST POST.
- The **gate adapter polls** `/devices` (unifi-gate exposes no client WS) and injects a configured `X-Verified-User` header (unifi-gate's localhost auth model).

---

## File Structure

```
home/
├── homed/
│   ├── __init__.py
│   ├── config.py          # load + validate home.toml
│   ├── model.py           # Control dataclass + serialization
│   ├── aggregator.py      # caches per-domain snapshots, fan-out to SSE subscribers
│   ├── auth.py            # silver-oauth gate, Flask port of fans/auth.py
│   ├── server.py          # Flask app: routes, SSE, wiring
│   └── adapters/
│       ├── __init__.py
│       ├── base.py        # Adapter ABC + shared HTTP helper
│       ├── fans.py        # fans daemon adapter (WS upstream)
│       ├── pool.py        # pentair daemon adapter (WS upstream)
│       └── gate.py        # unifi-gate adapter (polling upstream)
├── tests/
│   ├── test_config.py
│   ├── test_model.py
│   ├── test_fans_adapter.py
│   ├── test_pool_adapter.py
│   ├── test_gate_adapter.py
│   ├── test_aggregator.py
│   ├── test_auth.py
│   └── test_server.py
├── home.toml.example
├── requirements.txt
├── pytest.ini
├── services/home.service
└── .gitignore
```

Each adapter owns exactly one backend. `model.py` and `base.py` are the only shared contracts. Files split by responsibility, kept small enough to hold in context.

---

## Task 1: Scaffold the repository

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `.gitignore`, `home.toml.example`
- Create: `homed/__init__.py`, `homed/adapters/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
Flask==3.0.3
requests==2.32.3
websocket-client==1.8.0
PyJWT==2.9.0
```

- [ ] **Step 2: Create dev requirements / pytest config `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v
```

- [ ] **Step 3: Create `.gitignore`** (excludes instance config + brainstorm scratch)

```
__pycache__/
*.pyc
.pytest_cache/
.superpowers/
# instance-specific (never in the public repo)
home.toml
~/.home/
.venv/
venv/
```

- [ ] **Step 4: Create empty package markers**

`homed/__init__.py`, `homed/adapters/__init__.py`, `tests/__init__.py` — each an empty file.

- [ ] **Step 5: Create `home.toml.example`**

```toml
# Backend daemon base URLs — supplied per deployment, never hardcoded.
[backends.gate]
base_url     = "http://127.0.0.1:8000"
service_user = "home-gateway@local"   # injected as X-Verified-User

[backends.pool]
base_url = "http://127.0.0.1:8080"

[backends.fans]
base_url = "http://127.0.0.1:8095"

[web]
bind           = "0.0.0.0:8099"
remote_domain  = ""                    # e.g. "home.i.oursilverfamily.com"; empty = LAN-only
broker_url     = "https://auth.oursilverfamily.com"
allowed_emails = []                    # e.g. ["you@gmail.com"]

# Which controls surface on the unified Home card.
[[home.rows]]
domain  = "gate"
control = "unlock"

[[home.rows]]
domain   = "pool"
circuits = ["spa", "pool"]

[[home.rows]]
domain = "fans"
groups = ["fans", "lights"]
```

- [ ] **Step 6: Set up a virtualenv and install**

Run:
```bash
cd /home/ssilver/development/home
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest responses
```
Expected: installs cleanly.

- [ ] **Step 7: Initialize git and commit** (ask the user first per repo rules — do NOT run without approval)

```bash
git init && git add -A && git commit -m "chore: scaffold home gateway"
```

---

## Task 2: Config loader

**Files:**
- Create: `homed/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import textwrap
from homed.config import load_config

def test_load_config_parses_backends_and_home_rows(tmp_path):
    p = tmp_path / "home.toml"
    p.write_text(textwrap.dedent("""
        [backends.gate]
        base_url = "http://127.0.0.1:8000"
        service_user = "svc@local"
        [backends.pool]
        base_url = "http://127.0.0.1:8080"
        [backends.fans]
        base_url = "http://127.0.0.1:8095"
        [web]
        bind = "0.0.0.0:8099"
        remote_domain = ""
        allowed_emails = []
        [[home.rows]]
        domain = "gate"
        control = "unlock"
        [[home.rows]]
        domain = "fans"
        groups = ["fans", "lights"]
    """))
    cfg = load_config(p)
    assert cfg.backends["gate"]["base_url"] == "http://127.0.0.1:8000"
    assert cfg.backends["gate"]["service_user"] == "svc@local"
    assert cfg.web["bind"] == "0.0.0.0:8099"
    assert cfg.home_rows[0] == {"domain": "gate", "control": "unlock"}
    assert cfg.home_rows[1]["groups"] == ["fans", "lights"]

def test_missing_file_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/config.py
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    backends: dict = field(default_factory=dict)
    web: dict = field(default_factory=dict)
    home_rows: list = field(default_factory=list)


def load_config(path) -> Config:
    path = Path(path)
    with path.open("rb") as f:          # raises FileNotFoundError if absent
        data = tomllib.load(f)
    return Config(
        backends=data.get("backends", {}),
        web=data.get("web", {}),
        home_rows=data.get("home", {}).get("rows", []),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/config.py tests/test_config.py && git commit -m "feat: config loader"
```

---

## Task 3: Normalized Control model

**Files:**
- Create: `homed/model.py`
- Test: `tests/test_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model.py
from homed.model import Control

def test_control_to_dict_includes_all_fields():
    c = Control(domain="fans", id="all", name="All Fans", kind="speed",
                on=True, value=2, range=(1, 6), status="2 of 4", online=True)
    assert c.to_dict() == {
        "domain": "fans", "id": "all", "name": "All Fans", "kind": "speed",
        "on": True, "value": 2, "range": [1, 6], "status": "2 of 4", "online": True,
    }

def test_control_defaults():
    c = Control(domain="gate", id="front", name="Front", kind="momentary")
    d = c.to_dict()
    assert d["on"] is None and d["value"] is None and d["range"] is None
    assert d["status"] is None and d["online"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.model'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/model.py
from dataclasses import dataclass

VALID_KINDS = {"toggle", "momentary", "slider", "speed", "readout"}


@dataclass
class Control:
    domain: str
    id: str
    name: str
    kind: str
    on: bool | None = None
    value: float | None = None
    range: tuple | None = None
    status: str | None = None
    online: bool = True

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "on": self.on,
            "value": self.value,
            "range": list(self.range) if self.range is not None else None,
            "status": self.status,
            "online": self.online,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/model.py tests/test_model.py && git commit -m "feat: normalized Control model"
```

---

## Task 4: Adapter base class + HTTP helper

**Files:**
- Create: `homed/adapters/base.py`
- Test: covered indirectly by adapter tests; add a focused test `tests/test_base_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base_adapter.py
import responses
from homed.adapters.base import Adapter

class Dummy(Adapter):
    domain = "dummy"
    def snapshot(self): return []
    def command(self, control_id, payload): pass

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_base_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.adapters.base'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/adapters/base.py
from abc import ABC, abstractmethod
import requests


class Adapter(ABC):
    """One backend daemon, normalized. Subclasses set `domain`."""
    domain: str = ""

    def __init__(self, base_url: str, headers: dict | None = None, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout

    # ── shared HTTP ──────────────────────────────────────────────
    def get_json(self, path: str) -> dict:
        r = requests.get(self.base_url + path, headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def post_json(self, path: str, body: dict | None = None) -> dict:
        h = {**self.headers, "Content-Type": "application/json"}
        r = requests.post(self.base_url + path, json=body or {}, headers=h, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ── contract ─────────────────────────────────────────────────
    @abstractmethod
    def snapshot(self) -> list:
        """Return list[Control] for this domain."""

    @abstractmethod
    def command(self, control_id: str, payload: dict) -> None:
        """Translate a normalized command to native backend calls."""

    def start(self, on_change):
        """Optional: begin background updates, calling on_change() per update.
        Default no-op; overridden by WS/polling adapters."""
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_base_adapter.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/base.py tests/test_base_adapter.py && git commit -m "feat: adapter base + HTTP helper"
```

---

## Task 5: Fans adapter — snapshot mapping

**Files:**
- Create: `homed/adapters/fans.py`
- Test: `tests/test_fans_adapter.py`

Backend contract: `GET /api/fans` → `{"fans":[{"id","name","online","state":{"fanOn","fanSpeed","lightOn","lightBrightness"}}],"heaters":[{"id","name","online","state":{"on","level"}}]}`.

Normalization: emit aggregate controls keyed by the `groups` the Home row may ask for — `fans` (speed), `lights` (slider), `heaters` (slider). "All Fans" aggregates: `on` = any fanOn; `value` = shared speed or None; status = "N of M".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fans_adapter.py
import responses
from homed.adapters.fans import FansAdapter

SNAP = {
    "fans": [
        {"id": "a", "name": "A", "online": True,  "state": {"fanOn": True,  "fanSpeed": 2, "lightOn": True,  "lightBrightness": 60}},
        {"id": "b", "name": "B", "online": True,  "state": {"fanOn": False, "lightOn": False}},
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
    assert fans.status == "1 of 2"          # one of two fans on
    assert fans.value == 2                   # shared speed (only A is on)

    lights = controls["lights"]
    assert lights.kind == "slider" and lights.on is True
    assert lights.value == 60 and lights.range == (1, 100)

    heaters = controls["heaters"]
    assert heaters.kind == "slider" and heaters.on is True and heaters.value == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fans_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.adapters.fans'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/adapters/fans.py
from homed.adapters.base import Adapter
from homed.model import Control


def _shared(values):
    """Single agreed value among 'on' devices, else None."""
    s = set(values)
    return values[0] if len(s) == 1 else None


class FansAdapter(Adapter):
    domain = "fans"

    def snapshot(self):
        data = self.get_json("/api/fans")
        fans = data.get("fans", [])
        heaters = data.get("heaters", [])
        out = []

        # All Fans (speed)
        on_fans = [f for f in fans if f.get("state", {}).get("fanOn")]
        speeds = [f["state"].get("fanSpeed") for f in on_fans if f["state"].get("fanSpeed")]
        out.append(Control(
            domain="fans", id="fans", name="All Fans", kind="speed",
            on=bool(on_fans), value=_shared(speeds) if speeds else None, range=(1, 6),
            status=f"{len(on_fans)} of {len(fans)}" if fans else None,
            online=any(f.get("online") for f in fans) if fans else False,
        ))

        # All Lights (slider)
        on_lights = [f for f in fans if f.get("state", {}).get("lightOn")]
        brights = [f["state"].get("lightBrightness") for f in on_lights if f["state"].get("lightBrightness")]
        out.append(Control(
            domain="fans", id="lights", name="All Lights", kind="slider",
            on=bool(on_lights), value=_shared(brights) if brights else None, range=(1, 100),
            status=f"{len(on_lights)} of {len(fans)}" if fans else None,
            online=any(f.get("online") for f in fans) if fans else False,
        ))

        # All Heaters (slider) — only if present
        if heaters:
            on_h = [h for h in heaters if h.get("state", {}).get("on")]
            levels = [h["state"].get("level") for h in on_h if h["state"].get("level")]
            out.append(Control(
                domain="fans", id="heaters", name="All Heaters", kind="slider",
                on=bool(on_h), value=_shared(levels) if levels else None, range=(1, 100),
                status=f"{len(on_h)} of {len(heaters)}",
                online=any(h.get("online") for h in heaters),
            ))
        return out

    def command(self, control_id, payload):  # filled in Task 6
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fans_adapter.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/fans.py tests/test_fans_adapter.py && git commit -m "feat: fans adapter snapshot mapping"
```

---

## Task 6: Fans adapter — command translation

**Files:**
- Modify: `homed/adapters/fans.py` (replace `command`)
- Test: `tests/test_fans_adapter.py` (add cases)

Mapping: `fans` group → `POST /api/all {fanOn|fanSpeed}`; `lights` group → `POST /api/all {lightOn|lightBrightness}`; `heaters` group → per-heater `POST /api/heaters/{id}` is unavailable in aggregate, so broadcast by iterating snapshot heater ids. For the Home card we only need on/off + a value, so payload is `{"on": bool}` and/or `{"value": number}`.

- [ ] **Step 1: Write the failing test (add to file)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fans_adapter.py -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Replace `command` in `homed/adapters/fans.py`**

```python
    def command(self, control_id, payload):
        on = payload.get("on")
        value = payload.get("value")
        if control_id == "fans":
            body = {}
            if value is not None:
                body = {"fanOn": True, "fanSpeed": int(value)}
            elif on is not None:
                body = {"fanOn": bool(on)}
            self.post_json("/api/all", body)
        elif control_id == "lights":
            body = {}
            if value is not None:
                body = {"lightOn": True, "lightBrightness": int(value)}
            elif on is not None:
                body = {"lightOn": bool(on)}
            self.post_json("/api/all", body)
        elif control_id == "heaters":
            data = self.get_json("/api/fans")
            for h in data.get("heaters", []):
                if value is not None:
                    self.post_json(f"/api/heaters/{h['id']}", {"level": int(value)})
                elif on is not None:
                    self.post_json(f"/api/heaters/{h['id']}", {"power": bool(on)})
        else:
            raise ValueError(f"unknown fans control: {control_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fans_adapter.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/fans.py tests/test_fans_adapter.py && git commit -m "feat: fans adapter commands"
```

---

## Task 7: Fans adapter — upstream WebSocket updates

**Files:**
- Modify: `homed/adapters/fans.py` (add `start`)
- Test: `tests/test_fans_adapter.py` (add a thread-free unit test of the message handler)

The WS pushes the same `{fans,heaters}` snapshot. We don't parse the payload (we re-`snapshot()` on any nudge to keep one mapping path); the handler just calls `on_change`.

- [ ] **Step 1: Write the failing test (add to file)**

```python
def test_ws_message_triggers_on_change(monkeypatch):
    a = FansAdapter("http://f")
    hits = []
    a._on_change = lambda: hits.append(1)
    a._handle_ws_message("{}")
    assert hits == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fans_adapter.py::test_ws_message_triggers_on_change -v`
Expected: FAIL with `AttributeError: 'FansAdapter' object has no attribute '_handle_ws_message'`

- [ ] **Step 3: Add WS plumbing to `homed/adapters/fans.py`**

Add imports at top: `import threading`, `import websocket`. Add methods:

```python
    def start(self, on_change):
        self._on_change = on_change
        ws_url = self.base_url.replace("http", "ws", 1) + "/api/ws"
        t = threading.Thread(target=self._run_ws, args=(ws_url,), daemon=True)
        t.start()
        return t

    def _handle_ws_message(self, _msg):
        if getattr(self, "_on_change", None):
            self._on_change()

    def _run_ws(self, ws_url):
        import time
        while True:
            try:
                app = websocket.WebSocketApp(
                    ws_url,
                    on_message=lambda _ws, m: self._handle_ws_message(m),
                )
                app.run_forever(ping_interval=30)
            except Exception:
                pass
            time.sleep(3)   # reconnect backoff
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fans_adapter.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/fans.py tests/test_fans_adapter.py && git commit -m "feat: fans adapter upstream WS"
```

---

## Task 8: Pool adapter — snapshot mapping

**Files:**
- Create: `homed/adapters/pool.py`
- Test: `tests/test_pool_adapter.py`

Backend: `GET /api/pool` → `{"pool":{"on","temperature","setpoint","heating"},"spa":{"on","temperature","setpoint","spa_heat_progress":{...},"accessories":{"jets":bool}},"lights":{"on"},"auxiliaries":[{"id","name","on"}]}`. Any of pool/spa/lights may be `null`.

Normalize to: `spa` (toggle, status from heat progress, value=temperature readout shown in `value`), `pool` (toggle, value=temperature), `lights` (toggle), one control per auxiliary id.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pool_adapter.py
import responses
from homed.adapters.pool import PoolAdapter

SNAP = {
    "pool":  {"on": False, "temperature": 78, "setpoint": 82, "heating": "off"},
    "spa":   {"on": True,  "temperature": 88, "setpoint": 102,
              "spa_heat_progress": {"active": True, "minutes_remaining": 12, "target_temp_f": 102},
              "accessories": {"jets": True}},
    "lights": {"on": False},
    "auxiliaries": [{"id": "water_feature", "name": "Water Feature", "on": False}],
}

@responses.activate
def test_snapshot_maps_pool_spa_lights_aux():
    responses.add(responses.GET, "http://p/api/pool", json=SNAP, status=200)
    c = {x.id: x for x in PoolAdapter("http://p").snapshot()}

    assert c["spa"].kind == "toggle" and c["spa"].on is True
    assert c["spa"].value == 88
    assert "102" in c["spa"].status            # heating → target

    assert c["pool"].kind == "toggle" and c["pool"].on is False and c["pool"].value == 78
    assert c["lights"].kind == "toggle" and c["lights"].on is False
    assert c["water_feature"].kind == "toggle" and c["water_feature"].name == "Water Feature"

@responses.activate
def test_snapshot_tolerates_null_bodies():
    responses.add(responses.GET, "http://p/api/pool",
                  json={"pool": None, "spa": None, "lights": None, "auxiliaries": []}, status=200)
    assert PoolAdapter("http://p").snapshot() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pool_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.adapters.pool'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/adapters/pool.py
from homed.adapters.base import Adapter
from homed.model import Control


class PoolAdapter(Adapter):
    domain = "pool"

    def snapshot(self):
        data = self.get_json("/api/pool")
        out = []

        spa = data.get("spa")
        if spa is not None:
            prog = spa.get("spa_heat_progress") or {}
            if prog.get("active") and prog.get("target_temp_f"):
                mins = prog.get("minutes_remaining")
                status = f"Heating → {prog['target_temp_f']}°" + (f" ({mins}m)" if mins else "")
            else:
                status = "On" if spa.get("on") else "Off"
            out.append(Control(domain="pool", id="spa", name="Spa", kind="toggle",
                               on=bool(spa.get("on")), value=spa.get("temperature"), status=status))

        pool = data.get("pool")
        if pool is not None:
            out.append(Control(domain="pool", id="pool", name="Pool", kind="toggle",
                               on=bool(pool.get("on")), value=pool.get("temperature"),
                               status="On" if pool.get("on") else "Off"))

        lights = data.get("lights")
        if lights is not None:
            out.append(Control(domain="pool", id="lights", name="Pool Light", kind="toggle",
                               on=bool(lights.get("on"))))

        for aux in data.get("auxiliaries", []):
            out.append(Control(domain="pool", id=aux["id"], name=aux.get("name", aux["id"]),
                               kind="toggle", on=bool(aux.get("on"))))
        return out

    def command(self, control_id, payload):  # filled in Task 9
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pool_adapter.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/pool.py tests/test_pool_adapter.py && git commit -m "feat: pool adapter snapshot mapping"
```

---

## Task 9: Pool adapter — command translation

**Files:**
- Modify: `homed/adapters/pool.py` (replace `command`)
- Test: `tests/test_pool_adapter.py` (add cases)

Mapping (toggles → on/off endpoints): `spa`→`/api/spa/{on|off}`, `pool`→`/api/pool/{on|off}`, `lights`→`/api/lights/{on|off}`, any other id → `/api/auxiliary/{id}/{on|off}`.

- [ ] **Step 1: Write the failing test (add to file)**

```python
@responses.activate
def test_command_spa_on():
    responses.add(responses.POST, "http://p/api/spa/on", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("spa", {"on": True})
    assert responses.calls[0].request.url.endswith("/api/spa/on")

@responses.activate
def test_command_pool_off():
    responses.add(responses.POST, "http://p/api/pool/off", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("pool", {"on": False})
    assert responses.calls[0].request.url.endswith("/api/pool/off")

@responses.activate
def test_command_aux_on():
    responses.add(responses.POST, "http://p/api/auxiliary/water_feature/on", json={"ok": True}, status=200)
    PoolAdapter("http://p").command("water_feature", {"on": True})
    assert responses.calls[0].request.url.endswith("/api/auxiliary/water_feature/on")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pool_adapter.py -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Replace `command` in `homed/adapters/pool.py`**

```python
    def command(self, control_id, payload):
        verb = "on" if payload.get("on") else "off"
        named = {"spa", "pool", "lights"}
        if control_id in named:
            self.post_json(f"/api/{control_id}/{verb}", {})
        else:
            self.post_json(f"/api/auxiliary/{control_id}/{verb}", {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pool_adapter.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/pool.py tests/test_pool_adapter.py && git commit -m "feat: pool adapter commands"
```

---

## Task 10: Pool adapter — upstream WebSocket updates

**Files:**
- Modify: `homed/adapters/pool.py` (add `start` / `_handle_ws_message` / `_run_ws`)
- Test: `tests/test_pool_adapter.py` (add handler test)

The pool WS (`/api/ws`) pushes the same `/api/pool` snapshot. Same pattern as the fans adapter: nudge `on_change`.

- [ ] **Step 1: Write the failing test (add to file)**

```python
def test_ws_message_triggers_on_change():
    a = PoolAdapter("http://p")
    hits = []
    a._on_change = lambda: hits.append(1)
    a._handle_ws_message("{}")
    assert hits == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pool_adapter.py::test_ws_message_triggers_on_change -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add WS plumbing to `homed/adapters/pool.py`**

Add `import threading` and `import websocket` at top, then the identical three methods from Task 7 Step 3 (`start`, `_handle_ws_message`, `_run_ws`), unchanged except they live on `PoolAdapter`.

```python
    def start(self, on_change):
        self._on_change = on_change
        ws_url = self.base_url.replace("http", "ws", 1) + "/api/ws"
        t = threading.Thread(target=self._run_ws, args=(ws_url,), daemon=True)
        t.start()
        return t

    def _handle_ws_message(self, _msg):
        if getattr(self, "_on_change", None):
            self._on_change()

    def _run_ws(self, ws_url):
        import time
        while True:
            try:
                app = websocket.WebSocketApp(ws_url, on_message=lambda _ws, m: self._handle_ws_message(m))
                app.run_forever(ping_interval=30)
            except Exception:
                pass
            time.sleep(3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pool_adapter.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/pool.py tests/test_pool_adapter.py && git commit -m "feat: pool adapter upstream WS"
```

---

## Task 11: Gate adapter — snapshot mapping

**Files:**
- Create: `homed/adapters/gate.py`
- Test: `tests/test_gate_adapter.py`

Backend: `GET /devices` → `[{"id","name","status":"open|locked|unlocked","is_held","hold_state","expires_at","is_online"}]`. Auth: inject `X-Verified-User` (passed via `headers`).

Normalize: one `momentary` control per door (the "unlock" action), with `on` reflecting held state and `status` describing lock state. Also expose an aggregate `gate` control summarizing "N locked" for the Home `control="unlock"` row.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate_adapter.py
import responses
from homed.adapters.gate import GateAdapter

DEVICES = [
    {"id": "front", "name": "Front", "status": "locked",   "is_held": False, "hold_state": None,        "expires_at": None, "is_online": True},
    {"id": "side",  "name": "Side",  "status": "unlocked", "is_held": True,  "hold_state": "hold_forever","expires_at": None, "is_online": True},
]

@responses.activate
def test_snapshot_one_control_per_door_plus_aggregate():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    c = {x.id: x for x in GateAdapter("http://g", headers={"X-Verified-User": "svc@local"}).snapshot()}

    assert c["front"].kind == "momentary" and c["front"].on is False
    assert c["front"].status == "Locked"
    assert c["side"].on is True and c["side"].status == "Held open"

    agg = c["gate"]
    assert agg.kind == "momentary" and agg.status == "1 locked"

@responses.activate
def test_snapshot_injects_service_user_header():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    GateAdapter("http://g", headers={"X-Verified-User": "svc@local"}).snapshot()
    assert responses.calls[0].request.headers["X-Verified-User"] == "svc@local"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.adapters.gate'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/adapters/gate.py
import threading
import time
from homed.adapters.base import Adapter
from homed.model import Control


def _door_status(d):
    if d.get("is_held"):
        return "Held open"
    return {"locked": "Locked", "unlocked": "Unlocked", "open": "Open"}.get(d.get("status"), "Unknown")


class GateAdapter(Adapter):
    domain = "gate"

    def snapshot(self):
        doors = self.get_json("/devices")
        out = []
        for d in doors:
            out.append(Control(domain="gate", id=d["id"], name=d.get("name", d["id"]),
                               kind="momentary", on=bool(d.get("is_held")),
                               status=_door_status(d), online=bool(d.get("is_online", True))))
        locked = sum(1 for d in doors if d.get("status") == "locked")
        out.append(Control(domain="gate", id="gate", name="Gate", kind="momentary",
                           on=any(d.get("is_held") for d in doors),
                           status=f"{locked} locked", online=any(d.get("is_online", True) for d in doors)))
        return out

    def command(self, control_id, payload):  # filled in Task 12
        raise NotImplementedError

    # polling upstream (no client WS on unifi-gate)
    def start(self, on_change):
        self._on_change = on_change
        t = threading.Thread(target=self._poll, daemon=True)
        t.start()
        return t

    def _poll(self):
        while True:
            try:
                self.snapshot()
                if getattr(self, "_on_change", None):
                    self._on_change()
            except Exception:
                pass
            time.sleep(3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate_adapter.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/gate.py tests/test_gate_adapter.py && git commit -m "feat: gate adapter snapshot mapping"
```

---

## Task 12: Gate adapter — command translation

**Files:**
- Modify: `homed/adapters/gate.py` (replace `command`)
- Test: `tests/test_gate_adapter.py` (add cases)

The Home card needs only **unlock once**. The full Gate tab (Plan 2) will use hold endpoints; expose them here so they're ready. Payload `{"action": "unlock"|"hold_today"|"hold_forever"|"stop"}`; default `unlock`. For `hold_today`, optional `end_time`. The aggregate id `"gate"` unlocks all doors.

- [ ] **Step 1: Write the failing test (add to file)**

```python
@responses.activate
def test_command_unlock_once():
    responses.add(responses.POST, "http://g/unlock/front", json={"status": "success"}, status=200)
    GateAdapter("http://g").command("front", {"action": "unlock"})
    assert responses.calls[0].request.url.endswith("/unlock/front")

@responses.activate
def test_command_default_action_is_unlock():
    responses.add(responses.POST, "http://g/unlock/front", json={"status": "success"}, status=200)
    GateAdapter("http://g").command("front", {})
    assert responses.calls[0].request.url.endswith("/unlock/front")

@responses.activate
def test_command_hold_today_passes_end_time():
    responses.add(responses.POST, "http://g/hold/today/front", json={"status": "success"}, status=200)
    GateAdapter("http://g").command("front", {"action": "hold_today", "end_time": "20:30"})
    import json
    assert json.loads(responses.calls[0].request.body) == {"end_time": "20:30"}

@responses.activate
def test_command_aggregate_unlocks_all_doors():
    responses.add(responses.GET, "http://g/devices", json=DEVICES, status=200)
    responses.add(responses.POST, "http://g/unlock/front", json={"status": "success"}, status=200)
    responses.add(responses.POST, "http://g/unlock/side", json={"status": "success"}, status=200)
    GateAdapter("http://g").command("gate", {"action": "unlock"})
    unlocked = {c.request.url.rsplit("/", 1)[1] for c in responses.calls if "/unlock/" in c.request.url}
    assert unlocked == {"front", "side"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_adapter.py -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Replace `command` in `homed/adapters/gate.py`**

```python
    def command(self, control_id, payload):
        action = payload.get("action", "unlock")
        if control_id == "gate":
            for d in self.get_json("/devices"):
                self._door_action(d["id"], action, payload)
        else:
            self._door_action(control_id, action, payload)

    def _door_action(self, door_id, action, payload):
        if action == "unlock":
            self.post_json(f"/unlock/{door_id}", {})
        elif action == "hold_today":
            body = {}
            if payload.get("end_time"):
                body["end_time"] = payload["end_time"]
            self.post_json(f"/hold/today/{door_id}", body)
        elif action == "hold_forever":
            self.post_json(f"/hold/forever/{door_id}", {})
        elif action == "stop":
            self.post_json(f"/hold/stop/{door_id}", {})
        else:
            raise ValueError(f"unknown gate action: {action}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate_adapter.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/adapters/gate.py tests/test_gate_adapter.py && git commit -m "feat: gate adapter commands + hold endpoints"
```

---

## Task 13: Aggregator — cache snapshots + fan-out to subscribers

**Files:**
- Create: `homed/aggregator.py`
- Test: `tests/test_aggregator.py`

The Aggregator holds adapters, refreshes a per-domain cache, dispatches commands, and notifies SSE subscribers when any domain changes. It must be thread-safe (background updaters call it).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_aggregator.py
from homed.aggregator import Aggregator
from homed.model import Control

class FakeAdapter:
    def __init__(self, domain, controls):
        self.domain = domain
        self._controls = controls
        self.commands = []
        self.started = False
    def snapshot(self): return self._controls
    def command(self, cid, payload): self.commands.append((cid, payload))
    def start(self, on_change): self.started = True

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
    agg.refresh_domain("fans")        # simulate an update
    assert q.get_nowait() is not None  # a state payload was queued

def test_start_begins_all_adapters():
    a = FakeAdapter("fans", [])
    agg = Aggregator({"fans": a})
    agg.start()
    assert a.started is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_aggregator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.aggregator'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/aggregator.py
import queue
import threading


class Aggregator:
    def __init__(self, adapters: dict):
        self.adapters = adapters
        self._cache = {d: [] for d in adapters}     # domain -> list[Control]
        self._lock = threading.Lock()
        self._subscribers = set()                    # set[queue.Queue]

    # ── snapshots ────────────────────────────────────────────────
    def refresh_domain(self, domain):
        controls = self.adapters[domain].snapshot()
        with self._lock:
            self._cache[domain] = controls
        self._notify()

    def refresh_all(self):
        for d in self.adapters:
            try:
                self.refresh_domain(d)
            except Exception:
                pass

    def state(self) -> dict:
        with self._lock:
            controls = [c.to_dict() for cs in self._cache.values() for c in cs]
        return {"controls": controls}

    # ── commands ─────────────────────────────────────────────────
    def dispatch(self, domain, control_id, payload):
        self.adapters[domain].command(control_id, payload)   # KeyError if unknown
        try:
            self.refresh_domain(domain)
        except Exception:
            pass

    # ── pub/sub for SSE ──────────────────────────────────────────
    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=8)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def _notify(self):
        payload = self.state()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    # ── background updaters ──────────────────────────────────────
    def start(self):
        for d, adapter in self.adapters.items():
            adapter.start(lambda d=d: self._safe_refresh(d))

    def _safe_refresh(self, domain):
        try:
            self.refresh_domain(domain)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_aggregator.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/aggregator.py tests/test_aggregator.py && git commit -m "feat: aggregator with cache + pub/sub"
```

---

## Task 14: Auth gate (silver-oauth, Flask port)

**Files:**
- Create: `homed/auth.py`
- Test: `tests/test_auth.py`

Port `fans/auth.py` to Flask. Same model: LAN open; remote (Host matches `remote_domain`) requires `home_session` cookie minted from broker handoff JWT; `allowed_emails` allowlist; fail-closed. Cookie/secret names use `home_`. Secrets from `~/.home/` or env.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
import jwt
from homed.auth import AuthGate

CFG = {"remote_domain": "home.example.com", "broker_url": "https://b",
       "allowed_emails": ["you@gmail.com"]}

def make_gate(tmp_path, handoff="hs", session="ss"):
    g = AuthGate(CFG, state_dir=tmp_path)
    g.handoff_secret = handoff
    g.session_secret = session
    return g

def test_lan_request_is_open(tmp_path):
    g = make_gate(tmp_path)
    assert g.is_remote("192.168.1.9") is False
    assert g.is_remote("home.example.com") is True

def test_session_roundtrip(tmp_path):
    g = make_gate(tmp_path)
    cookie = g.make_session("you@gmail.com")
    assert g.verify_session(cookie) == "you@gmail.com"

def test_disallowed_email_rejected(tmp_path):
    g = make_gate(tmp_path)
    cookie = g.make_session("intruder@evil.com")
    # verify_session returns the email, but current_user enforces the allowlist
    assert g.email_allowed("intruder@evil.com") is False
    assert g.email_allowed("you@gmail.com") is True

def test_verify_handoff_uses_handoff_secret(tmp_path):
    g = make_gate(tmp_path)
    token = jwt.encode({"email": "you@gmail.com"}, "hs", algorithm="HS256")
    assert g.verify_handoff(token) == "you@gmail.com"

def test_fully_configured(tmp_path):
    g = make_gate(tmp_path)
    assert g.fully_configured is True
    g.handoff_secret = ""
    assert g.fully_configured is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.auth'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/auth.py
import os
import secrets
import time
from pathlib import Path

import jwt
from flask import request, jsonify, redirect, make_response
from urllib.parse import urlencode

SESSION_COOKIE = "home_session"
STATE_COOKIE = "home_oauth_state"
HANDOFF_PARAM = "silver_oauth"
SESSION_TTL = 30 * 86400
PUBLIC_PATHS = {"/", "/api/auth/login", "/api/auth/callback", "/api/auth/me", "/api/auth/logout"}


def _host(h):
    h = (h or "").strip()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    if h.count(":") == 1:
        return h.rsplit(":", 1)[0]
    return h


class AuthGate:
    def __init__(self, web_cfg, state_dir=None):
        self.remote_domain = (web_cfg.get("remote_domain") or "").strip().lower()
        self.broker_url = (web_cfg.get("broker_url") or "").rstrip("/")
        self.allowed = {e.strip().lower() for e in web_cfg.get("allowed_emails", []) if e.strip()}
        self.state_dir = Path(state_dir or Path("~/.home").expanduser())
        self.handoff_secret = self._read("BROKER_HANDOFF_SECRET", ".broker_handoff")
        self.session_secret = self._session_secret()

    def _read(self, env, name):
        v = os.environ.get(env, "").strip()
        if v:
            return v
        try:
            return (self.state_dir / name).read_text().strip()
        except FileNotFoundError:
            return ""

    def _session_secret(self):
        v = os.environ.get("HOME_SESSION_SECRET", "").strip()
        if v:
            return v
        p = self.state_dir / ".session_secret"
        try:
            return p.read_text().strip()
        except FileNotFoundError:
            s = secrets.token_hex(32)
            try:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                p.write_text(s)
                p.chmod(0o600)
            except OSError:
                pass
            return s

    @property
    def fully_configured(self):
        return bool(self.handoff_secret and self.session_secret and self.allowed)

    @property
    def active(self):
        return bool(self.remote_domain)

    def is_remote(self, host_header):
        if not self.remote_domain:
            return False
        host = _host(host_header).lower()
        return host == self.remote_domain or host.endswith("." + self.remote_domain)

    def email_allowed(self, email):
        return bool(email) and email.lower() in self.allowed

    def make_session(self, email):
        now = int(time.time())
        return jwt.encode({"email": email, "iat": now, "exp": now + SESSION_TTL},
                          self.session_secret, algorithm="HS256")

    def verify_session(self, value):
        try:
            return jwt.decode(value, self.session_secret, algorithms=["HS256"]).get("email")
        except jwt.PyJWTError:
            return None

    def verify_handoff(self, token):
        try:
            return jwt.decode(token, self.handoff_secret, algorithms=["HS256"]).get("email")
        except jwt.PyJWTError:
            return None

    def current_user(self):
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            return None
        email = self.verify_session(cookie)
        return email if self.email_allowed(email) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add homed/auth.py tests/test_auth.py && git commit -m "feat: silver-oauth gate (Flask port)"
```

---

## Task 15: Flask server — REST routes + SSE + wiring

**Files:**
- Create: `homed/server.py`
- Test: `tests/test_server.py`

Routes:
- `GET /api/state` → `aggregator.state()`
- `GET /api/home` → state filtered/ordered by `cfg.home_rows` (the unified Home card payload)
- `POST /api/command` body `{domain, id, payload}` → `aggregator.dispatch(...)`
- `GET /api/stream` → SSE of state payloads
- `GET /api/auth/login|callback|me|logout` → silver-oauth dance
- `before_request` → enforce auth on remote
- `GET /` and `GET /<path>` → serve `static/` (the SPA from Plan 2; for now serve a stub)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py
import json
from homed.server import create_app
from homed.aggregator import Aggregator
from homed.model import Control

class FakeAdapter:
    domain = "fans"
    def __init__(self): self.commands = []
    def snapshot(self): return [Control("fans", "fans", "All Fans", "speed", on=True)]
    def command(self, cid, payload): self.commands.append((cid, payload))
    def start(self, on_change): pass

def make_client(home_rows=None, web=None):
    adapter = FakeAdapter()
    agg = Aggregator({"fans": adapter})
    agg.refresh_all()
    app = create_app(agg, home_rows=home_rows or [{"domain": "fans", "groups": ["fans"]}],
                     web=web or {})
    return app.test_client(), adapter

def test_state_endpoint():
    client, _ = make_client()
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.get_json()["controls"][0]["id"] == "fans"

def test_home_endpoint_filters_by_rows():
    client, _ = make_client(home_rows=[{"domain": "fans", "groups": ["fans"]}])
    r = client.get("/api/home")
    ids = [c["id"] for c in r.get_json()["controls"]]
    assert ids == ["fans"]

def test_command_endpoint_dispatches():
    client, adapter = make_client()
    r = client.post("/api/command", json={"domain": "fans", "id": "fans", "payload": {"on": True}})
    assert r.status_code == 200
    assert adapter.commands == [("fans", {"on": True})]

def test_command_unknown_domain_returns_400():
    client, _ = make_client()
    r = client.post("/api/command", json={"domain": "nope", "id": "x", "payload": {}})
    assert r.status_code == 400

def test_lan_request_open_when_remote_domain_set():
    client, _ = make_client(web={"remote_domain": "home.example.com",
                                  "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"})
    # Host is the test client default (localhost) → LAN → open
    assert client.get("/api/state").status_code == 200

def test_remote_request_without_cookie_blocked():
    client, _ = make_client(web={"remote_domain": "home.example.com",
                                 "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"})
    r = client.get("/api/state", headers={"Host": "home.example.com"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'homed.server'`

- [ ] **Step 3: Write minimal implementation**

```python
# homed/server.py
import json
import queue
from pathlib import Path

from flask import Flask, request, jsonify, Response, redirect, make_response, send_from_directory
from werkzeug.utils import safe_join
from urllib.parse import urlencode

from homed.auth import AuthGate, SESSION_COOKIE, STATE_COOKIE, HANDOFF_PARAM, PUBLIC_PATHS, SESSION_TTL

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _filter_home(state, home_rows):
    """Order/limit controls to those named by home_rows."""
    by_id = {(c["domain"], c["id"]): c for c in state["controls"]}
    out = []
    for row in home_rows:
        dom = row["domain"]
        ids = row.get("groups") or row.get("circuits") or ([row["control"]] if row.get("control") else [])
        if dom == "gate" and row.get("control") == "unlock":
            ids = ["gate"]
        for cid in ids:
            c = by_id.get((dom, cid))
            if c:
                out.append(c)
    return {"controls": out}


def _https():
    return request.headers.get("x-forwarded-proto") == "https" or request.scheme == "https"


def create_app(aggregator, home_rows, web):
    app = Flask(__name__, static_folder=None)
    gate = AuthGate(web)

    @app.before_request
    def _auth():
        if not gate.is_remote(request.headers.get("Host", "")):
            return None                       # LAN: open
        if not gate.fully_configured:
            return jsonify({"error": "remote auth not configured"}), 503
        if request.path in PUBLIC_PATHS or request.path.startswith("/api/auth/"):
            return None
        if not gate.current_user():
            return jsonify({"error": "not signed in", "authRequired": True}), 401
        return None

    @app.get("/api/state")
    def state():
        return jsonify(aggregator.state())

    @app.get("/api/home")
    def home():
        return jsonify(_filter_home(aggregator.state(), home_rows))

    @app.post("/api/command")
    def command():
        body = request.get_json(silent=True) or {}
        try:
            aggregator.dispatch(body["domain"], body["id"], body.get("payload", {}))
        except KeyError:
            return jsonify({"error": "unknown domain or id"}), 400
        return jsonify({"ok": True})

    @app.get("/api/stream")
    def stream():
        q = aggregator.subscribe()

        def gen():
            try:
                yield f"data: {json.dumps(aggregator.state())}\n\n"
                while True:
                    payload = q.get()
                    yield f"data: {json.dumps(payload)}\n\n"
            finally:
                aggregator.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream")

    # ── auth dance ───────────────────────────────────────────────
    @app.get("/api/auth/login")
    def auth_login():
        if not gate.fully_configured:
            return "auth not configured", 503
        import secrets as _s
        st = _s.token_urlsafe(24)
        host = request.headers.get("Host", "")
        scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else request.scheme
        cb = f"{scheme}://{host}/api/auth/callback?{urlencode({'state': st})}"
        resp = make_response(redirect(f"{gate.broker_url}/start?{urlencode({'return_url': cb, 'scope': 'openid'})}"))
        resp.set_cookie(STATE_COOKIE, st, max_age=600, httponly=True,
                        secure=_https(), samesite="Lax", path="/")
        return resp

    @app.get("/api/auth/callback")
    def auth_callback():
        import secrets as _s
        st = request.args.get("state", "")
        cookie_st = request.cookies.get(STATE_COOKIE, "")
        if not st or not cookie_st or not _s.compare_digest(st, cookie_st):
            return "invalid state", 400
        email = gate.verify_handoff(request.args.get(HANDOFF_PARAM, ""))
        if not email:
            return "invalid handoff", 401
        if not gate.email_allowed(email):
            return f"{email} not allowed", 403
        resp = make_response(redirect("/"))
        resp.delete_cookie(STATE_COOKIE, path="/")
        resp.set_cookie(SESSION_COOKIE, gate.make_session(email),
                        max_age=SESSION_TTL, httponly=True,
                        secure=_https(), samesite="Lax", path="/")
        return resp

    @app.get("/api/auth/me")
    def auth_me():
        if not gate.is_remote(request.headers.get("Host", "")) or not gate.fully_configured:
            return jsonify({"email": None, "authRequired": False})
        email = gate.current_user()
        if not email:
            return jsonify({"authRequired": True}), 401
        return jsonify({"email": email, "authRequired": True})

    @app.post("/api/auth/logout")
    def auth_logout():
        resp = make_response(jsonify({"ok": True}))
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # ── static SPA (Plan 2 fills static/index.html) ──────────────
    # safe_join returns None on traversal (e.g. "../.."), so arbitrary-file
    # reads are impossible; send_from_directory sets correct MIME types and
    # streams binary assets. Never use STATIC_DIR / path + read_text().
    @app.get("/")
    @app.get("/<path:path>")
    def spa(path="index.html"):
        target = path or "index.html"
        safe = safe_join(str(STATIC_DIR), target)
        if safe and Path(safe).is_file():
            return send_from_directory(STATIC_DIR, target)
        if (STATIC_DIR / "index.html").is_file():
            return send_from_directory(STATIC_DIR, "index.html")
        return "home gateway (UI not built yet)", 200

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_server.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add homed/server.py tests/test_server.py && git commit -m "feat: Flask server (REST + SSE + auth wiring)"
```

---

## Task 16: Entry point + manual end-to-end check

**Files:**
- Create: `homed/__main__.py`
- Modify: none

- [ ] **Step 1: Write the entry point**

```python
# homed/__main__.py
import argparse
import logging
from pathlib import Path

from homed.config import load_config
from homed.adapters.fans import FansAdapter
from homed.adapters.pool import PoolAdapter
from homed.adapters.gate import GateAdapter
from homed.aggregator import Aggregator
from homed.server import create_app


def build(cfg):
    b = cfg.backends
    adapters = {
        "fans": FansAdapter(b["fans"]["base_url"]),
        "pool": PoolAdapter(b["pool"]["base_url"]),
        "gate": GateAdapter(b["gate"]["base_url"],
                            headers={"X-Verified-User": b["gate"].get("service_user", "home@local")}),
    }
    agg = Aggregator(adapters)
    agg.refresh_all()
    agg.start()
    return create_app(agg, home_rows=cfg.home_rows, web=cfg.web)


def main():
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="home.toml")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    app = build(cfg)
    bind = cfg.web.get("bind", "0.0.0.0:8099")
    host, _, port = bind.rpartition(":")
    app.run(host=host or "0.0.0.0", port=int(port), threaded=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual smoke test against live daemons**

Run (with fans/pentair/unifi-gate running locally; copy `home.toml.example` → `home.toml` first and fill base URLs):
```bash
cp home.toml.example home.toml
python -m homed --config home.toml &
sleep 2
curl -s localhost:8099/api/state | python -m json.tool | head -40
curl -s localhost:8099/api/home  | python -m json.tool
```
Expected: `/api/state` lists controls from all three domains; `/api/home` shows only the configured rows. If a daemon is down, its controls are simply absent (no crash).

- [ ] **Step 3: Manual command + stream check**

```bash
# in one terminal:
curl -N localhost:8099/api/stream
# in another:
curl -s -X POST localhost:8099/api/command \
  -H 'Content-Type: application/json' \
  -d '{"domain":"fans","id":"fans","payload":{"on":true}}'
```
Expected: the POST returns `{"ok":true}` and the streaming terminal prints a new `data: {...}` snapshot.

- [ ] **Step 4: Commit**

```bash
git add homed/__main__.py && git commit -m "feat: home gateway entry point"
```

---

## Task 17: Instance repo + deployment artifacts

**Files:**
- Create: `services/home.service`
- Create (in a SEPARATE private repo `../home-instance`): `config/home.toml`, `services/home.service`, `caddy/home.caddy`, `README.md`

- [ ] **Step 1: Create the example systemd unit `services/home.service`**

```ini
[Unit]
Description=Home control gateway
After=network-online.target
Wants=network-online.target

[Service]
User=ssilver
WorkingDirectory=/home/ssilver/development/home
ExecStart=/home/ssilver/development/home/.venv/bin/python -m homed --config /home/ssilver/development/home-instance/config/home.toml
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the private instance repo skeleton**

Run (ask the user before creating/initializing repos per repo rules):
```bash
mkdir -p ../home-instance/config ../home-instance/services ../home-instance/caddy
cp home.toml.example ../home-instance/config/home.toml      # then edit real values + secrets
cp services/home.service ../home-instance/services/home.service
```

- [ ] **Step 3: Create `../home-instance/caddy/home.caddy`** (registered via silver-oauth `register-caddy home 8099`)

```
# home.i.oursilverfamily.com → gateway on :8099
reverse_proxy 127.0.0.1:8099
```

- [ ] **Step 4: Create `../home-instance/README.md`** documenting deploy steps

```markdown
# home-instance

Private deployment config for the `home` gateway (public repo: ../home).

## Deploy
1. `cd ../home && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. Edit `config/home.toml`: backend base URLs, `[web] remote_domain`, `allowed_emails`.
3. Secrets in `~/.home/`: `.broker_handoff` (broker HMAC secret), `.session_secret` (auto-generated if absent).
4. `sudo cp services/home.service /etc/systemd/system/ && sudo systemctl enable --now home`
5. Remote access: `register-caddy home 8099` (silver-oauth Caddy).
```

- [ ] **Step 5: Commit the public-repo artifact**

```bash
git add services/home.service && git commit -m "chore: example systemd unit"
```

---

## Task 18: Security audit (per repo protocol)

**Files:** none (audit only)

- [ ] **Step 1: Dependency CVE scan**

```bash
. .venv/bin/activate && pip install pip-audit && pip-audit -r requirements.txt
```
Report direct-impact CVEs vs informational. Our new deps: Flask, requests, websocket-client, PyJWT — all mainstream, well-maintained.

- [ ] **Step 2: Code audit with codex (read-only)**

```bash
codex exec --sandbox read-only "Review the home gateway in homed/ for: (1) the gate adapter injecting X-Verified-User (homed/adapters/gate.py) — confirm it can't let an untrusted browser forge identity to unifi-gate; (2) the silver-oauth port in homed/auth.py — state-nonce check, handoff verification, fail-closed on remote; (3) SSE endpoint homed/server.py /api/stream for unbounded subscriber growth / resource leak; (4) command dispatch for SSRF or injection into backend URLs. Provide concrete line-level findings."
```

- [ ] **Step 3: Fix or explicitly punt findings** in this session; do not silently defer.

---

## Self-Review (completed during planning)

**Spec coverage:** §1 architecture → Tasks 15–16; §2 adapters+model → Tasks 3–12; §3 config-driven Home → Task 15 `/api/home`; §4 real-time → Tasks 7,10,11,13,15 (SSE deviation noted); §5 auth → Tasks 14–15; §6 repo+deploy → Tasks 1,17; §7 scope → respected (no scenes, no native apps, no backend-config editing). Open items resolved: pentair endpoints (Task 8/9), unifi-gate hold mapping (Task 12 — `momentary` unlock + hold endpoints exposed), React delivery (deferred to Plan 2).

**Placeholder scan:** none — every step has runnable code/commands.

**Type consistency:** `Control` fields and `to_dict` keys match across all adapters; `Aggregator.dispatch(domain, control_id, payload)` signature matches server `/api/command`; `AuthGate` method names (`is_remote`, `current_user`, `make_session`, `verify_session`, `verify_handoff`, `email_allowed`, `fully_configured`) consistent between `auth.py` and `server.py`.

---

## Plan 2 (next): React UI

After this backend lands and `curl` confirms `/api/state`, `/api/home`, `/api/command`, `/api/stream`, Plan 2 builds `static/index.html` (React + Tailwind CDN, fans dark/iOS style): app shell + tabs (Home · Gate · Pool · Fans), an EventSource subscription to `/api/stream`, the unified Home card (layout A) rendered from `/api/home`, and per-domain tabs rendered from `/api/state` filtered by domain. It targets the real, working endpoints this plan produces.
