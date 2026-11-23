from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

import pywintypes
import win32file
import win32pipe

FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
WORKER_PIPE = r"\\.\pipe\faceunlock_worker"
ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "worker.log"
TEMPLATE_PATH = ROOT / "data" / "face_template.json"
IDLE_SECONDS = 120.0
PID_FILE = LOG_DIR / "worker.pid"
_last_use = [time.time()]


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def janitor() -> None:
    while True:
        time.sleep(10)
        if time.time() - _last_use[0] > IDLE_SECONDS:
            log("worker idle timeout, exiting")
            try:
                PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            os._exit(0)


def recognize(engine, tpl, threshold: float, args, request: dict) -> dict:
    import cv2
    import numpy as np
    from face_engine import FaceEngine, open_camera, template_distance

    timeout = args.timeout
    if "timeout" in request:
        try:
            timeout = max(1.0, min(10.0, float(request["timeout"])))
        except (TypeError, ValueError):
            timeout = args.timeout

    cap = open_camera()
    if not cap.isOpened():
        return {"ok": False, "reason": "camera", "error": "camera_unavailable"}

    started = time.time()
    last_face = None
    bright_sum = 0.0
    bright_n = 0
    last_reject = None
    desc_buf: deque = deque(maxlen=max(args.smooth, 1))
    flags: deque = deque(maxlen=args.window)
    best = 99.0
    tries = 0

    def avg_brightness() -> float:
        return bright_sum / bright_n if bright_n else 0.0

    try:
        while time.time() - started < timeout:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            bright_sum += float(gray.mean())
            bright_n += 1
            elapsed = time.time() - started

            obs = engine.observe(frame)
            if obs is not None:
                last_reject = obs.reasons or None

            if elapsed > 1.0 and avg_brightness() < 45 and (obs is None or not obs.ok):
                return {"ok": False, "reason": "too_dark",
                        "brightness": round(avg_brightness(), 1),
                        "ms": int(elapsed * 1000), "frames": tries}

            if obs is None or not obs.ok:
                desc_buf.clear()
                if last_face is None and elapsed > args.no_face_timeout:
                    if last_reject:
                        return {"ok": False, "reason": "bad_pose",
                                "hint": last_reject[0] if last_reject else "",
                                "brightness": round(avg_brightness(), 1),
                                "ms": int(elapsed * 1000), "frames": tries}
                    return {"ok": False, "reason": "no_face",
                            "brightness": round(avg_brightness(), 1),
                            "ms": int(elapsed * 1000), "frames": tries}
                if last_face is not None and time.time() - last_face > args.no_face_timeout:
                    break
                continue

            last_face = time.time()
            tries += 1
            desc_buf.append(FaceEngine.descriptor(obs))
            rep = np.median(np.stack(desc_buf, axis=0), axis=0) if len(desc_buf) > 1 else desc_buf[-1]
            d = template_distance(rep, tpl)
            best = min(best, d)
            flags.append(d < threshold)
            if len(flags) >= args.need and sum(flags) >= args.need:
                return {"ok": True, "reason": "ok", "dist": round(d, 5),
                        "best": round(best, 5), "brightness": round(avg_brightness(), 1),
                        "ms": int((time.time() - started) * 1000), "frames": tries}
    finally:
        cap.release()

    if tries == 0:
        reason = "too_dark" if avg_brightness() < 45 else "no_face"
    else:
        reason = "no_match"
    return {"ok": False, "reason": reason,
            "best": round(best, 5) if best < 99 else None,
            "brightness": round(avg_brightness(), 1),
            "ms": int((time.time() - started) * 1000), "frames": tries}


def main() -> int:
    p = argparse.ArgumentParser(description="Face unlock worker")
    p.add_argument("--timeout", type=float, default=6.0)
    p.add_argument("--need", type=int, default=6)
    p.add_argument("--window", type=int, default=10)
    p.add_argument("--smooth", type=int, default=7)
    p.add_argument("--no-face-timeout", type=float, default=2.0)
    args = p.parse_args()

    from face_engine import FaceEngine, load_template
    engine = FaceEngine()
    tpl = load_template(TEMPLATE_PATH)
    threshold = float(tpl["threshold"])
    log(f"worker started pid={os.getpid()} threshold={threshold:.4f}")
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    threading.Thread(target=janitor, daemon=True).start()

    try:
        pipe = win32pipe.CreateNamedPipe(
            WORKER_PIPE,
            win32pipe.PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            1, 65536, 65536, 0, None,
        )
    except pywintypes.error as exc:
        if exc.winerror == 5:
            log("worker pipe already exists, exiting")
            return 0
        log(f"create pipe failed: {exc}")
        return 1

    while True:
        try:
            win32pipe.ConnectNamedPipe(pipe, None)
        except pywintypes.error as exc:
            log(f"connect error: {exc}")
            time.sleep(0.2)
            continue
        try:
            data = win32file.ReadFile(pipe, 4096)[1]
            text = data.decode("utf-8", "ignore").strip()
            try:
                request = json.loads(text) if text else {"cmd": "ping"}
            except json.JSONDecodeError:
                request = {"cmd": text}
            _last_use[0] = time.time()
            cmd = request.get("cmd")
            if cmd == "ping":
                response = {"ok": True, "worker": True}
            elif cmd == "shutdown":
                response = {"ok": True}
                win32file.WriteFile(pipe, (json.dumps(response) + "\n").encode("utf-8"))
                win32file.FlushFileBuffers(pipe)
                log("worker shutdown requested")
                try:
                    PID_FILE.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    win32pipe.DisconnectNamedPipe(pipe)
                except pywintypes.error:
                    pass
                os._exit(0)
            elif cmd == "recognize":
                response = recognize(engine, tpl, threshold, args, request)
                log(f"recognize -> ok={response.get('ok')} reason={response.get('reason')} "
                    f"best={response.get('best')}")
            else:
                response = {"ok": False, "error": "unknown_command"}
            win32file.WriteFile(pipe, (json.dumps(response) + "\n").encode("utf-8"))
            win32file.FlushFileBuffers(pipe)
        except pywintypes.error as exc:
            log(f"pipe error: {exc}")
        try:
            win32pipe.DisconnectNamedPipe(pipe)
        except pywintypes.error:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
