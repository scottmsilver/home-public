# Phase 1: One Front Door + One Login — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `home` the sole public entry for the house, consolidate all remote auth onto silver-oauth at `home` (with an on-network self-approve flow), and retire the per-app public endpoints + the Cloudflare Worker + Firebase — without ever breaking remote access mid-migration.

**Architecture:** Part 1A adds code to `home` (mutable allow-list + on-network broker-verified grant-ticket self-approve) and ships it *alongside* the existing auth, changing nothing externally. Part 1B is the cutover: migrate the existing approved users into home, verify, then flip topology (301 the old domains, stop their tunnels, bind backends LAN-only, delete the Worker/Firebase). Every cutover step has a one-command rollback.

**Tech Stack:** Python 3 / Flask (home), PyJWT (HS256 tickets + sessions), Incus (containers + proxy devices), cloudflared (named tunnels via systemd), Cloudflare Rules (edge 301s), wrangler (Worker teardown). Source design: `docs/2026-06-18-architecture-consolidation.md`.

**Decisions already made (do not relitigate):**
- One login = silver-oauth at home; delete Firebase + CF Worker + KV + per-app remote auth.
- Backends become headless, LAN-only.
- Self-approve trust = **Host-is-LAN** (`gate.is_remote(Host) == false`), relying on UniFi guest-network isolation. NOT a source-IP subnet check (the Incus `http8099` proxy NATs the client IP, so home can't see it). Hardening path (host Caddy + XFF) is documented but out of scope.
- Grant mechanism = broker-verified single-use HMAC ticket.

**Current infra facts (verified 2026-06-18):**
- Tunnels (systemd `cloudflared-*.service`, configs in `~/.cloudflared/`):
  - `cloudflared-home` → tunnel `cc83dd1e-e041-45ee-aae3-b9dd2b543c8e` → `home.oursilverfamily.com` → `http://10.182.70.9:8099` (home container)
  - `cloudflared-gate` → tunnel `ad03e67d-7d7e-494d-8b41-8ac6ec0e996c` → `gate.oursilverfamily.com` → `http://10.182.70.240:8000` (unifi-gate container)
  - `cloudflared-pool` → tunnel `08e11807-595a-4fe6-8535-013bae7aaa52` → `pool.oursilverfamily.com` → `http://localhost:8080` (pentair on host)
  - `config-patio.yml` → tunnel `c51e57a2-5386-408c-a904-16f38ca13c96` → `patio.oursilverfamily.com` → `http://localhost:8095` (fans on host) — note: no running `cloudflared-patio.service` was found; confirm at execution.
- unifi-gate Cloudflare Worker `unifi-gate-auth` enforces Firebase+KV auth for `gate.` and `pool.` via routes in `unifi-gate-instance/config/worker/wrangler.toml`; KV namespaces `APPROVED_USERS` (id `ff27392d0ce04cbc8de3407745c2a2cf`) and `POOL_APPROVED_USERS` (id `9d22aca240264e838ebdceef632d2326`).
- home container `home` IP `10.182.70.9`; inbound via Incus device `http8099` (`listen 0.0.0.0:8099` → `connect 127.0.0.1:8099`).

---

## File Structure

**Part 1A — home repo (`/home/ssilver/development/home`):**
- Modify `homed/auth.py` — `AuthGate`: mutable approved-email store (`approve_email`, `_load_approved`, `_persist_approved`, union in `email_allowed`); grant tickets (`make_grant_ticket`, `consume_grant_ticket`); relax `fully_configured`; new cookie/const names.
- Modify `homed/server.py` — new `POST /api/auth/grant-start`; `GET /api/auth/login` carries `?grant=`; `GET /api/auth/callback` consumes the grant and approves the verified email; expose `lan` flag in `/api/auth/me`.
- Modify `static/index.html` — on-LAN "Enable remote access" affordance that calls grant-start and opens the broker login URL.
- Tests: `tests/test_auth.py` (store + tickets), `tests/test_server.py` (endpoints).

**Part 1A — home-instance repo (`/home/ssilver/development/home-instance`):**
- Modify `config/home.container.toml` — seed `allowed_emails` with the migrated user list (Task 8).

**Part 1B — ops (no single repo; cloudflared/incus/wrangler/Cloudflare dashboard):**
- `~/.cloudflared/config-gate.yml`, `config-pool.yml`, `config-patio.yml` + their systemd units (stop/disable).
- `unifi-gate-instance/config/worker/wrangler.toml` (remove routes; teardown Worker + KV).
- pentair / unifi-gate / fans configs (drop Firebase / remote-auth) — cleanup tasks.

---

# PART 1A — home code (ships alongside existing auth; no external change)

### Task 1: Mutable approved-email store

**Files:**
- Modify: `homed/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_auth.py`:

```python
import json
from pathlib import Path

from homed.auth import AuthGate


def _gate(tmp_path, allowed=()):
    return AuthGate(
        {"remote_domain": "home.example.com", "broker_url": "https://b", "allowed_emails": list(allowed)},
        state_dir=tmp_path,
    )


def test_approve_email_persists_and_unions_with_config(tmp_path):
    g = _gate(tmp_path, allowed=["seed@x.com"])
    assert g.email_allowed("seed@x.com")          # from config
    assert not g.email_allowed("new@x.com")
    g.approve_email("New@x.com")                   # case-insensitive
    assert g.email_allowed("new@x.com")
    # persisted to disk and reloaded by a fresh gate
    g2 = _gate(tmp_path, allowed=["seed@x.com"])
    assert g2.email_allowed("new@x.com")
    assert json.loads((Path(tmp_path) / "approved_emails.json").read_text()) == ["new@x.com"]


def test_load_approved_tolerates_missing_and_corrupt(tmp_path):
    (Path(tmp_path) / "approved_emails.json").write_text("{ not json")
    g = _gate(tmp_path)
    assert g.email_allowed("anyone@x.com") is False   # corrupt file → empty set, no crash
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/ssilver/development/home && . .venv/bin/activate && pytest tests/test_auth.py::test_approve_email_persists_and_unions_with_config -v`
Expected: FAIL (`AttributeError: 'AuthGate' object has no attribute 'approve_email'`).

- [ ] **Step 3: Implement in `homed/auth.py`**

In `AuthGate.__init__`, after `self.session_secret = self._session_secret()` add:

```python
        self.approved_path = self.state_dir / "approved_emails.json"
        self._dynamic = self._load_approved()
        self._used_grant_jti = set()
```

Add these methods to `AuthGate` (place them next to `email_allowed`):

```python
    def _load_approved(self):
        import json

        try:
            data = json.loads(self.approved_path.read_text())
            return {str(e).strip().lower() for e in data if str(e).strip()}
        except (FileNotFoundError, ValueError, OSError):
            return set()

    def _persist_approved(self):
        import json
        import os

        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.approved_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(sorted(self._dynamic)))
            tmp.chmod(0o600)
            os.replace(tmp, self.approved_path)  # atomic
        except OSError:
            pass

    def approve_email(self, email):
        e = (email or "").strip().lower()
        if not e or e in self._dynamic:
            return
        self._dynamic.add(e)
        self._persist_approved()
```

Replace the existing `email_allowed` with the union form:

```python
    def email_allowed(self, email):
        return bool(email) and email.lower() in (self.allowed | self._dynamic)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v -k "approve_email or load_approved"`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add homed/auth.py tests/test_auth.py
git commit -m "feat(auth): mutable approved-email store (config seed + persisted runtime approvals)"
```

---

### Task 2: Broker-verified single-use grant tickets

**Files:**
- Modify: `homed/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_auth.py`:

```python
def test_grant_ticket_roundtrip_single_use(tmp_path):
    g = _gate(tmp_path)
    t = g.make_grant_ticket()
    assert g.consume_grant_ticket(t) is True     # first use ok
    assert g.consume_grant_ticket(t) is False    # replay rejected (single-use)


def test_grant_ticket_rejects_garbage_and_wrong_type(tmp_path):
    import time

    import jwt

    g = _gate(tmp_path)
    assert g.consume_grant_ticket("not-a-jwt") is False
    # a session token (typ absent) must not be accepted as a grant
    not_grant = jwt.encode({"email": "x@x.com", "exp": int(time.time()) + 60}, g.session_secret, algorithm="HS256")
    assert g.consume_grant_ticket(not_grant) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_auth.py::test_grant_ticket_roundtrip_single_use -v`
Expected: FAIL (`AttributeError: ... 'make_grant_ticket'`).

- [ ] **Step 3: Implement in `homed/auth.py`**

Add a module constant near the top (after `SESSION_TTL = 30 * 86400`):

```python
GRANT_TTL = 600  # on-network self-approve ticket lifetime (seconds)
```

Add to `AuthGate` (next to `make_session`):

```python
    def make_grant_ticket(self):
        now = int(time.time())
        return jwt.encode(
            {"typ": "grant", "jti": secrets.token_hex(8), "iat": now, "exp": now + GRANT_TTL},
            self.session_secret,
            algorithm="HS256",
        )

    def consume_grant_ticket(self, token):
        try:
            claims = jwt.decode(token, self.session_secret, algorithms=["HS256"], options={"require": ["exp"]})
        except jwt.PyJWTError:
            return False
        if claims.get("typ") != "grant":
            return False
        jti = claims.get("jti")
        if not jti or jti in self._used_grant_jti:
            return False
        self._used_grant_jti.add(jti)
        return True
```

(`secrets` and `time` and `jwt` are already imported at the top of `auth.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v -k grant`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add homed/auth.py tests/test_auth.py
git commit -m "feat(auth): single-use HMAC grant tickets for on-network self-approve"
```

---

### Task 3: Relax `fully_configured` so an empty allow-list can bootstrap via grant

**Files:**
- Modify: `homed/auth.py`
- Test: `tests/test_auth.py`

**Why:** Today `fully_configured` requires a non-empty `allowed_emails`, so a fresh deployment 503s before anyone can self-approve. After this change, valid secrets are enough; an empty allow-list just means "no users yet — use a LAN grant to add the first."

- [ ] **Step 1: Write the failing test**

```python
def test_fully_configured_does_not_require_allowlist(tmp_path):
    g = _gate(tmp_path, allowed=[])           # secrets present (auto-generated), no emails
    g.handoff_secret = "h"                     # simulate a configured handoff secret
    assert g.fully_configured is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_auth.py::test_fully_configured_does_not_require_allowlist -v`
Expected: FAIL (`assert False is True`) — current code requires `self.allowed`.

- [ ] **Step 3: Implement**

Replace the `fully_configured` property in `homed/auth.py`:

```python
    @property
    def fully_configured(self):
        # Secrets are what make remote auth *usable*. An empty allow-list is a
        # valid "no users approved yet" state — the on-network grant flow adds
        # the first user — so it must NOT gate fully_configured.
        return bool(self.handoff_secret and self.session_secret)
```

- [ ] **Step 4: Run the whole auth suite**

Run: `pytest tests/test_auth.py -v`
Expected: PASS. If a pre-existing test asserted `fully_configured is False` for an empty allow-list, update it to reflect the new contract (an empty allow-list is now configured-but-userless) and note it in the commit.

- [ ] **Step 5: Commit**

```bash
git add homed/auth.py tests/test_auth.py
git commit -m "feat(auth): fully_configured no longer requires a non-empty allow-list (grant bootstrap)"
```

---

### Task 4: `POST /api/auth/grant-start` (LAN-only ticket issue)

**Files:**
- Modify: `homed/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py` (reuse the existing `make_client(web=...)` helper that builds a real `AuthGate`):

```python
def test_grant_start_lan_issues_ticket_remote_forbidden():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    # LAN host (not the remote domain) → ticket issued
    r = client.post("/api/auth/grant-start", headers={"Host": "192.168.1.15:8099"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ticket"]
    assert body["login_url"].startswith("https://home.example.com/api/auth/login?grant=")
    # Remote host → refused (only on-network may self-approve)
    r2 = client.post("/api/auth/grant-start", headers={"Host": "home.example.com"})
    assert r2.status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_server.py::test_grant_start_lan_issues_ticket_remote_forbidden -v`
Expected: FAIL (404 — route doesn't exist).

- [ ] **Step 3: Implement in `homed/server.py`**

Add this route (place it just before the `@app.get("/api/auth/login")` route). `urlencode` is already imported at the top of `server.py`.

```python
    @app.post("/api/auth/grant-start")
    def auth_grant_start():
        # On-network only: the Cloudflare tunnel always sets the remote Host, so
        # is_remote==False proves the request came in on the LAN address.
        if gate.is_remote(request.headers.get("Host", "")):
            return jsonify({"error": "self-approve is only available on the local network"}), 403
        if not gate.remote_domain or not gate.fully_configured:
            return jsonify({"error": "remote access not configured"}), 503
        ticket = gate.make_grant_ticket()
        login_url = f"https://{gate.remote_domain}/api/auth/login?{urlencode({'grant': ticket})}"
        return jsonify({"ticket": ticket, "login_url": login_url})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_server.py::test_grant_start_lan_issues_ticket_remote_forbidden -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add homed/server.py tests/test_server.py
git commit -m "feat(auth): POST /api/auth/grant-start issues a LAN-only self-approve ticket"
```

---

### Task 5: `login` carries the grant; `callback` consumes it and approves the verified email

**Files:**
- Modify: `homed/auth.py` (add `GRANT_COOKIE` const), `homed/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

This test drives the full grant path: issue a ticket on LAN, start login with `?grant=`, then hit callback with a valid handoff for an email that is NOT pre-listed — it must be approved *because* of the grant, and a second callback with the same grant must be rejected.

```python
import time

import jwt as _jwt


def test_grant_login_callback_approves_unlisted_email():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": [], "broker_url": "https://b"}
    )
    # The real AuthGate is reachable for crafting a handoff token + reading state.
    from homed.server import create_app  # noqa: F401  (gate lives on the app via closure)

    # 1) LAN issues a grant ticket.
    g = client.post("/api/auth/grant-start", headers={"Host": "192.168.1.15:8099"}).get_json()
    ticket = g["ticket"]

    # 2) Start login carrying the grant (remote host). Capture the state cookie.
    client.get(f"/api/auth/login?grant={ticket}", headers={"Host": "home.example.com"})
    state = next(c.value for c in client.cookie_jar if c.name == "home_oauth_state")

    # 3) Forge a valid broker handoff for an unlisted email, signed with the gate's handoff secret.
    #    The handoff secret is auto-read from state_dir; pull it off the app's gate.
    gate = client.application.view_functions["auth_login"].__globals__  # not reliable across versions
    # Instead, sign using the same secret the gate uses: read it from the test client's gate.
```

> NOTE TO IMPLEMENTER: the test needs the gate's `handoff_secret`. The cleanest way is to have `make_client` return the gate. If `make_client` does not already expose it, extend the helper to `return app.test_client(), adapter, gate` (or add a `make_auth_client` that returns the gate) and use that here. Then finish the test:

```python
    # (continuing, with `gate` = the AuthGate the app was built with)
    handoff = _jwt.encode(
        {"email": "newuser@gmail.com", "exp": int(time.time()) + 60}, gate.handoff_secret, algorithm="HS256"
    )
    r = client.get(
        f"/api/auth/callback?state={state}&silver_oauth={handoff}", headers={"Host": "home.example.com"}
    )
    assert r.status_code == 302                       # success → redirect to /
    assert gate.email_allowed("newuser@gmail.com")    # approved via the grant
    # A session cookie was set
    assert any(c.name == "home_session" for c in client.cookie_jar)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_server.py::test_grant_login_callback_approves_unlisted_email -v`
Expected: FAIL (callback 403s "not allowed" because the grant is not yet consumed/approved, or the grant cookie is never set).

- [ ] **Step 3: Implement**

In `homed/auth.py`, add the cookie name next to the others:

```python
GRANT_COOKIE = "home_oauth_grant"
```

In `homed/server.py`, import it:

```python
from homed.auth import GRANT_COOKIE, HANDOFF_PARAM, PUBLIC_PATHS, SESSION_COOKIE, SESSION_TTL, STATE_COOKIE, AuthGate
```

Update `auth_login` to persist the grant alongside the state cookie. Replace its body with:

```python
    @app.get("/api/auth/login")
    def auth_login():
        if not gate.is_remote(request.headers.get("Host", "")):
            return "login only via remote domain", 403
        if not gate.fully_configured:
            return "auth not configured", 503
        import secrets as _s

        st = _s.token_urlsafe(24)
        cb = f"https://{gate.remote_domain}/api/auth/callback?{urlencode({'state': st})}"
        resp = make_response(redirect(f"{gate.broker_url}/start?{urlencode({'return_url': cb, 'scope': 'openid'})}"))
        resp.set_cookie(STATE_COOKIE, st, max_age=600, httponly=True, secure=_https(), samesite="Lax", path="/")
        grant = request.args.get("grant", "")
        if grant:
            resp.set_cookie(GRANT_COOKIE, grant, max_age=600, httponly=True, secure=_https(), samesite="Lax", path="/")
        return resp
```

Update `auth_callback` to consume the grant before the allow-list check. Replace its body with:

```python
    @app.get("/api/auth/callback")
    def auth_callback():
        import secrets as _s

        st = request.args.get("state", "")
        cookie_st = request.cookies.get(STATE_COOKIE, "")
        if not st or not cookie_st or not _s.compare_digest(st, cookie_st):
            return "invalid state", 400
        email = gate.verify_handoff(request.args.get(HANDOFF_PARAM, ""))
        if not email:
            resp = make_response("invalid handoff", 401)
            resp.delete_cookie(STATE_COOKIE, path="/")
            resp.delete_cookie(GRANT_COOKIE, path="/")
            return resp
        # On-network self-approve: a valid single-use grant ticket approves the
        # broker-VERIFIED email (identity proven by the handoff, network-trust
        # proven by the grant having been issued only to a LAN request).
        grant = request.cookies.get(GRANT_COOKIE, "")
        if grant and gate.consume_grant_ticket(grant):
            gate.approve_email(email)
        if not gate.email_allowed(email):
            resp = make_response(f"{email} not allowed", 403)
            resp.delete_cookie(STATE_COOKIE, path="/")
            resp.delete_cookie(GRANT_COOKIE, path="/")
            return resp
        resp = make_response(redirect("/"))
        resp.delete_cookie(STATE_COOKIE, path="/")
        resp.delete_cookie(GRANT_COOKIE, path="/")
        resp.set_cookie(
            SESSION_COOKIE,
            gate.make_session(email),
            max_age=SESSION_TTL,
            httponly=True,
            secure=_https(),
            samesite="Lax",
            path="/",
        )
        return resp
```

- [ ] **Step 4: Run the full server + auth suite**

Run: `pytest tests/test_server.py tests/test_auth.py -v`
Expected: PASS (including the new grant test and all pre-existing auth/callback tests).

- [ ] **Step 5: Commit**

```bash
git add homed/auth.py homed/server.py tests/test_server.py
git commit -m "feat(auth): callback consumes on-network grant to approve the broker-verified email"
```

---

### Task 6: Expose a `lan` flag on `/api/auth/me`

**Files:**
- Modify: `homed/server.py`
- Test: `tests/test_server.py`

**Why:** The SPA needs to know it's on the LAN (vs remote) to decide whether to show "Enable remote access".

- [ ] **Step 1: Write the failing test**

```python
def test_auth_me_reports_lan_flag():
    client, _ = make_client(
        web={"remote_domain": "home.example.com", "allowed_emails": ["you@gmail.com"], "broker_url": "https://b"}
    )
    lan = client.get("/api/auth/me", headers={"Host": "192.168.1.15:8099"}).get_json()
    assert lan["lan"] is True and lan["authRequired"] is False
    remote = client.get("/api/auth/me", headers={"Host": "home.example.com"})
    # remote, not signed in → 401, but still reports lan:false
    assert remote.status_code == 401 and remote.get_json()["lan"] is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_server.py::test_auth_me_reports_lan_flag -v`
Expected: FAIL (`KeyError: 'lan'`).

- [ ] **Step 3: Implement**

Replace `auth_me` in `homed/server.py`:

```python
    @app.get("/api/auth/me")
    def auth_me():
        remote = gate.is_remote(request.headers.get("Host", "")) and gate.fully_configured
        if not remote:
            return jsonify({"email": None, "authRequired": False, "lan": True})
        email = gate.current_user()
        if not email:
            return jsonify({"authRequired": True, "lan": False}), 401
        return jsonify({"email": email, "authRequired": True, "lan": False})
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_server.py -v -k "auth_me"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add homed/server.py tests/test_server.py
git commit -m "feat(auth): /api/auth/me reports a lan flag for the self-approve UI"
```

---

### Task 7: SPA "Enable remote access" affordance (on LAN only)

**Files:**
- Modify: `static/index.html`
- Test: manual via browse (no unit harness for the CDN SPA)

- [ ] **Step 1: Capture LAN state in the App**

In `App()` (where `/api/auth/me` is resolved, around the `apiFetch("/api/auth/me")` effect), add a state flag and set it from the response:

```javascript
  const [onLan, setOnLan] = useState(false);
```

In the `.then((d) => {...})` of the auth/me effect, after the existing redirect logic, add:

```javascript
        setOnLan(!!(d && d.lan));
```

- [ ] **Step 2: Add the grant action + UI**

Add this handler inside `App()` (next to `goodnight`):

```javascript
  const enableRemote = useCallback(() => {
    apiFetch("/api/auth/grant-start", { method: "POST" })
      .then((r) => r.json())
      .then((d) => {
        if (d && d.login_url) {
          // Same-device path: navigate to the broker login; after sign-in the
          // verified email is approved and this device gets a session.
          window.location.href = d.login_url;
        } else {
          alert("Remote access isn't configured on this gateway yet.");
        }
      })
      .catch(() => {});
  }, []);
```

In the header's `hdr-right` block (next to the moon button), render the action only on LAN:

```javascript
          {onLan ? (
            <button className="moon-btn" aria-label="Enable remote access"
                    title="Enable remote access for this account" onClick={enableRemote}>{KEY_SVG}</button>
          ) : null}
```

Add the `KEY_SVG` icon near the other SVG consts (e.g. by `MOON_SVG`):

```javascript
const KEY_SVG = (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3 21 2m-5 1 3 3m-6 0 3 3"/></svg>);
```

- [ ] **Step 3: Deploy to the container and smoke-test on LAN**

```bash
cd /home/ssilver/development/home && bash scripts/deploy.sh home
```

Then browse the LAN URL and confirm the key button shows; on the remote domain it must be hidden:

```bash
BR=~/.claude/skills/gstack/browse/dist/browse
$BR goto http://192.168.1.15:8099/ >/dev/null; sleep 5
$BR js "document.querySelector('[aria-label=\"Enable remote access\"]') ? 'KEY_SHOWN_ON_LAN' : 'MISSING'"
```

Expected: `KEY_SHOWN_ON_LAN`.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat(ui): on-network 'enable remote access' action wired to grant-start"
```

---

# PART 1B — cutover (sequenced; remote access never breaks)

> Ordering rule: **add the new path and prove it before removing the old one.** Do not stop a backend's tunnel or delete the Worker until home covers that domain and the user list is migrated.

### Task 8: Inventory + migrate the existing approved users into home

**Files:**
- Modify: `home-instance/config/home.container.toml`

- [ ] **Step 1: Dump the existing approved users from both KV namespaces**

```bash
cd /home/ssilver/development/unifi-gate-instance/config/worker
npx wrangler kv key list --namespace-id ff27392d0ce04cbc8de3407745c2a2cf | tee /tmp/gate_users.json
npx wrangler kv key list --namespace-id 9d22aca240264e838ebdceef632d2326 | tee /tmp/pool_users.json
```

Expected: JSON arrays of `{name: "<email>"}`. If `wrangler` prompts for auth, run `npx wrangler login` first.

- [ ] **Step 2: Merge the email list into home's seed allow-list**

Extract the emails and union them into `allowed_emails` in `home-instance/config/home.container.toml` (keep `scottmsilver@gmail.com`). Example result:

```toml
allowed_emails = ["scottmsilver@gmail.com", "person2@gmail.com", "person3@gmail.com"]
```

- [ ] **Step 3: Deploy home with the migrated list**

```bash
cd /home/ssilver/development/home && bash scripts/deploy.sh home
```

- [ ] **Step 4: Verify the list loaded**

```bash
incus exec home -- python3 -c "import tomllib; d=tomllib.load(open('/home/ssilver/home-instance/config/home.container.toml','rb')); print(d['web']['allowed_emails'])"
```

Expected: the full merged list. (Adjust the path if the container stores the config elsewhere; confirm via `scripts/deploy.sh`.)

- [ ] **Step 5: Commit the instance repo (ask owner first per repo policy)**

```bash
cd /home/ssilver/development/home-instance
git add config/home.container.toml
git commit -m "config: seed home allow-list with migrated gate+pool approved users"
```

---

### Task 9: Prove home's remote login + self-approve end-to-end (GATE — do not proceed if this fails)

- [ ] **Step 1: Remote login works for a migrated user**

From a remote network (or phone off-wifi), open `https://home.oursilverfamily.com`, complete silver-oauth, confirm the app loads and controls work. Confirm a non-listed email is rejected (403 "not allowed").

- [ ] **Step 2: On-network self-approve works**

On the home wifi, open `http://192.168.1.15:8099`, tap the key ("Enable remote access"), complete the broker login, and confirm:

```bash
incus exec home -- cat /home/ssilver/.home/approved_emails.json
```

Expected: the just-approved email is present. Then confirm that email now works from remote.

- [ ] **Step 3: STOP-CHECK**

Only continue to Task 10 if Steps 1–2 both pass. If not, fix home auth; the old gate/pool/patio paths are still fully live, so there is no outage.

---

### Task 10: 301 the old domains to home (edge redirect — additive, reversible)

**Files:** Cloudflare dashboard (Rules → Redirect Rules) for zone `oursilverfamily.com`.

- [ ] **Step 1: Create three Single Redirect Rules**

For each of `gate.oursilverfamily.com`, `pool.oursilverfamily.com`, `patio.oursilverfamily.com`:
- When incoming `Hostname equals <host>` → Static redirect to `https://home.oursilverfamily.com/` , status `301`, preserve query string off.

(Single Redirect Rules run at the edge before the tunnel, so they take precedence over the tunnel origin without touching cloudflared.)

- [ ] **Step 2: Verify**

```bash
for h in gate pool patio; do echo -n "$h -> "; curl -sI https://$h.oursilverfamily.com/ | awk 'tolower($1)=="location:"||$1=="HTTP/2"{print}'; done
```

Expected: each returns `301` with `location: https://home.oursilverfamily.com/`.

- [ ] **Rollback:** disable/delete the three redirect rules in the dashboard — traffic falls straight back through to the tunnels.

---

### Task 11: Stop the per-backend tunnels + bind backends LAN-only

> After Task 10 the public hostnames already 301 to home, so the gate/pool/patio tunnels serve nothing. Now disable them and ensure the backends are not otherwise public.

- [ ] **Step 1: Stop + disable the redundant tunnels**

```bash
sudo systemctl disable --now cloudflared-gate.service cloudflared-pool.service 2>&1
# patio: confirm the unit name first (no running service was found at planning time)
systemctl list-units --all 'cloudflared*'
# if a patio unit exists:
# sudo systemctl disable --now cloudflared-patio.service
```

- [ ] **Step 2: Confirm home still reaches the backends over the LAN (via the Incus proxy devices)**

```bash
H=http://192.168.1.15:8099
curl -s $H/api/raw/pool | head -c 80; echo
curl -s $H/api/raw/fans | head -c 80; echo
curl -s $H/api/raw/gate | head -c 80; echo
```

Expected: real JSON from each (the `poolproxy`/`fansproxy`/`gateproxy` Incus devices are unaffected by stopping the public tunnels).

- [ ] **Step 3: Confirm the backends are no longer publicly reachable**

```bash
for h in gate pool patio; do echo -n "$h direct origin -> "; curl -sI https://$h.oursilverfamily.com/api/ | head -1; done
```

Expected: 301 (from Task 10), NOT a backend response.

- [ ] **Rollback:** `sudo systemctl enable --now cloudflared-gate.service cloudflared-pool.service` (and patio) restores the old tunnels.

---

### Task 12: Retire the unifi-gate Cloudflare Worker + KV

**Files:**
- Modify: `unifi-gate-instance/config/worker/wrangler.toml`

- [ ] **Step 1: Remove the gate/pool routes from the Worker**

In `unifi-gate-instance/config/worker/wrangler.toml`, delete the `[[routes]]` blocks for `gate.oursilverfamily.com/*`, `gate-dev.oursilverfamily.com/*`, and `pool.oursilverfamily.com/*`. (The `home.oursilverfamily.com/*` route was already removed in a prior session.)

- [ ] **Step 2: Deploy the Worker without those routes (then delete it)**

```bash
cd /home/ssilver/development/unifi-gate-instance/config/worker
npx wrangler deploy            # publishes with routes removed (Worker now serves nothing)
# Confirm nothing depends on it, then tear down:
npx wrangler delete            # removes the unifi-gate-auth Worker
```

- [ ] **Step 3: Delete the KV namespaces (after confirming Task 8 migrated the users)**

```bash
npx wrangler kv namespace delete --namespace-id ff27392d0ce04cbc8de3407745c2a2cf   # APPROVED_USERS
npx wrangler kv namespace delete --namespace-id 9d22aca240264e838ebdceef632d2326   # POOL_APPROVED_USERS
```

- [ ] **Step 4: Verify**

```bash
curl -sI https://gate.oursilverfamily.com/ | head -1   # still 301 to home (edge rule), Worker gone
```

Expected: `HTTP/2 301`.

- [ ] **Step 5: Commit**

```bash
cd /home/ssilver/development/unifi-gate-instance
git add config/worker/wrangler.toml
git commit -m "infra: retire unifi-gate-auth Worker routes (home is the single front door)"
```

- [ ] **Rollback:** `git revert` the wrangler.toml change + `npx wrangler deploy` re-publishes the Worker routes. (KV deletion is irreversible — only do Step 3 once Task 9 has proven home works and Task 8 migrated users.)

---

### Task 13: Strip per-app remote auth + Firebase from the backends

> The backends are now LAN-only (Task 11) and never see public traffic, so their own remote-auth is dead code. Removing it eliminates the second auth system. Do ONE backend at a time; after each, re-run Task 11 Step 2 to confirm home still reads it.

- [ ] **Step 1: fans — remove the silver-oauth remote path**

In `/home/ssilver/development/fans`, the auth gate lives in `fans/auth.py` + its wiring in `fansd.py`. Make the app LAN-open: keep the `Host`/CORS allow-list (DNS-rebind protection) but remove the broker-handoff/session-cookie remote path. Verify the daemon still starts and `GET /api/fans` works locally:

```bash
cd /home/ssilver/development/fans && python -m pytest -q 2>&1 | tail -3
curl -s http://localhost:8095/api/fans | head -c 80; echo
```

Commit in the `fans` repo.

- [ ] **Step 2: unifi-gate — remove Firebase + `X-Verified-User` remote enforcement**

In `/home/ssilver/development/unifi-gate-public/server.py`, the `require_auth` path enforces the Worker's `X-Verified-User`. Since home calls gate over the LAN with its configured `service_user` header and gate is no longer public, simplify to LAN-open (keep Host validation). Remove Firebase config injection from `templates/index.html` (the gate UI is being retired in Phase 2; for Phase 1 just stop requiring remote auth). Verify:

```bash
cd /home/ssilver/development/unifi-gate-public && python -m pytest -q 2>&1 | tail -3
curl -s -H 'X-Verified-User: home-gateway@local' http://10.182.70.240:8000/devices | head -c 80; echo
```

Commit in the `unifi-gate-public` repo.

- [ ] **Step 3: pentair — remove Firebase remote auth**

In `/home/ssilver/development/pentair/pentair-daemon`, remove the optional Firebase verification (`fcm.rs`/auth path) from the request flow so the daemon is plain LAN-open. This is Rust — rebuild and run its test suite:

```bash
cd /home/ssilver/development/pentair/pentair-daemon && cargo test 2>&1 | tail -5
```

Confirm `GET /api/pool` works locally, then commit in the `pentair` repo.

- [ ] **Step 4: Final end-to-end pass**

```bash
H=http://192.168.1.15:8099
for p in pool fans gate; do echo -n "$p -> "; curl -s $H/api/raw/$p | head -c 40; echo; done
curl -sI https://home.oursilverfamily.com/ | head -1     # 200/302 via home tunnel
for h in gate pool patio; do echo -n "$h -> "; curl -sI https://$h.oursilverfamily.com/ | head -1; done  # all 301
```

Expected: home reads every backend over the LAN; the old domains all 301 to home; the backends answer only on the LAN.

- [ ] **Rollback (per backend):** `git revert` that backend's commit and redeploy it; its LAN API is unchanged either way, so home keeps working throughout.

---

## Phase 1 done — definition of done
- One public hostname (`home.oursilverfamily.com`); `gate.`/`pool.`/`patio.` 301 to it.
- One login (silver-oauth at home) + working on-network self-approve (grant ticket).
- No Firebase, no Cloudflare Worker, no KV, no per-app remote auth.
- Backends reachable only on the LAN, only via home.
- All home tests green; backend test suites green.

## Out of scope (later phases)
- Phase 2: strip the backend UIs (single app).
- Phase 3: modularize the home SPA.
- Phase 4: device-driver contract + one generic adapter + gate push.

---

## Self-Review (run by plan author)

**Spec coverage:** sole public entry (Tasks 10–11) ✓; bind backends LAN-only (Task 11) ✓; silver-oauth single login (already live; Tasks 1–6) ✓; mutable allow-list (Task 1) ✓; on-network broker-verified grant-ticket self-approve, Host-is-LAN (Tasks 2–7) ✓; retire Worker+KV (Task 12) ✓; retire Firebase + per-app remote auth (Task 13) ✓; 301 old domains (Task 10) ✓; cutover/rollback so remote never breaks (Tasks 9 STOP-CHECK + per-task rollbacks, add-before-remove ordering) ✓.

**Known implementer follow-ups (not placeholders, but call-outs):**
- Task 5's test needs the `AuthGate` instance; extend `make_client` to also return the gate (or add `make_auth_client`). The production code is fully specified; only the test harness helper needs the one-line return change.
- Task 8/11 patio: confirm the patio tunnel's exact systemd unit name (none was running at planning time) before disabling.
- Task 13 edits backends whose internals weren't read line-by-line here; each step says what to remove and how to verify (tests + a local curl), one backend at a time behind home's unchanged LAN adapter.
