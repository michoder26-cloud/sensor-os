"""What JARVIS may do on the PC. The AI only ever proposes actions from this list; each is checked
here before it runs. There is no "run a command" action: JARVIS can open, switch, type and adjust,
never execute arbitrary programs or delete anything. Risky actions wait for a spoken "confirm".

Pure parts (parse, match, plan) are tested; run() talks to the desktop through `desk`, an object
with the sensor_os.desktop functions, so tests can pass a fake.
"""
import difflib
import json
import re
import urllib.parse
from dataclasses import dataclass

from .speech_text import T

# type -> (what the AI is told it does, needs a spoken confirmation)
ACTIONS = {
    "open_app": ("open a program by name, e.g. Chrome, Word, Spotify", False),
    "open_website": ("open a website: a URL or a well-known site name (youtube, facebook, gmail...)", False),
    "web_search": ("search the web for the text", False),
    "youtube": ("search YouTube for the text (songs, music, videos, clips) and open the results", False),
    "open_folder": ("open a folder: desktop, documents, downloads, pictures, music, videos, or a path", False),
    "switch_window": ("bring an open window to the front, by words in its title", False),
    "minimize_window": ("minimize a window (target = words in its title, or 'current')", False),
    "maximize_window": ("maximize a window (target = words in its title, or 'current')", False),
    "close_window": ("close a window (target = words in its title, or 'current')", True),
    "media": ("music / video: play, pause, next, previous", False),
    "volume": ("speaker volume: a number 0-100, 'up', 'down' or 'mute'", False),
    "brightness": ("screen brightness: a number 0-100, 'up' or 'down'", False),
    "type_text": ("type the text into the window in front", False),
    "note": ("put a glass note panel on the desk with this text", False),
    "new_panel": ("put a glass panel on the desk: apps, windows, files, clock, system, music, picture, note, power, ai (choose JARVIS's AI)", False),
    "hide_panels": ("hide ALL glass panels and 3D models (target: empty)", False),
    "put_away": ("put away ONE kind of thing on the screen: '3d' (the floating 3D models), or a panel: apps, windows, files, clock, system, music, picture, note, power, ai, or words of its title", False),
    "show_panels": ("show the glass panels again (target: empty)", False),
    "use_tools": ("hand a task to Claude, which can use the user's own connected services: Gmail (read, search, summarize, draft, send), Google Drive, Shopify and any others they connected. target = the service first, then the whole task with every detail (who, what, when), e.g. 'Gmail: summarize my unread mail' or 'Google Drive: find the budget sheet'. It runs in the background (~10 s) and its answer is spoken when done; anything that sends, creates, changes or deletes is confirmed with the user by voice first", False),
    "lock": ("lock the PC (target: empty)", False),
    "sleep": ("put the PC to sleep (target: empty)", True),
    "restart": ("restart the PC (target: empty)", True),
    "shutdown": ("shut the PC down (target: empty)", True),
}
SITES = {"youtube": "https://www.youtube.com", "facebook": "https://www.facebook.com", "gmail": "https://mail.google.com",
         "google": "https://www.google.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
         "gemini": "https://gemini.google.com", "github": "https://github.com", "netflix": "https://www.netflix.com",
         "line": "https://line.me", "x": "https://x.com", "twitter": "https://x.com", "instagram": "https://www.instagram.com",
         "tiktok": "https://www.tiktok.com", "shopee": "https://shopee.co.th", "lazada": "https://www.lazada.co.th",
         "maps": "https://maps.google.com", "translate": "https://translate.google.com", "spotify": "https://open.spotify.com"}
FOLDERS = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
           "music": "Music", "videos": "Videos", "เดสก์ท็อป": "Desktop", "เอกสาร": "Documents", "ดาวน์โหลด": "Downloads",
           "รูปภาพ": "Pictures", "รูป": "Pictures", "เพลง": "Music", "วิดีโอ": "Videos"}


@dataclass
class Action:
    type: str
    target: str = ""

    @property
    def risky(self) -> bool:
        return ACTIONS[self.type][1]


@dataclass
class Reply:
    say: str
    actions: list


def describe_for_ai() -> str:
    return "\n".join(f"- {name}: {what}" + ("  (asks the user to confirm first)" if risky else "")
                     for name, (what, risky) in ACTIONS.items())


def parse(text: str) -> Reply:
    """The AI's answer -> Reply. Tolerates code fences and text around the JSON; drops unknown actions."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return Reply((text or "").strip()[:400], [])
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return Reply((text or "").strip()[:400], [])
    say = str(data.get("say") or "").strip()
    actions = []
    for a in data.get("actions") or []:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("type", "")).strip()
        if kind in ACTIONS:
            actions.append(Action(kind, str(a.get("target") or "").strip()[:2000]))
    return Reply(say, actions[:8])


def best_match(words: str, names: list, cutoff: float = 0.45):
    """Index of the name that best matches the spoken words (any language, any case), or None."""
    w = words.lower().strip()
    if not w or not names:
        return None
    low = [n.lower() for n in names]
    for i, n in enumerate(low):                              # whole words contained: "chrome" in "Google Chrome"
        if w == n:
            return i
    contains = [i for i, n in enumerate(low) if w in n]
    if contains:
        return min(contains, key=lambda i: len(low[i]))      # the shortest name that contains it
    tokens = [t for t in re.split(r"\W+", w) if len(t) > 1]
    scored = [(sum(t in n for t in tokens) / max(1, len(tokens)), -len(n), i) for i, n in enumerate(low)]
    best = max(scored)
    if best[0] >= 0.5:
        return best[2]
    close = difflib.get_close_matches(w, low, n=1, cutoff=cutoff)
    return low.index(close[0]) if close else None


def website_url(target: str) -> str:
    t = target.strip()
    key = t.lower().replace("www.", "").replace(".com", "").strip()
    if key in SITES:
        return SITES[key]
    if re.match(r"^https?://", t):
        return t
    if "." in t and " " not in t:
        return "https://" + t
    return "https://www.google.com/search?q=" + urllib.parse.quote(t)


def level(target: str, now: float | None, step: float = 0.1):
    """'50' / '50%' / 'up' / 'down' / 'เพิ่ม' / 'ลด' -> 0..1, or None if unclear."""
    t = target.strip().lower().replace("%", "")
    if t in ("up", "louder", "brighter", "เพิ่ม", "ดังขึ้น", "สว่างขึ้น"):
        return min(1.0, (now or 0.5) + step)
    if t in ("down", "quieter", "dimmer", "ลด", "เบาลง", "มืดลง"):
        return max(0.0, (now or 0.5) - step)
    try:
        return max(0.0, min(1.0, float(t) / 100.0))
    except ValueError:
        return None


class Runner:
    """Carries out checked actions. `desk` = sensor_os.desktop (or a fake); `system` = SystemControls;
    `ui` = a function taking ("note", text) / ("panel", kind) / ("hide_panels",) for the glass desk."""

    def __init__(self, desk, system=None, ui=None, folders=None):
        self.desk, self.system, self.ui = desk, system, ui or (lambda *a: None)
        self.folders = folders or (lambda name: None)

    def run(self, a: Action) -> str:
        """Do it. Returns "" when it worked, else a short reason (said back to the user)."""
        d = self.desk
        try:
            if a.type == "open_app":
                apps = d.app_list()
                i = best_match(a.target, [label for label, _ in apps])
                if i is None:
                    return T("no_app", what=a.target)
                d.open_path(apps[i][1])
            elif a.type == "open_website":
                d.open_path(website_url(a.target))
            elif a.type == "web_search":
                d.open_path("https://www.google.com/search?q=" + urllib.parse.quote(a.target))
            elif a.type == "youtube":
                d.open_path("https://www.youtube.com/results?search_query=" + urllib.parse.quote(a.target))
            elif a.type == "open_folder":
                name = FOLDERS.get(a.target.strip().lower(), None)
                path = self.folders(name) if name else a.target
                if not path:
                    return T("no_folder", what=a.target)
                d.open_path(path)
            elif a.type in ("switch_window", "minimize_window", "maximize_window", "close_window"):
                hwnd = self._window(a.target)
                if hwnd is None:
                    return T("no_window", what=a.target)
                {"switch_window": d.focus, "minimize_window": d.minimize, "maximize_window": d.maximize,
                 "close_window": d.close_window}[a.type](hwnd)
            elif a.type == "media":
                key = {"play": "play", "pause": "play", "next": "next", "previous": "prev", "prev": "prev",
                       "เล่น": "play", "หยุด": "play", "ถัดไป": "next", "ก่อนหน้า": "prev"}.get(a.target.lower(), "play")
                self.system.request("media", key)
            elif a.type == "volume":
                if a.target.lower() in ("mute", "ปิดเสียง"):
                    self.system.request("volume", "mute")
                else:
                    v = level(a.target, self.system.state.get("volume"))
                    if v is None:
                        return T("bad_volume")
                    self.system.request("volume", v)
            elif a.type == "brightness":
                v = level(a.target, self.system.state.get("brightness"))
                if v is None:
                    return T("bad_brightness")
                self.system.request("brightness", v)
            elif a.type == "type_text":
                d.type_text(a.target)
            elif a.type == "note":
                self.ui("note", a.target)
            elif a.type == "new_panel":
                self.ui("panel", a.target)
            elif a.type in ("hide_panels", "show_panels"):
                self.ui(a.type)
            elif a.type == "put_away":
                self.ui("put_away", a.target)
            elif a.type in ("lock", "sleep", "restart", "shutdown"):
                d.power(a.type)
            return ""
        except Exception as exc:                      # a program that will not open, a window that vanished
            return T("failed", err=exc.__class__.__name__)

    def _window(self, target: str):
        wins = self.desk.list_windows()
        if target.strip().lower() in ("", "current", "this", "นี้", "ปัจจุบัน"):
            front = [w for w in wins if not w.minimized]
            return front[0].hwnd if front else None
        i = best_match(target, [w.title for w in wins])
        return wins[i].hwnd if i is not None else None
