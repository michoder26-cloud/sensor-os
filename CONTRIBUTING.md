# Contributing to sensor-OS

Thanks for helping! Bug reports, ideas, translations and code are all welcome.

## Reporting a bug

Open an issue with:

- your OS and version, Python version, camera;
- what you did, what you expected, what happened;
- the error from the terminal if there is one, and the relevant lines of `run.log`.

**Before you paste logs, read them.** `run.log` contains window titles and what you said to JARVIS;
`trace.csv` contains hand positions. Remove anything private. Never attach `settings.json` or `space.json`
without checking them: they can contain your folder paths.

## Setting up

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest
```

All tests must pass. They use simulated hands, so they run without a camera, a microphone or a GPU.

## Code

- Keep gesture and panel logic free of Qt and of the operating system (`gestures.py`, `holo.py`, `space.py`,
  `stage.py`, `props.py` are pure logic with tests). Drawing lives in the `*_view.py` files and `hud.py`;
  everything that talks to the OS lives in `sensor_os/desktop/`.
- A behaviour change comes with a test that would have failed before it.
- Write code, comments and on-screen strings in English. Wrap on-screen strings in `tr("...")`.
- Values someone might want to tune go in `sensor_os/config.py` with a short comment saying what they do.
- JARVIS may only act through `sensor_os/jarvis/actions.py`. Anything that can lose data or reach other
  people (closing windows, power, sending mail...) must be confirmed by voice.

## Translating

- **On-screen text:** `sensor_os/i18n.py`. Copy the `"th"` block, name it with your language code
  (`"de"`, `"ja"`, ...) and translate the values, keeping `{}` placeholders and line breaks. Missing strings
  fall back to English, so partial translations are fine.
- **JARVIS's own phrases and AI notes:** `sensor_os/jarvis/speech_text.py` (`PHRASES` and `AI_NOTES`).
- **Voice commands done without the AI** (show / hide panels, switching AI, "confirm"): the word lists at
  the top of `sensor_os/jarvis/assistant.py`; the name as speech recognition writes it in
  `NAMES` in `sensor_os/jarvis/voice.py`.
- Add your language's name to `LANGUAGE_NAMES` in `speech_text.py`.

Test with `"language"`, `"jarvis_language"` and `"jarvis_voice"` set in your own `settings.json`.

## Security and privacy

If you find a way for JARVIS, a web page or a connected service to make sensor-OS do something the user did
not ask for, please report it privately through GitHub's "Report a vulnerability" (Security tab) instead of
a public issue.

## License

By contributing you agree that your contribution is released under the [MIT License](LICENSE).
