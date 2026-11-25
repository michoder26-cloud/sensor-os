"""What JARVIS says by itself (not the AI's answers), in the language JARVIS speaks.

The language comes from `jarvis_language` in settings.json ("en-US" by default, "th-TH" for Thai...).
English is the fallback for any phrase a language does not have yet, so a new language can start small:
add a block below, set `jarvis_language` and a matching `jarvis_voice` (any Microsoft neural voice, see
`edge-tts --list-voices`), and JARVIS listens, thinks and speaks in it.
"""

PHRASES = {
    "en": {
        "ack": "Yes?",
        "on_it": "Right away.",
        "morning": "Good morning.", "afternoon": "Good afternoon.", "evening": "Good evening.", "night": "Working late, I see.",
        "ready": "{hello} All systems are online.{extra}",
        "battery_low": " Battery is at {pct} percent.",
        "missed": "Sorry, I didn't catch that.",
        "done": "Done.",
        "cancelled": "Cancelled.",
        "panels_shown": "Here you are.",
        "panels_hidden": "Cleared.",
        "brain_switched": "Switched to {name}.",
        "brain_unavailable": "I can't use {name} yet. {why}",
        "mic_unavailable": "Microphone unavailable: {err}",
        "confirm_q": " Shall I go ahead? Say confirm.",
        "but": " But {what}",
        "tools_unavailable": "Your connected services aren't available yet.",
        "tools_failed": "That task didn't work ({err}).",
        "tools_ask": "May I do this: {what}? Say confirm.",
        "tools_yes": "On it.",
        "tools_no": "I won't, then.",
        "tools_too_long": "That task took too long, so I stopped it.",
        "tools_call_failed": "I couldn't reach Claude: {err}",
        "tools_too_many": "Too many services are switched on for me. Remove some from jarvis_tools.",
        "no_claude": "Claude Code isn't installed on this PC (claude.ai/download, then sign in once).",
        "no_codex": "Codex isn't installed on this PC (npm install -g @openai/codex, then codex login).",
        "no_gemini_cli": "The Gemini CLI isn't installed (npm install -g @google/gemini-cli).",
        "no_gemini_key": "Gemini needs an API key: get one free at aistudio.google.com and set GEMINI_API_KEY.",
        "slow": "{who} is taking too long.",
        "call_failed": "I couldn't reach {who}: {err}",
        "said_error": "{who} says: {err}",
        "closed": "{who} closed unexpectedly.",
        "empty": "{who} gave no answer.",
        "no_speech_service": "I can't turn speech into text right now (internet?).",
        "no_app": "I can't find the program {what}",
        "no_folder": "I can't find the folder {what}",
        "no_window": "I can't find the window {what}",
        "bad_volume": "I didn't understand that volume level",
        "bad_brightness": "I didn't understand that brightness level",
        "failed": "it didn't work ({err})",
        "warmup": "[System check. Do nothing; answer as briefly as possible.]",
    },
    "th": {
        "ack": "ครับ",
        "on_it": "ได้เลยครับ",
        "morning": "อรุณสวัสดิ์ครับ", "afternoon": "สวัสดีตอนบ่ายครับ", "evening": "สวัสดีตอนเย็นครับ", "night": "ดึกแล้วนะครับ",
        "ready": "{hello} ระบบทั้งหมดพร้อมทำงานแล้วครับ{extra}",
        "battery_low": " แบตเตอรี่เหลือ {pct} เปอร์เซ็นต์",
        "missed": "ขอโทษครับ ฟังไม่ทันครับ",
        "done": "เรียบร้อยครับ",
        "cancelled": "ยกเลิกแล้วครับ",
        "panels_shown": "ได้ครับ",
        "panels_hidden": "เก็บให้แล้วครับ",
        "brain_switched": "เปลี่ยนเป็น {name} แล้วครับ",
        "brain_unavailable": "ยังใช้ {name} ไม่ได้ครับ {why}",
        "mic_unavailable": "ไมโครโฟนใช้ไม่ได้: {err}",
        "confirm_q": " ยืนยันไหมครับ พูดว่า ยืนยัน",
        "but": " แต่{what}",
        "tools_unavailable": "ยังใช้บริการที่เชื่อมไว้ไม่ได้ครับ",
        "tools_failed": "งานนั้นไม่สำเร็จครับ ({err})",
        "tools_ask": "ขออนุญาตทำรายการนี้ครับ: {what} ยืนยันไหมครับ",
        "tools_yes": "ได้ครับ",
        "tools_no": "ไม่ทำครับ",
        "tools_too_long": "งานนี้ใช้เวลานานเกินไปครับ ผมหยุดไว้ก่อน",
        "tools_call_failed": "เรียก Claude ไม่สำเร็จครับ: {err}",
        "tools_too_many": "บริการที่เปิดไว้มีมากเกินไปครับ ลองลดรายการ jarvis_tools ลง",
        "no_claude": "ไม่เจอ Claude Code ในเครื่อง (ติดตั้งจาก claude.ai/download แล้วล็อกอินครั้งแรก)",
        "no_codex": "ไม่เจอ Codex ในเครื่อง (npm install -g @openai/codex แล้ว codex login)",
        "no_gemini_cli": "ไม่เจอ Gemini CLI ในเครื่อง (npm install -g @google/gemini-cli)",
        "no_gemini_key": "Gemini ต้องใช้ API key: ขอฟรีที่ aistudio.google.com แล้วตั้งค่า GEMINI_API_KEY",
        "slow": "{who} ตอบช้าเกินไป",
        "call_failed": "เรียก {who} ไม่สำเร็จ: {err}",
        "said_error": "{who} แจ้งว่า: {err}",
        "closed": "{who} ปิดตัวไป",
        "empty": "{who} ไม่ได้ตอบอะไรมา",
        "no_speech_service": "แปลงเสียงเป็นข้อความไม่ได้ (เน็ต?)",
        "no_app": "ไม่เจอโปรแกรม {what}",
        "no_folder": "ไม่เจอโฟลเดอร์ {what}",
        "no_window": "ไม่เจอหน้าต่าง {what}",
        "bad_volume": "ไม่เข้าใจระดับเสียง",
        "bad_brightness": "ไม่เข้าใจระดับความสว่าง",
        "failed": "ทำไม่สำเร็จ ({err})",
        "warmup": "[ตรวจระบบ ไม่ต้องทำอะไร ตอบสั้นที่สุด]",
    },
}

# Extra instructions for the AI, per language (see brain.SYSTEM).
AI_NOTES = {
    "en": "- Address the user as \"sir\" now and then (not every time).",
    "th": """- Thai, with male polite particles (ครับ). Address the user as "ท่าน" now and then (not every time).
- Never sound like customer service in Thai either: never "มีอะไรให้ช่วยไหมครับ", "ยินดีให้บริการ", "ยินดีช่วยเหลือ", "รับทราบครับ".
- Glass panels are "แผง"; Thai speech recognition often writes it "แผน" - in this app it means แผง, not a plan.
- Thai speech recognition mishears English names: คอร์ด / คลอด / คล็อด / คลาวด์ = Claude ("คอร์ดโค้ด" = Claude Code), แชทจีพีที = ChatGPT, เจมิไน = Gemini, ยูทูป = YouTube. Read them as meant, including in search text.
- Numbers and times as spoken Thai (สามทุ่มสิบนาที, not 21:10).
- "เก็บ X" about one thing on the screen = put_away just that.""",
}

LANGUAGE_NAMES = {"en": "English", "th": "Thai", "de": "German", "ja": "Japanese", "zh": "Chinese", "es": "Spanish",
                  "fr": "French", "ko": "Korean", "vi": "Vietnamese", "id": "Indonesian", "pt": "Portuguese"}

# A natural male neural voice per language, used when `jarvis_voice` is not for that language.
DEFAULT_VOICES = {"en": "en-GB-RyanNeural", "th": "th-TH-NiwatNeural", "de": "de-DE-ConradNeural",
                  "ja": "ja-JP-KeitaNeural", "zh": "zh-CN-YunxiNeural", "es": "es-ES-AlvaroNeural",
                  "fr": "fr-FR-HenriNeural", "ko": "ko-KR-InJoonNeural", "vi": "vi-VN-NamMinhNeural",
                  "id": "id-ID-ArdiNeural", "pt": "pt-BR-AntonioNeural"}

_lang = "en"              # the language JARVIS is speaking right now (the one you last spoke)
_languages = ["en"]       # every language JARVIS listens for; the first is the main one


def code(language: str) -> str:
    """"th-TH" -> "th"."""
    return (language or "en").split("-")[0].lower()


def configure(languages) -> None:
    """The languages JARVIS listens for ("en-US", "th-TH", ...), main one first."""
    global _languages
    _languages = [code(x) for x in languages] or ["en"]
    set_language(_languages[0])


def set_language(language: str) -> None:
    global _lang
    _lang = code(language)


def language() -> str:
    return _lang


def languages() -> list:
    return list(_languages)


def language_name(lang: str | None = None) -> str:
    c = code(lang) if lang else _lang
    return LANGUAGE_NAMES.get(c, c)


def voice_for(lang: str, configured: str = "") -> str:
    """The voice for a language: `jarvis_voice` if it is one of that language's voices, else a default."""
    c = code(lang)
    if configured and code(configured) == c:
        return configured
    return DEFAULT_VOICES.get(c, configured or DEFAULT_VOICES["en"])


def T(key: str, **kw) -> str:
    """A phrase in JARVIS's language (English if that language does not have it)."""
    text = PHRASES.get(_lang, {}).get(key) or PHRASES["en"][key]
    return text.format(**kw) if kw else text


def ai_notes(lang: str | None = None) -> str:
    """Notes for one language, or (no argument) for every language JARVIS listens for."""
    if lang:
        return AI_NOTES.get(code(lang), "")
    return "\n".join(AI_NOTES[c] for c in _languages if c in AI_NOTES)
