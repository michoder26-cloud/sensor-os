"""The parts of the Mac and Linux backends that are plain logic, checked on any OS.

What talks to the real desktop (Quartz, Accessibility, X11) can only be checked on those systems."""
from types import SimpleNamespace

from sensor_os import desktop
from sensor_os.controller import Controller
from sensor_os.desktop import common, mac, x11
from sensor_os.desktop.common import WinInfo
from sensor_os.sysctl import parse_netsh, parse_nmcli, parse_pactl_volume


def test_backend_is_picked_from_the_os_and_session():
    assert desktop.pick("win32", {}) == "windows"
    assert desktop.pick("darwin", {}) == "mac"
    assert desktop.pick("linux", {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}) == "x11"
    assert desktop.pick("linux", {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}) == "null"   # XWayland is not enough
    assert desktop.pick("linux", {"WAYLAND_DISPLAY": "wayland-0"}) == "null"
    assert desktop.pick("linux", {}) == "null"
    assert desktop.pick("freebsd13", {"DISPLAY": ":0"}) == "null"
    assert desktop.pick("win32", {"SENSOR_OS_DESKTOP": "null"}) == "null"
    assert desktop.pick("linux", {"SENSOR_OS_DESKTOP": "bogus", "DISPLAY": ":0"}) == "x11"


def test_every_backend_offers_the_same_names():
    names = {"NAME", "FEATURES", "WinInfo", "screen_size", "list_windows", "window_at", "frame_bounds", "place",
             "minimize", "maximize", "restore", "focus", "Thumbnail", "HoloSkin", "attach_to_desktop",
             "refresh_wallpaper", "make_dpi_aware", "make_input", "start_hotkeys", "hotkey_down", "open_path",
             "is_hidden", "app_list", "power", "type_text", "close_window"}
    from sensor_os.desktop import null, win
    for backend in (win, mac, x11, null):
        missing = {n for n in names if not hasattr(backend, n)}
        assert not missing, (backend.NAME, missing)


def test_x11_visible_frame_and_placing_are_inverse():
    for net, gtk in (((0, 0, 0, 0), (0, 0, 0, 0)),          # no decoration at all
                     ((1, 1, 30, 1), (0, 0, 0, 0)),          # a window manager title bar
                     ((0, 0, 0, 0), (24, 24, 20, 28))):      # GTK client-side decoration with a shadow
        x, y, w, h = x11.moveresize_args((100, 50, 900, 650), net, gtk)
        # the window manager puts the outer frame at (x, y) and sizes the client to w x h
        client = (x + net[0], y + net[2], x + net[0] + w, y + net[2] + h)
        assert x11.visible_frame(client, net, gtk) == (100, 50, 900, 650)


def test_x11_reads_desktop_entries():
    entry = "[Desktop Entry]\nType=Application\nName=Firefox\nName[th]=ไฟร์ฟอกซ์\nExec=firefox %u\n[Desktop Action new]\nName=New"
    assert x11.parse_desktop_entry(entry) == ("Firefox", "firefox %u")
    assert x11.parse_desktop_entry(entry.replace("Type=Application", "Type=Link")) is None
    assert x11.parse_desktop_entry(entry + "\n").__class__ is tuple
    assert x11.parse_desktop_entry("[Desktop Entry]\nName=Hidden\nExec=x\nNoDisplay=true") is None


def test_x11_hotkey_bits():
    keys = [0] * 32
    keys[37 // 8] |= 1 << (37 % 8)
    assert x11.key_is_down(keys, 37) and not x11.key_is_down(keys, 38) and not x11.key_is_down(keys, 0)


def test_mac_pairs_quartz_and_accessibility_windows():
    entries = [(11, (0, 25, 800, 600), "", True), (12, (100, 100, 500, 400), "Notes", False)]
    ax = [("a", (100, 100, 500, 400), "Notes", True), ("b", (1, 26, 801, 601), "Doc", False)]
    assert mac.match_windows(entries, ax) == {11: "b", 12: "a"}
    assert mac.match_windows([(13, (0, 0, 50, 50), "", True)], ax) == {}


def test_title_bar_hit_and_window_under_a_point():
    assert common.on_title_bar((50, 60), (0, 40, 400, 300), 32)
    assert not common.on_title_bar((50, 90), (0, 40, 400, 300), 32)
    wins = [WinInfo(1, "a", (0, 0, 100, 100), (), True, False), WinInfo(2, "b", (0, 0, 200, 200), (), False, False)]
    assert common.hit(wins, 50, 50) == 2 and common.hit(wins, 300, 50) is None


def test_snap_rects():
    assert common.snap_rect("left", (0, 0, 1440, 900)) == (0, 0, 720, 900)
    assert common.snap_rect("right", (0, 0, 1440, 900)) == (720, 0, 1440, 900)
    assert common.snap_rect("up", (0, 0, 1440, 900)) is None


def test_wheel_keeps_the_remainder():
    w = common.WheelAccumulator(120)
    steps = [w.add(0, 50)[1] for _ in range(12)]
    assert sum(steps) == 5 and set(steps) <= {0, 1}
    assert w.add(-130, 0)[0] == -1


def test_pactl_volume():
    assert parse_pactl_volume("Volume: front-left: 26214 /  40% / -23.88 dB,   front-right: 26214 /  40%") == 0.40
    assert parse_pactl_volume("nothing") is None


class FakeIO:
    def __init__(self):
        self.calls = []
        self.pos = (0, 0)

    def screen(self):
        return 1000, 500

    def bounds(self):
        return 0, 0, 1000, 500

    def cursor(self):
        return self.pos

    def set_cursor(self, x, y):
        self.pos = (x, y)

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, *args))
            return name == "on_title_bar" or name == "snap"
        return record


def test_controller_speaks_only_through_the_desktop_layer():
    io = FakeIO()
    c = Controller(io, fling_speed=4.5)
    try:
        ev = lambda kind, x=0.0, y=0.0: SimpleNamespace(kind=kind, x=x, y=y)
        c.handle(ev("click"))
        c.handle(ev("right_click"))
        c.handle(ev("drag_start"))
        assert c.handle(ev("drag_end", 9.0, 1.0)) == "fling"
        c.handle(ev("scroll", 0, 240))
        c.handle(ev("zoom", 0, 120))
        names = [call[0] for call in io.calls]
        assert names == ["click", "click", "on_title_bar", "button", "button", "snap", "wheel", "zoom"]
        assert ("snap", "right") in io.calls and ("wheel", 0, 240) in io.calls
    finally:
        c.close()


def test_wifi_readers():
    netsh = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : Home Net
    BSSID                  : aa:bb:cc:dd:ee:ff
    Signal                 : 81%
"""
    assert parse_netsh(netsh) == ("Home Net", 0.81)
    assert parse_netsh("    State : disconnected") is None
    assert parse_nmcli("no:Other:40\nyes:Home Net:67\n") == ("Home Net", 0.67)
    assert parse_nmcli("") is None
