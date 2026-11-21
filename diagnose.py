"""Measure the hand tracking chain on this machine: camera, light, hand model, head model, delay.

    .venv\\Scripts\\python diagnose.py            # 20 s, hold a hand up and move it around
    .venv\\Scripts\\python diagnose.py 8 quiet    # no window, for a quick check without a hand

A running sensor-OS is asked to close first (the camera works for one program at a time).
Writes diagnose.json next to this file.
"""
import json
import statistics
import sys
import time
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sensor_os.camera import Camera                # noqa: E402
from sensor_os.config import Config                # noqa: E402
from sensor_os.gestures import pinch_features      # noqa: E402
from sensor_os.head import HeadTracker             # noqa: E402
from sensor_os.tracker import HandTracker          # noqa: E402


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def close_running_app():
    flag = HERE / "stop.flag"
    try:
        import psutil
        running = [p for p in psutil.process_iter(["cmdline"]) if any("main.py" in c for c in (p.info["cmdline"] or []))]
    except Exception:
        running = []
    if not running:
        return
    print("Closing the running sensor-OS first...")
    flag.touch()
    for _ in range(40):
        if not any(p.is_running() for p in running):
            break
        time.sleep(0.25)
    flag.unlink(missing_ok=True)


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    quiet = "quiet" in sys.argv
    close_running_app()
    cfg = Config.load()
    cam = Camera(cfg.camera_index, cfg.frame_width, cfg.frame_height, cfg.camera_fps)
    tracker = HandTracker(2)
    head = HeadTracker()
    seq = -1
    rows = []
    t_start = time.perf_counter()
    try:
        while time.perf_counter() - t_start < seconds:
            seq, frame = cam.read(seq, timeout=1.0)
            if frame is None:
                continue
            got = time.perf_counter()
            age_at_read = got - cam.stamp
            frame = cv2.flip(frame, 1)
            t0 = time.perf_counter()
            hands = tracker.process(frame, t0)
            t1 = time.perf_counter()
            head.update(frame, t1)
            t2 = time.perf_counter()
            bright = float(frame.mean())
            closed = pinch = None
            if hands:
                pinch, reach = pinch_features(hands[0].world)
                closed, pinch = round(sum(reach) / 4, 3), round(pinch, 3)
            rows.append({"t": round(got - t_start, 3), "hands": len(hands), "hand_ms": round((t1 - t0) * 1000, 2),
                         "head_ms": round((t2 - t1) * 1000, 2), "wait_ms": round(age_at_read * 1000, 2),
                         "delay_ms": round((t2 - cam.stamp) * 1000, 2), "bright": round(bright, 1),
                         "wrist": [round(v, 4) for v in hands[0].lm[0]] if hands else None, "reach": closed,
                         "pinch": pinch})
            if not quiet:
                view = frame.copy()
                for h in hands:
                    for x, y in h.lm:
                        cv2.circle(view, (int(x * view.shape[1]), int(y * view.shape[0])), 3, (255, 220, 90), -1)
                left = seconds - (time.perf_counter() - t_start)
                cv2.putText(view, f"Move around, pinch 10 times, make a fist   {left:4.1f}s", (12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.imshow("sensor-OS diagnose", view)
                if cv2.waitKey(1) == 27:
                    break
    finally:
        cam.close()
        tracker.close()
        head.close()
        cv2.destroyAllWindows()

    dur = rows[-1]["t"] - rows[0]["t"] if len(rows) > 1 else 1
    gaps = [b["t"] - a["t"] for a, b in zip(rows, rows[1:])]
    seen = [r for r in rows if r["hands"]]
    # Hand lost for a few frames in the middle of being tracked: the "it keeps dropping my hand" symptom.
    drops, run = 0, 0
    for a, b in zip(rows, rows[1:]):
        if a["hands"] and not b["hands"]:
            run = 1
        elif run and not b["hands"]:
            run += 1
        elif run and b["hands"]:
            if run <= 8:
                drops += 1
            run = 0
    jitter = []
    for a, b in zip(seen, seen[1:]):
        if abs(b["t"] - a["t"]) < 0.06:
            jitter.append(((a["wrist"][0] - b["wrist"][0]) ** 2 + (a["wrist"][1] - b["wrist"][1]) ** 2) ** 0.5)
    report = {
        "frames": len(rows), "fps": round(len(rows) / max(dur, 1e-6), 1),
        "frame_gap_ms_p95": round(1000 * pct(gaps, 0.95), 1) if gaps else None,
        "brightness_mean": round(statistics.mean(r["bright"] for r in rows), 1) if rows else None,
        "hand_model_ms_median": pct([r["hand_ms"] for r in rows], 0.5),
        "hand_model_ms_p95": pct([r["hand_ms"] for r in rows], 0.95),
        "head_model_ms_p95": pct([r["head_ms"] for r in rows], 0.95),
        "frame_waited_ms_median": pct([r["wait_ms"] for r in rows], 0.5),
        "camera_to_result_ms_median": pct([r["delay_ms"] for r in rows], 0.5),
        "share_of_frames_with_a_hand": round(len(seen) / max(len(rows), 1), 3),
        "short_dropouts_while_tracking": drops,
        "wrist_step_median": pct(jitter, 0.5),
        "pinch_min_max": [min((r["pinch"] for r in seen), default=None), max((r["pinch"] for r in seen), default=None)],
        "pinch_threshold_on_off": [cfg.pinch_on_index, cfg.pinch_off_index],
        "reach_min_max": [min((r["reach"] for r in seen), default=None), max((r["reach"] for r in seen), default=None)],
    }
    (HERE / "diagnose.json").write_text(json.dumps({"report": report, "frames": rows}, indent=1), encoding="utf-8")
    for k, v in report.items():
        print(f"{k:34} {v}")
    print(f"\nSaved {HERE / 'diagnose.json'}")


if __name__ == "__main__":
    main()
