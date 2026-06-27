# homed/server.py
import json
import queue
from pathlib import Path
from urllib.parse import urlencode, urlparse

from flask import Flask, Response, jsonify, make_response, redirect, request, send_from_directory
from werkzeug.utils import safe_join

from homed.auth import GRANT_COOKIE, HANDOFF_PARAM, PUBLIC_PATHS, SESSION_COOKIE, SESSION_TTL, STATE_COOKIE, AuthGate

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _home_sections(state, home_rows):
    """Group controls into one section per home_row, in config order.

    Each section is {"title": <str>, "controls": [<control dicts>]}. The title is
    the row's explicit ``title`` if present, else the capitalized domain. Rows that
    resolve to zero controls are omitted (so a down backend renders no empty card).
    """
    by_id = {(c["domain"], c["id"]): c for c in state["controls"]}
    sections = []
    for row in home_rows:
        dom = row["domain"]
        ids = row.get("groups") or row.get("circuits") or ([row["control"]] if row.get("control") else [])
        if dom == "gate" and row.get("control") == "unlock":
            ids = ["gate"]
        if dom == "gate" and row.get("doors"):
            # Map door name -> id, EXCLUDING the synthetic aggregate (id == domain)
            # so real doors win the name match (the aggregate shares name "Gate").
            by_name = {c["name"]: c["id"] for c in state["controls"] if c["domain"] == "gate" and c["id"] != "gate"}
            ids = [by_name[name] for name in row["doors"] if name in by_name]
        controls = [c for cid in ids if (c := by_id.get((dom, cid)))]
        if not controls:
            continue
        title = row.get("title") or dom.capitalize()
        sections.append({"title": title, "controls": controls})
    return sections


def create_app(aggregator, home_rows, web):
    app = Flask(__name__, static_folder=None)
    gate = AuthGate(web)

    def _https():
        return request.headers.get("x-forwarded-proto") == "https" or request.scheme == "https"

    @app.before_request
    def _auth():
        if not gate.is_remote(request.headers.get("Host", "")):
            return None  # LAN: open
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
        return jsonify({"sections": _home_sections(aggregator.state(), home_rows)})

    @app.post("/api/command")
    def command():
        body = request.get_json(silent=True) or {}
        try:
            aggregator.dispatch(body["domain"], body["id"], body.get("payload", {}))
        except KeyError:
            return jsonify({"error": "unknown domain or id"}), 400
        except Exception as e:
            app.logger.warning("command failed: %s", e)
            return jsonify({"error": "backend command failed"}), 502
        return jsonify({"ok": True})

    @app.get("/api/raw/pool")
    def raw_pool():
        try:
            adapter = aggregator.adapters["pool"]
        except KeyError:
            return jsonify({"error": "no pool backend"}), 404
        try:
            return jsonify(adapter.raw())
        except Exception as e:
            app.logger.warning("raw pool fetch failed: %s", e)
            return jsonify({"error": "backend fetch failed"}), 502

    @app.get("/api/raw/fans")
    def raw_fans():
        try:
            adapter = aggregator.adapters["fans"]
        except KeyError:
            return jsonify({"error": "no fans backend"}), 404
        try:
            return jsonify(adapter.raw())
        except Exception as e:
            app.logger.warning("raw fans fetch failed: %s", e)
            return jsonify({"error": "backend fetch failed"}), 502

    @app.post("/api/raw/fans/cmd")
    def raw_fans_cmd():
        body = request.get_json(silent=True)
        # A non-object body (list/string/null) would AttributeError below; treat
        # any non-dict payload as an empty request so error mapping stays clean.
        if not isinstance(body, dict):
            body = {}
        path = body.get("path", "")
        try:
            adapter = aggregator.adapters["fans"]
        except KeyError:
            return jsonify({"error": "no fans backend"}), 404
        try:
            result = adapter.raw_command(path, body.get("body") or {})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            app.logger.warning("raw fans command failed: %s", e)
            return jsonify({"error": "backend command failed"}), 502
        return jsonify(result if isinstance(result, dict) else {"ok": True})

    @app.get("/api/raw/gate")
    def raw_gate():
        try:
            adapter = aggregator.adapters["gate"]
        except KeyError:
            return jsonify({"error": "no gate backend"}), 404
        try:
            return jsonify(adapter.raw())
        except Exception as e:
            app.logger.warning("raw gate fetch failed: %s", e)
            return jsonify({"error": "backend fetch failed"}), 502

    @app.get("/api/raw/gate/image/<path:door_id>")
    def raw_gate_image(door_id):
        try:
            adapter = aggregator.adapters["gate"]
        except KeyError:
            return jsonify({"error": "no gate backend"}), 404
        try:
            content, content_type = adapter.door_image(door_id)
        except Exception as e:
            app.logger.warning("raw gate image fetch failed: %s", e)
            return jsonify({"error": "door image unavailable"}), 404
        # nosniff: the content-type is reflected from the upstream door-image
        # response, so stop the browser from sniffing it into something active
        # (e.g. an upstream text/html body executing in our same-origin context).
        return Response(content, mimetype=content_type, headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/api/raw/pool/cmd")
    def raw_pool_cmd():
        body = request.get_json(silent=True) or {}
        path = body.get("path", "")
        try:
            adapter = aggregator.adapters["pool"]
        except KeyError:
            return jsonify({"error": "no pool backend"}), 404
        try:
            result = adapter.raw_command(path, body.get("body") or {})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            app.logger.warning("raw pool command failed: %s", e)
            return jsonify({"error": "backend command failed"}), 502
        return jsonify(result if isinstance(result, dict) else {"ok": True})

    @app.post("/api/goodnight")
    def goodnight():
        """Whole-house bedtime scene: every adapter that defines goodnight()
        gets called (patio off, spa + pool light + on-auxiliaries off). Each
        domain is independent — one backend failing doesn't block the others.
        """
        results = {}
        for name, adapter in aggregator.adapters.items():
            fn = getattr(adapter, "goodnight", None)
            if not callable(fn):
                continue
            try:
                fn()
                results[name] = "ok"
            except Exception as e:
                app.logger.warning("goodnight failed for %s: %s", name, e)
                results[name] = "error"
            # Refresh whether the call succeeded or partially failed: devices may
            # have changed either way, and this re-snapshots + pushes a fresh SSE
            # frame so the UI reconciles deterministically instead of waiting on
            # each backend's own websocket to fire.
            try:
                aggregator.refresh_domain(name)
            except Exception:
                pass
        ok = all(v == "ok" for v in results.values())
        return jsonify({"ok": ok, "domains": results})

    @app.get("/api/stream")
    def stream():
        q = aggregator.subscribe()

        def gen():
            try:
                yield f"data: {json.dumps(aggregator.state())}\n\n"
                while True:
                    try:
                        payload = q.get(timeout=20)
                    except queue.Empty:
                        # Heartbeat: keeps proxies/tunnels from idling the
                        # connection out, and lets the client detect a dead
                        # ("zombie") stream — no ping in ~35s → it reconnects.
                        yield "event: ping\ndata: 1\n\n"
                        continue
                    yield f"data: {json.dumps(payload)}\n\n"
            finally:
                aggregator.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream")

    # ── auth dance ───────────────────────────────────────────────
    @app.post("/api/auth/grant-start")
    def auth_grant_start():
        # On-network only, with POSITIVE local-origin validation (not merely
        # "not the remote domain"): an IP-literal/localhost/allow-listed Host,
        # which a DNS-rebinding page in a LAN browser cannot forge.
        if not gate.is_trusted_local(request.headers.get("Host", "")):
            return jsonify({"error": "self-approve is only available on the local network"}), 403
        # Defense in depth: a cross-origin POST (rebinding / CSRF) carries an
        # Origin; reject it unless that origin is itself local.
        origin = request.headers.get("Origin", "")
        if origin and not gate.is_trusted_local(urlparse(origin).netloc):
            return jsonify({"error": "bad origin"}), 403
        if not gate.remote_domain or not gate.fully_configured:
            return jsonify({"error": "remote access not configured"}), 503
        ticket = gate.make_grant_ticket()
        login_url = f"https://{gate.remote_domain}/api/auth/login?{urlencode({'grant': ticket})}"
        return jsonify({"ticket": ticket, "login_url": login_url})

    @app.get("/api/auth/login")
    def auth_login():
        if not gate.is_remote(request.headers.get("Host", "")):
            return "login only via remote domain", 403
        if not gate.fully_configured:
            return "auth not configured", 503
        import secrets as _s

        st = _s.token_urlsafe(24)
        # Build the callback host from the CONFIGURED remote domain, never the
        # request Host header (which an attacker could spoof to redirect the
        # broker to a hostile domain). The remote domain is tunneled TLS → https.
        cb = f"https://{gate.remote_domain}/api/auth/callback?{urlencode({'state': st})}"
        resp = make_response(redirect(f"{gate.broker_url}/start?{urlencode({'return_url': cb, 'scope': 'openid'})}"))
        resp.set_cookie(STATE_COOKIE, st, max_age=600, httponly=True, secure=_https(), samesite="Lax", path="/")
        grant = request.args.get("grant", "")
        if grant:
            resp.set_cookie(GRANT_COOKIE, grant, max_age=600, httponly=True, secure=_https(), samesite="Lax", path="/")
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

    @app.get("/api/auth/me")
    def auth_me():
        host = request.headers.get("Host", "")
        # lan reflects where self-approve will actually work (trusted-local), so
        # the SPA only shows "enable remote access" when grant-start will succeed.
        lan = gate.is_trusted_local(host)
        remote = gate.is_remote(host) and gate.fully_configured
        if not remote:
            return jsonify({"email": None, "authRequired": False, "lan": lan})
        email = gate.current_user()
        if not email:
            return jsonify({"authRequired": True, "lan": False}), 401
        return jsonify({"email": email, "authRequired": True, "lan": False})

    @app.post("/api/auth/logout")
    def auth_logout():
        resp = make_response(jsonify({"ok": True}))
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    # ── static SPA (Plan 2 fills static/index.html) ──────────────
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
