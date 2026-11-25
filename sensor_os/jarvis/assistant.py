"""The conversation: idle -> (Hey Jarvis) -> listening -> thinking -> speaking -> listening for a
follow-up for a few seconds -> idle.

Each request goes to the AI with a little context it cannot know by itself (the time, battery,
volume, Wi-Fi, which windows are open) and the last few exchanges, so "and close that one too" works.
Safe actions run at once; risky ones wait for the user to say "confirm" (anything else cancels them).
The screen follows through shared.jarvis: {state, heard, reply, level, error}.
"""
import re
import threading
import time
from collections import deque

from .actions import Runner, parse
from .brain import make_brain
from .actions import FOLDERS, SITES, Action
from .speech_text import T, configure, language_name, set_language, voice_for

CONFIRM_WORDS = ("confirm", "yes", "do it", "go ahead", "ok", "ยืนยัน", "ตกลง", "ใช่", "เอาเลย")
STATES = ("off", "idle", "listening", "thinking", "speaking", "error")


def is_confirm(text: str) -> bool:
    t = text.strip().lower()
    words = set(re.findall(r"[a-z']+", t))
    no = bool(words & {"no", "not", "don't", "dont", "cancel", "stop", "nope"}) or any(n in t for n in ("ไม่", "ยกเลิก"))
    yes = any((w in words) if w.isascii() and " " not in w else (w in t) for w in CONFIRM_WORDS)
    return yes and not no                          # whole English words: "now" is not "no"


BRAIN_WORDS = {
    "chatgpt": ("chatgpt", "chat gpt", "แชทจีพีที", "แชต จีพีที", "แชทจีพีที", "แชท gpt", "จีพีที", "gpt", "openai"),
    "gemini": ("gemini", "เจมินี", "เจมิไน", "เจมินาย", "เจมีไน"),
    "claude_code": ("claude", "cloud", "คลอด", "คล็อด", "คลาวด์", "คลอดด์", "คอร์ด"),   # Google hears "คอร์ดโค้ด"
}
MODEL_WORDS = {"haiku": ("haiku", "ไฮกุ", "ไฮคุ", "ไฮกู"), "sonnet": ("sonnet", "ซอนเน็ต", "ซอนเนต", "ซอเน็ต"),
               "opus": ("opus", "โอปุส", "โอพัส", "โอปัส")}
SWITCH_WORDS = ("switch", "use", "change", "ใช้", "เปลี่ยน", "สลับ")
BRAIN_NAMES = {"claude_code": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini"}


def brain_request(text: str):
    """ "เปลี่ยนไปใช้ ChatGPT" / "ใช้ Claude Haiku" / "switch to Gemini" -> (brain, model), else None.
    Done here, not by the AI, so it works at once and even when the current AI does not."""
    t = text.strip().lower()
    if len(t) > 45 or not any(w in t for w in SWITCH_WORDS):
        return None                                    # a longer sentence is a request for the AI
    model = next((m for m, words in MODEL_WORDS.items() if any(w in t for w in words)), "")
    brain = next((b for b, words in BRAIN_WORDS.items() if any(w in t for w in words)), None)
    if model and brain in (None, "claude_code"):
        return "claude_code", model
    return (brain, "") if brain else None


SHOW_WORDS = ("show all panels", "show the panels", "bring up the panels", "panels up", "แสดงแผง", "เปิดแผง", "เรียกแผง", "เอาแผง", "แผงออกมา", "show panels", "show everything", "แสดงทั้งหมด",
              "เปิดหน้าจอ", "แสดงหน้าจอ", "แสดงแผน", "เปิดแผน", "เรียกแผน")   # Google often hears แผง as แผน
HIDE_WORDS = ("hide all panels", "hide the panels", "clear the screen", "hide everything", "panels away", "ซ่อนแผง", "เก็บแผง", "ปิดแผง", "hide panels", "เคลียร์จอ", "เก็บทั้งหมด", "ล้างจอ", "เก็บหน้าจอ",
              "ซ่อนแผน", "เก็บแผน", "ปิดแผน")


def panels_request(text: str):
    """ "แสดงแผง" -> "show_panels", "เก็บแผง" -> "hide_panels", else None (done here, at once)."""
    t = text.strip().lower().replace(" ", "")
    if len(t) > 30:
        return None
    if any(w.replace(" ", "") in t for w in HIDE_WORDS):
        return "hide_panels"
    if any(w.replace(" ", "") in t for w in SHOW_WORDS):
        return "show_panels"
    return None


def greeting(state: dict, now: float | None = None) -> str:
    h = time.localtime(now).tm_hour
    hello = T("morning") if 5 <= h < 12 else T("afternoon") if h < 17 else T("evening") if h < 21 else T("night")
    battery = state.get("battery") if state else None
    low = battery is not None and battery < 0.25 and not state.get("plugged")
    return T("ready", hello=hello, extra=T("battery_low", pct=round(battery * 100)) if low else "")


def brain_label(brain: str, model: str) -> str:
    return BRAIN_NAMES.get(brain, brain) + (" " + model.capitalize() if model else "")


def context(system_state: dict, windows: list, now: float | None = None) -> str:
    """What the AI should know about the PC right now (it cannot see it). In English: every AI reads it."""
    lt = time.localtime(now)
    lines = [time.strftime("Time now: %H:%M, %A %d %B %Y", lt)]
    s = system_state or {}
    if s.get("battery") is not None:
        lines.append(f"Battery: {round(s['battery'] * 100)}%" + (" (charging)" if s.get("plugged") else ""))
    if s.get("volume") is not None:
        lines.append("Volume: " + ("muted" if s.get("muted") else f"{round(s['volume'] * 100)}%"))
    if s.get("brightness") is not None:
        lines.append(f"Screen brightness: {round(s['brightness'] * 100)}%")
    if s.get("wifi"):
        lines.append(f"Wi-Fi: {s['wifi'][0]}")
    if windows:
        lines.append("Open windows (front first): " + " | ".join(
            ("[minimized] " if w.minimized else "") + w.title[:60] for w in windows[:12]))
    return "\n".join(lines)


def build_prompt(text: str, ctx: str, history) -> str:
    parts = ["[The PC right now]", ctx]
    if history:
        parts.append("[Earlier in this conversation]")
        for heard, said in history:
            parts += [f"User: {heard}", f"JARVIS: {said}"]
    parts += [f"[The user said, in {language_name()}: reply in {language_name()}]", text]
    return "\n".join(parts)


VOLUME_WORDS = {"up": ("volume up", "louder", "turn it up", "turn up the volume", "เพิ่มเสียง", "เสียงดังขึ้น",
                       "ดังขึ้น", "เร่งเสียง"),
                "down": ("volume down", "quieter", "turn it down", "turn down the volume", "ลดเสียง", "เบาเสียง",
                         "เสียงเบาลง", "เบาลง"),
                "mute": ("mute", "mute the sound", "ปิดเสียง")}
OPEN_EN = re.compile(r"^(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+|my\s+)?(.+?)(?:\s+(?:app|please))?$")
OPEN_TH = re.compile(r"^(?:ช่วย|ขอ)?เปิด\s*(.+?)\s*(?:ให้หน่อย|ให้ที|หน่อย|ที|ให้|ครับ|ค่ะ|คะ)*$")


def quick_action(text: str, app_names=()) -> list | None:
    """Plain commands done at once, without the AI (~2 s saved): "open Chrome", "เปิด YouTube",
    "open downloads", "volume up", "ปิดเสียง". Anything less plain returns None and goes to the AI."""
    t = re.sub(r"[.!?,]+$", "", text.strip().lower()).strip()
    for level, words in VOLUME_WORDS.items():
        if t in words:
            return [Action("volume", level)]
    m = OPEN_EN.match(t) or OPEN_TH.match(t)
    if not m:
        return None
    target = m.group(1).strip()
    if len(target) >= 3:                       # an installed app first: "open Spotify" means the app, not the site
        for name in app_names:
            low = name.lower()
            if low == target or target in low.split() or low.startswith(target + " "):
                return [Action("open_app", name)]
    key = target.replace("www.", "").replace(".com", "").replace(" ", "")
    if key in SITES:
        return [Action("open_website", key)]
    if target in FOLDERS:
        return [Action("open_folder", target)]
    return None


class Jarvis(threading.Thread):
    def __init__(self, cfg, shared, system, desk, ui=None, chime=None, folders=None, scene=None):
        super().__init__(daemon=True, name="jarvis")
        self.cfg, self.shared, self.system, self.desk = cfg, shared, system, desk
        # The languages JARVIS hears, main first; it answers in whichever you spoke.
        self.languages = list(getattr(cfg, "jarvis_languages", ()) or ()) or [getattr(cfg, "jarvis_language", "en-US")]
        configure(self.languages)
        self._apps = None                      # app names, for "open X" done without the AI
        self.scene = scene or (lambda: "")    # what panels / 3D models are on the screen now
        self.ui = ui or (lambda *a: None)
        self.chime = chime or (lambda name: None)
        self.brain = make_brain(cfg)
        self.runner = Runner(desk, system, ui, folders)
        self.history = deque(maxlen=6)
        self.pending = None                    # risky actions waiting for "confirm"
        self.ears = None
        self.voice = None
        self.agent = None                      # Claude with your connected services (made in run())
        self.approval = None                   # (question, done event, answer dict) a tool task waits on
        self.talk_lock = threading.RLock()     # one voice at a time: a finished tool task waits its turn
        self.ack = None                        # "Yes?", made once, said the moment you stop talking
        self.stop_event = threading.Event()
        self.view = {"state": "off", "heard": "", "reply": "", "level": 0.0, "error": "", "t": time.time()}

    # ---------------------------------------------------------------- the screen
    def show(self, **kw) -> None:
        if "state" in kw:
            kw["t"] = time.time()
        self.view = {**self.view, **kw}
        self.shared.update(jarvis=dict(self.view))

    def listen_now(self) -> None:
        """Ctrl+Alt+J: listen without the wake word."""
        if self.ears is not None:
            self.ears.wake()

    def stop(self) -> None:
        self.stop_event.set()

    def set_brain(self, brain: str, model: str = "", announce: bool = False) -> str:
        """Use another AI from now on, and remember it in settings.json. Returns what to say."""
        old = (self.cfg.jarvis_brain, self.cfg.jarvis_model)
        self.cfg.jarvis_brain, self.cfg.jarvis_model = brain, model
        new = make_brain(self.cfg)
        ok, why = new.available()
        if not ok:
            self.cfg.jarvis_brain, self.cfg.jarvis_model = old
            say = T("brain_unavailable", name=brain_label(brain, model), why=why)
        else:
            old_brain, self.brain = self.brain, new
            if hasattr(old_brain, "close"):
                old_brain.close()                  # the ChatGPT kept open is not needed any more
            if hasattr(new, "warm"):
                threading.Thread(target=new.warm, daemon=True, name="jarvis-warm").start()
            try:
                self.cfg.save(["jarvis_brain", "jarvis_model"])
            except OSError:
                pass
            say = T("brain_switched", name=brain_label(brain, model))
            print(f"JARVIS brain -> {new.name}" + (f" ({model})" if model else ""), flush=True)
        self.ui("brain", self.cfg.jarvis_brain, self.cfg.jarvis_model)
        self.show(brain=brain_label(self.cfg.jarvis_brain, self.cfg.jarvis_model))
        if announce and self.voice is not None and self.view["state"] in ("idle", "error"):
            threading.Thread(target=self._announce, args=(say,), daemon=True).start()
        return say

    def next_brain(self) -> str:
        """Ctrl+Alt+A: the next AI in the list that is ready on this PC (skips one with no login / key)."""
        from types import SimpleNamespace
        from ..space import AI_CHOICES
        order = [(b, m) for b, m, _label in AI_CHOICES]
        now = (self.cfg.jarvis_brain, self.cfg.jarvis_model)
        i = order.index(now) if now in order else -1
        for k in range(1, len(order) + 1):
            brain, model = order[(i + k) % len(order)]
            trial = SimpleNamespace(**{**vars(self.cfg), "jarvis_brain": brain, "jarvis_model": model})
            if make_brain(trial).available()[0]:
                return self.set_brain(brain, model, announce=True)
        return ""

    def _announce(self, say: str) -> None:
        self.speak(say)
        self.show(state="idle")

    # ---------------------------------------------------------------- the loop
    def run(self) -> None:
        from .voice import Ears, Voice
        ok, why = self.brain.available()
        try:
            self.ears = Ears(self.cfg.jarvis_wake_threshold, self.languages,
                             getattr(self.cfg, "jarvis_name_check", True), getattr(self.cfg, "jarvis_pause", 0.6))
        except Exception as exc:              # no microphone, blocked by privacy settings, missing package
            self.show(state="error", error=T("mic_unavailable", err=exc.__class__.__name__))
            print(f"JARVIS: microphone unavailable: {exc}", flush=True)
            return
        self.voice = Voice(lambda lang: voice_for(lang, self.cfg.jarvis_voice), self.ears)
        self.voice.lang = self.languages[0]
        if not ok:
            self.show(state="error", error=why)
            print("JARVIS:", why, flush=True)
        else:
            self.show(state="idle", error="")
        self.show(brain=brain_label(self.cfg.jarvis_brain, self.cfg.jarvis_model))
        print(f"JARVIS ready ({self.brain.name}). Say \"Hey Jarvis\" or press Ctrl+Alt+J.", flush=True)
        threading.Thread(target=self._meter, daemon=True, name="jarvis-meter").start()
        if getattr(self.cfg, "jarvis_tools_enabled", True):
            from .agent import ToolAgent
            self.agent = ToolAgent(self.cfg)
            self.agent.refresh_servers()           # which services to leave out: `claude mcp list` takes ~25 s
        if ok and hasattr(self.brain, "warm"):     # the AI starts now, not at the first question
            threading.Thread(target=self.brain.warm, daemon=True, name="jarvis-warm").start()
        self.ack = self.voice.ready(T("ack"))        # also opens the voice's connection (the first is slow)
        threading.Thread(target=self._prepare, daemon=True, name="jarvis-prepare").start()
        if getattr(self.cfg, "jarvis_greeting", False) and ok:
            self.speak(greeting(self.system.state if self.system else {}))
            self.show(state="idle")
        try:
            while not self.stop_event.is_set():
                if not self.ears.listen_for_wake(self.stop_event):
                    break
                if self.approval is not None:      # woken by a tool task that needs your yes
                    self.handle_approval()
                    self.show(state="idle")
                    continue
                self.chime("touch")
                self.conversation()
                self.show(state="idle")
        finally:
            self.ears.close()
            if hasattr(self.brain, "close"):
                self.brain.close()

    def _prepare(self) -> None:
        """Ready-made replies in every language (played at once), and the app list for quick commands."""
        for lang in self.languages:
            for key in ("ack", "on_it"):
                self._phrase(key, lang)
        set_language(self.voice.lang)
        try:
            self._apps = [label for label, _path in self.desk.app_list()]
        except Exception:
            self._apps = []

    def _phrase(self, key: str, lang: str | None = None):
        """The recording of a ready-made phrase in a language (made once)."""
        if self.voice is None:
            return None
        lang = lang or self.voice.lang
        before = self.voice.lang
        set_language(lang)
        self.voice.lang = lang
        try:
            return self.voice.ready(T(key))
        finally:
            self.voice.lang = before

    def use_language(self, lang: str | None) -> None:
        """You spoke this language: JARVIS answers in it, in its voice."""
        if not lang:
            return
        set_language(lang)
        if self.voice is not None:
            self.voice.lang = lang

    def _meter(self) -> None:
        """How loud the microphone is, for the orb, while JARVIS listens."""
        while not self.stop_event.wait(0.08):
            if self.view["state"] == "listening" and self.ears is not None:
                self.view["level"] = self.ears.level
                self.shared.update(jarvis=dict(self.view))

    def conversation(self) -> None:
        wait = 6.0
        first = self.ears.take_first() if hasattr(self.ears, "take_first") else None
        if first:
            # People pause after the name: what came with it may be only the start ("จาร์วิส เปิด ... ยูทูบ").
            try:
                more = self.ears.hear(self.stop_event, 0.9)
            except RuntimeError:
                more = None
            if more:
                first = f"{first} {more}"
        while not self.stop_event.is_set():
            if getattr(self, "approval", None) is not None:
                self.handle_approval()
            self.show(state="listening", heard="", reply="")
            try:
                # "จาร์วิส เปิด YouTube" in one breath: the request came with the name
                text, first = (first, None) if first else (self.ears.hear(self.stop_event, wait), None)
            except RuntimeError as exc:
                self.speak(str(exc))
                return
            if text is None:                   # nobody spoke: back to waiting for the name
                return
            if not text:
                self.speak(T("missed"))
                return
            self.use_language(getattr(self.ears, "last_language", None))
            self.show(heard=text)
            print(f"JARVIS heard: {text}" + (f"  [{getattr(self.ears, 'last_language', '')}]"
                                              if len(getattr(self, "languages", [])) > 1 else ""), flush=True)
            if self.pending is not None:
                actions, self.pending = self.pending, None
                if is_confirm(text):
                    failed = [r for r in (self.runner.run(a) for a in actions) if r]
                    self.speak(" ".join(failed) or T("done"))
                    wait = self.cfg.jarvis_followup
                    continue
                self.speak(T("cancelled"))
                wait = self.cfg.jarvis_followup
                continue
            switch = brain_request(text)
            panels = panels_request(text)
            if switch:
                self.speak(self.set_brain(*switch))
            elif panels:                       # instant, no AI: "แสดงแผง" / "เก็บแผง"
                print(f"JARVIS: {panels} (done here, no AI)", flush=True)
                self.ui(panels)
                self.speak(T("panels_shown") if panels == "show_panels" else T("panels_hidden"))
            elif not self.quick(text):
                self.respond(text)
            wait = self.cfg.jarvis_followup

    def quick(self, text: str) -> bool:
        """A plain command done at once, with a ready-made reply, without the AI. False = ask the AI."""
        if not getattr(self.cfg, "jarvis_quick", True):
            return False
        actions = quick_action(text, getattr(self, "_apps", None) or ())
        if not actions:
            return False
        t = time.perf_counter()
        failed = [r for r in (self.runner.run(a) for a in actions) if r]
        if failed:
            return False                       # not that simple after all: let the AI deal with it
        print(f"JARVIS: {T('on_it')}  actions={[(a.type, a.target) for a in actions]}  (quick, no AI, "
              f"{time.perf_counter() - t:.2f} s)", flush=True)
        self.history.append((text, T("on_it")))
        self.show(state="speaking", reply=T("on_it"))
        path = self._phrase("on_it")
        if path and self.voice is not None:
            with self.talk_lock:
                self.voice.play_file(path)
        else:
            self.speak(T("on_it"))
        return True

    def respond(self, text: str) -> None:
        self.show(state="thinking")
        try:
            windows = self.desk.list_windows()
        except Exception:
            windows = []
        ctx = context(dict(self.system.state) if self.system else {}, windows)
        try:
            scene = getattr(self, "scene", lambda: "")()
        except Exception:                      # the screen changed while we looked: go on without it
            scene = ""
        if scene:
            ctx += "\n" + scene
        if getattr(self, "agent", None) is not None:
            from .agent import ACCOUNT
            ready = list(ACCOUNT) + [str(x) for x in (getattr(self.cfg, "jarvis_tools", ()) or ())]
            ctx += ("\nThe user's services, already connected and ready through use_tools: " + ", ".join(ready)
                    + " (nothing more to connect: if asked about MCP or connecting, say they are ready and invite a real task)")
        ctx += f"\nJARVIS's AI now: {brain_label(self.cfg.jarvis_brain, self.cfg.jarvis_model)}" \
               " (the user switches by saying 'use ChatGPT' / 'use Claude' / 'use Gemini', or the JARVIS-brain panel)"
        live = getattr(self.brain, "live", False)
        # A brain kept open remembers the conversation itself; one started per question is told it again.
        prompt = build_prompt(text, ctx, () if live else self.history)
        result = {}
        early = threading.Event()              # its words are in, the rest of the answer still coming
        t0 = time.perf_counter()

        def on_say(say):
            result["say"] = say
            result["say_at"] = time.perf_counter() - t0
            early.set()

        def think():
            try:
                result["raw"] = (self.brain.think(prompt, on_say=on_say) if live else self.brain.think(prompt))
            except RuntimeError as exc:
                result["error"] = str(exc)
            early.set()

        worker = threading.Thread(target=think, daemon=True, name="jarvis-think")
        worker.start()
        ack = self._phrase("ack") if self.voice is not None else None
        if ack is not None and not early.wait(0.5):
            self.voice.play_file(ack)          # "Yes?" when the answer is not there in half a second
            self.show(state="thinking")
        early.wait()
        talking = None
        if result.get("say") and "error" not in result:
            # Speak now; the actions are carried out while JARVIS talks (an app opens as he says so).
            talking = threading.Thread(target=self.speak, args=(result["say"],), daemon=True, name="jarvis-talk")
            talking.start()
        worker.join()
        if "error" in result:
            if talking is not None:
                talking.join()
            self.speak(result["error"])
            return
        raw = result.get("raw", "")
        reply = parse(raw)
        said = result.get("say") if talking is not None else None
        say = reply.say or said or T("ack")
        for a in [a for a in reply.actions if a.type == "use_tools"]:
            self.start_tool_task(a.target or text)  # your services: in the background, the answer spoken later
        safe = [a for a in reply.actions if not a.risky and a.type != "use_tools"]
        risky = [a for a in reply.actions if a.risky]
        failed = [r for r in (self.runner.run(a) for a in safe) if r]
        more = ""
        if failed:
            more += T("but", what=" ".join(failed))
        if risky:
            self.pending = risky
            more += T("confirm_q")
        took = getattr(self.brain, "last_seconds", None)
        first = result.get("say_at")
        print(f"JARVIS: {say}{more}  actions={[(a.type, a.target) for a in reply.actions]}"
              + (f"  ({took:.1f} s" + (f", words at {first:.1f} s" if first else "") + ")" if took else ""),
              flush=True)
        self.history.append((text, reply.say))
        if talking is not None:
            talking.join()
            if more.strip():
                self.speak(more.strip())
        else:
            self.speak(say + more)

    # ---------------------------------------------------------------- your services (Claude + MCP)
    def start_tool_task(self, task: str) -> None:
        if getattr(self, "agent", None) is None:
            self.speak(T("tools_unavailable"))
            return
        print(f"JARVIS tools: {task}", flush=True)

        def work():
            try:
                said = self.agent.run(task, self.ask_user)
            except Exception as exc:               # never let a failed task take JARVIS down
                said = T("tools_failed", err=exc.__class__.__name__)
            print(f"JARVIS tools said: {said}", flush=True)
            self.history.append((task, said))
            self.speak(said)
            if self.view.get("state") == "speaking":
                self.show(state="idle")

        threading.Thread(target=work, daemon=True, name="jarvis-tools").start()

    def ask_user(self, question: str, wait: float = 40.0) -> bool:
        """From a tool task: have JARVIS ask out loud and wait for "confirm". No answer = no."""
        done, answer = threading.Event(), {}
        self.approval = (question, done, answer)
        if self.ears is not None:
            self.ears.wake()                       # out of waiting for the name, to ask now
        done.wait(wait)
        self.approval = None
        return bool(answer.get("yes"))

    def handle_approval(self) -> None:
        question, done, answer = self.approval
        self.chime("touch")
        self.speak(T("tools_ask", what=question))
        self.show(state="listening", heard="", reply="")
        try:
            text = self.ears.hear(self.stop_event, 8.0)
        except RuntimeError:
            text = None
        yes = bool(text) and is_confirm(text)
        print(f"JARVIS asked to allow {question}: heard {text!r} -> {'yes' if yes else 'no'}", flush=True)
        answer["yes"] = yes
        self.approval = None
        done.set()
        self.speak(T("tools_yes") if yes else T("tools_no"))

    def speak(self, text: str) -> None:
        with getattr(self, "talk_lock", None) or threading.RLock():
            self.show(state="speaking", reply=text)
            try:
                self.voice.say(text)
            except Exception as exc:              # offline: the words stay on the screen
                print(f"JARVIS voice unavailable: {exc}", flush=True)
                time.sleep(min(4.0, 0.6 + len(text) * 0.05))
