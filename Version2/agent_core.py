"""
agent_core.py — importable refactor of magic_pigeon.py

This module preserves ALL behavior of the terminal agent (magic_pigeon.py):
  - 8 tools, 4-layer memory, dual-path history search
  - context compaction at 90% capacity (max(last 4 rounds, last 20K tokens))
  - dangerous-command blacklist + auto git snapshots before writes
  - prompt caching with 3 breakpoints

Differences from the terminal version, required to drive it from a web UI:
  - the module-level `while True: input()` loop is replaced by AgentSession.run_turn()
  - terminal print()/input() side-effects become callbacks (on_text, on_dangerous, ...)
  - per-session state lives on AgentSession instead of module globals
  - adds an optional auto-generated chat title stored in titles.json (the terminal
    version's files/behavior are untouched; titles.json is a NEW, separate index)

The original magic_pigeon.py is left completely untouched and still works standalone.
All data lives in ~/.agent_history/ in the SAME formats, so the terminal and web
versions share history, memories, and tags seamlessly.
"""

import os
import re
import json
import time
import copy
import uuid
import signal
import threading
import subprocess
import glob as glob_module

import urllib.request
import urllib.parse

import anthropic

client = anthropic.Anthropic()

# USD pricing per 1M tokens (cost display only), per tier.
_OPUS_PRICING = {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}
_SONNET_PRICING = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}
_HAIKU_PRICING = {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10}

# Selectable models as a tier → versions cascade. Only versions from the 4.6
# generation onward (Haiku's latest is 4.5, the exception, since there is no
# 4.6+ Haiku). `effort` marks versions accepting the output_config.effort
# parameter (4.6-generation and newer); sending it to one without support 400s.
MODEL_TIERS = [
    {"tier": "opus", "label": "Opus", "pricing": _OPUS_PRICING, "versions": [
        {"id": "claude-opus-4-8", "label": "4.8", "effort": True},
        {"id": "claude-opus-4-7", "label": "4.7", "effort": True},
        {"id": "claude-opus-4-6", "label": "4.6", "effort": True},
    ]},
    {"tier": "sonnet", "label": "Sonnet", "pricing": _SONNET_PRICING, "versions": [
        {"id": "claude-sonnet-4-6", "label": "4.6", "effort": True},
    ]},
    {"tier": "haiku", "label": "Haiku", "pricing": _HAIKU_PRICING, "versions": [
        {"id": "claude-haiku-4-5", "label": "4.5", "effort": False},
    ]},
]

# Flat id -> metadata, derived from the tiers (used for validation, effort
# gating, pricing, and usage display).
MODELS = {}
for _tier in MODEL_TIERS:
    for _ver in _tier["versions"]:
        MODELS[_ver["id"]] = {
            "label": f'{_tier["label"]} {_ver["label"]}',
            "tier": _tier["tier"],
            "effort": _ver["effort"],
            "pricing": _tier["pricing"],
        }

DEFAULT_MODEL = "claude-opus-4-8"
MODEL = DEFAULT_MODEL              # back-compat default (module-level summary/compaction)
TAG_MODEL = "claude-haiku-4-5"     # cheap/fast model for auto-titles and tags

# Effort levels for output_config.effort (Claude 4.6-generation and newer).
EFFORT_LEVELS = ["high", "medium", "low"]

# Back-compat flat pricing (the default model's), still returned by /api/config.
PRICING = MODELS[DEFAULT_MODEL]["pricing"]

# ── Paths for persistent storage (shared with the terminal version) ──
HISTORY_DIR = os.path.expanduser("~/.agent_history")
os.makedirs(HISTORY_DIR, exist_ok=True)
MEMORIES_FILE = os.path.join(HISTORY_DIR, "memories.json")
TAGS_FILE = os.path.join(HISTORY_DIR, "tags.json")
TITLES_FILE = os.path.join(HISTORY_DIR, "titles.json")  # NEW — web-only index

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

CONTEXT_WINDOW = 1000000
MAX_OUTPUT = 128000

# JSON index files are read/written from multiple request threads; guard each.
_titles_lock = threading.Lock()
_memories_lock = threading.Lock()
_tags_lock = threading.Lock()


def _atomic_write_json(path, data):
    """Write JSON to `path` atomically: dump to a temp file in the same dir, then
    os.replace() it into place. os.replace is atomic on POSIX, so a crash mid-write
    can never leave a truncated/corrupt file that would wipe memories/tags/titles
    on the next load. Without this, opening with "w" truncates first, so a kill
    between truncate and full write loses the whole file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ── Memory management ──

def load_memories():
    if os.path.exists(MEMORIES_FILE):
        try:
            with open(MEMORIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_memories(memories):
    _atomic_write_json(MEMORIES_FILE, memories)


def update_memory(action, content, index=None):
    # Lock the whole read-modify-write so two concurrent sessions can't clobber
    # each other's add/delete (last-writer-wins would silently drop a memory).
    with _memories_lock:
        memories = load_memories()
        if action == "add":
            memories.append({"content": content, "time": time.time()})
            save_memories(memories)
            return f"Memory saved: {content}"
        elif action == "delete":
            if index is not None and 0 <= index < len(memories):
                removed = memories.pop(index)
                save_memories(memories)
                return f"Memory deleted: {removed['content']}"
            else:
                return f"Invalid index. You have {len(memories)} memories (0-{len(memories)-1})."
        elif action == "list":
            if not memories:
                return "No saved memories."
            lines = []
            for i, m in enumerate(memories):
                lines.append(f"[{i}] {m['content']}")
            return "\n".join(lines)
        return "Unknown action. Use add, delete, or list."


def format_memories_for_prompt():
    memories = load_memories()
    if not memories:
        return ""
    lines = ["\n\n## Saved Memories (persistent across sessions)\n"]
    for m in memories:
        lines.append(f"- {m['content']}")
    return "\n".join(lines)


# ── Tag system ──

def load_tags():
    if os.path.exists(TAGS_FILE):
        try:
            with open(TAGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_tags(tags):
    with _tags_lock:
        _atomic_write_json(TAGS_FILE, tags)


def generate_session_tags(messages):
    """Generate English topic tags for a session (run on exit)."""
    if len(messages) < 2:
        return []

    readable_parts = []
    for msg in messages:
        text = extract_readable(msg)
        if text:
            readable_parts.append(text[:200])

    conversation_text = "\n".join(readable_parts[-50:])

    try:
        response = client.messages.create(
            model=TAG_MODEL,
            max_tokens=512,
            system="You generate search tags for conversations. Output ONLY a JSON array of strings. Each string is a short tag (2-6 words) in ENGLISH ONLY describing one topic discussed. Use multiple synonyms and related terms in each tag to maximize future search hits. Even if the conversation was in Chinese or other languages, always generate English tags. Generate 3-15 tags depending on conversation length.",
            messages=[{
                "role": "user",
                "content": f"Generate tags for this conversation:\n\n{conversation_text}"
            }],
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        tags = json.loads(raw)
        if isinstance(tags, list):
            return [str(t) for t in tags]
    except Exception:
        pass
    return []


def save_session_tags(session_filename, messages):
    """Generate and store tags for a given session filename."""
    if len(messages) < 2:
        return []
    tags_list = generate_session_tags(messages)
    if tags_list:
        all_tags = load_tags()
        all_tags[session_filename] = {
            "time": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "tags": tags_list,
        }
        save_tags(all_tags)
    return tags_list


def format_tags_for_prompt():
    all_tags = load_tags()
    if not all_tags:
        return ""

    lines = ["\n\n## Recent Chat Tags (topics from past sessions)\n"]
    sorted_sessions = sorted(
        all_tags.items(),
        key=lambda x: x[1].get("time", ""),
        reverse=True,
    )
    for session_name, info in sorted_sessions[:50]:
        t = info.get("time", "unknown")
        tags = ", ".join(info.get("tags", []))
        lines.append(f"- [{t}] {session_name}: {tags}")
    return "\n".join(lines)


# ── Title index (web-only, separate file; does not touch terminal data) ──

def load_titles():
    with _titles_lock:
        if os.path.exists(TITLES_FILE):
            try:
                with open(TITLES_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}


def save_title(session_filename, title):
    with _titles_lock:
        titles = {}
        if os.path.exists(TITLES_FILE):
            try:
                with open(TITLES_FILE, "r", encoding="utf-8") as f:
                    titles = json.load(f)
            except Exception:
                titles = {}
        titles[session_filename] = title
        _atomic_write_json(TITLES_FILE, titles)


def delete_title(session_filename):
    """Drop a session's title from the index (called when a session is deleted).
    Reads the file directly under the lock — must NOT call load_titles(), which
    takes the same non-reentrant lock and would deadlock."""
    with _titles_lock:
        if not os.path.exists(TITLES_FILE):
            return
        try:
            with open(TITLES_FILE, "r", encoding="utf-8") as f:
                titles = json.load(f)
        except Exception:
            return
        if session_filename in titles:
            del titles[session_filename]
            _atomic_write_json(TITLES_FILE, titles)


def generate_title(messages):
    """Generate a short chat title from the opening of a conversation."""
    readable_parts = []
    for msg in messages:
        text = extract_readable(msg)
        if text:
            readable_parts.append(text[:300])
    convo = "\n".join(readable_parts[:8])
    if not convo.strip():
        return "New chat"
    try:
        response = client.messages.create(
            model=TAG_MODEL,
            max_tokens=40,
            system="You write very short chat titles. Output ONLY the title text, 2-6 words, no quotes, no punctuation at the end. Use the conversation's own language.",
            messages=[{
                "role": "user",
                "content": f"Write a short title for this conversation:\n\n{convo}"
            }],
        )
        title = response.content[0].text.strip().strip('"').strip()
        title = title.split("\n")[0][:60]
        return title or "New chat"
    except Exception:
        return "New chat"


# ── Logging and serialization ──

def make_serializable(obj):
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    elif hasattr(obj, "__dict__") and not isinstance(obj, (str, int, float, bool)):
        return {k: make_serializable(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    elif isinstance(obj, list):
        return [make_serializable(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    else:
        return obj


def extract_readable(msg):
    role = msg.get("role", "")
    content = msg.get("content", "")
    if isinstance(content, str):
        return f"{role}: {content}"
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool: {block.get('name', '')}({json.dumps(block.get('input', {}), ensure_ascii=False)[:100]})]")
                elif block.get("type") == "tool_result":
                    parts.append(f"[tool_result: {str(block.get('content', ''))[:200]}]")
            elif hasattr(block, "type"):
                if block.type == "text":
                    parts.append(block.text)
                elif block.type == "tool_use":
                    parts.append(f"[tool: {block.name}]")
        if parts:
            return f"{role}: {'  '.join(parts)}"
    return ""


# ── History search (dual-path: tags + raw content) ──

def search_full_history(keywords):
    keyword_list = keywords.lower().split()
    results = []
    matched_sessions = set()

    all_tags = load_tags()
    for session_name, info in all_tags.items():
        tags_text = " ".join(info.get("tags", [])).lower()
        match_count = sum(1 for kw in keyword_list if kw in tags_text)
        if match_count > 0:
            matched_sessions.add(session_name)
            t = info.get("time", "unknown")
            tags_str = ", ".join(info.get("tags", []))
            results.append({
                "score": match_count + 5,
                "session": session_name,
                "time": 0,
                "content": f"[TAG MATCH] [{t}] Tags: {tags_str}",
            })

    for fname in sorted(os.listdir(HISTORY_DIR)):
        if not fname.endswith(".jsonl"):
            continue
        fpath = os.path.join(HISTORY_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        msg = record.get("message", {})
                        msg_text = json.dumps(msg, ensure_ascii=False).lower()
                        match_count = sum(1 for kw in keyword_list if kw in msg_text)
                        if match_count > 0:
                            readable = extract_readable(msg)
                            if readable:
                                results.append({
                                    "score": match_count,
                                    "session": fname,
                                    "time": record.get("timestamp", 0),
                                    "content": readable[:500],
                                })
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:30]

    if not results:
        return "No matching history found."

    output = []
    for r in results:
        if r["time"]:
            t = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["time"]))
        else:
            t = "summary"
        output.append(f"[{t}] (session: {r['session']}) {r['content']}")
    return "\n---\n".join(output)


# Tools that can modify the local environment. Editing/regenerating messages
# before the last use of any of these is blocked to prevent the model from
# losing awareness of changes it already made to the filesystem or memory.
IMPACTFUL_TOOLS = {"general_bash", "write_file", "edit", "update_memory"}

# ── Tool definitions (identical to terminal version) ──

tools = [
    {
        "name": "read_file",
        "description": "open a file to read its contents",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "path of the file to read"}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "edit",
        "description": "open a file to replace some of its contents",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "path of the file to edit"},
                "target_contents": {"type": "string", "description": "the target old contents of the file to be replaced"},
                "new_contents": {"type": "string", "description": "the new contents used to replace the target contents"},
            },
            "required": ["file_path", "target_contents", "new_contents"],
        },
    },
    {
        "name": "write_file",
        "description": "create a new file or overwrite an existing file with new contents",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "path of the file to write"},
                "contents": {"type": "string", "description": "the new contents of the file"},
            },
            "required": ["file_path", "contents"],
        },
    },
    {
        "name": "general_bash",
        "description": "run necessary bash code to solve issues progressively",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "use shell code to collect essential info, plan steps, and take direct actions in directories"}
            },
            "required": ["command"],
        },
    },
    {
        "name": "grep",
        "description": "search for a pattern across all files in a directory recursively",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "directory to search"},
                "content": {"type": "string", "description": "the target pattern to find"},
            },
            "required": ["directory", "content"],
        },
    },
    {
        "name": "glob",
        "description": "use file name patterns to search for files in a directory",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "the directory to search in"},
                "names": {"type": "string", "description": "the glob pattern to match (e.g. *.py, **/*.ts)"},
            },
            "required": ["file_path", "names"],
        },
    },
    {
        "name": "search_history",
        "description": "Search through conversation history across all sessions. This searches BOTH session tags (lightweight topic labels) AND full raw conversation content. Use this when the user asks about something discussed earlier. Generate your own search keywords — do NOT just pass the user's words directly. Use multiple synonyms and related terms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string", "description": "space-separated search keywords. Generate these yourself based on what you think the relevant conversation contained. Use ENGLISH keywords to match tags, and also include Chinese/original-language keywords to match raw conversation content. Use multiple synonyms to maximize recall."}
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web using Google. Use this when you need up-to-date information, facts you're unsure about, or anything beyond your training data. Returns top search results with titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the search query — be specific and use keywords likely to surface relevant results"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_memory",
        "description": "Add, delete, or list persistent memories that survive across sessions. These memories are injected into EVERY future session's system prompt, so only save things that are genuinely important across all conversations. SAVE: user identity (name, role, location), long-term preferences (language, coding style), important project context (tech stack, architecture decisions), corrections to your understanding. DO NOT SAVE: temporary task details, one-off questions, trivial preferences, anything only relevant to the current conversation. EXCEPTION: if the user explicitly says 'remember this' or asks you to save something, ALWAYS save it regardless of importance. Keep each memory concise — one fact per entry. When information changes, delete the old memory and add the updated one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "delete", "list"], "description": "add: save a new memory. delete: remove a memory by index. list: show all current memories."},
                "content": {"type": "string", "description": "the fact to remember (for add action). Keep it concise, one fact per memory."},
                "index": {"type": "integer", "description": "index of the memory to delete (for delete action, 0-based)"},
            },
            "required": ["action"],
        },
    },
]

# Cache tools: mark last tool for prompt caching (90% input cost reduction)
cached_tools = copy.deepcopy(tools)
cached_tools[-1]["cache_control"] = {"type": "ephemeral"}


# ── Safety and backup ──

# Dangerous-command detection. Two kinds, both as regexes (case-insensitive):
#
#  - COMMAND-POSITION dangers: a binary that's harmful merely to *run* (passwd,
#    shutdown, mkfs, …). Anchored to a command position (line start, after a
#    shell separator `; & |`, or after `sudo`) via _CMD, so the same word used
#    as a *path argument* — `cat /etc/passwd`, `grep passwd file` — does NOT trip
#    the gate. The old substring matcher over-blocked all of those.
#
#  - DESTRUCTIVE-PATTERN dangers: catastrophic rm/chmod/dd/mv/redirects and
#    pipe-to-shell, written flag-order-tolerantly so `rm -fr ~` (reversed flags)
#    and `find / -delete` are caught too — the old list missed both.
#
# This is a secondary safety net; the primary one is the git snapshot taken
# before every impactful tool (see git_snapshot). It is not meant to be
# bulletproof against a deliberately obfuscated command.
_CMD = r"(?:^|[;&|]|\bsudo\b)\s*"

DANGEROUS_REGEXES = [
    # whole-command dangers, only when actually invoked as a command
    _CMD + r"passwd\b",
    _CMD + r"useradd\b",
    _CMD + r"userdel\b",
    _CMD + r"shutdown\b",
    _CMD + r"reboot\b",
    _CMD + r"halt\b",
    _CMD + r"poweroff\b",
    _CMD + r"init\s+[06]\b",
    _CMD + r"mkfs\b",
    # fork bomb
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    # recursive rm of a catastrophic target (root, home, or root wildcard),
    # tolerant of flag order/grouping: -rf, -fr, -r -f, -Rf, …
    r"\brm\b\s+(?:-\S+\s+)*-\S*r\S*\s+(?:-\S+\s+)*[\"']?(?:/|~|\$\{?home\}?)(?:[\"'\s/*]|$)",
    r"\brm\b\s+(?:-\S+\s+)*-\S*r\S*\s+[\"']?/\*",
    # find over / or ~ that deletes
    r"\bfind\b\s+[\"']?(?:/|~|\$\{?home\}?).*-(?:delete|exec\s+rm)\b",
    # chmod/chown wiping permissions on root
    r"\bchmod\b\s+(?:-\S+\s+)*-\S*r\S*\s+[0-7]{3,4}\s+/(?:\s|$)",
    r"\bchmod\b\s+-R\s+000",
    # dd writing to / redirect into a raw block device
    r"\bdd\b.*\bof=/dev/(?:sd|disk|nvme|hd)",
    r">\s*/dev/(?:sd|disk|nvme|hd)",
    # mv of the filesystem root
    r"\bmv\b\s+/(?:\s|\*)",
    # piping a network download straight into a shell
    r"\b(?:curl|wget)\b.*\|\s*(?:sudo\s+)?(?:ba|z|da|t?c)?sh\b",
]

_DANGEROUS_COMPILED = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_REGEXES]


def is_dangerous(command):
    cmd = command.strip()
    return any(rx.search(cmd) for rx in _DANGEROUS_COMPILED)


def git_snapshot(label="auto-snapshot"):
    """Auto-commit current state before write operations for rollback capability.

    Every git call is bounded by a timeout: a wedged git process (a credential
    prompt, a stale index.lock, a slow pre-commit hook) would otherwise hang the
    whole turn forever. If git is missing or times out, we log and skip the
    snapshot rather than blocking the agent — the snapshot is best-effort."""
    cwd = os.getcwd()

    def _git(args, timeout=15):
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              cwd=cwd, timeout=timeout)

    try:
        check = _git(["rev-parse", "--is-inside-work-tree"])
        if check.returncode != 0:
            _git(["init"])
            _git(["add", "-A"], timeout=60)
            _git(["commit", "-m", "initial snapshot before agent modifications"])
        _git(["add", "-A"], timeout=60)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        _git(["commit", "-m", f"[agent-{label}] {timestamp}", "--allow-empty-message"])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"[git_snapshot] skipped ({type(e).__name__}: {e}) — proceeding without snapshot")


class CommandBlocked(Exception):
    pass


def _kill_process_group(proc):
    """Terminate the whole process group of `proc` (SIGTERM, then SIGKILL)."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            return
    # Grace period, then hard kill anything still alive.
    for _ in range(15):
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_interruptible(command, should_stop=None, shell=False, timeout=600):
    """Run a command, killing it (and its children) if should_stop() turns true
    or the timeout elapses. Returns combined stdout+stderr text."""
    proc = subprocess.Popen(
        command,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # own process group so we can kill the whole tree
    )
    out = {"stdout": "", "stderr": ""}

    def _reader():
        try:
            out["stdout"], out["stderr"] = proc.communicate()
        except Exception as e:
            out["stderr"] = f"{type(e).__name__}: {e}"

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    start = time.time()
    note = ""
    while reader.is_alive():
        reader.join(timeout=0.2)
        if should_stop and should_stop():
            _kill_process_group(proc)
            note = "\n[stopped by user — process killed]"
            break
        if time.time() - start > timeout:
            _kill_process_group(proc)
            note = f"\n[timed out after {timeout}s — process killed]"
            break
    reader.join(timeout=3)
    return (out.get("stdout") or "") + (out.get("stderr") or "") + note


def _web_search(query, num_results=8):
    """Search the web. Uses Google Custom Search API if configured, otherwise
    falls back to DuckDuckGo (no API key needed)."""
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        return _google_search(query, num_results)
    return _ddg_search(query, num_results)


def _google_search(query, num_results=8):
    """Search via Google Custom Search JSON API."""
    params = urllib.parse.urlencode({
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": min(num_results, 10),
    })
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "magic-pigeon/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = data.get("items", [])
    if not items:
        return f"No results found for: {query}"
    lines = []
    for i, item in enumerate(items, 1):
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "").replace("\n", " ")
        lines.append(f"[{i}] {title}\n    {link}\n    {snippet}")
    return "\n\n".join(lines)


def _ddg_search(query, num_results=8):
    """Search via DuckDuckGo HTML endpoint (free, no API key, no dependencies)."""
    import html.parser

    class _DDGParser(html.parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.results = []
            self._in_link = False
            self._in_snippet = False
            self._cur = {}
            self._text = ""

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "a" and "result__a" in d.get("class", ""):
                self._in_link = True
                self._cur = {"link": d.get("href", ""), "title": "", "snippet": ""}
            elif tag == "a" and "result__snippet" in d.get("class", ""):
                self._in_snippet = True

        def handle_endtag(self, tag):
            if tag == "a" and self._in_link:
                self._cur["title"] = self._text.strip()
                self._text = ""
                self._in_link = False
            elif tag == "a" and self._in_snippet:
                self._cur["snippet"] = self._text.strip()
                self._text = ""
                self._in_snippet = False
                if self._cur.get("title"):
                    self.results.append(self._cur)
                self._cur = {}

        def handle_data(self, data):
            if self._in_link or self._in_snippet:
                self._text += data

    data = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=data,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        page = resp.read().decode("utf-8", errors="replace")

    parser = _DDGParser()
    parser.feed(page)
    results = parser.results[:num_results]
    if not results:
        return f"No results found for: {query}"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\n    {r['link']}\n    {r['snippet']}")
    return "\n\n".join(lines)


def execute_tool(name, arguments, confirm_dangerous=None, should_stop=None):
    """Execute a tool. `confirm_dangerous(command) -> bool` replaces the terminal
    input() prompt; if it returns False the command is blocked."""
    try:
        if name == "read_file":
            with open(arguments["file_path"], "r", encoding="utf-8") as f:
                return f.read()

        elif name == "general_bash":
            command = arguments["command"]
            if is_dangerous(command):
                allowed = bool(confirm_dangerous(command)) if confirm_dangerous else False
                if not allowed:
                    raise CommandBlocked("User denied dangerous command.")
            git_snapshot("pre-bash")
            return _run_interruptible(command, should_stop=should_stop, shell=True, timeout=600)

        elif name == "edit":
            with open(arguments["file_path"], "r", encoding="utf-8") as f:
                content = f.read()
            target = arguments["target_contents"]
            occurrences = content.count(target)
            if occurrences == 0:
                return ("Error: target_contents not found in file — nothing was changed. "
                        "Re-read the file and copy the exact text (including whitespace) you want to replace.")
            if occurrences > 1:
                return (f"Error: target_contents appears {occurrences} times — the edit is ambiguous and "
                        "would change every occurrence. Include more surrounding context so the target is unique.")
            git_snapshot("pre-edit")
            newcon = content.replace(target, arguments["new_contents"])
            with open(arguments["file_path"], "w", encoding="utf-8") as f:
                f.write(newcon)
            return "File edited successfully."

        elif name == "write_file":
            git_snapshot("pre-write")
            with open(arguments["file_path"], "w", encoding="utf-8") as f:
                f.write(arguments["contents"])
            return "File written successfully."

        elif name == "grep":
            return _run_interruptible(
                ["grep", "-rn", arguments["content"], arguments["directory"]],
                should_stop=should_stop, shell=False, timeout=30,
            )

        elif name == "glob":
            pattern = os.path.join(arguments["file_path"], arguments["names"])
            matches = glob_module.glob(pattern, recursive=True)
            if matches:
                return "\n".join(matches)
            else:
                return "No files found matching pattern."

        elif name == "web_search":
            return _web_search(arguments["query"])

        elif name == "search_history":
            return search_full_history(arguments["keywords"])

        elif name == "update_memory":
            return update_memory(
                arguments["action"],
                arguments.get("content", ""),
                arguments.get("index"),
            )

        else:
            return f"Error: Unknown tool '{name}'"

    except CommandBlocked:
        raise
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# ── Context management ──

def estimate_messages_tokens(messages):
    return len(json.dumps(messages, ensure_ascii=False)) // 3


def deter_compact(messages, new_input, last_input_tokens, last_output_tokens):
    if last_input_tokens > 0:
        estimated = last_input_tokens + last_output_tokens + len(new_input) // 3
    else:
        estimated = estimate_messages_tokens(messages)
    usable = (CONTEXT_WINDOW - MAX_OUTPUT) * 0.90
    return estimated >= usable


def find_safe_cut(messages, target_index):
    cut = target_index
    while cut > 0:
        msg = messages[cut]
        if (msg.get("role") == "user"
                and isinstance(msg.get("content"), list)
                and len(msg["content"]) > 0
                and isinstance(msg["content"][0], dict)
                and msg["content"][0].get("type") == "tool_result"):
            cut -= 1
            continue
        if (msg.get("role") == "assistant"
                and isinstance(msg.get("content"), list)):
            cut -= 1
            continue
        break
    return max(cut, 0)


def do_compact(messages):
    """Compress old messages, keeping max(last 4 rounds, last 20K tokens)."""
    if len(messages) < 6:
        return messages

    round_starts = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            is_tool_result = (isinstance(content, list)
                              and len(content) > 0
                              and isinstance(content[0], dict)
                              and content[0].get("type") == "tool_result")
            if not is_tool_result:
                round_starts.append(i)

    if len(round_starts) > 4:
        cut_by_rounds = round_starts[-4]
    else:
        cut_by_rounds = 0

    token_count = 0
    cut_by_tokens = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        token_count += len(json.dumps(messages[i], ensure_ascii=False)) // 3
        if token_count >= 20000:
            cut_by_tokens = i
            break

    raw_cut = min(cut_by_rounds, cut_by_tokens)
    cut = find_safe_cut(messages, raw_cut)
    old = messages[:cut]
    keep = messages[cut:]

    if len(old) < 2:
        return messages

    summary_messages = list(old)
    summary_messages.append({
        "role": "user",
        "content": "Summarize the conversation above concisely. Preserve key information: what was done, which files were involved, and what to do next.",
    })

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system="You are a summarizer. Provide a concise but thorough summary.",
            messages=summary_messages,
        )
        summary = response.content[0].text
    except Exception:
        return messages

    return [{"role": "user", "content": "[Conversation summary]\n" + summary}] + keep


# ── System prompt ──

def build_system_prompt():
    base = f"""You are a coding assistant. Working directory: {os.getcwd()}
Use tools to take actions. Be concise.
You have access to a search_history tool that searches both session tags and full raw conversation history across all past sessions. Use it when the user asks about something that may have been discussed earlier.
You have access to an update_memory tool to save important facts about the user across sessions. Only use it for genuinely important cross-session information (identity, preferences, key project context), or when the user explicitly asks you to remember something.
You have access to a web_search tool that searches Google. Use it when you need current information, facts you're unsure about, or anything that may have changed after your training cutoff."""
    base += format_memories_for_prompt()
    base += format_tags_for_prompt()
    return base


def _build_cached_api_messages(messages):
    """Add a cache breakpoint on the second-to-last message (terminal-identical)."""
    api_messages = []
    for idx, msg in enumerate(messages):
        m = dict(msg)
        if idx == len(messages) - 2 and len(messages) >= 2:
            content = m.get("content", "")
            if isinstance(content, str):
                m["content"] = [{
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }]
            elif isinstance(content, list) and len(content) > 0:
                last_block = content[-1]
                if isinstance(last_block, dict):
                    last_block = dict(last_block)
                    last_block["cache_control"] = {"type": "ephemeral"}
                    m["content"] = list(content[:-1]) + [last_block]
        api_messages.append(m)
    return api_messages


# ── Session helpers for the web layer ──

def list_sessions():
    """Return session metadata for the sidebar (newest first)."""
    titles = load_titles()
    all_tags = load_tags()
    sessions = []
    for fname in os.listdir(HISTORY_DIR):
        if not (fname.startswith("session_") and fname.endswith(".jsonl")):
            continue
        fpath = os.path.join(HISTORY_DIR, fname)
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            mtime = 0
        tag_info = all_tags.get(fname, {})
        sessions.append({
            "filename": fname,
            "title": titles.get(fname) or "Untitled chat",
            "tags": tag_info.get("tags", []),
            "mtime": mtime,
            "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else "",
        })
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def _normalize_message(message):
    """Turn a stored message dict into display blocks for the frontend."""
    role = message.get("role", "")
    content = message.get("content", "")
    blocks = []
    if isinstance(content, str):
        blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                blocks.append({"type": "text", "text": b.get("text", "")})
            elif btype == "tool_use":
                blocks.append({
                    "type": "tool_use",
                    "id": b.get("id"),
                    "name": b.get("name"),
                    "input": b.get("input", {}),
                })
            elif btype == "tool_result":
                rc = b.get("content", "")
                if isinstance(rc, list):
                    rc = " ".join(
                        x.get("text", "") if isinstance(x, dict) else str(x) for x in rc
                    )
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": b.get("tool_use_id"),
                    "content": rc,
                })
    return {"role": role, "blocks": blocks}


def load_session_messages(filename):
    """Load a session's raw messages (for resuming) plus display form."""
    if "/" in filename or "\\" in filename:
        raise ValueError("invalid filename")
    fpath = os.path.join(HISTORY_DIR, filename)
    raw = []
    display = []
    if not os.path.exists(fpath):
        return raw, display
    with open(fpath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = record.get("message")
            if not isinstance(msg, dict):
                continue
            raw.append(msg)
            d = _normalize_message(msg)
            d["timestamp"] = record.get("timestamp", 0)
            display.append(d)
    return raw, display


# ── The session object the web layer drives ──

class AgentSession:
    """One conversation backed by a message tree (supports edit/regenerate/branching)."""

    def __init__(self, session_id=None):
        self.session_id = session_id or str(int(time.time()))
        self.filename = f"session_{self.session_id}.jsonl"
        self.history_file = os.path.join(HISTORY_DIR, self.filename)
        self.messages = []  # flat view of the active branch (for API calls)
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        self.model = DEFAULT_MODEL
        self.effort = "high"
        self.stop_event = threading.Event()
        self.busy = False
        # Guards the busy flag and structural tree edits (switch_branch) so a
        # turn running in a background thread can't race a concurrent branch
        # switch / second turn and corrupt the tree. Held only briefly (to
        # claim/release the turn or to do a fast switch), never for the whole
        # turn, so quick operations don't block on a long-running turn.
        self._turn_lock = threading.Lock()
        # ── message tree ──
        self.nodes = {}       # node_id -> {id, parent_id, role, content, children}
        self.root_ids = []    # top-level node IDs
        self.current_leaf_id = None

    # ── tree operations ──

    def _gen_id(self):
        return uuid.uuid4().hex[:12]

    def _add_node(self, role, content, parent_id=None):
        nid = self._gen_id()
        safe = make_serializable(content)
        node = {"id": nid, "parent_id": parent_id, "role": role,
                "content": safe, "children": []}
        self.nodes[nid] = node
        if parent_id and parent_id in self.nodes:
            self.nodes[parent_id]["children"].append(nid)
        if parent_id is None:
            self.root_ids.append(nid)
        self.current_leaf_id = nid
        self.messages.append({"role": role, "content": safe})
        self._log_node(nid, parent_id, {"role": role, "content": safe})
        return nid

    def _log_node(self, node_id, parent_id, message):
        try:
            record = {"timestamp": time.time(), "message": message,
                      "node_id": node_id, "parent_id": parent_id}
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _log_branch_switch(self):
        try:
            record = {"timestamp": time.time(), "type": "branch_switch",
                      "current_leaf_id": self.current_leaf_id}
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _flatten_branch(self, leaf_id=None):
        leaf_id = leaf_id or self.current_leaf_id
        if not leaf_id:
            return []
        path = []
        nid = leaf_id
        seen = set()  # guard against a parent_id cycle in a corrupted history file
        while nid and nid not in seen:
            seen.add(nid)
            node = self.nodes.get(nid)
            if not node:
                break
            path.append({"role": node["role"], "content": node["content"]})
            nid = node["parent_id"]
        path.reverse()
        return path

    def _branch_node_ids(self, leaf_id=None):
        leaf_id = leaf_id or self.current_leaf_id
        if not leaf_id:
            return []
        ids = []
        nid = leaf_id
        seen = set()  # guard against a parent_id cycle in a corrupted history file
        while nid and nid not in seen:
            seen.add(nid)
            ids.append(nid)
            node = self.nodes.get(nid)
            nid = node["parent_id"] if node else None
        ids.reverse()
        return ids

    def _get_sibling_info(self, node_id):
        node = self.nodes.get(node_id)
        if not node:
            return [], 0
        pid = node["parent_id"]
        siblings = self.nodes[pid]["children"] if pid and pid in self.nodes else self.root_ids
        idx = siblings.index(node_id) if node_id in siblings else 0
        return siblings, idx

    def _node_has_impact(self, node):
        content = node.get("content", [])
        if not isinstance(content, list):
            return False
        return any(
            isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in IMPACTFUL_TOOLS
            for b in content
        )

    def _check_branch_safety(self, from_node_id):
        """Check if any node from from_node_id to current leaf used impactful tools.
        Returns None if safe, or an error message string if blocked."""
        branch_ids = self._branch_node_ids()
        if from_node_id not in branch_ids:
            return None
        pos = branch_ids.index(from_node_id)
        for i in range(pos, len(branch_ids)):
            node = self.nodes.get(branch_ids[i])
            if node and self._node_has_impact(node):
                tool_names = [
                    b["name"] for b in node.get("content", [])
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in IMPACTFUL_TOOLS
                ]
                return (
                    f"Cannot edit: a later turn used {', '.join(tool_names)} which already "
                    f"changed local state. You can only edit messages after the last impactful action."
                )
        return None

    def _find_deepest_leaf(self, node_id):
        nid = node_id
        seen = set()  # guard against a children cycle in a corrupted history file
        while nid not in seen:
            seen.add(nid)
            node = self.nodes.get(nid)
            if not node or not node["children"]:
                return nid
            nid = node["children"][-1]
        return nid

    def _find_last_user_input(self):
        nid = self.current_leaf_id
        seen = set()  # guard against a parent_id cycle in a corrupted history file
        while nid and nid not in seen:
            seen.add(nid)
            node = self.nodes.get(nid)
            if not node:
                return None
            if node["role"] == "user":
                c = node["content"]
                is_tr = (isinstance(c, list) and len(c) > 0
                         and isinstance(c[0], dict) and c[0].get("type") == "tool_result")
                if not is_tr:
                    return nid
            nid = node["parent_id"]
        return None

    def branch_display(self):
        """Display messages for the current branch, annotated with sibling info."""
        nids = self._branch_node_ids()
        last_impact = -1
        for i in range(len(nids) - 1, -1, -1):
            node = self.nodes.get(nids[i])
            if node and self._node_has_impact(node):
                last_impact = i
                break
        out = []
        for i, nid in enumerate(nids):
            node = self.nodes[nid]
            msg = {"role": node["role"], "content": make_serializable(node["content"])}
            d = _normalize_message(msg)
            siblings, idx = self._get_sibling_info(nid)
            d["node_id"] = nid
            d["sibling_count"] = len(siblings)
            d["sibling_index"] = idx
            d["frozen"] = i <= last_impact
            out.append(d)
        return out

    # ── persistence ──

    def load_from_disk(self):
        if not os.path.exists(self.history_file):
            return []
        records = []
        with open(self.history_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        for r in reversed(records):
            if r.get("type") == "usage_totals" and isinstance(r.get("totals"), dict):
                for k in self.totals:
                    self.totals[k] = r["totals"].get(k, 0)
                break

        has_tree = any("node_id" in r for r in records if r.get("message"))
        last_leaf = None

        if has_tree:
            for r in records:
                if r.get("type") == "branch_switch":
                    last_leaf = r.get("current_leaf_id")
                    continue
                msg = r.get("message")
                if not isinstance(msg, dict):
                    continue
                nid = r.get("node_id")
                pid = r.get("parent_id")
                if not nid:
                    continue
                node = {"id": nid, "parent_id": pid, "role": msg.get("role", ""),
                        "content": msg.get("content", ""), "children": []}
                self.nodes[nid] = node
                if pid and pid in self.nodes:
                    if nid not in self.nodes[pid]["children"]:
                        self.nodes[pid]["children"].append(nid)
                if pid is None:
                    if nid not in self.root_ids:
                        self.root_ids.append(nid)
                last_leaf = nid
        else:
            prev_id = None
            for r in records:
                msg = r.get("message")
                if not isinstance(msg, dict):
                    continue
                nid = f"legacy_{len(self.nodes)}"
                node = {"id": nid, "parent_id": prev_id, "role": msg.get("role", ""),
                        "content": msg.get("content", ""), "children": []}
                self.nodes[nid] = node
                if prev_id and prev_id in self.nodes:
                    self.nodes[prev_id]["children"].append(nid)
                else:
                    self.root_ids.append(nid)
                prev_id = nid
            last_leaf = prev_id

        self.current_leaf_id = last_leaf
        self.messages = self._flatten_branch()
        return self.branch_display()

    # ── usage / cost ──

    def _record_usage(self, usage):
        self.last_input_tokens = usage.input_tokens
        self.last_output_tokens = usage.output_tokens
        self.totals["input"] += usage.input_tokens
        self.totals["output"] += usage.output_tokens
        self.totals["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.totals["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        self._log_usage_totals()

    def _log_usage_totals(self):
        try:
            record = {"timestamp": time.time(), "type": "usage_totals",
                      "totals": dict(self.totals)}
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def set_model(self, model=None, effort=None):
        """Set the model and/or effort for subsequent turns. Unknown values are
        ignored. Returns the resulting {model, effort}."""
        if model in MODELS:
            self.model = model
        if effort in EFFORT_LEVELS:
            self.effort = effort
        return {"model": self.model, "effort": self.effort}

    def _effort_extra(self):
        """Extra request body for the effort parameter, or {} when it must not be
        sent — the model doesn't support output_config.effort (sending it 400s),
        or effort is the default 'high' (equivalent to omitting it)."""
        meta = MODELS.get(self.model, MODELS[DEFAULT_MODEL])
        if meta["effort"] and self.effort != "high":
            return {"extra_body": {"output_config": {"effort": self.effort}}}
        return {}

    def usage_summary(self):
        pricing = MODELS.get(self.model, MODELS[DEFAULT_MODEL])["pricing"]
        cost = (
            self.totals["input"] / 1e6 * pricing["input"]
            + self.totals["output"] / 1e6 * pricing["output"]
            + self.totals["cache_write"] / 1e6 * pricing["cache_write"]
            + self.totals["cache_read"] / 1e6 * pricing["cache_read"]
        )
        return {
            "model": self.model, "effort": self.effort, "tokens": dict(self.totals),
            "total_tokens": sum(self.totals.values()), "cost_usd": round(cost, 4),
        }

    def request_stop(self):
        self.stop_event.set()

    # ── core API loop (shared by run_turn / run_regenerate / run_edit) ──

    def _run_api_loop(self, handlers):
        def cb(name, *args, **kwargs):
            fn = handlers.get(name)
            return fn(*args, **kwargs) if fn else None

        system_prompt = build_system_prompt()

        if deter_compact(self.messages, "", self.last_input_tokens, self.last_output_tokens):
            self.messages = do_compact(self.messages)
            cb("on_compaction")

        while True:
            if self.stop_event.is_set():
                self._add_node("assistant", "[Generation stopped by user.]", self.current_leaf_id)
                break

            api_messages = _build_cached_api_messages(self.messages)

            accumulated = ""
            stopped_mid_stream = False
            with client.messages.stream(
                model=self.model, max_tokens=4096,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                tools=cached_tools, messages=api_messages,
                **self._effort_extra(),
            ) as stream:
                for text_chunk in stream.text_stream:
                    if self.stop_event.is_set():
                        stopped_mid_stream = True
                        break
                    accumulated += text_chunk
                    cb("on_text", text_chunk)

                if stopped_mid_stream:
                    txt = accumulated if accumulated.strip() else "[Generation stopped by user.]"
                    self._add_node("assistant", txt, self.current_leaf_id)
                    cb("on_assistant_done", txt)
                    break

                response = stream.get_final_message()

            self._add_node("assistant", response.content, self.current_leaf_id)
            self._record_usage(response.usage)
            cb("on_usage", self.usage_summary())

            if accumulated.strip():
                cb("on_assistant_done", accumulated)

            if response.stop_reason == "end_turn":
                break

            for block in response.content:
                if block.type == "tool_use":
                    cb("on_tool_use", block.id, block.name, block.input)

                    def _confirm(command, _cb=cb):
                        return _cb("on_dangerous", command)

                    if self.stop_event.is_set():
                        outcome = "[stopped by user — tool not run]"
                    else:
                        try:
                            outcome = execute_tool(
                                block.name, block.input, _confirm,
                                should_stop=lambda: self.stop_event.is_set(),
                            )
                        except CommandBlocked:
                            outcome = "[BLOCKED] User denied this dangerous command."
                    tool_ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                    self._add_node("user", [{
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": f"[{tool_ts}] {outcome}",
                    }], self.current_leaf_id)
                    cb("on_tool_result", block.id, outcome)

            if deter_compact(self.messages, "", self.last_input_tokens, self.last_output_tokens):
                self.messages = do_compact(self.messages)
                cb("on_compaction")

    # ── entry points ──

    def _claim_turn(self, cb):
        """Atomically take ownership of the agent for a turn. Returns False (and
        signals an error) if a turn is already running, so two concurrent turns
        can't mutate the same tree."""
        with self._turn_lock:
            if self.busy:
                cb("on_error", "Agent is busy with the current turn.")
                return False
            self.busy = True
            self.stop_event.clear()
            return True

    def run_turn(self, user_input, handlers):
        def cb(name, *args, **kwargs):
            fn = handlers.get(name)
            return fn(*args, **kwargs) if fn else None

        if not self._claim_turn(cb):
            return
        is_first_turn = len(self.nodes) == 0

        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            self._add_node("user", f"[{timestamp}] {user_input}", self.current_leaf_id)
            self._run_api_loop(handlers)

            if is_first_turn and self.filename not in load_titles():
                title = generate_title(self.messages)
                save_title(self.filename, title)
                cb("on_title", title)
        except Exception as e:
            cb("on_error", f"{type(e).__name__}: {e}")
        finally:
            self.busy = False

    def run_regenerate(self, handlers):
        def cb(name, *args, **kwargs):
            fn = handlers.get(name)
            return fn(*args, **kwargs) if fn else None

        if not self._claim_turn(cb):
            return
        try:
            user_nid = self._find_last_user_input()
            if not user_nid:
                cb("on_error", "No user message to regenerate from.")
                return
            branch_ids = self._branch_node_ids()
            if user_nid in branch_ids:
                user_pos = branch_ids.index(user_nid)
                for i in range(user_pos + 1, len(branch_ids)):
                    node = self.nodes.get(branch_ids[i])
                    if node and self._node_has_impact(node):
                        tool_names = [
                            b["name"] for b in node.get("content", [])
                            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in IMPACTFUL_TOOLS
                        ]
                        cb("on_error",
                           f"Cannot regenerate: the current response used {', '.join(tool_names)} "
                           f"which already changed local state.")
                        return
            self.current_leaf_id = user_nid
            self.messages = self._flatten_branch()
            self._run_api_loop(handlers)
        except Exception as e:
            cb("on_error", f"{type(e).__name__}: {e}")
        finally:
            self.busy = False

    def run_edit(self, node_id, new_content, handlers):
        def cb(name, *args, **kwargs):
            fn = handlers.get(name)
            return fn(*args, **kwargs) if fn else None

        if not self._claim_turn(cb):
            return
        try:
            target = self.nodes.get(node_id)
            if not target:
                cb("on_error", "Message not found.")
                return
            err = self._check_branch_safety(node_id)
            if err:
                cb("on_error", err)
                return
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            parent_id = target["parent_id"]
            self._add_node("user", f"[{timestamp}] {new_content}", parent_id)
            self.messages = self._flatten_branch()
            self._run_api_loop(handlers)
        except Exception as e:
            cb("on_error", f"{type(e).__name__}: {e}")
        finally:
            self.busy = False

    def switch_branch(self, node_id, direction):
        # Refuse while a turn is running: switching rewrites self.messages /
        # current_leaf_id, which the running turn is also reading/mutating.
        with self._turn_lock:
            if self.busy:
                return None
            siblings, idx = self._get_sibling_info(node_id)
            if direction == "prev" and idx > 0:
                new_sib = siblings[idx - 1]
            elif direction == "next" and idx < len(siblings) - 1:
                new_sib = siblings[idx + 1]
            else:
                return None
            leaf = self._find_deepest_leaf(new_sib)
            self.current_leaf_id = leaf
            self.messages = self._flatten_branch()
            self._log_branch_switch()
            return self.branch_display()

    def finalize(self):
        return save_session_tags(self.filename, self.messages)
