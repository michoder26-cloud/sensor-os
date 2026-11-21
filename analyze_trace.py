"""Read trace.csv (written by sensor-OS every frame) and say what the hands did.

    .venv\\Scripts\\python analyze_trace.py

Answers: was a hand seen, where on the screen, how close to the edges of the camera picture,
how often the fingers really came together vs how often a pinch was counted, and where hands
were lost.
"""
import csv
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sensor_os.config import Config  # noqa: E402


def region(x):
    return "left" if x < 0.33 else "right" if x > 0.67 else "middle"


def main(path=HERE / "trace.csv"):
    cfg = Config.load()
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        print("trace.csv is empty")
        return
    frames = {}
    for r in rows:
        frames.setdefault(r["t"], []).append(r)
    times = sorted(frames, key=float)
    dur = float(times[-1]) - float(times[0])
    with_hand = [t for t in times if frames[t][0].get("hands") not in ("0", None)]
    print(f"recording: {dur:.1f} s, {len(times)} frames ({len(times) / max(dur, 1e-6):.1f} fps), "
          f"a hand seen in {100 * len(with_hand) / len(times):.0f}% of frames")

    hands = [r for r in rows if r.get("slot")]
    for r in hands:
        for k in ("x", "y", "pinch", "cam_left", "cam_right", "cam_top", "cam_bottom"):
            r[k] = float(r[k])
        r["pinched"], r["fist"], r["blocked"] = r["pinched"] == "1", r["fist"] == "1", r["blocked"] == "1"

    print("\nwhere the hand was (pointer on the screen):")
    for reg in ("left", "middle", "right"):
        rs = [r for r in hands if region(r["x"]) == reg]
        if not rs:
            print(f"  {reg:6}: never")
            continue
        edge = [min(r["cam_left"], 1 - r["cam_right"]) for r in rs]
        print(f"  {reg:6}: {len(rs):4} frames  y {min(r['y'] for r in rs):.2f}-{max(r['y'] for r in rs):.2f}  "
              f"closest the hand came to a side of the camera picture: {min(edge):.3f}")
    print(f"  pointer x went from {min(r['x'] for r in hands):.2f} to {max(r['x'] for r in hands):.2f}, "
          f"y from {min(r['y'] for r in hands):.2f} to {max(r['y'] for r in hands):.2f}")
    bottom = [r for r in hands if r["cam_bottom"] > 0.97]
    print(f"  frames with the hand touching the bottom of the camera picture: {len(bottom)}")

    # Fingers coming together, frame by frame, against what was counted as a pinch.
    on, off = cfg.pinch_on_index, cfg.pinch_off_index
    print(f"\npinch distance (thumb to index, in hand lengths): pinch counts below {on}, lets go above {off}")
    ps = [r["pinch"] for r in hands if not r["fist"]]
    if ps:
        q = statistics.quantiles(ps, n=20)
        print(f"  open hand typically {q[15]:.2f}  ·  smallest seen {min(ps):.2f}  ·  5% of frames below {q[0]:.2f}")
    closes, counted = [], 0
    by_slot = {}
    for r in hands:
        by_slot.setdefault(r["slot"], []).append(r)
    for slot, rs in by_slot.items():
        low = None
        for a, b in zip(rs, rs[1:]):
            if b["pinch"] < off and a["pinch"] >= off:
                low = {"start": float(b["t"]), "min": b["pinch"], "counted": b["pinched"], "x": b["x"], "y": b["y"],
                       "blocked": b["blocked"]}
            elif low is not None:
                low["min"] = min(low["min"], b["pinch"])
                low["counted"] = low["counted"] or b["pinched"]
                low["blocked"] = low["blocked"] or b["blocked"]
                if b["pinch"] >= off:
                    low["dur"] = float(b["t"]) - low["start"]
                    closes.append(low)
                    low = None
    print(f"  the fingers came together {len(closes)} times:")
    for c in closes:
        verdict = "PINCH" if c["counted"] else ("too far apart" if c["min"] >= on else "not counted")
        print(f"    t={c['start']:6.1f}s  at x={c['x']:.2f} ({region(c['x'])})  closest {c['min']:.2f}  "
              f"for {c.get('dur', 0):.2f}s  -> {verdict}{'  (on a panel)' if c['blocked'] else ''}")
    missed = [c for c in closes if not c["counted"] and c["min"] < on]
    near = [c for c in closes if not c["counted"] and on <= c["min"] < on + 0.12]
    print(f"  counted {sum(c['counted'] for c in closes)}, below the threshold but not counted {len(missed)}, "
          f"just above the threshold {len(near)}")

    # Hand lost: last frame before a gap.
    print("\nhands lost (a gap of more than 0.2 s):")
    for slot, rs in by_slot.items():
        for a, b in zip(rs, rs[1:]):
            if float(b["t"]) - float(a["t"]) > 0.2:
                edge = min(a["cam_left"], 1 - a["cam_right"], a["cam_top"], 1 - a["cam_bottom"])
                side = min((("left", a["cam_left"]), ("right", 1 - a["cam_right"]), ("top", a["cam_top"]),
                            ("bottom", 1 - a["cam_bottom"])), key=lambda kv: kv[1])[0]
                print(f"    t={float(a['t']):6.1f}s  slot {slot}  pointer x={a['x']:.2f} y={a['y']:.2f}  "
                      f"gone for {float(b['t']) - float(a['t']):.1f}s  hand {edge:.3f} from the {side} of the picture")


if __name__ == "__main__":
    main(*[Path(a) for a in sys.argv[1:]])
