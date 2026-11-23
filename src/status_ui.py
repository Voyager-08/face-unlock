from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "logs" / "state.json"
TEMPLATE_PATH = ROOT / "data" / "face_template.json"
CONFIG_PATH = ROOT / "data" / "credential.json"
PIPE_NAME = r"\\.\pipe\faceunlock"
ENROLL_SCRIPT = ROOT / "src" / "enroll.py"
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SETTINGS_PATH = ROOT / "data" / "settings.json"

BG = "#0b0e14"
CARD = "#121722"
BORDER = "#1e2733"
TEXT = "#e6edf3"
MUTED = "#8b98a5"
ACCENT = "#4cc2ff"
GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
MONO = ("Consolas", 10)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class PMC(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def process_alive(pid) -> bool:
    if not pid:
        return False
    h = kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    kernel32.CloseHandle(h)
    return True


def ws_mb(pid):
    if not pid:
        return None
    h = kernel32.OpenProcess(0x1000 | 0x0010, False, int(pid))
    if not h:
        return None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / (1024 * 1024), 1)
        return None
    finally:
        kernel32.CloseHandle(h)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def tail_lines(path: Path, count: int = 2) -> str:
    try:
        data = path.read_bytes()[-2048:]
        lines = data.decode("utf-8", "ignore").splitlines()
        return "\n".join(lines[-count:])
    except Exception:
        return ""


def mask_account(user: str, domain: str) -> str:
    if not user:
        return "—"
    local, _, host = user.partition("@")
    if host:
        local = local[:4] + "***" if len(local) > 4 else local + "***"
        user = f"{local}@{host}"
    return f"{domain}\\{user}" if domain else user


REASON = {
    "ok": "识别成功",
    "no_match": "未识别到本人",
    "no_face": "未检测到人脸",
    "too_dark": "光线太暗",
    "bad_pose": "姿势或角度不合适",
    "worker": "识别进程未就绪",
    "camera": "摄像头不可用",
}


class Panel:
    def __init__(self, root: tk.Tk, agent_pid):
        self.root = root
        self.agent_pid = agent_pid
        self.bar_width = 400
        self._build()
        self.refresh()

    def _card(self, title: str, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        tk.Label(frame, text=title, bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=14, pady=(9, 2))
        body = tk.Frame(frame, bg=CARD)
        body.pack(fill="x", padx=14, pady=(0, 11))
        return body

    def _row(self, body: tk.Frame, label: str) -> tk.Label:
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=CARD, fg=MUTED, font=FONT, anchor="w", width=10).pack(side="left")
        value = tk.Label(row, text="—", bg=CARD, fg=TEXT, font=MONO, anchor="e")
        value.pack(side="right")
        return value

    def _build(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=20, pady=(16, 2))
        tk.Label(header, text="人脸解锁", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 20)).pack(side="left")
        status_frame = tk.Frame(header, bg=BG)
        status_frame.pack(side="right", pady=4)
        self.dot = tk.Canvas(status_frame, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 7))
        self.status_text = tk.Label(status_frame, text="运行中", bg=BG, fg=GREEN, font=FONT_B)
        self.status_text.pack(side="left")

        tk.Label(self.root, text="Face Unlock Console", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=22, pady=(0, 8))

        buttons = tk.Frame(self.root, bg=BG)
        buttons.pack(side="bottom", fill="x", padx=20, pady=(6, 14))
        self._button(buttons, "刷新", self.refresh).pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._button(buttons, "释放内存", self.release).pack(side="left", expand=True, fill="x", padx=6)
        self._button(buttons, "关闭", self.root.destroy).pack(side="left", expand=True, fill="x", padx=(6, 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=5)
        mid = tk.Frame(body, bg=BG)
        mid.pack(side="left", fill="both", expand=True, padx=5)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=5)

        proc = self._card("进程状态", left)
        self.lbl_agent_pid = self._row(proc, "常驻进程")
        self.lbl_agent_mem = self._row(proc, "常驻内存")
        self.lbl_worker_pid = self._row(proc, "识别进程")
        self.lbl_worker_mem = self._row(proc, "识别内存")
        self.lbl_total = self._row(proc, "合计占用")
        self.lbl_uptime = self._row(proc, "运行时长")
        self.bar = tk.Canvas(proc, height=8, bg=BG, highlightthickness=0)
        self.bar.pack(fill="x", pady=(8, 2))

        tpl = self._card("识别参数", left)
        self.lbl_threshold = self._row(tpl, "匹配阈值")
        self.lbl_poses = self._row(tpl, "姿态模板")
        self.lbl_enrolled = self._row(tpl, "录入时间")
        self.lbl_method = self._row(tpl, "匹配方式")
        self.lbl_method.configure(text="EDM 轮廓")
        self.lbl_smooth = self._row(tpl, "平滑窗口")
        self.lbl_smooth.configure(text="7 帧")
        self.lbl_need = self._row(tpl, "连续匹配")
        self.lbl_need.configure(text="6 帧")

        cam = self._card("摄像头", mid)
        cam_row = tk.Frame(cam, bg=CARD)
        cam_row.pack(fill="x", pady=(2, 4))
        self.camera_var = tk.StringVar()
        self.camera_box = ttk.Combobox(cam_row, textvariable=self.camera_var,
                                       state="readonly", width=18)
        self.camera_box.pack(side="left", fill="x", expand=True)
        self._button(cam_row, "扫描", self.scan_cameras).pack(side="left", padx=(8, 0))
        self._button(cam_row, "保存", self.save_camera).pack(side="left", padx=(6, 0))
        self.camera_note = tk.Label(cam, text="", bg=CARD, fg=MUTED,
                                    font=("Segoe UI", 9), anchor="w")
        self.camera_note.pack(fill="x", pady=(4, 0))
        self.camera_map = {}
        tk.Label(cam, text="选择本机摄像头后点“保存”，识别、录入、测试都会使用它。",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=300).pack(fill="x", pady=(6, 0))

        actions = self._card("快捷操作", mid)
        action_row = tk.Frame(actions, bg=CARD)
        action_row.pack(fill="x", pady=(2, 4))
        self._button(action_row, "录入人脸", self.enroll).pack(
            side="left", expand=True, fill="x", padx=(0, 6))
        self._button(action_row, "测试识别", self.test_recognition).pack(
            side="left", expand=True, fill="x", padx=(6, 0))
        self._button(actions, "测试锁屏", self.lock_test).pack(fill="x", pady=(0, 2))
        self.note = tk.Label(actions, text="就绪", bg=CARD, fg=MUTED,
                             font=("Segoe UI", 9), anchor="w")
        self.note.pack(fill="x", pady=(4, 0))
        tk.Label(actions, text="提示：录入会打开摄像头，按屏幕提示转头；切换后识别进程会自动重启。",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=300).pack(fill="x", pady=(6, 0))

        log_card = self._card("最近日志", mid)
        self.log_label = tk.Label(log_card, text="—", bg=CARD, fg=MUTED,
                                  font=("Consolas", 8), anchor="nw", justify="left",
                                  wraplength=320)
        self.log_label.pack(fill="both", expand=True)

        last = self._card("最近识别", right)
        self.lbl_result = self._row(last, "结果")
        self.lbl_dist = self._row(last, "距离")
        self.lbl_last_time = self._row(last, "时间")
        self.lbl_best = self._row(last, "最佳距离")

        info = self._card("账户与自启", right)
        self.lbl_account = self._row(info, "账户")
        self.lbl_autostart = self._row(info, "自启")
        self.lbl_autostart.configure(text="登录时自动启动", fg=GREEN, font=FONT_B)
        self.lbl_task = self._row(info, "计划任务")
        self.lbl_task.configure(text="FaceUnlockAgent")
        self.lbl_path = self._row(info, "安装目录")
        self.lbl_path.configure(text=str(ROOT), font=("Consolas", 9))

        tips = self._card("使用提示", right)
        tk.Label(tips,
                 text="锁屏后正对摄像头即可自动解锁。\n"
                      "识别失败不影响密码 / PIN / 指纹登录。\n"
                      "重新录入后新模板立即生效。\n"
                      "切换摄像头后识别进程会自动重启。",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="nw",
                 justify="left", wraplength=320).pack(fill="both", expand=True)

        self._load_camera_settings()

    def _load_camera_settings(self):
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
        try:
            idx = int(settings.get("camera_index", 0))
        except Exception:
            idx = 0
        self.camera_map = {f"摄像头 {idx}": idx}
        name = settings.get("camera_name")
        if name:
            self.camera_map = {str(name): idx}
        self.camera_box.configure(values=list(self.camera_map.keys()))
        self.camera_var.set(next(iter(self.camera_map)))
        self.camera_note.configure(text=f"当前：{self.camera_var.get()}（点扫描可列出全部）", fg=MUTED)

    def scan_cameras(self):
        self.camera_note.configure(text="正在枚举摄像头设备…", fg=ACCENT)
        code = (
            "import json\n"
            "from pygrabber.dshow_graph import FilterGraph\n"
            "print(json.dumps(FilterGraph().get_input_devices()))\n"
        )

        def work():
            try:
                out = subprocess.run(
                    [str(VENV_PYTHON), "-c", code],
                    capture_output=True, text=True, timeout=40,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                names = json.loads((out.stdout or "").strip() or "[]")
                if not isinstance(names, list):
                    names = []
            except Exception:
                names = []
            self.root.after(0, lambda: self._apply_scan(names))

        threading.Thread(target=work, daemon=True).start()

    def _apply_scan(self, names):
        self.camera_map = {}
        for idx, name in enumerate(names):
            self.camera_map[f"{idx} · {name}"] = idx
        if not self.camera_map:
            self.camera_note.configure(text="未找到摄像头（请确认已安装 pygrabber）", fg=RED)
            return
        self.camera_box.configure(values=list(self.camera_map.keys()))
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
        current = int(settings.get("camera_index", 0) or 0)
        current_name = settings.get("camera_name")
        for label, idx in self.camera_map.items():
            if idx == current:
                self.camera_var.set(label)
                break
        else:
            self.camera_var.set(next(iter(self.camera_map)))
        self.camera_note.configure(text=f"找到 {len(self.camera_map)} 个摄像头设备", fg=GREEN)

    def save_camera(self):
        label = self.camera_var.get()
        idx = self.camera_map.get(label)
        if idx is None:
            self.camera_note.configure(text="请先扫描并选择摄像头", fg=RED)
            return
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
        settings["camera_index"] = idx
        settings["camera_name"] = label
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        self.send_cmd({"cmd": "unload"}, 4000)
        self.camera_note.configure(text=f"已保存：{label}（识别与录入已切换）", fg=GREEN)

    def _button(self, parent, text, command) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg="#18202c", fg=TEXT,
                         activebackground="#223044", activeforeground=TEXT,
                         relief="flat", bd=0, font=FONT_B, padx=10, pady=8, cursor="hand2")

    def set_note(self, text: str, color: str = MUTED):
        try:
            self.note.configure(text=text, fg=color)
        except Exception:
            pass

    def send_cmd(self, req: dict, timeout_ms: int = 15000):
        try:
            import win32file
            import win32pipe
            deadline = time.time() + timeout_ms / 1000.0
            handle = None
            while True:
                try:
                    handle = win32file.CreateFile(
                        PIPE_NAME, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                        0, None, win32file.OPEN_EXISTING, 0, None)
                    break
                except Exception:
                    if time.time() > deadline:
                        return None
                    time.sleep(0.1)
            try:
                win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
                win32file.WriteFile(handle, (json.dumps(req) + "\n").encode("utf-8"))
                data = win32file.ReadFile(handle, 65536)[1]
                return json.loads(data.decode("utf-8", "ignore").strip())
            finally:
                win32file.CloseHandle(handle)
        except Exception:
            return None

    def enroll(self):
        self.set_note("正在打开录入窗口，请按屏幕提示操作…", ACCENT)

        def work():
            try:
                camera = self.camera_map.get(self.camera_var.get())
                args = [str(VENV_PYTHONW), str(ENROLL_SCRIPT)]
                if camera is not None:
                    args += ["--camera", str(camera)]
                proc = subprocess.Popen(args, cwd=str(ROOT / "src"))
                proc.wait()
                self.send_cmd({"cmd": "unload"}, 5000)
                if proc.returncode == 0:
                    self.root.after(0, lambda: self.set_note("录入完成，新模板已生效", GREEN))
                else:
                    self.root.after(0, lambda: self.set_note("录入已取消或失败", RED))
            except Exception as exc:
                self.root.after(0, lambda: self.set_note(f"录入启动失败：{exc}", RED))

        threading.Thread(target=work, daemon=True).start()

    def test_recognition(self):
        self.set_note("请正对摄像头，正在识别…", ACCENT)

        def work():
            resp = self.send_cmd({"cmd": "recognize", "timeout": 6}, 25000)
            if not resp:
                self.root.after(0, lambda: self.set_note("无法连接识别服务", RED))
                return
            if resp.get("ok"):
                msg = f"识别成功，距离 {float(resp.get('dist') or 0):.4f}"
                color = GREEN
            else:
                msg = "识别失败：" + REASON.get(resp.get("reason", ""), "未知原因")
                color = RED
            self.root.after(0, lambda: self.set_note(msg, color))

        threading.Thread(target=work, daemon=True).start()

    def lock_test(self):
        self.set_note("正在锁定屏幕，请正对摄像头进行真实测试…", ACCENT)
        self.root.after(400, lambda: ctypes.windll.user32.LockWorkStation())

    def release(self):
        self.send_cmd({"cmd": "unload"}, 5000)
        self.root.after(600, self.refresh)

    def _draw_bar(self, fraction: float):
        self.bar.delete("all")
        w = self.bar.winfo_width()
        if w < 10:
            w = self.bar_width
        h = 8
        self.bar.create_rectangle(0, 0, w, h, fill="#1b2430", outline="")
        fill = int(max(0.0, min(1.0, fraction)) * w)
        color = GREEN if fraction < 0.4 else (YELLOW if fraction < 0.75 else RED)
        self.bar.create_rectangle(0, 0, fill, h, fill=color, outline="")

    def refresh(self):
        state = read_json(STATE_PATH)
        agent_pid = self.agent_pid or state.get("agent_pid")
        worker_pid = state.get("worker_pid")
        worker_alive = process_alive(worker_pid)
        if not worker_alive:
            worker_pid = None

        agent_mb = ws_mb(agent_pid)
        worker_mb = ws_mb(worker_pid) if worker_pid else None
        total = (agent_mb or 0.0) + (worker_mb or 0.0)

        agent_ok = process_alive(agent_pid)
        self.dot.delete("all")
        if not agent_ok:
            self.dot.create_oval(1, 1, 11, 11, fill=RED, outline="")
            self.status_text.configure(text="已停止", fg=RED)
        elif worker_pid:
            self.dot.create_oval(1, 1, 11, 11, fill=GREEN, outline="")
            self.status_text.configure(text="识别中", fg=GREEN)
        else:
            self.dot.create_oval(1, 1, 11, 11, fill=ACCENT, outline="")
            self.status_text.configure(text="待机", fg=ACCENT)

        self.lbl_agent_pid.configure(text=str(agent_pid) if agent_pid else "—")
        self.lbl_agent_mem.configure(text=f"{agent_mb:.1f} MB" if agent_mb else "—")
        self.lbl_worker_pid.configure(text=str(worker_pid) if worker_pid else "未运行")
        self.lbl_worker_mem.configure(text=f"{worker_mb:.1f} MB" if worker_mb else "—")
        self.lbl_total.configure(text=f"{total:.1f} MB")
        started = state.get("started_at")
        if started:
            secs = max(0, int(time.time() - float(started)))
            hh, rem = divmod(secs, 3600)
            mm, ss = divmod(rem, 60)
            self.lbl_uptime.configure(text=f"{hh:02d}:{mm:02d}:{ss:02d}")
        else:
            self.lbl_uptime.configure(text="—")
        self.log_label.configure(text=tail_lines(ROOT / "logs" / "agent.log", 2) or "—")
        self._draw_bar(total / 500.0)

        tpl = read_json(TEMPLATE_PATH)
        if tpl:
            self.lbl_threshold.configure(text=f"{float(tpl.get('threshold', 0)):.4f}")
            self.lbl_poses.configure(text=str(len(tpl.get("centroids", []))))
            self.lbl_enrolled.configure(text=str(tpl.get("created", "—")))
        else:
            self.lbl_threshold.configure(text="—")
            self.lbl_poses.configure(text="—")
            self.lbl_enrolled.configure(text="未录入")

        cfg = read_json(CONFIG_PATH)
        self.lbl_account.configure(
            text=mask_account(cfg.get("username", ""), cfg.get("domain", "")))

        last = state.get("last") or {}
        if last:
            reason = last.get("reason") or "ok"
            ok = last.get("ok")
            text = "识别成功" if ok else REASON.get(reason, "未识别到本人")
            color = GREEN if ok else RED
            self.lbl_result.configure(text=text, fg=color, font=FONT_B)
            dist = last.get("dist")
            best = last.get("best")
            if dist is not None:
                self.lbl_dist.configure(text=f"{dist:.4f}")
            elif best is not None:
                self.lbl_dist.configure(text=f"最佳 {best:.4f}")
            else:
                self.lbl_dist.configure(text="—")
            self.lbl_best.configure(text=f"{float(best):.4f}" if best is not None else "—")
            ts = last.get("time")
            self.lbl_last_time.configure(
                text=datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S") if ts else "—")
        else:
            self.lbl_result.configure(text="尚无记录", fg=MUTED, font=FONT)
            self.lbl_dist.configure(text="—")
            self.lbl_best.configure(text="—")
            self.lbl_last_time.configure(text="—")

        self.root.after(1000, self.refresh)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent-pid", type=int, default=0)
    args = p.parse_args()

    mutex = kernel32.CreateMutexW(None, False, "Global\\FaceUnlockStatusUI")
    if ctypes.get_last_error() == 183:
        return 0

    root = tk.Tk()
    root.title("人脸解锁 · 状态面板")
    root.configure(bg=BG)
    root.geometry("1180x660")
    root.resizable(False, False)
    root.lift()
    root.after(150, root.focus_force)
    Panel(root, args.agent_pid)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
