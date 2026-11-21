"""Start sensor-OS when you log in, like an operating system's shell.

    python main.py --autostart on     # add it
    python main.py --autostart off    # take it away again

Windows: a shortcut in the Startup folder that runs the project's pythonw.exe (no console window).
macOS: a LaunchAgent. Linux: an XDG autostart entry. Windows itself is untouched either way:
quit with Ctrl+Alt+X and the normal desktop and taskbar are back.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "sensor-OS"


def _python(console: bool = False) -> Path:
    exe = Path(sys.executable)
    if sys.platform == "win32" and not console:
        quiet = exe.with_name("pythonw.exe")
        if quiet.exists():
            return quiet
    return exe


def _entry() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / f"{NAME}.lnk"
    if sys.platform == "darwin":
        return Path.home() / "Library/LaunchAgents/com.sensor-os.plist"
    return Path.home() / ".config/autostart/sensor-os.desktop"


def enabled() -> bool:
    return _entry().exists()


def enable() -> Path:
    entry = _entry()
    entry.parent.mkdir(parents=True, exist_ok=True)
    py, main = _python(), ROOT / "main.py"
    if sys.platform == "win32":
        script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SOS_LINK); "
                  "$s.TargetPath = $env:SOS_PY; $s.Arguments = '\"' + $env:SOS_MAIN + '\"'; "
                  "$s.WorkingDirectory = $env:SOS_DIR; $s.Description = 'sensor-OS'; $s.Save()")
        env = dict(os.environ, SOS_LINK=str(entry), SOS_PY=str(py), SOS_MAIN=str(main), SOS_DIR=str(ROOT))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True,
                       capture_output=True, creationflags=0x08000000)
    elif sys.platform == "darwin":
        entry.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sensor-os</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{main}</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>RunAtLoad</key><true/>
</dict></plist>
""", encoding="utf-8")
    else:
        entry.write_text(f"""[Desktop Entry]
Type=Application
Name={NAME}
Exec="{py}" "{main}"
Path={ROOT}
X-GNOME-Autostart-enabled=true
""", encoding="utf-8")
    return entry


def disable() -> bool:
    entry = _entry()
    if entry.exists():
        entry.unlink()
        return True
    return False
