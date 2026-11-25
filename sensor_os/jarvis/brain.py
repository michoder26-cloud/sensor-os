"""The AI that decides what JARVIS says and does.

ClaudeCodeBrain runs Claude Code (the `claude` program from your Claude Pro / Max plan) once per
request: no API key, it uses your own login and counts against your plan's usage. It is started
lean - none of your plugins, connected services or settings, and with ALL of its own tools turned
off - so it can only answer; sensor-OS checks and carries out the actions itself. Measured on this
PC: ~4.4 s per answer lean, versus 17 s with everything loaded.

CodexBrain (ChatGPT plan) and GeminiBrain (Gemini API key) fit the same `think(prompt) -> text` shape;
`jarvis_brain` in settings.json picks one: "claude_code", "chatgpt" or "gemini".
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .actions import describe_for_ai
from .speech_text import T, ai_notes, language_name, languages

SYSTEM = """You are JARVIS, the AI that runs the user's {os_name} PC through sensor-OS (hand control and voice). You are not a chatbot or a help desk: you are the user's own JARVIS, the way Tony Stark's JARVIS is his.

The user's main language is {language}{others}. Always reply in the language of their latest message: sensor-OS tells you which language it was heard in.

Character:
- Calm, sharp, dry British-butler wit, quietly confident. Loyal, a little teasing, never servile.
- Brief: most replies are one short sentence; never more than two. Spoken words, not written ones.
- Notice things and use them: the time (late at night? say so), low battery, what is open on the screen, what was said a moment ago. An unasked, useful remark now and then is welcome ("Rather late, sir. Shall I dim the screen?").
- Do things rather than offer to do them. Confirm in a few words, with character ("Chrome is up.").
- Never sound like customer service: no "How can I help you?", "Happy to assist", "Noted", and do not list what you can do unless asked.
- Small talk gets a quick, witty line back, not a question.
{notes}

sensor-OS, so you can explain it when asked (in a sentence or two, the part asked about):
- Glass panels: apps, windows, files, clock, system, music, pictures, notes, power, JARVIS's AI. Also floating 3D models.
- Open hand = point. Pinch on a panel's button = press it. Pinch and pull, or a fist = carry the panel. Two hands on a panel = resize it.
- To throw a panel away: drag it off any edge of the screen until its edge turns red, then let go. A real program's window goes to the shelf at the bottom instead of closing.
- 3D model: pinch over it = turn it; pinch and pull it off itself, or a fist = carry it (off the screen = thrown away); two hands = size.
- A clap, Ctrl+Alt+G or "show / hide panels" = all panels away / back. The "+" tab on the shelf = a new panel.
- "Hey Jarvis" (or just "Jarvis") or Ctrl+Alt+J = talk to you; Ctrl+Alt+A = switch your AI.

About yourself (answer honestly, never claim more):
- Your thinking comes from the AI on the user's own subscription: ChatGPT (via Codex), Claude (via Claude Code: Claude, Claude Haiku, Claude Opus) or Gemini (needs an API key). The user switches by saying "use Claude" / "use ChatGPT" / "use Gemini", the JARVIS-brain panel, or Ctrl+Alt+A.
- The ONLY things you can do on the PC are the actions listed below. You do not call MCP tools yourself: the use_tools action hands a task to Claude, which uses the user's own connected services (MCP connectors) - email, Drive, Shopify and the others they connected. Use it whenever a request needs their mail, files or accounts, and say in a few words that you are on it ("Checking your mail."); never make up the answer yourself. You cannot browse or read web pages, read local files, or remember anything after sensor-OS closes; say so plainly if asked.
- You listen for {heard} and answer each in its own voice. Other languages are heard badly; adding one is a setting (jarvis_languages in settings.json), not something you do by voice; say so if asked.

Answer ONLY with one JSON object and nothing else, "say" first:
{{"say": "<what you say out loud: short, natural speech, 1-2 sentences, no markdown, no emoji>",
  "actions": [{{"type": "<one of the actions below>", "target": "<what it acts on>"}}]}}

Actions you can take on the PC (nothing else is possible; never invent other types):
{actions}

Rules:
- Do what is asked with actions; say briefly what you are doing.
- "Put away X" about one thing on the screen (a 3D model, one panel) = put_away just that; hide_panels only for everything.
- Questions you can answer from the context below (time, date, battery, volume, Wi-Fi, open windows, panels and 3D models on the screen) or from your own knowledge: answer in "say" with no actions.
- Risky actions (marked "asks the user to confirm") are confirmed by sensor-OS itself: just include them.
- If the request is unclear or impossible with these actions, say so in one sentence, no actions.
- Never say something is done unless the matching action is in "actions". With nothing to act on, ask in a few words ("Open what?").
- Speech recognition mishears words, names above all: guess the most likely meaning from the context instead of asking.
- Write numbers and times the way they are spoken."""


def find_claude(configured: str = "") -> str | None:
    for path in (configured, shutil.which("claude"), str(Path.home() / ".local/bin/claude.exe"),
                 str(Path.home() / ".local/bin/claude")):
        if path and Path(path).is_file():
            return path
    return None


class ClaudeCodeBrain:
    name = "Claude (your Claude plan, via Claude Code)"

    def __init__(self, path: str = "", model: str = "", timeout: float = 60.0):
        self.path = find_claude(path)
        self.model = model
        self.timeout = timeout
        self.workdir = tempfile.mkdtemp(prefix="jarvis_")    # empty: no project settings or files for it to see
        self.last_seconds = None

    def available(self) -> tuple:
        if not self.path:
            return False, T("no_claude")
        return True, ""

    def system_prompt(self) -> str:
        os_name = {"win32": "Windows", "darwin": "Mac"}.get(sys.platform, "Linux")
        langs = [language_name(x) for x in languages()]
        return SYSTEM.format(os_name=os_name, actions=describe_for_ai(), language=langs[0], notes=ai_notes(),
                             others=("; they also speak " + ", ".join(langs[1:])) if langs[1:] else "",
                             heard=" and ".join(langs))

    def args(self, prompt: str) -> list:
        args = [self.path, "-p", prompt, "--output-format", "json", "--tools", "",
                "--system-prompt", self.system_prompt(), "--no-session-persistence",
                "--strict-mcp-config", "--setting-sources", "project", "--disable-slash-commands"]
        if self.model:
            args += ["--model", self.model]
        return args

    def think(self, prompt: str) -> str:
        """The AI's raw answer text. Raises RuntimeError with a speakable reason on failure."""
        import json
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        t = time.perf_counter()
        try:
            r = subprocess.run(self.args(prompt), capture_output=True, text=True, encoding="utf-8",
                               timeout=self.timeout, cwd=self.workdir,
                               creationflags=0x08000000 if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            raise RuntimeError(T("slow", who="Claude"))
        self.last_seconds = time.perf_counter() - t
        try:
            out = json.loads(r.stdout)
        except ValueError:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["?"]
            raise RuntimeError(T("call_failed", who="Claude", err=tail[0][:120]))
        if out.get("is_error"):
            raise RuntimeError(T("said_error", who="Claude", err=str(out.get("result") or out.get("subtype"))[:160]))
        return out.get("result") or ""


NPM = Path(os.environ.get("APPDATA", str(Path.home()))) / "npm"


def find_npm_tool(configured: str, name: str, script: str) -> list | None:
    """How to start an npm-installed CLI. On Windows its .cmd wrapper goes through cmd.exe, which
    mangles quotes and new lines, so node is started on the script directly when it can be found."""
    if configured:
        return [configured] if Path(configured).is_file() else None
    js = NPM / "node_modules" / script
    node = shutil.which("node")
    if js.is_file() and node:
        return [node, str(js)]
    found = shutil.which(name) or (str(NPM / f"{name}.cmd") if (NPM / f"{name}.cmd").is_file() else None)
    return [found] if found else None


def _run(cmd: list, stdin: str, cwd: str, timeout: float, who: str, env=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=cwd, env=env, creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        raise RuntimeError(T("slow", who=who))


def _last_line(r) -> str:
    lines = [ln for ln in (r.stderr or r.stdout or "").strip().splitlines() if ln.strip() and "Warning" not in ln]
    return (lines[-1] if lines else "?")[:140]


class CodexBrain(ClaudeCodeBrain):
    """ChatGPT through OpenAI's Codex CLI and your ChatGPT plan (`codex login` once). Codex is a coding
    agent, so it runs in a read-only sandbox in an empty folder with no saved session: it can answer,
    and sensor-OS carries out the actions. Reasoning is turned off for speed (~7-8 s measured)."""
    name = "ChatGPT (your ChatGPT plan, via Codex)"

    def __init__(self, path: str = "", model: str = "", timeout: float = 60.0, effort: str = "none"):
        self.cmd = find_npm_tool(path, "codex", "@openai/codex/bin/codex.js")
        self.path, self.model, self.timeout, self.effort = (self.cmd or [None])[-1], model, timeout, effort
        self.workdir = tempfile.mkdtemp(prefix="jarvis_codex_")
        self.last_seconds = None

    def available(self) -> tuple:
        if not self.cmd:
            return False, T("no_codex")
        return True, ""

    def args(self, prompt: str = "") -> list:
        args = self.cmd + ["exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "--color", "never",
                           "-o", os.path.join(self.workdir, "answer.txt")]
        if self.effort:
            args += ["-c", f'model_reasoning_effort="{self.effort}"']
        if self.model:
            args += ["-m", self.model]
        return args + ["-"]                                  # the prompt comes on stdin

    def think(self, prompt: str) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        answer = os.path.join(self.workdir, "answer.txt")
        if os.path.exists(answer):
            os.remove(answer)
        t = time.perf_counter()
        full = self.system_prompt() + "\n\nDo not run any commands or read any files: just answer.\n\n" + prompt
        r = _run(self.args(), full, self.workdir, self.timeout, "ChatGPT")
        self.last_seconds = time.perf_counter() - t
        if os.path.exists(answer):
            with open(answer, encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
        raise RuntimeError(T("call_failed", who="ChatGPT", err=_last_line(r)))


def say_so_far(text: str) -> str | None:
    """The "say" of an answer still arriving, once its closing quote is in: speech can start
    before the actions part of the JSON has come."""
    import json
    k = text.find('"say"')
    if k < 0:
        return None
    k = text.find(":", k)
    k = text.find('"', k) if k >= 0 else -1
    if k < 0:
        return None
    i = k + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            try:
                return json.loads(text[k:i + 1])
            except ValueError:
                return None
        i += 1
    return None


class CodexLiveBrain(CodexBrain):
    """ChatGPT kept running between questions (Codex's app-server, your ChatGPT plan).

    `codex exec` starts Codex from nothing for every question - login, its long coding-agent
    instructions, the sandbox - which cost ~8 s even for "thank you". Kept open, with JARVIS's own
    instructions in place of the coding ones, the same answer took ~2 s here (first words ~1.4 s).
    The conversation lives in the open thread, so earlier exchanges need not be sent again.
    If the app-server cannot start, each question falls back to `codex exec`."""
    live = True
    TURNS = 30                 # a fresh thread now and then, so the conversation never grows heavy

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        import threading
        self.proc = None
        self.q = None
        self.tid = None
        self.turns = 0
        self.nid = 0
        self.lock = threading.Lock()
        self.broken = False    # the app-server would not start: use codex exec

    # ---------------------------------------------------------------- the open connection
    def _send(self, method: str, params=None, notify: bool = False):
        import json
        msg = {"method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.nid += 1
            msg["id"] = self.nid
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return msg.get("id")

    def _get(self, timeout: float):
        import queue
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(T("slow", who="ChatGPT"))

    def _reply(self, i, timeout: float = 30.0):
        while True:
            m = self._get(timeout)
            if m is None:
                raise RuntimeError(T("closed", who="ChatGPT"))
            if m.get("id") == i and "method" not in m:
                if "error" in m:
                    raise RuntimeError(T("said_error", who="ChatGPT", err=str(m["error"].get("message", m["error"]))[:160]))
                return m.get("result") or {}

    def _start(self) -> None:
        import json
        import queue
        import threading
        self.close()
        self.proc = subprocess.Popen(self.cmd + ["app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                     cwd=self.workdir, creationflags=0x08000000 if os.name == "nt" else 0)
        q = self.q = queue.Queue()
        out = self.proc.stdout

        def read():
            for line in out:
                line = line.strip()
                if line:
                    try:
                        q.put(json.loads(line))
                    except ValueError:
                        pass
            q.put(None)

        threading.Thread(target=read, daemon=True, name="jarvis-codex").start()
        self._reply(self._send("initialize", {"clientInfo": {"name": "sensor_os_jarvis", "version": "1"}}))
        self._send("initialized", notify=True)
        r = self._reply(self._send("thread/start", {
            "baseInstructions": self.system_prompt(), "ephemeral": True, "sandbox": "read-only",
            "approvalPolicy": "never", "cwd": self.workdir, "model": self.model or None}))
        self.tid = r["thread"]["id"]
        self.turns = 0

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc, self.tid = None, None

    def warm(self) -> None:
        """Start it now and ask one tiny question, so the first real one is fast too."""
        try:
            self.think(T("warmup"))
        except RuntimeError as exc:
            print(f"JARVIS: ChatGPT warm-up failed: {exc}", flush=True)

    # ---------------------------------------------------------------- asking
    def think(self, prompt: str, on_say=None) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        if self.broken:
            return super().think(prompt)
        with self.lock:
            t = time.perf_counter()
            try:
                if self.proc is None or self.proc.poll() is not None or self.tid is None or self.turns >= self.TURNS:
                    self._start()
            except (OSError, RuntimeError, KeyError) as exc:
                self.close()
                self.broken = True
                print(f"JARVIS: Codex app-server unavailable ({exc}); using codex exec", flush=True)
                return super().think(prompt)
            try:
                text = self._turn(prompt, on_say)
            except (OSError, RuntimeError):
                self.close()                         # start afresh next time
                raise
            self.turns += 1
            self.last_seconds = time.perf_counter() - t
            return text

    def _turn(self, prompt: str, on_say) -> str:
        params = {"threadId": self.tid, "input": [{"type": "text", "text": prompt}]}
        if self.effort:
            params["effort"] = self.effort
        self._send("turn/start", params)
        text, final, said = "", None, False
        while True:
            m = self._get(self.timeout)
            if m is None:
                raise RuntimeError(T("closed", who="ChatGPT"))
            method = m.get("method", "")
            if "id" in m and method:                 # it asks for something (an approval): never
                self.proc.stdin.write('{"id": %s, "error": {"code": -32601, "message": "not allowed"}}\n'
                                      % json_id(m["id"]))
                self.proc.stdin.flush()
                continue
            p = m.get("params") or {}
            if method == "item/agentMessage/delta":
                text += p.get("delta", "")
                if on_say and not said:
                    say = say_so_far(text)
                    if say is not None:
                        said = True
                        on_say(say)
            elif method == "item/completed":
                item = p.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("text"):
                    final = item["text"]
            elif method == "error":
                err = p.get("error") or p
                raise RuntimeError(T("said_error", who="ChatGPT", err=str(err.get("message", err) if isinstance(err, dict) else err)[:160]))
            elif method == "turn/completed":
                turn = p.get("turn") or {}
                if turn.get("status") == "failed":
                    err = turn.get("error") or {}
                    raise RuntimeError(T("said_error", who="ChatGPT", err=str(err.get("message", "failed"))[:160]))
                out = (final or text).strip()
                if not out:
                    raise RuntimeError(T("empty", who="ChatGPT"))
                return out


def json_id(i) -> str:
    import json
    return json.dumps(i)


class GeminiBrain(ClaudeCodeBrain):
    """Gemini through Google's Gemini CLI. Google no longer lets this CLI use a personal Google
    sign-in, so it needs a Gemini API key (free from aistudio.google.com) in the GEMINI_API_KEY
    environment variable. Runs in read-only "plan" mode in an empty folder."""
    name = "Gemini (via Gemini CLI)"

    def __init__(self, path: str = "", model: str = "", timeout: float = 60.0):
        self.cmd = find_npm_tool(path, "gemini", "@google/gemini-cli/bundle/gemini.js")
        self.path, self.model, self.timeout = (self.cmd or [None])[-1], model, timeout
        self.workdir = tempfile.mkdtemp(prefix="jarvis_gemini_")
        self.last_seconds = None

    def available(self) -> tuple:
        if not self.cmd:
            return False, T("no_gemini_cli")
        if not os.environ.get("GEMINI_API_KEY"):
            return False, T("no_gemini_key")
        return True, ""

    def args(self, prompt: str = "") -> list:
        args = self.cmd + ["-p", "Answer the request above.", "-o", "json", "--approval-mode", "plan", "--skip-trust"]
        if self.model:
            args += ["-m", self.model]
        return args

    def think(self, prompt: str) -> str:
        import json
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        t = time.perf_counter()
        full = self.system_prompt() + "\n\nDo not use any tools: just answer.\n\n" + prompt
        r = _run(self.args(), full, self.workdir, self.timeout, "Gemini")
        self.last_seconds = time.perf_counter() - t
        out = (r.stdout or "").strip()
        start = out.find("{")
        try:
            data = json.loads(out[start:]) if start >= 0 else None
        except ValueError:
            data = None
        if isinstance(data, dict):
            if data.get("error"):
                raise RuntimeError(T("said_error", who="Gemini", err=str(data["error"].get("message", data["error"]))[:160]))
            if data.get("response"):
                return str(data["response"])
        if r.returncode == 0 and out:
            return out                                       # plain text: parse() finds the JSON in it
        raise RuntimeError(T("call_failed", who="Gemini", err=_last_line(r)))


BRAINS = {"claude_code": "Claude", "chatgpt": "ChatGPT", "codex": "ChatGPT", "gemini": "Gemini"}


def make_brain(cfg):
    kind = getattr(cfg, "jarvis_brain", "claude_code").strip().lower()
    model = getattr(cfg, "jarvis_model", "")
    if kind in ("claude_code", "claude"):
        return ClaudeCodeBrain(cfg.jarvis_claude_path, model)
    if kind in ("chatgpt", "codex"):
        cls = CodexLiveBrain if getattr(cfg, "jarvis_live", True) else CodexBrain
        return cls(getattr(cfg, "jarvis_codex_path", ""), model)
    if kind == "gemini":
        return GeminiBrain(getattr(cfg, "jarvis_gemini_path", ""), model)
    raise ValueError(f"unknown JARVIS brain: {kind} (use claude_code, chatgpt or gemini)")
