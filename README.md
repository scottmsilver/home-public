# homed — unified home-control gateway

`homed` is a small Flask service that puts several independent home-automation
backends behind **one** authenticated front door and **one** single-page UI.
Instead of a separate hostname, login, and app per subsystem (pool controller,
gate/access, patio fans, multiroom audio), everything is aggregated into a
single tabbed web app served from this process.

```
        browser (LAN or remote)
                │
                ▼
        ┌───────────────┐     Google sign-in via broker → signed JWT
        │     homed     │◀──── auth gate (allowed-emails + LAN self-approve)
        │  Flask + SSE  │
        └───────┬───────┘
   normalized adapters (HTTP/WS to each daemon)
     ┌──────┬──────┬──────┬──────┐
     ▼      ▼      ▼      ▼      ▼
   pool   gate   fans   music   …        (separate backend daemons)
```

## What it does

- **Aggregates backends** — each subsystem is a backend daemon reached over
  HTTP/WebSocket. An `Adapter` (`homed/adapters/`) normalizes each one into a
  common shape; the `Aggregator` fans out reads/writes and merges state.
- **One live UI** — `static/index.html` is a dependency-free SPA. State streams
  to the browser over Server-Sent Events; actions POST back as REST.
- **One auth boundary** — the gateway verifies identity via an external
  Google-OAuth broker that hands back a signed JWT, then checks the caller
  against an allow-list. Users physically on the home network can self-approve
  (see below).
- **Weather-informed estimates** — when a live pool/spa reading is unavailable,
  the UI shows a weather-informed temperature estimate rather than a stale value.

## Layout

| Path | Responsibility |
|------|----------------|
| `homed/__main__.py`   | Entrypoint: load config, build adapters, run the app |
| `homed/server.py`     | Flask app — routes, SSE stream, auth callback/gate |
| `homed/auth.py`       | `AuthGate`: sessions, JWT handoff, allow-list, LAN trust |
| `homed/aggregator.py` | Fan-out over adapters, merged live state |
| `homed/adapters/`     | One module per backend (`pool`, `gate`, `fans`, `music`) |
| `homed/model.py`      | Shared data shapes |
| `homed/config.py`     | Loads `home.toml` |
| `static/index.html`   | Single-page UI |
| `scripts/deploy.sh`   | Deploy to an Incus container |
| `tests/`              | pytest suite |

## Configuration

Runtime config is a TOML file (default `home.toml`). **This repo ships only
`home.toml.example`** — backend URLs, the public domain, the broker URL, and the
allow-list are all supplied per deployment and never hardcoded. Copy the example
and fill it in:

```bash
cp home.toml.example home.toml
$EDITOR home.toml
```

Key `[web]` fields:

- `remote_domain` — public hostname reached through a Cloudflare tunnel; empty = LAN-only.
- `local_hosts` — LAN-only vhosts that count as "on the home network" for self-approve.
- `broker_url` — the Google-OAuth broker that verifies identity and returns the JWT.
- `allowed_emails` — the access allow-list.

> Real per-deployment config (actual domain, IPs, allow-list, secrets) lives in
> a **separate private instance repo**, not here. This repo is the public code.

### Self-approve on the home network

A user who has authenticated but isn't yet on `allowed_emails` can approve
themselves **if they are physically on the home LAN**. Because public traffic
egresses through the Cloudflare tunnel (which hides the client's LAN IP), a
LAN-only vhost listed in `local_hosts` is what lets the gateway recognize an
on-network request and offer the self-approve action.

## Running

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m homed --config home.toml   # serves on [web].bind (default 0.0.0.0:8099)
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Deploy

```bash
./scripts/deploy.sh home   # create/refresh an Incus container and restart the service
```

## Design docs

Longer-form design notes and implementation plans live under `docs/` (and
`docs/superpowers/`). They describe the architecture and the migration to a
single front door.
