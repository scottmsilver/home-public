# homed/server.py
import json
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, make_response, redirect, request, send_from_directory
from werkzeug.utils import safe_join

from homed.auth import HANDOFF_PARAM, PUBLIC_PATHS, SESSION_COOKIE, SESSION_TTL, STATE_COOKIE, AuthGate

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
            return resp
        if not gate.email_allowed(email):
            resp = make_response(f"{email} not allowed", 403)
            resp.delete_cookie(STATE_COOKIE, path="/")
            return resp
        resp = make_response(redirect("/"))
        resp.delete_cookie(STATE_COOKIE, path="/")
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
        if not gate.is_remote(request.headers.get("Host", "")) or not gate.fully_configured:
            return jsonify({"email": None, "authRequired": False})
        email = gate.current_user()
        if not email:
            return jsonify({"authRequired": True}), 401
        return jsonify({"email": email, "authRequired": True})

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
