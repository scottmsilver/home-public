# Home Gateway React UI Implementation Plan (Plan 2 of 2)

> **For agentic workers:** This plan produces one artifact, `static/index.html`, consuming the live Plan 1 backend. Verification is manual (load it in a browser against the running gateway) since it's a CDN-React single-file SPA with no JS build/test harness. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A single-file React + Tailwind SPA served by the gateway at `/`, in the fans dark/iOS visual language, with a unified **Home** tab plus **Gate / Pool / Fans** tabs, live-updating over SSE.

**Architecture:** `static/index.html` loads React 18 + ReactDOM + Babel + Tailwind from CDNs (no build step, mirroring `unifi-gate-public/templates/index.html`). It renders entirely from the gateway's normalized control model. State arrives via an `EventSource('/api/stream')` (initial snapshot + live pushes); commands go via `POST /api/command`. The UI is generic over control `kind`, so it needs no per-domain code.

**Tech Stack:** React 18 (CDN, `htm` or Babel JSX), Tailwind CDN, EventSource (SSE), fetch.

---

## The backend contract (already live on :8099)

- `GET /api/state` → `{"controls": [Control, ...]}` — every control, all domains.
- `GET /api/home` → `{"controls": [...]}` — only the controls configured for the Home card, in order.
- `POST /api/command` `{"domain","id","payload"}` → `{"ok":true}` (400 on unknown).
- `GET /api/stream` → SSE; each event is `data: {"controls":[...]}` (full state snapshot). First event is the current snapshot.
- `GET /api/auth/me` → `{"email":..., "authRequired":bool}` (LAN: `authRequired:false`).

**Control shape:**
```
{ domain: "gate"|"pool"|"fans", id, name,
  kind: "toggle"|"momentary"|"slider"|"speed"|"readout",
  on: bool|null, value: number|null, range: [min,max]|null,
  status: string|null, online: bool }
```

**Command payload conventions (must match the adapters):**
- `toggle`  → `{"on": bool}`
- `momentary` (gate) → `{"action": "unlock"}` (Home); Gate tab may also send `{"action":"hold_today","end_time":"HH:MM"}` / `hold_forever` / `stop`.
- `slider`  → `{"value": number}` (and `{"on": true}` is implied by value; sending `{"on":false}` turns off)
- `speed`   → `{"value": number}` to set speed, `{"on": bool}` to toggle.

---

## Visual language (match the fans Home tab)

- Dark theme: page `#000`, cards `#1c1c1e`, secondary text `#8e8e93`, dividers `#2c2c2e`, radius 22px cards / 11px segmented control. System font stack. Max content width ~420px, centered, mobile-first, safe-area padding.
- Accent colors: fans cyan `#0a84ff`, lights amber `#ffd60a`, pool/spa orange `#ff9f0a`, online/locked green `#30d158`, offline amber badge.
- Header: iOS segmented control (Home · Gate · Pool · Fans) + a connection dot (green when SSE open, red when reconnecting).
- Control rows: 38px circular icon (glows/accent-ringed when on), name + sub-status, right-aligned control affordance.

---

## Task 1: Scaffold the SPA shell + tabs + SSE

**Files:** Create `static/index.html`

- [ ] **Step 1: Create `static/index.html`** with:
  - CDN script tags: React 18, ReactDOM 18, Babel standalone, Tailwind. (Use Babel `type="text/babel"` for JSX, matching unifi-gate.)
  - A root `<div id="root">` and dark base styles (inline `<style>` for the theme tokens above; Tailwind for layout).
  - A top-level `App` component holding `state` (the `{controls:[]}` object), `tab` (default `"home"`), and `connected` (bool).
  - `useEffect` that (a) fetches `/api/state` once for an instant first paint, (b) opens `EventSource('/api/stream')`, sets `connected=true` on open, replaces `state` on each `message`, and on `error` sets `connected=false` and lets EventSource auto-reconnect.
  - A header with the segmented tab control (Home · Gate · Pool · Fans) and the connection dot.
  - A `cards` area that renders per `tab`.
  - A `command(domain, id, payload)` helper that `POST`s `/api/command` and optimistically does nothing (SSE will push the new state); on failure, log.

- [ ] **Step 2: Verify shell loads** — restart/keep the gateway running, open `http://localhost:8099/`, confirm the page renders the header + tabs + connection dot turns green (SSE connected). (Manual / via /browse.)

- [ ] **Step 3: Commit** `static/index.html`.

---

## Task 2: Control renderers (generic by kind)

**Files:** Modify `static/index.html`

- [ ] **Step 1: Implement a `<ControlRow control />` component** that switches on `control.kind`:
  - `toggle` → icon button + name + `status`; tapping sends `{on: !control.on}`. Icon glows when `on`.
  - `momentary` → name + `status` + a pill button (label "Unlock" for gate) that sends `{action:"unlock"}`. Shows a brief "…" busy state.
  - `slider` → name + an `<input type=range min max>` (from `control.range`) bound to `value`; on change (debounced ~200ms) send `{value:n}`; a small on/off tap target toggles `{on:!on}`. Show `value`+unit.
  - `speed` → name + a row of speed pills (1..range[max]); tapping pill n sends `{value:n}`; an on/off tap toggles. Highlight the active speed; show "Mixed" when `value===null && on`.
  - `readout` → name + `value`/`status`, no control.
  - Offline: dim the row and show an "offline" badge when `!control.online`.

- [ ] **Step 2: Verify** each kind renders and actuates against the live gateway (toggle the spa, unlock a door momentarily-safe?, move a fan speed). Confirm SSE pushes the updated state and the row reflects it. (Manual.)

- [ ] **Step 3: Commit.**

---

## Task 3: Home tab (unified card, layout A) + per-domain tabs

**Files:** Modify `static/index.html`

- [ ] **Step 1: Home tab** — fetch the Home subset by filtering: render from a separate `GET /api/home` call (kept in sync by re-fetching on each SSE push, or by filtering the SSE `state` to the home ids). Simplest: keep a `homeIds` set from one `/api/home` fetch at load, then render those controls from the live `state`, grouped by domain with `GATE` / `POOL` / `FANS` divider labels — one unified card (layout A).

- [ ] **Step 2: Per-domain tabs** — Gate/Pool/Fans each render `state.controls.filter(c => c.domain === tab)` as `<ControlRow>`s in a card titled by domain. The Gate tab additionally surfaces hold actions (Until time / Forever / Stop) per door via a small action sheet that sends the `hold_*`/`stop` actions.

- [ ] **Step 3: Verify** the Home card shows the configured rows with dividers and the per-domain tabs show the fuller set, all live. (Manual / via /browse, screenshot.)

- [ ] **Step 4: Commit.**

---

## Task 4: QA pass + polish

- [ ] **Step 1:** Load via the `/browse` skill against `http://localhost:8099`. Walk every tab; confirm live data, actuation, SSE updates, reconnect behavior (kill+restart gateway → dot goes red then green). Screenshot Home + one domain tab.
- [ ] **Step 2:** Fix any rendering/interaction bugs found. Tighten spacing/affordances to match the fans aesthetic.
- [ ] **Step 3:** Final commit.

---

## Notes / scope
- No JS unit-test harness (CDN single-file). Verification is manual via a real browser against the live gateway — this is the established pattern for these home apps (unifi-gate, fans).
- Auth: on LAN, `/api/auth/me` returns `authRequired:false`, so the SPA shows no login. The remote sign-in flow (redirect to `/api/auth/login`) is wired in the backend; the SPA only needs to redirect there if a `401 {authRequired:true}` is ever received.
- Security: the gateway already blocks static path traversal; the SPA loads only same-origin `/api/*`. No secrets in the client.
