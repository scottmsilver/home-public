# Home Automation Architecture Consolidation

**Status:** Design — approved direction, not yet implemented
**Date:** 2026-06-18
**Goal (verbatim from owner):** clean logins, single app, reusable patterns.

## Where we are today

Five services, three languages, two auth systems, five frontends.

| Piece | Lang | Devices via | Clients via | Remote auth | Own UI? | Public domain |
|---|---|---|---|---|---|---|
| **home** (gateway) | Python/Flask | WS (pentair), WS (fans), HTTP poll (gate) | SSE + REST | silver-oauth broker → JWT | yes (canonical) | home.oursilverfamily.com |
| **pentair** | Rust/Axum | Pentair TCP protocol | WebSocket + REST | Firebase (mobile) | yes | pool.oursilverfamily.com |
| **unifi-gate** | Python/Flask | UniFi Access/Protect HTTP | WS-ish + REST | Cloudflare Worker + Firebase + KV | yes | gate.oursilverfamily.com |
| **fans** | Python/aiohttp | Modern Forms HTTP + Leviton cloud | WebSocket + REST | silver-oauth broker → JWT | yes | patio.oursilverfamily.com |
| **CF Worker** | JS | — | — | the gate/pool auth gate | — | — |

**The mishmash, named:**
1. Frontend duplicated 5× (each app ships a React-CDN+Babel single-file SPA; home re-creates pool/gate/patio a 2nd time as tabs).
2. Two competing auth systems — silver-oauth (home, fans) vs Firebase+Cloudflare-Worker+KV (gate, pentair-mobile).
3. Overlapping public front doors — home aggregates everything, but gate./pool./patio. still exist independently.
4. Three realtime patterns — backends push WS, home converts to SSE for browsers, gate is HTTP-polled.
5. Three languages (Python ×3, Rust ×1, JS Worker). **Accepted, not fixed** — pentair stays Rust.

## Target architecture: "One front door"

**The keystone move: backends stop being publicly reachable.** Only `home` is exposed
to the internet; it reaches pentair/fans/gate over the LAN (the Incus proxy devices that
already exist). Everything else follows from that.

### Decisions (approved)

- **Auth — silver-oauth at home is the single login.** home is the only public entry and
  terminates auth via the silver-oauth broker → JWT session cookie + an allow-list.
  Cloudflare Tunnel becomes transport-only. Because backends are no longer public, they
  need no auth of their own (LAN-open is safe). **Delete:** Firebase from pentair +
  unifi-gate; the `unifi-gate-auth` Cloudflare Worker + its KV namespaces
  (`APPROVED_USERS`, `POOL_APPROVED_USERS`); fans' and gate's remote-auth paths; the
  `X-Verified-User` handoff. One identity system, one allow-list, one login screen.
  Tradeoff accepted: auth runs at the origin (home), not the edge.

- **Self-approve on-network (REQUIRED).** Today a user physically on the network can
  approve themselves (gate's flow: LAN request → HMAC-signed URL → Worker → KV). This
  MUST be preserved when we delete the Worker. home replaces it with:
  - **Mutable allow-list.** `allowed_emails` in config becomes the *seed*; runtime
    approvals persist to `~/.home/approved_emails.json`. `email_allowed()` checks the
    union. (Today `auth.py:30` is a frozen set — this is the core change.)
  - **LAN-gated grant flow (DECIDED: broker-verified grant ticket).** Mirrors gate's
    portable proof-of-LAN:
    1. On-network device taps "enable remote access" → `POST /api/auth/grant-start`.
       home confirms the request is from a trusted-subnet source IP, then issues a
       short-lived, single-use **HMAC grant ticket** (signed with the session secret,
       ~10 min, carries a `jti` to block replay).
    2. User completes a normal silver-oauth broker login carrying the ticket
       (`/api/auth/login?grant=<ticket>`; the ticket rides in the state cookie through
       the broker round-trip).
    3. At `/api/auth/callback` home verifies **both** the broker handoff (real,
       verified email) **and** the ticket (valid, unexpired, unused) → writes the
       verified email to `~/.home/approved_emails.json` → mints the session.
    Binds real identity + proof a LAN device authorized it; nothing is self-asserted.
  - **Trusted-network definition (DECIDED + hardened: positive local-origin check).**
    Self-approve is offered only when `gate.is_trusted_local(Host)` is true: an
    **IP-literal, `localhost`, or an explicitly allow-listed `local_hosts` name** — plus
    an `Origin` cross-check on the POST. NOT merely "`is_remote == false`": codex flagged
    (P1) that bare not-remote is **DNS-rebinding-exploitable** — a malicious page in a LAN
    browser could rebind to the gateway and mint a grant ticket. An IP-literal/localhost
    Host can't be forged by rebinding (the attacker page always sends its own hostname,
    never a victim IP), so the running deployment (reached at `192.168.1.15:8099`) is
    trusted with zero config while rebinding is rejected. The Cloudflare tunnel always
    sets the remote Host, so remote traffic is excluded regardless.
    - **Why not a literal subnet check:** home runs in an Incus container behind an
      `http8099` proxy device (`listen 0.0.0.0:8099` → `connect 127.0.0.1:8099`) that
      NATs the source address. The container sees the proxy IP, not the real client IP,
      so a `192.168.1.0/24` source-IP check is not implementable without added plumbing.
    - **Security predicate:** this is airtight **iff** `silver-guest` is isolated so guest
      devices cannot reach the LAN `:8099` (the default on UniFi guest networks). If guest
      isolation were ever disabled, a guest could self-approve.
    - **Documented hardening path (not now):** put a host Caddy in front of `:8099` that
      sets a trusted `X-Forwarded-For`; home trusts it only from the local proxy and then
      checks the real client IP against a configured trusted subnet. Adds one infra
      component; defer unless guest isolation proves insufficient.

- **Frontend — stay no-build, modularize.** Keep vendored React + in-browser Babel (no
  toolchain). Break the ~111KB `home/static/index.html` into shared modules. Since the
  other four UIs are being deleted, "reuse" becomes "one well-factored SPA," not a
  cross-repo component library.

- **Backends — headless drivers behind home.** Strip UI + remote auth from
  pentair/fans/unifi-gate; they expose only their JSON API + WS on the LAN. Retire
  gate./pool./patio. (301 → home tabs). The faithful Pool/Gate/Patio tabs we already
  built become THE UI.

### Resulting shape

```
                       Internet
                          │
              Cloudflare Tunnel (transport only)
                          │
                 home.oursilverfamily.com
        ┌──────────────── home (Flask) ────────────────┐
        │  • silver-oauth (the ONE login)               │
        │  • single SPA (Home/Pool/Gate/Patio tabs)     │
        │  • SSE to browser; one generic adapter        │
        └───────────────────┬───────────────────────────┘
                  LAN-only (no public exposure, no per-app auth)
        ┌───────────┬───────────────┬──────────────┐
     pentair       fans          unifi-gate     (future devices)
     (Rust)        (aiohttp)      (Flask)
   state/cmd/ws  state/cmd/ws   state/cmd/ws   ← common device-driver contract
```

### Reusable patterns

**Frontend (no-build, modular).** Split `home/static/index.html` into:
- theme tokens / design-system CSS (navy/teal)
- `primitives` — Card, ControlRow, Slider, Segmented, ColorPicker/ModesRow, Sheet, pills
- `useStream` — the SSE + auto-reconnect + optimistic-overlay hook (already written; extract it)
- per-tab modules — Home, Pool, Gate, Patio
- still vendored React + in-browser Babel, loaded as separate `<script type="text/babel">`. No toolchain.

**Backend (device-driver contract).** Every backend implements:
- `GET /state` → common envelope `{domain, controls:[{id, kind, ...}], raw:{…}}`
- `POST /command` → `{id, payload}`
- `GET /ws` → push the same envelope on change
- (optional) `GET /raw` passthrough retained for the faithful tabs

home collapses its three bespoke adapters → **one generic adapter + a per-domain
normalization map**. gate moves from home-side polling to push (UniFi Access already has
an event stream via `AccessEventStream`), or stays polled behind the same contract.

## Migration plan (value-ordered, each phase ships independently)

### Phase 1 — One front door + one login  *(the "clean logins" goal; highest value)*
- Point all remote access at home; bind pentair/fans/gate to LAN only.
- Retire the CF Worker + Firebase + per-app remote auth; replace gate's "LAN user requests
  approval" flow with home's `allowed_emails`.
- 301 `gate./pool./patio.` → `home.oursilverfamily.com` tabs; remove their tunnels/Worker routes.
- **Resolves tensions #2 and #3.**
- **Risk:** remote-login regression; make sure every current approved user is in
  `allowed_emails` before cutting over. Test remote sign-in end-to-end.

### Phase 2 — Single app  *(the "single app" goal)*
- Strip `static/`/templates UI from pentair, fans, unifi-gate.
- home SPA is canonical.
- **Risk:** lose standalone fallback. Mitigation: confirm the faithful tabs cover 100% of
  each app's controls before deleting; keep old UIs in git history (and optionally behind
  a `--with-ui` flag for emergencies).

### Phase 3 — Reusable frontend  *(modularize the home SPA)*
- Split the single file into theme + primitives + hooks + tabs. Pure refactor, no behavior
  change. Covered by browse smoke tests at 320px + desktop.

### Phase 4 — Reusable backend contract
- Define the contract; refactor home to one generic adapter + mappers; add gate push.
- Conform each backend (pentair already close; fans close; gate adds `/state`+`/command`
  aliases + event-stream push). One backend at a time, behind the existing adapter, so
  home keeps working throughout.

## Out of scope / explicitly not doing
- Rewriting pentair (Rust) for language uniformity. It works and already fits the contract.
- A frontend build toolchain. No-build (vendored CDN) stays.
- Edge auth enforcement (CF Worker). home enforces; revisit only if we want defense-in-depth.
