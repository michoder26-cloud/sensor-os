"""JARVIS without a microphone, a network or an AI: the parts that decide what happens."""
import threading
import time
from types import SimpleNamespace

import numpy as np

from sensor_os.jarvis import actions as A
from sensor_os.jarvis.assistant import Jarvis, build_prompt, context, is_confirm
from sensor_os.jarvis.brain import ClaudeCodeBrain
from sensor_os.jarvis.voice import CHUNK, Segmenter

import pytest

from sensor_os.jarvis import speech_text


@pytest.fixture(autouse=True)
def thai_jarvis():
    """Most tests below talk to JARVIS in Thai (the phrases checked are Thai); English is tested on its own."""
    speech_text.set_language("th-TH")
    yield
    speech_text.set_language("en-US")


# ---------------------------------------------------------------- parsing what the AI said
def test_parse_json_with_fence_and_unknown_actions():
    raw = 'นี่ครับ\n```json\n{"say": "เปิดให้แล้วครับ", "actions": [{"type": "open_app", "target": "Chrome"},' \
          ' {"type": "run_shell", "target": "del *"}, {"type": "volume", "target": 40}]}\n```'
    r = A.parse(raw)
    assert r.say == "เปิดให้แล้วครับ"
    assert [(a.type, a.target) for a in r.actions] == [("open_app", "Chrome"), ("volume", "40")]


def test_parse_plain_text_is_just_speech():
    r = A.parse("สวัสดีครับ")
    assert r.say == "สวัสดีครับ" and r.actions == []


def test_risky_flags():
    assert A.Action("shutdown").risky and A.Action("close_window").risky
    assert not A.Action("open_app", "x").risky and not A.Action("lock").risky


def test_no_command_execution_action_exists():
    assert not any(k in A.ACTIONS for k in ("run", "shell", "run_command", "delete_file", "execute"))


# ---------------------------------------------------------------- matching names
def test_best_match():
    apps = ["Google Chrome", "Microsoft Word", "Spotify", "Visual Studio Code", "Chrome Remote Desktop"]
    assert apps[A.best_match("chrome", apps)] == "Google Chrome"
    assert apps[A.best_match("word", apps)] == "Microsoft Word"
    assert apps[A.best_match("visual studio", apps)] == "Visual Studio Code"
    assert apps[A.best_match("spotfy", apps)] == "Spotify"
    assert A.best_match("photoshop", apps) is None


def test_website_url():
    assert A.website_url("YouTube") == "https://www.youtube.com"
    assert A.website_url("pantip.com") == "https://pantip.com"
    assert A.website_url("https://a.b/c") == "https://a.b/c"
    assert A.website_url("ราคาทอง วันนี้").startswith("https://www.google.com/search?q=")


def test_level():
    assert A.level("50", 0.2) == 0.5 and A.level("70%", None) == 0.7
    assert abs(A.level("up", 0.5) - 0.6) < 1e-9 and abs(A.level("ลด", 0.5) - 0.4) < 1e-9
    assert A.level("150", 0.5) == 1.0 and A.level("loud-ish", 0.5) is None


# ---------------------------------------------------------------- running actions on a fake desktop
class FakeDesk:
    def __init__(self):
        self.done = []
        self.wins = [SimpleNamespace(hwnd=1, title="YouTube - Google Chrome", minimized=False),
                     SimpleNamespace(hwnd=2, title="Document1 - Word", minimized=False)]

    def app_list(self):
        return [("Google Chrome", "C:/chrome.lnk"), ("Spotify", "C:/spotify.lnk")]

    def open_path(self, p):
        self.done.append(("open", p))

    def list_windows(self):
        return self.wins

    def focus(self, h):
        self.done.append(("focus", h))

    def minimize(self, h):
        self.done.append(("min", h))

    def maximize(self, h):
        self.done.append(("max", h))

    def close_window(self, h):
        self.done.append(("close", h))

    def type_text(self, t):
        self.done.append(("type", t))

    def power(self, a):
        self.done.append(("power", a))


class FakeSystem:
    def __init__(self):
        self.state = {"volume": 0.5, "brightness": 0.8, "muted": False, "battery": 0.64, "plugged": True,
                      "wifi": ("HomeNet", 80)}
        self.asked = []

    def request(self, name, action):
        self.asked.append((name, action))


def test_runner():
    desk, system, ui = FakeDesk(), FakeSystem(), []
    run = A.Runner(desk, system, lambda *a: ui.append(a), folders=lambda n: f"C:/Users/me/{n}").run
    assert run(A.Action("open_app", "chrome")) == ""
    assert run(A.Action("open_app", "photoshop")).startswith("ไม่เจอ")
    assert run(A.Action("open_folder", "ดาวน์โหลด")) == ""
    assert run(A.Action("switch_window", "word")) == ""
    assert run(A.Action("minimize_window", "current")) == ""
    assert run(A.Action("volume", "up")) == "" and run(A.Action("volume", "mute")) == ""
    assert run(A.Action("media", "next")) == ""
    assert run(A.Action("type_text", "สวัสดี")) == ""
    assert run(A.Action("note", "ซื้อนม")) == "" and run(A.Action("hide_panels")) == ""
    assert desk.done == [("open", "C:/chrome.lnk"), ("open", "C:/Users/me/Downloads"), ("focus", 2), ("min", 1),
                         ("type", "สวัสดี")]
    assert ("media", "next") in system.asked and ("volume", "mute") in system.asked
    assert any(n == "volume" and abs(v - 0.6) < 1e-9 for n, v in system.asked if not isinstance(v, str))
    assert ui == [("note", "ซื้อนม"), ("hide_panels",)]


def test_runner_reports_failures_instead_of_crashing():
    desk = FakeDesk()
    desk.open_path = lambda p: (_ for _ in ()).throw(OSError("nope"))
    assert A.Runner(desk).run(A.Action("open_website", "youtube")).startswith("ทำไม่สำเร็จ")


# ---------------------------------------------------------------- what the AI is told
def test_context_and_prompt():
    ctx = context(FakeSystem().state, FakeDesk().wins, now=0)
    assert "Battery: 64%" in ctx and "charging" in ctx and "HomeNet" in ctx and "Word" in ctx
    p = build_prompt("ปิดอันนั้นด้วย", ctx, [("เปิดเวิร์ด", "เปิดแล้วครับ")])
    assert p.index("เปิดเวิร์ด") < p.index("ปิดอันนั้นด้วย")


def test_claude_code_call_is_lean_and_toolless(tmp_path):
    exe = tmp_path / "claude.exe"
    exe.write_text("")
    brain = ClaudeCodeBrain(str(exe), "haiku")
    assert brain.path == str(exe)
    args = brain.args("hi")
    assert args[args.index("--tools") + 1] == ""                # the AI itself cannot touch the PC
    assert "--no-session-persistence" in args and args[args.index("--model") + 1] == "haiku"


def test_make_brain_picks_the_ai(tmp_path, monkeypatch):
    from sensor_os.jarvis import brain as B
    cfg = SimpleNamespace(jarvis_brain="chatgpt", jarvis_claude_path="", jarvis_codex_path="", jarvis_gemini_path="",
                          jarvis_model="")
    assert isinstance(B.make_brain(cfg), B.CodexBrain)
    cfg.jarvis_brain = "Gemini"
    assert isinstance(B.make_brain(cfg), B.GeminiBrain)
    cfg.jarvis_brain = "claude_code"
    assert type(B.make_brain(cfg)) is B.ClaudeCodeBrain


def test_codex_call_is_read_only_and_prompt_on_stdin(tmp_path):
    from sensor_os.jarvis.brain import CodexBrain
    exe = tmp_path / "codex.exe"
    exe.write_text("")
    args = CodexBrain(str(exe), "gpt-x").args()
    assert args[args.index("--sandbox") + 1] == "read-only" and "--ephemeral" in args
    assert args[-1] == "-" and args[args.index("-m") + 1] == "gpt-x"


def test_gemini_needs_a_key(tmp_path, monkeypatch):
    from sensor_os.jarvis.brain import GeminiBrain
    exe = tmp_path / "gemini.exe"
    exe.write_text("")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ok, why = GeminiBrain(str(exe)).available()
    assert not ok and "GEMINI_API_KEY" in why
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    assert GeminiBrain(str(exe)).available()[0]
    args = GeminiBrain(str(exe)).args()
    assert args[args.index("--approval-mode") + 1] == "plan"


def test_confirm_words():
    assert is_confirm("ยืนยัน") and is_confirm("ใช่ครับ") and is_confirm("OK")
    assert not is_confirm("ไม่ใช่") and not is_confirm("ยกเลิก") and not is_confirm("อะไรนะ")


# ---------------------------------------------------------------- cutting one request out of the sound
def _chunks(levels):
    rng = np.random.default_rng(0)
    return [(rng.standard_normal(CHUNK) * lv).astype(np.int16) for lv in levels]


def test_segmenter_hears_one_request():
    seg = Segmenter(noise=100, pause=0.8)
    states = [seg.feed(c) for c in _chunks([100] * 5 + [3000] * 15 + [100] * 12)]
    assert "done" in states and states.index("done") > 20
    assert len(seg.audio()) > 15 * CHUNK * 2


def test_segmenter_gives_up_on_silence():
    seg = Segmenter(noise=100, wait=1.0)
    states = [seg.feed(c) for c in _chunks([100] * 20)]
    assert "nothing" in states and states.index("nothing") <= 13


# ---------------------------------------------------------------- the conversation, with fakes
class FakeEars:
    def __init__(self, said):
        self.said = list(said)

    def hear(self, stop, wait):
        return self.said.pop(0) if self.said else None


class FakeBrain:
    name, last_seconds = "fake", None

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def think(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def _jarvis(said, answers):
    cfg = SimpleNamespace(jarvis_brain="none", jarvis_claude_path="", jarvis_model="", jarvis_followup=6.0)
    shared = SimpleNamespace(update=lambda **kw: None)
    desk, system = FakeDesk(), FakeSystem()
    j = Jarvis.__new__(Jarvis)
    threading.Thread.__init__(j, daemon=True)
    j.cfg, j.shared, j.system, j.desk = cfg, shared, system, desk
    j.chime, j.brain, j.ui, j.voice, j.ack = (lambda n: None), FakeBrain(answers), (lambda *a: None), None, None
    j.runner = A.Runner(desk, system)
    from collections import deque
    j.history, j.pending, j.stop_event = deque(maxlen=6), None, threading.Event()
    j.ears, j.spoken = FakeEars(said), []
    j.view = {"state": "idle", "heard": "", "reply": "", "level": 0.0, "error": "", "t": 0}
    j.speak = lambda text: j.spoken.append(text)
    return j, desk


def test_risky_action_waits_for_confirmation():
    j, desk = _jarvis(["ปิดเวิร์ด", "ยืนยัน"],
                      ['{"say": "จะปิด Word นะครับ", "actions": [{"type": "close_window", "target": "word"}]}'])
    j.conversation()
    assert ("close", 2) in desk.done
    assert "ยืนยัน" in j.spoken[0] and j.spoken[1] == "เรียบร้อยครับ"


def test_risky_action_cancelled_by_anything_else():
    j, desk = _jarvis(["ปิดเครื่อง", "ไม่เอาดีกว่า"], ['{"say": "จะปิดเครื่องนะครับ", "actions": [{"type": "shutdown"}]}'])
    j.conversation()
    assert ("power", "shutdown") not in desk.done and j.spoken[-1] == "ยกเลิกแล้วครับ"


def test_followup_remembers_the_conversation():
    j, desk = _jarvis(["เปิดยูทูบ", "แล้วเบาเสียงหน่อย"],
                      ['{"say": "เปิดแล้วครับ", "actions": [{"type": "open_website", "target": "youtube"}]}',
                       '{"say": "เบาลงแล้วครับ", "actions": [{"type": "volume", "target": "down"}]}'])
    j.conversation()
    assert ("open", "https://www.youtube.com") in desk.done
    assert "เปิดยูทูบ" in j.brain.prompts[1] and j.spoken == ["เปิดแล้วครับ", "เบาลงแล้วครับ"]


class LiveBrain(FakeBrain):
    """Kept open: hands over the words before the whole answer is in, and remembers by itself."""
    live = True

    def think(self, prompt, on_say=None):
        from sensor_os.jarvis.brain import say_so_far
        self.prompts.append(prompt)
        raw = self.answers.pop(0)
        cut = raw.find('"actions"')
        if on_say and say_so_far(raw[:cut]) is not None:
            on_say(say_so_far(raw[:cut]))          # only the "say" part has arrived
            self.early = True
        return raw


def test_a_live_brain_speaks_its_words_and_still_does_the_actions():
    j, desk = _jarvis(["เปิดยูทูบ", "ปิดเวิร์ด", "ยืนยัน"],
                      ['{"say": "ยูทูบมาแล้วครับ", "actions": [{"type": "open_website", "target": "youtube"}]}',
                       '{"say": "จะปิด Word นะครับ", "actions": [{"type": "close_window", "target": "word"}]}'])
    j.brain = LiveBrain(j.brain.answers)
    j.conversation()
    assert j.brain.early and ("open", "https://www.youtube.com") in desk.done
    assert j.spoken[:2] == ["ยูทูบมาแล้วครับ", "จะปิด Word นะครับ"] and "ยืนยัน" in j.spoken[2]
    assert ("close", 2) in desk.done
    assert "เปิดยูทูบ" not in j.brain.prompts[1]           # it remembers: the history is not sent again


def test_a_tool_task_runs_in_the_background_and_asks_before_writing():
    j, desk = _jarvis(["ร่างอีเมลถึงแม่", "ยืนยัน"],
                      ['{"say": "ร่างให้อยู่ครับ", "actions": [{"type": "use_tools", "target": "ร่างอีเมลถึงแม่"}]}'])
    asked, finished = [], threading.Event()

    class Agent:
        def run(self, task, ask):
            asked.append((task, ask("Gmail create draft (to แม่)")))
            finished.set()
            return "ร่างไว้แล้วครับ"

    j.agent, j.approval, j.talk_lock = Agent(), None, threading.RLock()
    j.ears.wake = lambda: None
    j.conversation()                     # says "ร่างให้อยู่ครับ"; the task asks; "ยืนยัน" answers it
    finished.wait(5)
    assert asked == [("ร่างอีเมลถึงแม่", True)]
    for _ in range(50):
        if "ร่างไว้แล้วครับ" in j.spoken:
            break
        time.sleep(0.05)
    assert j.spoken[0] == "ร่างให้อยู่ครับ" and "ยืนยันไหม" in " ".join(j.spoken) and "ร่างไว้แล้วครับ" in j.spoken


def test_a_task_loads_only_the_service_it_needs():
    from sensor_os.jarvis.agent import pick_services
    assert pick_services("Gmail: สรุปอีเมลที่ยังไม่ได้อ่าน") == ["Gmail"]
    assert pick_services("มีเมลใหม่ไหม") == ["Gmail"]
    assert pick_services("Google Drive: หาไฟล์งบประมาณ") == ["Google Drive"]
    assert pick_services("ออเดอร์วันนี้มีกี่รายการ") == ["Shopify"]
    assert pick_services("Notion: เพิ่มงาน", extras=["Notion"]) == ["Notion"]
    assert pick_services("ทำอะไรสักอย่าง") == ["Gmail", "Google Drive"]


def test_only_look_ups_go_through_without_asking():
    from sensor_os.jarvis.approve_server import is_read_only
    assert is_read_only("mcp__claude_ai_Gmail__search_threads") and is_read_only("mcp__claude_ai_Notion__notion-search")
    assert is_read_only("mcp__x__list_drafts") and is_read_only("mcp__x__get_message")
    for tool in ("send_message", "create_draft", "trash_thread", "delete_page", "update_event", "share_file"):
        assert not is_read_only("mcp__x__" + tool), tool


def test_the_name_heard_by_speech_recognition():
    from sensor_os.jarvis.voice import after_name
    assert after_name("จาร์วิส") == "" and after_name("Jarvis") == "" and after_name("จา วิส") == ""
    assert after_name("จาร์วิสเปิด YouTube") == "เปิด YouTube"
    assert after_name("จาวิส ครับ ปิดเสียงหน่อย") == "ปิดเสียงหน่อย"
    assert after_name("ขอบคุณครับ") is None and after_name("") is None


def _ears(model_scores, heard):
    """Ears without a microphone: chunks are fed in, the wake model and Google are faked."""
    import queue
    import numpy as np
    from sensor_os.jarvis import voice as V
    e = V.Ears.__new__(V.Ears)
    e.threshold, e.name_check, e.noise, e.first_request = 0.3, True, 100.0, None
    e.forced = threading.Event()
    e.q = queue.Queue()
    scores = iter(model_scores)
    e.model = SimpleNamespace(reset=lambda: None, predict=lambda c: {"hey_jarvis": next(scores, 0.0)})
    e.heard = []
    e.to_text = lambda pcm: (e.heard.append(len(pcm)), heard)[1]
    quiet, loud = np.full(V.CHUNK, 10, np.int16), (np.sin(np.arange(V.CHUNK)) * 3000).astype(np.int16)
    return e, quiet, loud


def test_the_name_said_alone_wakes_jarvis_and_a_request_with_it_goes_straight_in():
    e, quiet, loud = _ears([], "จาร์วิส เปิด YouTube")
    for c in [quiet] * 10 + [loud] * 12 + [quiet] * 10:             # about a second of speech, then quiet
        e.q.put(c)
    stop = threading.Event()
    assert e.listen_for_wake(stop) is True and e.heard
    assert e.take_first() == "เปิด YouTube" and e.take_first() is None


def test_other_short_phrases_do_not_wake_it_and_long_talk_is_not_sent():
    e, quiet, loud = _ears([], "ขอบคุณครับ")
    for c in [quiet] * 5 + [loud] * 10 + [quiet] * 8 + [loud] * 80 + [quiet] * 8:   # short phrase, then long talk
        e.q.put(c)
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    assert e.listen_for_wake(stop) is False
    assert len(e.heard) == 1                                         # only the short phrase was checked


def test_hey_jarvis_still_wakes_at_once():
    e, quiet, loud = _ears([0.0, 0.0, 0.35], "")
    for c in [quiet, loud, loud]:
        e.q.put(c)
    assert e.listen_for_wake(threading.Event()) is True and not e.heard


def test_say_so_far_waits_for_the_closing_quote():
    from sensor_os.jarvis.brain import say_so_far
    assert say_so_far('{"say": "เปิดแล้') is None
    assert say_so_far('{"say": "เปิด \\"Chrome\\" แล้วครับ", "act') == 'เปิด "Chrome" แล้วครับ'
    assert say_so_far('{"actions": []') is None


def test_brain_request_words():
    from sensor_os.jarvis.assistant import brain_request
    assert brain_request("เปลี่ยนไปใช้ ChatGPT") == ("chatgpt", "")
    assert brain_request("ใช้แชท GPT") == ("chatgpt", "")
    assert brain_request("switch to Gemini") == ("gemini", "")
    assert brain_request("ใช้ Claude Haiku") == ("claude_code", "haiku")
    assert brain_request("ใช้โอปุส") == ("claude_code", "opus")
    assert brain_request("ใช้คลอด") == ("claude_code", "")
    assert brain_request("เปิด ChatGPT ในเว็บ") is None                    # not a switch word: the AI handles it
    assert brain_request("ใช้ ChatGPT ช่วยเขียนอีเมลลาป่วยถึงหัวหน้าให้หน่อยแบบสุภาพๆ ยาวๆ") is None


def test_switch_by_voice_saves_and_tells_the_desk(tmp_path, monkeypatch):
    from sensor_os.jarvis import assistant
    saved, ui = [], []
    j, _ = _jarvis(["ใช้ ChatGPT"], [])
    j.cfg.save = lambda keys: saved.append({k: getattr(j.cfg, k) for k in keys})
    j.ui, j.voice = (lambda *a: ui.append(a)), None
    fake = SimpleNamespace(available=lambda: (True, ""), name="ChatGPT")
    monkeypatch.setattr(assistant, "make_brain", lambda cfg: fake)
    j.conversation()
    assert j.brain is fake and saved == [{"jarvis_brain": "chatgpt", "jarvis_model": ""}]
    assert ui == [("brain", "chatgpt", "")] and j.spoken == ["เปลี่ยนเป็น ChatGPT แล้วครับ"]


def test_switch_to_an_ai_that_is_not_ready_keeps_the_old_one(monkeypatch):
    from sensor_os.jarvis import assistant
    j, _ = _jarvis(["ใช้ Gemini"], [])
    j.cfg.jarvis_brain, j.cfg.save, j.voice = "claude_code", (lambda keys: None), None
    old = j.brain
    monkeypatch.setattr(assistant, "make_brain", lambda cfg: SimpleNamespace(available=lambda: (False, "ไม่มี key")))
    j.conversation()
    assert j.brain is old and j.cfg.jarvis_brain == "claude_code" and "ไม่มี key" in j.spoken[0]


def test_ctrl_alt_a_goes_to_the_next_ready_ai(monkeypatch):
    from sensor_os.jarvis import assistant
    from sensor_os.jarvis.brain import GeminiBrain
    j, _ = _jarvis([], [])
    j.cfg.jarvis_brain, j.cfg.jarvis_model, j.cfg.save = "chatgpt", "", (lambda keys: None)
    ready = {"claude_code": True, "chatgpt": True, "gemini": False}       # Gemini has no key: skipped
    monkeypatch.setattr(assistant, "make_brain", lambda cfg: SimpleNamespace(
        available=lambda: (ready[cfg.jarvis_brain], ""), name=cfg.jarvis_brain))
    assert j.next_brain() == "เปลี่ยนเป็น Claude แล้วครับ"
    assert (j.cfg.jarvis_brain, j.cfg.jarvis_model) == ("claude_code", "")
    j.next_brain()
    assert (j.cfg.jarvis_brain, j.cfg.jarvis_model) == ("claude_code", "haiku")


def test_panels_by_voice_are_instant():
    from sensor_os.jarvis.assistant import panels_request
    assert panels_request("แสดงแผง") == "show_panels" and panels_request("Jarvis show panels") == "show_panels"
    assert panels_request("เก็บแผงทั้งหมด") == "hide_panels" and panels_request("เคลียร์จอหน่อย") == "hide_panels"
    assert panels_request("เปิด Chrome") is None
    j, _ = _jarvis(["เรียกแผงขึ้นมา"], [])
    ui = []
    j.ui = lambda *a: ui.append(a)
    j.conversation()
    assert ui == [("show_panels",)] and j.brain.prompts == []          # the AI was not even asked


def test_greeting_by_time_of_day():
    from sensor_os.jarvis.assistant import greeting
    noon = time.mktime((2026, 11, 21, 13, 0, 0, 0, 0, -1))
    assert greeting({}, noon).startswith("สวัสดีตอนบ่าย")
    assert "แบตเตอรี่เหลือ 12" in greeting({"battery": 0.12, "plugged": False}, noon)


def test_brain_failure_is_spoken_not_crashed():
    j, _ = _jarvis(["สวัสดี"], [])
    j.brain.think = lambda p: (_ for _ in ()).throw(RuntimeError("ต่อ Claude ไม่ได้"))
    j.conversation()
    assert j.spoken == ["ต่อ Claude ไม่ได้"]


def test_english_is_the_default_and_other_languages_fall_back_to_it():
    from sensor_os.config import Config
    from sensor_os.jarvis.assistant import greeting
    from sensor_os.jarvis.brain import ClaudeCodeBrain
    cfg = Config()
    assert cfg.jarvis_language.startswith("en") and cfg.jarvis_voice.startswith("en-")
    speech_text.configure([cfg.jarvis_language])
    noon = time.mktime((2026, 11, 21, 13, 0, 0, 0, 0, -1))
    assert greeting({}, noon) == "Good afternoon. All systems are online."
    prompt = ClaudeCodeBrain().system_prompt()
    assert "main language is English." in prompt and "ครับ" not in prompt
    speech_text.configure(["de-DE"])                  # no German phrases yet: English, but the AI answers in German
    assert speech_text.T("done") == "Done." and "main language is German" in ClaudeCodeBrain().system_prompt()
    speech_text.configure(["en-US", "th-TH"])         # English first, Thai too: the Thai notes are in, English leads
    prompt = ClaudeCodeBrain().system_prompt()
    assert "main language is English; they also speak Thai" in prompt and "ครับ" in prompt
    assert speech_text.T("done") == "Done."
    speech_text.set_language("th-TH")                 # you spoke Thai: JARVIS's own words are Thai
    assert speech_text.T("done") == "เรียบร้อยครับ"
    assert speech_text.voice_for("th-TH", "en-GB-RyanNeural") == "th-TH-NiwatNeural"
    assert speech_text.voice_for("en-US", "en-US-GuyNeural") == "en-US-GuyNeural"


def test_the_language_you_spoke_is_picked_by_confidence_and_script():
    from sensor_os.jarvis.voice import pick_transcript
    # measured with real speech: both recognizers answer, the right one is surer and in its own script
    assert pick_transcript([("en-US", "do I have new email", 0.987), ("th-TH", "Do you have New e-mail", 0.924)]) \
        == ("do I have new email", "en-US")
    assert pick_transcript([("en-US", "my email my my", 0.61), ("th-TH", "มีอีเมลใหม่ไหม", 0.947)]) \
        == ("มีอีเมลใหม่ไหม", "th-TH")
    assert pick_transcript([("en-US", "", 0.0), ("th-TH", "เปิด Chrome ให้หน่อย", 0.947)])[1] == "th-TH"
    assert pick_transcript([("en-US", "", 0.0)]) == ("", None)


def test_the_prompt_says_which_language_to_answer_in():
    speech_text.configure(["en-US", "th-TH"])
    speech_text.set_language("th-TH")
    assert "[The user said, in Thai: reply in Thai]" in build_prompt("เปิดเพลง", "ctx", [])


def test_plain_commands_are_done_without_the_ai():
    from sensor_os.jarvis.assistant import quick_action
    apps = ["Google Chrome", "Spotify", "Visual Studio Code", "Notepad"]
    assert quick_action("Open Chrome", apps)[0].target == "Google Chrome"
    assert quick_action("เปิด Spotify ให้หน่อย", apps)[0].target == "Spotify"
    assert quick_action("open YouTube.", apps)[0].type == "open_website"
    assert quick_action("เปิดยูทูบ", apps) is None                     # a Thai spelling: the AI knows it
    assert quick_action("open downloads", apps)[0].type == "open_folder"
    assert quick_action("เปิดดาวน์โหลด", apps)[0].type == "open_folder"
    assert quick_action("volume up", apps)[0].target == "up" and quick_action("ปิดเสียง", apps)[0].target == "mute"
    assert quick_action("open the door for me", apps) is None           # not an app: the AI decides
    assert quick_action("open YouTube and play relaxing music", apps) is None
    assert quick_action("what time is it", apps) is None


def test_confirm_takes_whole_english_words():
    assert is_confirm("yes go ahead now")                 # "now" is not "no"
    assert is_confirm("confirm") and is_confirm("ยืนยันครับ")
    assert not is_confirm("no, cancel that") and not is_confirm("ไม่ยืนยัน") and not is_confirm("don't do it")
