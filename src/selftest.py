import sys
import time

import cv2

from face_engine import FaceEngine, LOG_DIR, draw_observation, open_camera


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cap = open_camera()
    if not cap.isOpened():
        print("ERROR: cannot open camera")
        return 2

    engine = FaceEngine()
    obs = None
    frame = None
    deadline = time.time() + 6.0
    while time.time() < deadline:
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        obs = engine.observe(frame)
        if obs is not None and obs.ok:
            break
        time.sleep(0.05)

    ok, frame = cap.read()
    if obs is None:
        obs = engine.observe(frame) if ok and frame is not None else None

    cap.release()
    engine.close()

    if frame is None:
        print("ERROR: no frame captured")
        return 2

    lines = []
    if obs is None:
        lines.append("no face detected")
        print("RESULT: no face detected")
    else:
        desc = FaceEngine.descriptor(obs)
        print(f"RESULT: landmarks={len(obs.points)} ok={obs.ok} reasons={obs.reasons}")
        print(f"interocular_px={obs.interocular:.1f} roll_deg={obs.rotation_deg:.1f} mouth_w={obs.mouth_width:.1f}")
        print(f"descriptor_dim={desc.shape[0]}")
        lines.append(f"lm={len(obs.points)} ok={obs.ok} {obs.reasons}")
        lines.append(f"io={obs.interocular:.0f}px roll={obs.rotation_deg:.1f}")

    annotated = draw_observation(frame, obs, lines)
    out_path = LOG_DIR / "selftest.jpg"
    cv2.imwrite(str(out_path), annotated)
    print(f"saved {out_path}")
    return 0 if obs is not None and obs.ok else 1


if __name__ == "__main__":
    sys.exit(main())
