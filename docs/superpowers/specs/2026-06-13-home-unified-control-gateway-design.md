# Home — Unified Control Gateway

**Date:** 2026-06-13
**Status:** Design approved, pending implementation plan

## Summary

`home` is a unified web hub that controls three existing home-automation
backends — **unifi-gate** (door locks), **pentair** (pool), and **fans**
(Modern Forms fans + Leviton heaters) — from a single app with a **Home** tab
plus a tab per domain (**Gate · Pool · Fans**), all in the visual language of
the fans app's Home tab.

It is an **aggregating gateway**, not a rewrite: the three existing daemons keep
running untouched as the source of truth for their hardware. `home` talks to
them over their existing REST + WebSocket APIs, normalizes their state into one
model, and presents one consistent UI behind one auth front door.

It ships as a **core repo + private instance repo** pair, mirroring the
pentair / pentair-instance paradigm.

## Decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Technical approach | **Aggregating gateway** | One auth door, consistent UI, daemons untouched as source of truth. UI-only was rejected (3 auth schemes + CORS in the browser, no place for server-side logic); iframe-embed rejected (3 inconsistent looks). |
| Stack | **Python Flask + React (CDN/Tailwind)** | Mirrors `unifi-gate-public`, which already proves the gateway + auth + React pattern in this exact stack. |
| Home tab purpose | **Aggregated controls** | Fans-Home-tab style widened across domains. No multi-domain scenes (explicitly out of scope). |
| Home tab layout | **One unified card with domain dividers (layout A)** | Closest to the actual fans Home tab; most compact. |
| Auth / remote access | **silver-oauth broker** (port `fans/auth.py`) | Lighter than Firebase+Cloudflare; LAN open, remote gated by session cookie + allowlist, fail-closed. |

## Architecture

```
Browser (React SPA, fans dark/iOS style)
        │  one WS (/api/ws) + REST
        ▼
home gateway  (Flask, server.py)
        ├─ auth gate (silver-oauth; LAN open, remote gated)
        ├─ adapters/gate.py   ──REST+WS──▶ unifi-gate daemon
        ├─ adapters/pool.py   ──REST+WS──▶ pentair daemon
        └─ adapters/fans.py   ──REST+WS──▶ fans daemon
```

- The gateway is the **only** process the browser talks to. It fronts the three
  daemons; the browser never contacts them directly.
- Each backend keeps owning its hardware protocol, polling, and push. `home`
  reimplements none of that.
- Backend base URLs come from instance config — **never hardcoded** (per repo
  rules).

## Components

### Adapters + normalized model

Three adapters — `adapters/gate.py`, `adapters/pool.py`, `adapters/fans.py` —
each exposing the same interface:

- `snapshot()` → normalized list of controls for that domain
- `command(control_id, payload)` → translate to the backend's native API call
- `subscribe(on_update)` → maintain an upstream WS (or poll) and call back on
  state change

Every control is normalized to one shape so the UI is generic over it:

```
{
  domain:  "gate" | "pool" | "fans",
  id:      str,
  name:    str,
  kind:    "toggle" | "momentary" | "slider" | "speed" | "readout",
  on:      bool | null,
  value:   number | null,      # slider %, speed 1-6, temp, etc.
  range:   [min, max] | null,
  status:  str | null,         # e.g. "Heating → 102°", "2 locked"
  online:  bool
}
```

Adding a future domain = one new adapter, no UI rewrite.

### Real-time data flow

The gateway holds **persistent upstream WS connections** to each daemon that
offers one (fans `/api/ws`, pentair WS, unifi-gate events), polling any backend
that lacks push. It merges all upstream updates into **one downstream WS**
(`/api/ws`) that pushes `{ gate, pool, fans }` snapshots to the browser.

The React UI reuses the fans patterns: optimistic local update on tap, WS push
reconciliation (skip reconcile while a slider drag is pending), auto-reconnect.

### Home tab (layout A) — config-driven

The unified Home tab is **one card with GATE / POOL / FANS dividers**. Which
controls appear in each section is declared in instance config, so each
deployment chooses without code changes:

```toml
[[home.rows]]
domain  = "gate"
control = "unlock"            # momentary unlock + lock status

[[home.rows]]
domain   = "pool"
circuits = ["spa", "pool"]    # toggles + temperature readouts

[[home.rows]]
domain = "fans"
groups = ["fans", "lights"]   # speed pills + brightness slider
```

### Per-domain tabs

Gate / Pool / Fans tabs render the **fuller** control set for that one domain
(all doors / all circuits / fans + lights + heaters) from the same normalized
model, in the unified style. Full feature parity with each native app is an
incremental goal, not a launch requirement.

### Auth & remote access

Port `fans/auth.py` (silver-oauth gate) from aiohttp to a Flask `before_request`
hook. Behavior is unchanged:

- LAN requests: open.
- Remote requests (Host matches configured `remote_domain`): require the
  session cookie, minted from the broker's handoff JWT, with the email on the
  `allowed_emails` allowlist. Fail-closed if secrets/allowlist are missing.
- Secrets (`broker handoff secret`, `session secret`) live in the instance repo
  / `~/.home/`, never hardcoded.
- Remote reach = a `register-caddy home <port>` route under the silver-oauth
  Caddy setup; off-LAN via Tailscale.

## Repository layout (pentair paradigm)

**`home-public`** (this directory, the shared/core repo):

```
home/
├── server.py                 # Flask gateway: routes, WS merge, auth wiring
├── auth.py                   # silver-oauth gate, Flask port of fans/auth.py
├── adapters/
│   ├── base.py               # adapter interface + normalized model
│   ├── gate.py               # unifi-gate adapter
│   ├── pool.py               # pentair adapter
│   └── fans.py               # fans adapter
├── static/                   # React + Tailwind SPA (CDN, no build step)
│   └── index.html
├── home.toml.example         # config template (backend URLs, web, home.rows)
├── tests/
├── requirements.txt
├── services/home.service     # example systemd unit (template)
└── .gitignore                # excludes instance config/secrets
```

**`home-instance`** (private deployment repo):

```
home-instance/
├── config/home.toml          # 3 backend base URLs, broker/allowlist, home.rows
├── services/home.service     # actual systemd unit (paths, user)
├── caddy/home.caddy          # caddy route snippet
└── README.md                 # deployment steps
```

## Configuration (`home.toml`)

```toml
[backends.gate]
base_url = "http://..."       # from instance, never hardcoded

[backends.pool]
base_url = "http://..."

[backends.fans]
base_url = "http://..."

[web]
remote_domain  = "home.i.oursilverfamily.com"
broker_url     = "https://auth.oursilverfamily.com"
allowed_emails = ["scottmsilver@gmail.com"]

# [[home.rows]] entries as shown above
```

## Testing

- **Adapter unit tests**: feed recorded backend responses → assert normalized
  model output; assert `command()` produces the correct native API call (mocked
  HTTP). One fixture set per backend.
- **Auth tests**: port the existing fans auth tests — LAN open, remote without
  cookie → 401, valid handoff → session cookie, disallowed email → 403,
  unconfigured remote → fail-closed 503.
- **WS merge test**: simulate upstream updates from each adapter → assert one
  merged downstream snapshot.

## Scope

**In scope:** 4 tabs (Home + 3 domains), 3 adapters, normalized model, one merged
WS, config-driven Home card, single silver-oauth front door, core + instance repo
split.

**Out of scope (YAGNI):**
- Multi-domain scenes (aggregated controls chosen instead).
- Native mobile apps (web PWA is sufficient).
- Editing the backends' own settings from `home` (each native app retains that).
- Reimplementing any device protocol.

## Open items for the implementation plan

- Confirm exact pentair REST/WS endpoints + circuit naming for the pool adapter.
- Confirm unifi-gate door/lock endpoints + how "hold open" maps to a `momentary`
  vs `toggle` control on the Gate tab.
- Decide the React delivery detail (CDN + Babel like unifi-gate vs a tiny build
  step) — default: match unifi-gate (CDN, no build step).
```
