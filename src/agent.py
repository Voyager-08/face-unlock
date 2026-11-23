from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pywintypes
import win32file
import win32pipe

FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_NAME = r"\\.\pipe\faceunlock"
WORKER_PIPE = r"\\.\pipe\faceunlock_worker"
ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "agent.log"
STATE_PATH = LOG_DIR / "state.json"
WORKER_PID_FILE = LOG_DIR / "worker.pid"
WORKER_EXE = ROOT / ".venv" / "Scripts" / "pythonw.exe"
WORKER_SCRIPT = ROOT / "src" / "worker.py"
STATUS_UI = ROOT / "src" / "status_ui.py"

_worker = None
_worker_lock = threading.Lock()
_started_at = time.time()
_last_result = None


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def worker_listening() -> bool:
    try:
        win32pipe.WaitNamedPipe(WORKER_PIPE, 0)
        return True
    except pywintypes.error:
        return False


def current_worker_pid():
    if _worker is not None and _worker.poll() is None:
        return _worker.pid
    try:
        return int(WORKER_PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_state() -> None:
    pid = current_worker_pid()
    alive = pid is not None and (_worker is not None and _worker.poll() is None or worker_listening())
    data = {
        "agent_pid": os.getpid(),
        "started_at": _started_at,
        "updated_at": time.time(),
        "worker_alive": bool(alive),
        "worker_pid": pid if alive else None,
        "last": _last_result,
    }
    try:
        STATE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def ensure_worker() -> bool:
    global _worker
    with _worker_lock:
        if worker_listening():
            return True
        if _worker is None or _worker.poll() is not None:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _worker = subprocess.Popen([str(WORKER_EXE), str(WORKER_SCRIPT)],
                                       cwd=str(ROOT / "src"), creationflags=flags)
            log(f"worker spawned pid={_worker.pid}")
            write_state()
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if worker_listening():
                return True
            time.sleep(0.15)
    return False


def forward(req: dict, timeout_ms: int = 12000) -> dict:
    deadline = time.time() + timeout_ms / 1000.0
    handle = None
    while True:
        try:
            handle = win32file.CreateFile(
                WORKER_PIPE, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None)
            break
        except pywintypes.error as exc:
            if exc.winerror == 2 or time.time() > deadline:
                raise
            time.sleep(0.1)
    try:
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(handle, (json.dumps(req) + "\n").encode("utf-8"))
        data = win32file.ReadFile(handle, 65536)[1]
        return json.loads(data.decode("utf-8", "ignore").strip())
    finally:
        win32file.CloseHandle(handle)


def handle(request: dict) -> dict:
    global _last_result
    cmd = request.get("cmd", "")
    if cmd == "ping":
        return {"ok": True, "agent": True, "engine": worker_listening()}
    if cmd == "recognize":
        try:
            if not ensure_worker():
                _last_result = {"time": time.time(), "ok": False, "reason": "worker"}
                write_state()
                return {"ok": False, "reason": "worker", "error": "worker_timeout"}
            response = forward(request)
            _last_result = {
                "time": time.time(),
                "ok": bool(response.get("ok")),
                "reason": response.get("reason"),
                "dist": response.get("dist"),
                "best": response.get("best"),
            }
            write_state()
            return response
        except Exception as exc:
            log(f"worker forward failed: {exc}")
            _last_result = {"time": time.time(), "ok": False, "reason": "worker"}
            write_state()
            return {"ok": False, "reason": "worker", "error": str(exc)}
    if cmd == "unload":
        try:
            forward({"cmd": "shutdown"}, 3000)
        except Exception:
            pass
        write_state()
        return {"ok": True}
    return {"ok": False, "error": "unknown_command"}


def serve(args: argparse.Namespace) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log(f"agent started pipe={args.pipe}")
    try:
        pipe = win32pipe.CreateNamedPipe(
            args.pipe,
            win32pipe.PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            1, 65536, 65536, 0, None,
        )
    except pywintypes.error as exc:
        if exc.winerror == 5:
            log("pipe already owned by another agent, exiting")
            return
        log(f"create pipe failed: {exc}")
        return

    while True:
        try:
            win32pipe.ConnectNamedPipe(pipe, None)
        except pywintypes.error as exc:
            log(f"connect pipe error: {exc}")
            time.sleep(0.2)
            continue
        try:
            data = win32file.ReadFile(pipe, 4096)[1]
            text = data.decode("utf-8", "ignore").strip()
            try:
                request = json.loads(text) if text else {"cmd": "ping"}
            except json.JSONDecodeError:
                request = {"cmd": text}
            log(f"request: {text[:200]}")
            response = handle(request)
            win32file.WriteFile(pipe, (json.dumps(response) + "\n").encode("utf-8"))
            win32file.FlushFileBuffers(pipe)
        except pywintypes.error as exc:
            log(f"pipe error: {exc}")
        try:
            win32pipe.DisconnectNamedPipe(pipe)
        except pywintypes.error:
            pass


def spawn_status_ui() -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([str(WORKER_EXE), str(STATUS_UI), "--agent-pid", str(os.getpid())],
                     cwd=str(ROOT / "src"), creationflags=flags)
    log("status ui spawned")


def run_tray() -> bool:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception as exc:
        log(f"tray unavailable: {exc}")
        return False

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(255, 203, 50, 255), outline=(224, 168, 20, 255), width=2)
    draw.ellipse((19, 23, 27, 34), fill=(70, 50, 10, 255))
    draw.ellipse((37, 23, 45, 34), fill=(70, 50, 10, 255))
    draw.arc((17, 28, 47, 52), start=15, end=165, fill=(70, 50, 10, 255), width=4)
    draw.ellipse((12, 10, 22, 18), fill=(255, 245, 200, 170))

    def on_status(icon, item):
        spawn_status_ui()

    def on_unload(icon, item):
        try:
            forward({"cmd": "shutdown"}, 3000)
        except Exception:
            pass
        write_state()

    def on_quit(icon, item):
        try:
            forward({"cmd": "shutdown"}, 2000)
        except Exception:
            pass
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("状态面板", on_status),
        pystray.MenuItem("释放内存", on_unload),
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("faceunlock", img, "人脸解锁 - 运行中", menu)
    try:
        icon.run()
    except Exception as exc:
        log(f"tray run failed: {exc}")
        return False
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Face unlock agent (light parent)")
    p.add_argument("--pipe", default=PIPE_NAME)
    args = p.parse_args()

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = kernel32.CreateMutexW(None, False, "Global\\FaceUnlockAgent")
    if ctypes.get_last_error() == 183:
        log("another agent is already running, exiting")
        return 0

    log("agent boot")
    write_state()
    threading.Thread(target=serve, args=(args,), daemon=True).start()
    if not run_tray():
        log("tray unavailable, running headless")
        while True:
            time.sleep(3600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
