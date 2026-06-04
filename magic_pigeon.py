import anthropic
import os
import json
import subprocess
import glob as glob_module
import time
import re

client = anthropic.Anthropic()

# Paths for persistent storage
HISTORY_DIR = os.path.expanduser("~/.agent_history")
os.makedirs(HISTORY_DIR, exist_ok=True)
SESSION_ID = str(int(time.time()))
HISTORY_FILE = os.path.join(HISTORY_DIR, f"session_{SESSION_ID}.jsonl")
MEMORIES_FILE = os.path.join(HISTORY_DIR, "memories.json")
TAGS_FILE = os.path.join(HISTORY_DIR, "tags.json")

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
    with open(MEMORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)

def update_memory(action, content, index=None):
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
    """Load all session tags from disk."""
    if os.path.exists(TAGS_FILE):
        try:
            with open(TAGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_tags(tags):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=2)

def generate_session_tags(messages):
    """Generate English topic tags for the current session on exit."""
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
            model="claude-opus-4-6",
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

def save_current_session_tags(messages):
    """Generate and store tags for the current session."""
    if len(messages) < 2:
        return
    
    print("[generating session tags...]")
    tags_list = generate_session_tags(messages)
    
    if tags_list:
        all_tags = load_tags()
        session_filename = f"session_{SESSION_ID}.jsonl"
        all_tags[session_filename] = {
            "time": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "tags": tags_list
        }
        save_tags(all_tags)
        print(f"[saved {len(tags_list)} tags for this session]")

def format_tags_for_prompt():
    """Format recent session tags for system prompt injection."""
    all_tags = load_tags()
    if not all_tags:
        return ""
    
    lines = ["\n\n## Recent Chat Tags (topics from past sessions)\n"]

    sorted_sessions = sorted(
        all_tags.items(),
        key=lambda x: x[1].get("time", ""),
        reverse=True
    )

    for session_name, info in sorted_sessions[:50]:
        t = info.get("time", "unknown")
        tags = ", ".join(info.get("tags", []))
        lines.append(f"- [{t}] {session_name}: {tags}")
    
    return "\n".join(lines)

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

def log_to_disk(message):
    try:
        safe_msg = make_serializable(message)
        record = {"timestamp": time.time(), "message": safe_msg}
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

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
    """Search all session history by tags and raw conversation content."""
    keyword_list = keywords.lower().split()
    results = []
    matched_sessions = set()
    
    # Step 1: search tags
    all_tags = load_tags()
    for session_name, info in all_tags.items():
        tags_text = " ".join(info.get("tags", [])).lower()
        match_count = sum(1 for kw in keyword_list if kw in tags_text)
        if match_count > 0:
            matched_sessions.add(session_name)
            t = info.get("time", "unknown")
            tags_str = ", ".join(info.get("tags", []))
            results.append({
                "score": match_count + 5,  # tag matches weighted higher
                "session": session_name,
                "time": 0,
                "content": f"[TAG MATCH] [{t}] Tags: {tags_str}"
            })
    
    # Step 2: search raw content
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
                                    "content": readable[:500]
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

# ── Tool definitions ──

tools = [
    {
        "name": "read_file",
        "description": "open a file to read its contents",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "path of the file to read"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "edit",
        "description": "open a file to replace some of its contents",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "path of the file to edit"
                },
                "target_contents": {
                    "type": "string",
                    "description": "the target old contents of the file to be replaced"
                },
                "new_contents": {
                    "type": "string",
                    "description": "the new contents used to replace the target contents"
                }
            },
            "required": ["file_path", "target_contents", "new_contents"]
        }
    },
    {
        "name": "write_file",
        "description": "create a new file or overwrite an existing file with new contents",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "path of the file to write"
                },
                "contents": {
                    "type": "string",
                    "description": "the new contents of the file"
                }
            },
            "required": ["file_path", "contents"]
        }
    },
    {
        "name": "general_bash",
        "description": "run necessary bash code to solve issues progressively",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "use shell code to collect essential info, plan steps, and take direct actions in directories"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "grep",
        "description": "search for a pattern across all files in a directory recursively",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "directory to search"
                },
                "content": {
                    "type": "string",
                    "description": "the target pattern to find"
                }
            },
            "required": ["directory", "content"]
        }
    },
    {
        "name": "glob",
        "description": "use file name patterns to search for files in a directory",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "the directory to search in"
                },
                "names": {
                    "type": "string",
                    "description": "the glob pattern to match (e.g. *.py, **/*.ts)"
                }
            },
            "required": ["file_path", "names"]
        }
    },
    {
        "name": "search_history",
        "description": "Search through conversation history across all sessions. This searches BOTH session tags (lightweight topic labels) AND full raw conversation content. Use this when the user asks about something discussed earlier. Generate your own search keywords — do NOT just pass the user's words directly. Use multiple synonyms and related terms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": "space-separated search keywords. Generate these yourself based on what you think the relevant conversation contained. Use ENGLISH keywords to match tags, and also include Chinese/original-language keywords to match raw conversation content. Use multiple synonyms to maximize recall."
                }
            },
            "required": ["keywords"]
        }
    },
    {
        "name": "update_memory",
        "description": "Add, delete, or list persistent memories that survive across sessions. These memories are injected into EVERY future session's system prompt, so only save things that are genuinely important across all conversations. SAVE: user identity (name, role, location), long-term preferences (language, coding style), important project context (tech stack, architecture decisions), corrections to your understanding. DO NOT SAVE: temporary task details, one-off questions, trivial preferences, anything only relevant to the current conversation. EXCEPTION: if the user explicitly says 'remember this' or asks you to save something, ALWAYS save it regardless of importance. Keep each memory concise — one fact per entry. When information changes, delete the old memory and add the updated one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "delete", "list"],
                    "description": "add: save a new memory. delete: remove a memory by index. list: show all current memories."
                },
                "content": {
                    "type": "string",
                    "description": "the fact to remember (for add action). Keep it concise, one fact per memory."
                },
                "index": {
                    "type": "integer",
                    "description": "index of the memory to delete (for delete action, 0-based)"
                }
            },
            "required": ["action"]
        }
    }
]

# ── Safety and backup ──

DANGEROUS_PATTERNS = [
    "rm -rf /", "rm -rf ~", "rm -rf /*", "rm -rf ~/*",
    "rm -r /", "rm -r ~", "rm -r /*",
    "chmod -R 777 /", "chmod -R 000",
    "mkfs.", "dd if=", "> /dev/sd", "> /dev/disk",
    ":(){ :|:& };:",  # fork bomb
    "mv / ", "mv /* ",
    "wget -O- | sh", "curl | sh", "curl | bash",
    "shutdown", "reboot", "init 0", "init 6",
    "passwd", "useradd", "userdel",
]

def is_dangerous(command):

    cmd_lower = command.lower().strip()
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in cmd_lower:
            return True
    return False

def git_snapshot(label="auto-snapshot"):
    """Auto-commit current state before write operations for rollback capability."""
    cwd = os.getcwd()

    check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True, cwd=cwd)
    if check.returncode != 0:

        subprocess.run(["git", "init"], capture_output=True, cwd=cwd)
        subprocess.run(["git", "add", "-A"], capture_output=True, cwd=cwd)
        subprocess.run(["git", "commit", "-m", "initial snapshot before agent modifications"],
                      capture_output=True, cwd=cwd)

    subprocess.run(["git", "add", "-A"], capture_output=True, cwd=cwd)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    subprocess.run(
        ["git", "commit", "-m", f"[agent-{label}] {timestamp}", "--allow-empty-message"],
        capture_output=True, cwd=cwd
    )

# ── Tool execution ──

class CommandBlocked(Exception):
    pass
    pass

def execute_tool(name, arguments):
    try:
        if name == "read_file":
            with open(arguments["file_path"], "r", encoding="utf-8") as f:
                return f.read()

        elif name == "general_bash":
            command = arguments["command"]
            if is_dangerous(command):
                print(f"\033[1;37;41m  ⚠️ BLOCKED: {command}  \033[0m")
                confirm = input("\033[1;31m  This command is flagged as dangerous. Allow? (y/n): \033[0m")
                if confirm.lower() != "y":
                    raise CommandBlocked("User denied dangerous command.")
            git_snapshot("pre-bash")
            outcome = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=600)
            return outcome.stdout + outcome.stderr

        elif name == "edit":
            git_snapshot("pre-edit")
            with open(arguments["file_path"], "r", encoding="utf-8") as f:
                content = f.read()
            newcon = content.replace(arguments["target_contents"], arguments["new_contents"])
            with open(arguments["file_path"], "w", encoding="utf-8") as f:
                f.write(newcon)
            return "File edited successfully."

        elif name == "write_file":
            git_snapshot("pre-write")
            with open(arguments["file_path"], "w", encoding="utf-8") as f:
                f.write(arguments["contents"])
            return "File written successfully."

        elif name == "grep":
            outcome = subprocess.run(
                ["grep", "-rn", arguments["content"], arguments["directory"]],
                capture_output=True, text=True, timeout=30
            )
            return outcome.stdout + outcome.stderr

        elif name == "glob":
            pattern = os.path.join(arguments["file_path"], arguments["names"])
            matches = glob_module.glob(pattern, recursive=True)
            if matches:
                return "\n".join(matches)
            else:
                return "No files found matching pattern."

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
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 10 minutes."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"

# ── Message logging ──

def append_message(messages, msg):
    messages.append(msg)
    log_to_disk(msg)

# ── Context management ──

CONTEXT_WINDOW = 1000000
MAX_OUTPUT = 128000

last_input_tokens = 0
last_output_tokens = 0

def estimate_messages_tokens(messages):
    return len(json.dumps(messages, ensure_ascii=False)) // 3

def deter_compact(messages, new_input=""):
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

    # Find where the last 4 rounds start.
    # A "round" begins with a user message that is NOT a tool_result.
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

    # Find where the last 20000 tokens start
    token_count = 0
    cut_by_tokens = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        token_count += len(json.dumps(messages[i], ensure_ascii=False)) // 3
        if token_count >= 20000:
            cut_by_tokens = i
            break

    # Take whichever keeps more (smaller index = more kept)
    raw_cut = min(cut_by_rounds, cut_by_tokens)

    cut = find_safe_cut(messages, raw_cut)
    old = messages[:cut]
    keep = messages[cut:]

    if len(old) < 2:
        return messages

    summary_messages = list(old)
    summary_messages.append({
        "role": "user",
        "content": "Summarize the conversation above concisely. Preserve key information: what was done, which files were involved, and what to do next."
    })

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
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
You have access to an update_memory tool to save important facts about the user across sessions. Only use it for genuinely important cross-session information (identity, preferences, key project context), or when the user explicitly asks you to remember something."""
    base += format_memories_for_prompt()
    base += format_tags_for_prompt()
    return base

system_prompt = build_system_prompt()

# ── Exit handler ──

def on_exit(messages):

    save_current_session_tags(messages)

# ── Main loop ──

# Cache tools: mark last tool for prompt caching (90% input cost reduction)
import copy
cached_tools = copy.deepcopy(tools)
cached_tools[-1]["cache_control"] = {"type": "ephemeral"}

messages = []

while True:
    try:
        user_input = input("u : ")
    except (KeyboardInterrupt, EOFError):
        print("\n[saving session...]")
        on_exit(messages)
        print("[goodbye]")
        break

    if user_input == "/exit":
        print("[saving session...]")
        on_exit(messages)
        print("[goodbye]")
        break

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    append_message(messages, {"role": "user", "content": f"[{timestamp}] {user_input}"})

    system_prompt = build_system_prompt()

    if deter_compact(messages, user_input):
        messages = do_compact(messages)
        print("[context compressed]")

    try:
        while True:
            # Add cache breakpoint on second-to-last message
            api_messages = []
            for idx, msg in enumerate(messages):
                m = dict(msg)

                if idx == len(messages) - 2 and len(messages) >= 2:
                    content = m.get("content", "")
                    if isinstance(content, str):

                        m["content"] = [{
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"}
                        }]
                    elif isinstance(content, list) and len(content) > 0:

                        last_block = content[-1]
                        if isinstance(last_block, dict):
                            last_block = dict(last_block)
                            last_block["cache_control"] = {"type": "ephemeral"}
                            m["content"] = list(content[:-1]) + [last_block]
                api_messages.append(m)

            # Stream response line by line
            line_buffer = ""
            first_line = True
            with client.messages.stream(
                model="claude-opus-4-6",
                max_tokens=4096,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }],
                tools=cached_tools,
                messages=api_messages,
            ) as stream:
                for text_chunk in stream.text_stream:
                    line_buffer += text_chunk
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        if first_line:
                            print("magic-pigeon: " + line)
                            first_line = False
                        else:
                            print(line)
                        time.sleep(0.05)
            if line_buffer:
                if first_line:
                    print("magic-pigeon: " + line_buffer)
                else:
                    print(line_buffer)

            response = stream.get_final_message()

            append_message(messages, {"role": "assistant", "content": response.content})

            last_input_tokens = response.usage.input_tokens
            last_output_tokens = response.usage.output_tokens

            if response.stop_reason == "end_turn":
                break
            else:
                for i in response.content:
                    if i.type == "tool_use":
                        outcome = execute_tool(i.name, i.input)
                        tool_timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                        append_message(messages, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": i.id, "content": f"[{tool_timestamp}] {outcome}"}]})

                if deter_compact(messages):
                    messages = do_compact(messages)
                    print("[context compressed]")

    except CommandBlocked:
        print("\033[1;33m[agent halted — waiting for new input]\033[0m")
    except KeyboardInterrupt:
        print("\n[interrupted]")
