"""
gateway.py — OpenAI-compatible API gateway + WhatsApp webhook for magic-pigeon.

Exposes the agent via two interfaces:
  1. POST /v1/chat/completions  — OpenAI-compatible endpoint (works with LobeChat,
     Open WebUI, any OpenAI SDK client, curl, etc.)
  2. POST /webhook/whatsapp     — Twilio WhatsApp webhook (receives incoming messages,
     replies via Twilio API)

Both interfaces drive agent_core.AgentSession under the hood, so all tools,
memory, history, branching, and safety features work identically.

Run:
  python gateway.py                              # default port 5002
  GATEWAY_PORT=8080 python gateway.py            # custom port
  TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... python gateway.py  # enable WhatsApp

Requires: flask, twilio (pip install flask twilio)
"""

import json
import os
import secrets
import threading
import time
import uuid

from flask import Flask, Response, jsonify, request

import agent_core

PORT = int(os.environ.get("GATEWAY_PORT", "5002"))
AUTH_TOKEN = os.environ.get("GATEWAY_TOKEN") or secrets.token_urlsafe(24)

# Per-phone-number sessions for WhatsApp (keyed by normalized phone)
_wa_sessions = {}
_wa_lock = threading.Lock()

# Per-API-key sessions (simple: one session per bearer token by default,
# or pass a session_id in the request body to multiplex)
_api_sessions = {}
_api_lock = threading.Lock()

app = Flask(__name__)


# ── Auth ──

def _check_bearer():
    """Validate Bearer token from Authorization header. Returns None if ok,
    or a (response, status) tuple if rejected."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.headers.get("X-Auth-Token", "")
    if not token or not secrets.compare_digest(token, AUTH_TOKEN):
        return jsonify({"error": {"message": "Invalid API key", "type": "invalid_api_key"}}), 401
    return None


# ── Session management ──

MAX_SESSIONS = int(os.environ.get("GATEWAY_MAX_SESSIONS", "200"))


def _get_or_create_session(key, store, lock):
    with lock:
        if key in store:
            return store[key]
        # Cap the number of live sessions. Without this, a client passing a fresh
        # session_id (or each new phone number) allocates an AgentSession that's
        # never freed — unbounded memory growth. Evict the oldest (dicts keep
        # insertion order) when at capacity.
        if len(store) >= MAX_SESSIONS:
            oldest_key = next(iter(store))
            store.pop(oldest_key, None)
        agent = agent_core.AgentSession()
        store[key] = agent
        return agent


def _collect_response(agent, user_text):
    """Run a full agent turn synchronously, collecting text output.
    Returns the final assistant text and token usage."""
    parts = []
    collected = {"error": None}

    handlers = {
        "on_text": lambda chunk: parts.append(chunk),
        "on_assistant_done": lambda txt: None,
        "on_tool_use": lambda tid, name, inp: None,
        "on_tool_result": lambda tid, content: None,
        "on_dangerous": lambda command: False,
        "on_usage": lambda summary: collected.__setitem__("usage", summary),
        "on_compaction": lambda: None,
        "on_title": lambda title: None,
        "on_error": lambda msg: collected.__setitem__("error", msg),
    }

    agent.run_turn(user_text, handlers)
    collected["text"] = "".join(parts)
    return collected


def _stream_response(agent, user_text, model_name, req_id):
    """Generator that yields SSE chunks in OpenAI streaming format."""
    import collections
    chunks_queue = collections.deque()
    queue_lock = threading.Lock()
    done_event = threading.Event()

    def on_text(chunk):
        with queue_lock:
            chunks_queue.append(chunk)

    handlers = {
        "on_text": on_text,
        "on_assistant_done": lambda txt: None,
        "on_tool_use": lambda tid, name, inp: None,
        "on_tool_result": lambda tid, content: None,
        "on_dangerous": lambda command: False,
        "on_usage": lambda summary: None,
        "on_compaction": lambda: None,
        "on_title": lambda title: None,
        "on_error": lambda msg: None,
    }

    def run():
        try:
            agent.run_turn(user_text, handlers)
        finally:
            done_event.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()

    try:
        while not done_event.is_set() or chunks_queue:
            with queue_lock:
                batch = list(chunks_queue)
                chunks_queue.clear()
            if batch:
                for chunk_text in batch:
                    chunk_obj = {
                        "id": req_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": chunk_text},
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(chunk_obj)}\n\n"
            else:
                time.sleep(0.02)
    finally:
        # If the client disconnected (Flask closes the generator → GeneratorExit
        # here), tell the still-running turn thread to stop instead of letting it
        # run to completion as a zombie that keeps the session busy.
        if not done_event.is_set():
            agent.request_stop()

    # Final chunk with finish_reason
    final = {
        "id": req_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


# ── OpenAI-compatible endpoint ──

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    auth_err = _check_bearer()
    if auth_err:
        return auth_err

    body = request.get_json(force=True) or {}
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    model_name = body.get("model", agent_core.MODEL)
    session_id = body.get("session_id")

    if not messages:
        return jsonify({"error": {"message": "messages is required", "type": "invalid_request_error"}}), 400

    # Extract the last user message (support both string and content-array format)
    user_text = None
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue  # tolerate malformed entries instead of 500-ing on m.get
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                parts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(b.get("text", ""))
                    elif isinstance(b, str):
                        parts.append(b)
                user_text = " ".join(parts)
            else:
                user_text = str(content)
            break

    if not user_text or not user_text.strip():
        return jsonify({"error": {"message": "No user message found", "type": "invalid_request_error"}}), 400

    # Get or create session
    sess_key = session_id or "default"
    agent = _get_or_create_session(sess_key, _api_sessions, _api_lock)

    if agent.busy:
        return jsonify({"error": {"message": "Agent is busy with another request", "type": "server_error"}}), 429

    req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        return Response(
            _stream_response(agent, user_text.strip(), model_name, req_id),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming
    result = _collect_response(agent, user_text.strip())

    if result.get("error"):
        return jsonify({"error": {"message": result["error"], "type": "server_error"}}), 500

    usage_info = result.get("usage", {})
    tokens = usage_info.get("tokens", {})

    return jsonify({
        "id": req_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result["text"]},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": tokens.get("input", 0),
            "completion_tokens": tokens.get("output", 0),
            "total_tokens": tokens.get("input", 0) + tokens.get("output", 0),
        },
    })


# ── Model listing (some clients need this) ──

@app.route("/v1/models", methods=["GET"])
def list_models():
    auth_err = _check_bearer()
    if auth_err:
        return auth_err
    return jsonify({
        "object": "list",
        "data": [{
            "id": agent_core.MODEL,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "magic-pigeon",
        }],
    })


# ── WhatsApp webhook (Twilio) ──

@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Receive WhatsApp messages via Twilio webhook, reply via Twilio API."""
    try:
        from twilio.rest import Client as TwilioClient
    except ImportError:
        return jsonify({"error": "twilio package not installed — pip install twilio"}), 500

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        return jsonify({"error": "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required"}), 500

    # Verify the request is genuinely from Twilio (HMAC-signed with your auth
    # token). Without this, anyone who learns the public URL can POST here and
    # drive the agent, which can run shell — an unauthenticated RCE. Set
    # GATEWAY_SKIP_TWILIO_VALIDATION=1 only for local testing.
    if os.environ.get("GATEWAY_SKIP_TWILIO_VALIDATION") != "1":
        try:
            from twilio.request_validator import RequestValidator
        except ImportError:
            return jsonify({"error": "twilio package not installed"}), 500
        validator = RequestValidator(auth_token)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(request.url, request.form.to_dict(), signature):
            return jsonify({"error": "invalid Twilio signature"}), 403

    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip()
    to_number = request.form.get("To", "")

    if not body:
        return "", 204

    # Normalize phone for session key
    phone_key = from_number.replace("whatsapp:", "").strip()

    # Special commands
    if body.lower() in ("/new", "/reset"):
        with _wa_lock:
            _wa_sessions.pop(phone_key, None)
        _send_whatsapp(account_sid, auth_token, to_number, from_number,
                       "Session reset. Send a new message to start fresh.")
        return "", 204

    agent = _get_or_create_session(phone_key, _wa_sessions, _wa_lock)

    if agent.busy:
        _send_whatsapp(account_sid, auth_token, to_number, from_number,
                       "Agent is busy processing your previous message. Please wait.")
        return "", 204

    # Run in background so we don't block Twilio's webhook timeout
    def process():
        result = _collect_response(agent, body)
        reply = result.get("text", "").strip()
        if result.get("error"):
            reply = f"Error: {result['error']}"
        if not reply:
            reply = "(no response)"

        # WhatsApp has a 1600 char limit per message; split if needed
        for chunk in _split_message(reply, 1500):
            _send_whatsapp(account_sid, auth_token, to_number, from_number, chunk)

    threading.Thread(target=process, daemon=True).start()

    # Return 204 immediately — reply is sent asynchronously via Twilio API
    return "", 204


def _send_whatsapp(account_sid, auth_token, from_num, to_num, body):
    """Send a WhatsApp message via Twilio."""
    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(account_sid, auth_token)
        client.messages.create(body=body, from_=from_num, to=to_num)
    except Exception as e:
        print(f"[whatsapp] Failed to send: {e}")


def _split_message(text, max_len=1500):
    """Split a long message into chunks respecting line boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ── Health check ──

@app.route("/health")
def health():
    return jsonify({"status": "ok", "model": agent_core.MODEL})


# ── Entry point ──

if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY is not set.")

    print("=" * 70)
    print("magic-pigeon gateway")
    print(f"  OpenAI-compatible API: http://127.0.0.1:{PORT}/v1/chat/completions")
    print(f"  Model listing:        http://127.0.0.1:{PORT}/v1/models")
    print(f"  Health check:         http://127.0.0.1:{PORT}/health")
    print(f"  Bearer token:         {AUTH_TOKEN}")
    print()

    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    if twilio_sid:
        print(f"  WhatsApp webhook:     http://<public-url>:{PORT}/webhook/whatsapp")
        print(f"  (Set this URL in your Twilio WhatsApp Sandbox config)")
    else:
        print("  WhatsApp: disabled (set TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN to enable)")

    print()
    print("Usage with curl:")
    print(f'  curl http://127.0.0.1:{PORT}/v1/chat/completions \\')
    print(f'    -H "Authorization: Bearer {AUTH_TOKEN}" \\')
    print(f'    -H "Content-Type: application/json" \\')
    print(f'    -d \'{{"model":"{agent_core.MODEL}","messages":[{{"role":"user","content":"hello"}}]}}\'')
    print()
    print("Usage with LobeChat / Open WebUI:")
    print(f"  API Base URL: http://127.0.0.1:{PORT}/v1")
    print(f"  API Key:      {AUTH_TOKEN}")
    print(f"  Model:        {agent_core.MODEL}")
    print("=" * 70)

    app.run(host="127.0.0.1", port=PORT, threaded=True)
