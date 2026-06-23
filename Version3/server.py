"""
server.py — Flask + Socket.IO backend for the magic-pigeon web UI.

Single process: serves the React frontend as static files AND runs the agent.
Wraps agent_core.AgentSession (which preserves all terminal-version behavior).
Terminal and web versions share ~/.agent_history/ seamlessly.

Run:  python server.py        (needs ANTHROPIC_API_KEY in the environment)
"""

import os
import secrets
import threading

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit

import agent_core

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
PORT = int(os.environ.get("PORT", "5001"))

# Bind address. Defaults to localhost-only (safe). Set MAGIC_PIGEON_HOST=0.0.0.0
# to also accept connections from other devices on your network — e.g. open the
# UI from your phone's browser over the same Wi-Fi. This exposes a shell-capable
# server to the LAN, gated only by the access token, so it's strictly opt-in.
HOST = os.environ.get("MAGIC_PIGEON_HOST", "127.0.0.1")
LAN_MODE = HOST not in ("127.0.0.1", "localhost", "::1")
# Relax CORS when the UI may be reached from a non-local origin: LAN mode, or a
# tunnel (cloudflared/ngrok) that serves the page from an unpredictable public
# domain. The token still gates every connection, so CORS adds little here.
ALLOW_REMOTE = LAN_MODE or os.environ.get("MAGIC_PIGEON_ALLOW_REMOTE") == "1"

# Access token. This server can run ARBITRARY shell on the host (general_bash),
# so every API/WebSocket caller must present this token. Set MAGIC_PIGEON_TOKEN
# to pin it across restarts; otherwise a fresh one is generated each run and
# printed as part of the access URL.
AUTH_TOKEN = os.environ.get("MAGIC_PIGEON_TOKEN") or secrets.token_urlsafe(24)

app = Flask(__name__, static_folder=None)
# threading async mode keeps it a single plain process (no eventlet/gevent),
# which avoids monkey-patching conflicts with the anthropic SDK's httpx client.
# CORS is locked to local origins (token is the real gate; this stops a random
# web page in your browser from opening a cross-origin socket). In LAN mode the
# page is served from the machine's LAN IP, whose origin we can't predict, so we
# relax CORS to any origin — the token still gates every connection.
socketio = SocketIO(
    app,
    cors_allowed_origins="*" if ALLOW_REMOTE else [f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"],
    async_mode="threading",
)


def _valid_token(token):
    return bool(token) and secrets.compare_digest(str(token), AUTH_TOKEN)


@app.before_request
def _require_auth():
    """Gate the data API. The static shell (/ and /static/*) loads unauthenticated
    so the page can read its token from the URL; everything else needs the token."""
    p = request.path
    if p == "/" or p.startswith("/static/") or p.startswith("/assets/") or p.startswith("/socket.io"):
        return None
    token = request.headers.get("X-Auth-Token") or request.args.get("token")
    if not _valid_token(token):
        return jsonify({"error": "unauthorized"}), 401

# Per-connection state: sid -> dict(agent, confirm_event, confirm_result)
_clients = {}
_clients_lock = threading.Lock()


def _client(sid):
    with _clients_lock:
        return _clients.get(sid)


def _turn_error(exc):
    """Turn an exception from a running turn into a user-facing message. The
    most common operational failure is a missing/invalid API key, which the
    Anthropic SDK surfaces as an authentication error — call that out plainly
    instead of dumping a raw traceback string."""
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY is not set on the server. Restart it with the key in the environment."
    if "authentication" in low or "api key" in low or "401" in low:
        return f"API authentication failed (check ANTHROPIC_API_KEY): {msg}"
    return f"Turn failed: {msg}"


# ── Static frontend ──

@app.route("/")
def index():
    # index.html must never be cached: it's the entry point that references the
    # content-hashed JS/CSS bundles, so a stale cached copy would keep loading an
    # old build after a rebuild. The hashed assets themselves can cache forever.
    resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


@app.route("/assets/<path:path>")
def asset_files(path):
    return send_from_directory(os.path.join(STATIC_DIR, "assets"), path)


# ── REST API ──

@app.route("/api/config")
def api_config():
    return jsonify({
        "model": agent_core.DEFAULT_MODEL,
        "pricing": agent_core.PRICING,
        "cwd": os.getcwd(),
        "modelTiers": [
            {
                "tier": t["tier"],
                "label": t["label"],
                "versions": [
                    {"id": v["id"], "label": v["label"], "effort": v["effort"]}
                    for v in t["versions"]
                ],
            }
            for t in agent_core.MODEL_TIERS
        ],
        "effortLevels": agent_core.EFFORT_LEVELS,
    })


@app.route("/api/sessions")
def api_sessions():
    return jsonify(agent_core.list_sessions())


@app.route("/api/sessions/<filename>")
def api_session(filename):
    try:
        _, display = agent_core.load_session_messages(filename)
    except ValueError:
        return jsonify({"error": "invalid filename"}), 400
    titles = agent_core.load_titles()
    all_tags = agent_core.load_tags()
    return jsonify({
        "filename": filename,
        "title": titles.get(filename) or "Untitled chat",
        "tags": all_tags.get(filename, {}).get("tags", []),
        "messages": display,
    })


@app.route("/api/memories", methods=["GET"])
def api_memories():
    return jsonify(agent_core.load_memories())


@app.route("/api/memories", methods=["POST"])
def api_add_memory():
    data = request.get_json(force=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    # This panel manages the GLOBAL memory store (load_memories); pass scope
    # explicitly since update_memory now defaults to the project observation store.
    agent_core.update_memory("add", content, scope="global")
    return jsonify(agent_core.load_memories())


@app.route("/api/memories/<int:index>", methods=["DELETE"])
def api_delete_memory(index):
    agent_core.update_memory("delete", "", index, scope="global")
    return jsonify(agent_core.load_memories())


@app.route("/api/sessions/<filename>/title", methods=["PUT"])
def api_rename_session(filename):
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "invalid filename"}), 400
    agent_core.save_title(filename, title)
    return jsonify({"ok": True, "title": title})


@app.route("/api/sessions/<filename>", methods=["DELETE"])
def api_delete_session(filename):
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "invalid filename"}), 400
    fpath = os.path.join(agent_core.HISTORY_DIR, filename)
    if not os.path.exists(fpath):
        return jsonify({"error": "not found"}), 404
    os.remove(fpath)
    agent_core.delete_title(filename)
    return jsonify({"ok": True})


@app.route("/api/tags")
def api_tags():
    return jsonify(agent_core.load_tags())


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"result": ""})
    return jsonify({"result": agent_core.search_full_history(q)})


# ── Socket.IO: live agent stream ──

@socketio.on("connect")
def on_connect(auth):
    if not _valid_token((auth or {}).get("token")):
        return False  # reject the connection
    sid = request.sid
    agent = agent_core.AgentSession()
    with _clients_lock:
        _clients[sid] = {
            "agent": agent,
            "confirm_event": threading.Event(),
            "confirm_result": False,
            "plan_event": threading.Event(),
            "plan_result": {"approved": False, "feedback": ""},
        }
    emit("session_info", {
        "filename": agent.filename,
        "session_id": agent.session_id,
        "usage": agent.usage_summary(),
        "title": None,
        "messages": [],
    })


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    state = _client(sid)
    if state:
        agent = state["agent"]
        agent.request_stop()
        # Generate tags on disconnect (terminal on_exit equivalent), in background.
        socketio.start_background_task(agent.finalize)
    with _clients_lock:
        _clients.pop(sid, None)


@socketio.on("new_session")
def on_new_session():
    sid = request.sid
    state = _client(sid)
    if not state:
        return
    old = state["agent"]
    old.request_stop()
    if len(old.messages) >= 2:
        socketio.start_background_task(old.finalize)
    agent = agent_core.AgentSession()
    state["agent"] = agent
    emit("session_info", {
        "filename": agent.filename,
        "session_id": agent.session_id,
        "usage": agent.usage_summary(),
        "title": None,
        "messages": [],
    })


@socketio.on("load_session")
def on_load_session(data):
    sid = request.sid
    state = _client(sid)
    if not state:
        return
    filename = (data or {}).get("filename")
    if not filename:
        return
    # Reject path traversal — same guard the REST session routes use. Without it,
    # the client-supplied filename flows into session_id → history_file path.
    if "/" in filename or "\\" in filename or ".." in filename:
        emit("error", {"message": "invalid filename"})
        return
    old = state["agent"]
    old.request_stop()
    try:
        session_id = filename.replace("session_", "").replace(".jsonl", "")
        agent = agent_core.AgentSession(session_id=session_id)
        display = agent.load_from_disk()
    except Exception as e:
        emit("error", {"message": f"Failed to load session: {e}"})
        return
    state["agent"] = agent
    titles = agent_core.load_titles()
    emit("session_info", {
        "filename": agent.filename,
        "session_id": agent.session_id,
        "usage": agent.usage_summary(),
        "title": titles.get(agent.filename),
        "messages": display,
    })


@socketio.on("stop")
def on_stop():
    state = _client(request.sid)
    if state:
        state["agent"].request_stop()
        # Unblock any pending dangerous-command wait as a denial.
        state["confirm_result"] = False
        state["confirm_event"].set()


@socketio.on("confirm_dangerous")
def on_confirm_dangerous(data):
    state = _client(request.sid)
    if state:
        state["confirm_result"] = bool((data or {}).get("allow"))
        state["confirm_event"].set()


def _await_plan(sid, plan):
    """Emit the proposed plan and block this turn until the client approves it or
    requests changes (or the turn is stopped). Returns {approved, feedback}."""
    state = _client(sid)
    if not state:
        return {"approved": False, "feedback": ""}
    state["plan_result"] = {"approved": False, "feedback": "(turn stopped)"}
    state["plan_event"].clear()
    socketio.emit("plan_proposed", {"plan": plan}, to=sid)
    while not state["plan_event"].wait(timeout=0.5):
        if state["agent"].stop_event.is_set():
            return {"approved": False, "feedback": "(turn stopped)"}
    return state["plan_result"]


@socketio.on("respond_plan")
def on_respond_plan(data):
    state = _client(request.sid)
    if state:
        data = data or {}
        state["plan_result"] = {
            "approved": bool(data.get("approved")),
            "feedback": str(data.get("feedback", "")),
        }
        state["plan_event"].set()


def _make_handlers(sid, agent):
    return {
        "on_text": lambda chunk: socketio.emit("stream", {"chunk": chunk}, to=sid),
        "on_assistant_done": lambda txt: socketio.emit("assistant_done", {"text": txt}, to=sid),
        "on_tool_use": lambda tid, name, inp: socketio.emit(
            "tool_use", {"id": tid, "name": name, "input": inp}, to=sid),
        "on_tool_result": lambda tid, content: socketio.emit(
            "tool_result", {"id": tid, "content": content}, to=sid),
        "on_dangerous": lambda command: _await_confirm(sid, command),
        "on_plan": lambda plan: _await_plan(sid, plan),
        "on_usage": lambda summary: socketio.emit("usage", summary, to=sid),
        "on_compaction": lambda: socketio.emit("compaction", {}, to=sid),
        "on_title": lambda title: socketio.emit(
            "title", {"title": title, "filename": agent.filename}, to=sid),
        "on_error": lambda msg: socketio.emit("error", {"message": msg}, to=sid),
    }


def _emit_branch(sid, agent):
    try:
        socketio.emit("branch_update", {"messages": agent.branch_display()}, to=sid)
    except Exception as e:
        print(f"[branch_update error] {e}")
        socketio.emit("error", {"message": f"branch_update failed: {e}"}, to=sid)


@socketio.on("send_message")
def on_send_message(data):
    sid = request.sid
    state = _client(sid)
    if not state:
        return
    agent = state["agent"]
    text = (data or {}).get("text", "")
    if not text.strip():
        return
    if agent.busy:
        emit("error", {"message": "Agent is busy with the current turn."})
        return

    def run():
        socketio.emit("turn_start", {}, to=sid)
        try:
            agent.run_turn(text, _make_handlers(sid, agent))
        except Exception as e:
            socketio.emit("error", {"message": _turn_error(e)}, to=sid)
        finally:
            socketio.emit("turn_done", {"usage": agent.usage_summary()}, to=sid)
            _emit_branch(sid, agent)

    socketio.start_background_task(run)


@socketio.on("regenerate")
def on_regenerate():
    sid = request.sid
    state = _client(sid)
    if not state:
        return
    agent = state["agent"]
    if agent.busy:
        emit("error", {"message": "Agent is busy."})
        return

    def run():
        socketio.emit("turn_start", {}, to=sid)
        try:
            agent.run_regenerate(_make_handlers(sid, agent))
        except Exception as e:
            socketio.emit("error", {"message": _turn_error(e)}, to=sid)
        finally:
            socketio.emit("turn_done", {"usage": agent.usage_summary()}, to=sid)
            _emit_branch(sid, agent)

    socketio.start_background_task(run)


@socketio.on("edit_message")
def on_edit_message(data):
    sid = request.sid
    state = _client(sid)
    if not state:
        return
    agent = state["agent"]
    node_id = (data or {}).get("node_id")
    text = (data or {}).get("text", "")
    if not node_id or not text.strip():
        return
    if agent.busy:
        emit("error", {"message": "Agent is busy."})
        return

    def run():
        socketio.emit("turn_start", {}, to=sid)
        try:
            agent.run_edit(node_id, text, _make_handlers(sid, agent))
        except Exception as e:
            socketio.emit("error", {"message": _turn_error(e)}, to=sid)
        finally:
            socketio.emit("turn_done", {"usage": agent.usage_summary()}, to=sid)
            _emit_branch(sid, agent)

    socketio.start_background_task(run)


@socketio.on("switch_branch")
def on_switch_branch(data):
    sid = request.sid
    state = _client(sid)
    if not state:
        return
    agent = state["agent"]
    node_id = (data or {}).get("node_id")
    direction = (data or {}).get("direction")
    if not node_id or direction not in ("prev", "next"):
        return
    display = agent.switch_branch(node_id, direction)
    if display is not None:
        socketio.emit("branch_update", {"messages": display}, to=sid)


@socketio.on("set_model")
def on_set_model(data):
    state = _client(request.sid)
    if not state:
        return
    agent = state["agent"]
    if agent.busy:
        emit("error", {"message": "Can't switch model mid-turn."})
        return
    agent.set_model((data or {}).get("model"), (data or {}).get("effort"))
    # usage_summary carries model + effort, so emitting it updates the UI.
    emit("usage", agent.usage_summary())


@socketio.on("set_plan_mode")
def on_set_plan_mode(data):
    state = _client(request.sid)
    if not state:
        return
    agent = state["agent"]
    if agent.busy:
        emit("error", {"message": "Can't toggle plan mode mid-turn."})
        return
    agent.set_plan_mode(bool((data or {}).get("on")))
    # usage_summary carries plan_mode, so emitting it updates the UI indicator.
    emit("usage", agent.usage_summary())


def _await_confirm(sid, command):
    """Emit a dangerous-command warning and block this turn until the client
    answers y/n (or the turn is stopped). Returns True to allow."""
    state = _client(sid)
    if not state:
        return False
    state["confirm_result"] = False
    state["confirm_event"].clear()
    socketio.emit("dangerous", {"command": command}, to=sid)
    # Wait, but stay responsive to a stop request.
    while not state["confirm_event"].wait(timeout=0.5):
        if state["agent"].stop_event.is_set():
            return False
    return state["confirm_result"]


def _lan_ip():
    """Best-effort local network IP (for the phone-access URL). No packets are
    actually sent — connecting a UDP socket just resolves the outbound route."""
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY is not set. The agent will fail to call the API.")
    print("=" * 70)
    print("magic-pigeon web UI — open this URL (it carries your access token):")
    print(f"  http://127.0.0.1:{PORT}/?token={AUTH_TOKEN}")
    if LAN_MODE:
        ip = _lan_ip()
        if ip:
            print("on your phone (same Wi-Fi), open:")
            print(f"  http://{ip}:{PORT}/?token={AUTH_TOKEN}")
        print("⚠ LAN mode: anyone on this network with the token can run shell here.")
    else:
        print("(to use it from your phone on the same Wi-Fi, restart with MAGIC_PIGEON_HOST=0.0.0.0)")
    if not os.environ.get("MAGIC_PIGEON_TOKEN"):
        print("  (token is random for this run; set MAGIC_PIGEON_TOKEN to keep it stable)")
    print("=" * 70)
    socketio.run(app, host=HOST, port=PORT, allow_unsafe_werkzeug=True)
