from __future__ import annotations

import argparse
import json
import sys
import time

import pywintypes
import win32file
import win32pipe

PIPE_NAME = r"\\.\pipe\faceunlock"


def call(request: dict, pipe_name: str = PIPE_NAME, wait_ms: int = 15000) -> dict:
    deadline = time.time() + wait_ms / 1000.0
    while True:
        try:
            handle = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None,
            )
            break
        except pywintypes.error as exc:
            if exc.winerror == 2 or time.time() > deadline:
                raise
            time.sleep(0.2)
    try:
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(handle, (json.dumps(request) + "\n").encode("utf-8"))
        data = win32file.ReadFile(handle, 65536)[1]
        return json.loads(data.decode("utf-8", "ignore").strip())
    finally:
        win32file.CloseHandle(handle)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["ping", "recognize"], nargs="?", default="ping")
    p.add_argument("--pipe", default=PIPE_NAME)
    args = p.parse_args()
    try:
        res = call({"cmd": args.cmd}, args.pipe)
    except pywintypes.error as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
