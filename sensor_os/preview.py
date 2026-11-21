"""Debug window (--debug): the camera image with the hand skeleton and the current mode."""
import cv2

CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)

MODE_COLORS = {
    "move": (255, 220, 70),
    "press": (60, 165, 255),
    "drag": (60, 165, 255),
    "rpress": (255, 140, 195),
    "scroll": (255, 140, 195),
    "pause": (180, 165, 150),
}

WINDOW = "sensor-OS debug"


def draw(frame, landmarks, mode: str, progress: float, fps: float, enabled: bool, dry_run: bool):
    h, w = frame.shape[:2]
    color = MODE_COLORS.get(mode, (160, 160, 160))
    if landmarks:
        pts = [(int(x * w), int(y * h)) for x, y in landmarks]
        for a, b in CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], color, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(frame, p, 3, (255, 255, 255), -1, cv2.LINE_AA)
    control = "DRY RUN" if dry_run else ("ON" if enabled else "OFF")
    cv2.rectangle(frame, (0, 0), (w, 30), (0, 0, 0), -1)
    cv2.putText(frame, f"{mode.upper()}  pinch {progress:.2f}  {fps:4.1f} fps  control: {control}", (8, 21),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    cv2.imshow(WINDOW, frame)


def poll_key() -> int:
    return cv2.waitKey(1) & 0xFF


def close():
    cv2.destroyAllWindows()
