import base64
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

CONFIG = Path(r"D:\Program Files (x86)\FaceUnlock\data\credential.json")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)


def unprotect_machine(data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    din = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dout = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(din), None, None, None, None, 0x4, ctypes.byref(dout)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(dout.pbData, dout.cbData)
    finally:
        kernel32.LocalFree(dout.pbData)


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    blob = base64.b64decode(cfg["password_dpapi"])
    plain = unprotect_machine(blob)
    password = plain.decode("utf-16-le")
    user = cfg["username"]
    domain = cfg["domain"]

    token = wintypes.HANDLE()
    advapi32.LogonUserW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.LogonUserW.restype = wintypes.BOOL
    ok = advapi32.LogonUserW(user, domain, password, 2, 0, ctypes.byref(token))
    err = ctypes.get_last_error()
    print(f"user={domain}\\{user}")
    if ok:
        print("LOGON OK")
        kernel32.CloseHandle(token)
        return 0
    print(f"LOGON FAILED error={err}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

