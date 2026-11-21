"""JARVIS: talk to sensor-OS. "Hey Jarvis" -> listen -> an AI decides -> it speaks and acts on the PC.

    actions.py    what JARVIS may do, checked before anything runs (risky things need a spoken "confirm")
    brain.py      the AI that decides (Claude Code on your own Claude plan; other AIs plug in the same way)
    voice.py      wake word, listening, speech to text, Thai voice
    assistant.py  the conversation: idle -> listening -> thinking -> speaking -> follow-up
"""
