"""JARVIS's hands for your connected services (Gmail, Google Drive, Shopify, ... and any you add):
Claude Code with YOUR OWN Claude login and connectors, started for one task at a time.

Why it is started lean: with all ~130 services this PC's Claude has, a single task came to 600,000+
tokens and failed ("Prompt is too long"). So for JARVIS the services listed by `claude mcp list` are turned
off (a settings file just for this, nothing in your own settings changes) except those named in
`jarvis_tools`, and of the connectors your account brings itself (Gmail, Google Drive, Shopify here) only
the ones the task needs are on (see ACCOUNT below). Measured: a Gmail question ~6 s, ~39,000 tokens.

Safety: Claude Code's own tools (shell, files) are off. Every service call that is not a plain look-up goes
through approve_server.py, which makes JARVIS ask you out loud first.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .brain import find_claude
from .speech_text import T, language_name

FOLDER = Path.home() / ".sensor-os" / "jarvis-agent"
PROMPT = """You are the hands of JARVIS, a voice assistant on the user's PC. Do the task below with your tools
(the user's connected services). Rules:
- Do only what the task asks. Look things up freely. Sending, creating, changing, deleting or buying is checked
  with the user automatically when you call the tool: just call it.
- Your final answer is spoken aloud: {language} (or the task's language), 1-2 short sentences, no markdown, no lists,
  no links, no IDs. Summarize; never read long text out. Numbers and times as spoken words are fine.
- If a service is missing or not signed in, say so in one sentence."""


# The connectors your claude.ai account brings itself (not in `claude mcp list`), the words that mean a task
# needs one, and tools left out to keep it small. Measured with one Gmail question: everything Claude Code
# loads = ~155,000 tokens a task; only Gmail = ~70,000; Gmail cut to the tools below + no skills, no hooks
# and a short system prompt = ~39,000, of which ~1,600 new (the rest read back from Anthropic's cache).
ACCOUNT = {
    "Gmail": ("claude.ai Gmail", ("gmail", "email", "e-mail", "mail", "อีเมล", "อีเมล์", "เมล", "จดหมาย", "inbox"),
              ("apply_sensitive_message_label apply_sensitive_thread_label create_label delete_draft delete_label "
               "download_gmail_attachment get_draft label_message label_thread list_labels mark_message_spam "
               "mark_thread_spam trash_message unlabel_message unlabel_thread unmark_message_spam unmark_thread_spam "
               "untrash_message untrash_thread update_draft update_label update_message_labels").split()),
    "Google Drive": ("claude.ai Google Drive", ("drive", "ไดรฟ์", "ไดร์ฟ", "ไฟล์", "เอกสาร", "doc", "sheet", "slide",
                                                 "สเปรดชีต", "สไลด์"), ()),
    "Shopify": ("claude.ai Shopify", ("shopify", "ร้าน", "ออเดอร์", "order", "สินค้า", "product", "ลูกค้า", "customer"), ()),
}


def pick_services(task: str, extras=()) -> list:
    """Which services a task needs, from its words ("Gmail: ..." from JARVIS, or "email" / "อีเมล" in it).
    Nothing recognised: Gmail and Drive, the likeliest."""
    t = task.lower()
    picked = [name for name, (_s, words, _d) in ACCOUNT.items() if name.lower() in t or any(w in t for w in words)]
    picked += [x for x in extras if x.lower() in t]
    return picked or ["Gmail", "Google Drive"]


def describe_tool(tool: str, args: dict) -> str:
    """What to ask the user out loud: "Gmail: send_message (to: a@b.com, subject: ...)"."""
    parts = tool.split("__")
    service = re.sub(r"^(claude_ai_|plugin_[^_]+_)", "", parts[1]) if len(parts) > 2 else tool
    action = parts[-1].replace("_", " ").replace("-", " ")
    bits = []
    for key in ("to", "recipient", "recipients", "subject", "title", "name", "summary", "query", "amount"):
        if key in args and args[key]:
            v = args[key]
            v = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
            bits.append(f"{key} {v[:60]}")
    return f"{service.replace('_', ' ')} {action}" + (f" ({'; '.join(bits[:3])})" if bits else "")


class ToolAgent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.claude = find_claude(getattr(cfg, "jarvis_claude_path", ""))
        self.folder = FOLDER
        self.folder.mkdir(parents=True, exist_ok=True)
        self.servers_file = self.folder / "servers.json"
        self.last_seconds = None
        self._listing = None

    def available(self) -> tuple:
        if not self.claude:
            return False, T("no_claude")
        return True, ""

    # ---------------------------------------------------------------- which services stay off
    def refresh_servers(self) -> None:
        """`claude mcp list` takes ~25 s, so it runs in the background at start; the last list is kept."""
        if not self.claude or (self._listing and self._listing.is_alive()):
            return

        def run():
            try:
                out = subprocess.run([self.claude, "mcp", "list"], capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", cwd=self.folder, timeout=120,
                                     creationflags=0x08000000 if os.name == "nt" else 0).stdout
            except (OSError, subprocess.TimeoutExpired):
                return
            names = server_names(out)
            if names:
                tmp = self.servers_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self.servers_file)

        self._listing = threading.Thread(target=run, daemon=True, name="jarvis-mcp-list")
        self._listing.start()

    def _servers(self) -> list:
        if not self.servers_file.exists() and self._listing is not None:
            self._listing.join(timeout=60)                   # the very first time: wait for the list
        try:
            return json.loads(self.servers_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _files(self, picked: list) -> tuple:
        keep = {s.lower() for s in picked}
        deny = [n for n in self._servers() if n.lower() not in keep and n.lower().removeprefix("claude.ai ") not in keep]
        deny += [server for name, (server, _w, _d) in ACCOUNT.items() if name not in picked]
        settings = self.folder / "settings.json"
        settings.write_text(json.dumps({"deniedMcpServers": [{"serverName": n} for n in deny],
                                        "disableAllHooks": True}, ensure_ascii=False), encoding="utf-8")
        mcp = self.folder / "mcp.json"
        server = str(Path(__file__).with_name("approve_server.py"))
        mcp.write_text(json.dumps({"mcpServers": {"jarvis": {"command": sys.executable, "args": ["-I", server, str(self.folder)]}}}),
                       encoding="utf-8")
        return settings, mcp

    # ---------------------------------------------------------------- one task
    def run(self, task: str, ask) -> str:
        """Do the task; returns what to say. `ask(question) -> bool` asks the user out loud (from this thread)."""
        ok, why = self.available()
        if not ok:
            return why
        extras = [str(x).strip() for x in (getattr(self.cfg, "jarvis_tools", ()) or ()) if str(x).strip()]
        picked = pick_services(task, extras)
        settings, mcp = self._files(picked)
        for name in ("approve_req.json", "approve_resp.json"):
            try:
                (self.folder / name).unlink()
            except OSError:
                pass
        drop = [f"mcp__claude_ai_{name.replace(' ', '_')}__{tool}" for name in picked if name in ACCOUNT
                for tool in ACCOUNT[name][2]]
        # No skills, no hooks, no Claude Code instructions of its own: only what the task needs is sent.
        args = [self.claude, "-p", task, "--tools", "", "--output-format", "json", "--no-session-persistence",
                "--model", getattr(self.cfg, "jarvis_tools_model", "haiku") or "haiku",
                "--settings", str(settings), "--mcp-config", str(mcp), "--disable-slash-commands",
                "--permission-prompt-tool", "mcp__jarvis__approve", "--system-prompt", PROMPT.format(language=language_name())]
        if drop:
            args += ["--disallowedTools", *drop]
        print(f"JARVIS tools: using {', '.join(picked)}", flush=True)
        t = time.perf_counter()
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             errors="replace", cwd=self.folder, creationflags=0x08000000 if os.name == "nt" else 0)
        out = {}
        reader = threading.Thread(target=lambda: out.update(zip(("stdout", "stderr"), p.communicate())), daemon=True)
        reader.start()
        req = self.folder / "approve_req.json"
        limit = float(getattr(self.cfg, "jarvis_tools_timeout", 150.0))
        while reader.is_alive():
            if req.exists():
                try:
                    q = json.loads(req.read_text(encoding="utf-8"))
                    req.unlink()
                except (OSError, ValueError):
                    time.sleep(0.1)
                    continue
                allow = bool(ask(describe_tool(q.get("tool", ""), q.get("input") or {})))
                tmp = self.folder / "approve_resp.tmp"
                tmp.write_text(json.dumps({"id": q.get("id"), "allow": allow}), encoding="utf-8")
                os.replace(tmp, self.folder / "approve_resp.json")
            if time.perf_counter() - t > limit:
                p.kill()
                return T("tools_too_long")
            reader.join(0.2)
        self.last_seconds = time.perf_counter() - t
        try:
            data = json.loads(out.get("stdout") or "")
        except ValueError:
            tail = (out.get("stderr") or out.get("stdout") or "").strip().splitlines()[-1:] or ["?"]
            return T("tools_call_failed", err=tail[0][:100])
        u = data.get("usage") or {}
        new = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        print(f"JARVIS tools: {self.last_seconds:.1f} s, {new + (u.get('cache_read_input_tokens') or 0)} tokens "
              f"({new} new, {u.get('cache_read_input_tokens') or 0} from cache), {data.get('num_turns')} turns", flush=True)
        if data.get("is_error") or "Prompt is too long" in str(data.get("result")):
            return T("tools_too_many")
        return str(data.get("result") or T("done")).strip()


def server_names(listing: str) -> list:
    """Names from `claude mcp list`: "claude.ai Notion: https://... - ✓ Connected" -> "claude.ai Notion"."""
    names = []
    for line in listing.splitlines():
        m = re.match(r"^(claude\.ai [^:]+|plugin:[^:]+:[^:]+|[\w.@-]+): ", line)
        if m and m.group(1) != "jarvis":
            names.append(m.group(1))
    return names
