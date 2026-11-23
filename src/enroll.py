from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOG_DIR = _ROOT / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
if sys.stdout is None:
    sys.stdout = open(_LOG_DIR / "enroll.log", "a", encoding="utf-8", buffering=1)
if sys.stderr is None:
    sys.stderr = sys.stdout

import cv2

from face_engine import (
    LOG_DIR,
    TEMPLATE_PATH,
    FaceEngine,
    build_template,
    draw_observation,
    draw_text_cn,
    open_camera,
    save_template,
)

WINDOW = "人脸录入"

PHASES = [
    (0.00, "请正视摄像头"),
    (0.30, "请把头稍微向左转"),
    (0.52, "请把头稍微向右转"),
    (0.74, "请稍微抬高下巴"),
    (0.88, "请回正，保持"),
]
REASON_CN = {
    "roll": "请摆正头",
    "yaw": "转动角度太大",
    "too_far": "请靠近一点",
    "too_close": "请离远一点",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="多姿态面部轮廓录入")
    p.add_argument("--camera", type=int, default=None)
    p.add_argument("--countdown", type=float, default=5.0)
    p.add_argument("--capture", type=float, default=14.0)
    p.add_argument("--max-samples", type=int, default=160)
    p.add_argument("--min-samples", type=int, default=12)
    p.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    return p.parse_args()


def phase_text(fraction: float) -> str:
    label = PHASES[0][1]
    for start, text in PHASES:
        if fraction >= start:
            label = text
    return label


def main() -> int:
    args = parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    cap = open_camera(args.camera)
    if not cap.isOpened():
        print("错误：无法打开摄像头")
        return 2

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 960, 540)
    cv2.moveWindow(WINDOW, 60, 60)

    engine = FaceEngine()
    descriptors = []
    last_obs = None
    preview = None
    last_frame = None
    reject_counts = {}

    start = time.time()
    capture_start = start + args.countdown
    capture_end = capture_start + args.capture
    print(f"请按屏幕提示操作。{args.countdown:.0f} 秒后开始采集，共 {args.capture:.0f} 秒。")

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            now = time.time()
            if now > capture_end:
                break

            obs = engine.observe(frame)
            last_obs = obs
            last_frame = frame
            if now < capture_start:
                main_text = "请坐好，正对摄像头"
                sub = [f"{capture_start - now:4.1f} 秒后开始采集"]
            else:
                fraction = (now - capture_start) / args.capture
                main_text = phase_text(fraction)
                sub = [f"剩余 {args.capture * (1 - fraction):4.1f} 秒", f"已采集 {len(descriptors)} 帧"]
                if obs is not None and obs.ok and len(descriptors) < args.max_samples:
                    descriptors.append(FaceEngine.descriptor(obs))
                    preview = frame.copy()
                if obs is None:
                    reject_counts["no_face"] = reject_counts.get("no_face", 0) + 1
                    sub.append("未检测到人脸")
                elif not obs.ok:
                    for reason in obs.reasons:
                        reject_counts[reason] = reject_counts.get(reason, 0) + 1
                    sub.append("提示：" + "，".join(REASON_CN.get(r, r) for r in obs.reasons))

            annotated = draw_observation(frame, obs, [])
            annotated = draw_text_cn(annotated, [main_text], origin=(20, 14), size=40)
            annotated = draw_text_cn(annotated, sub, origin=(20, 86), size=26)
            cv2.imshow(WINDOW, annotated)
            if (cv2.waitKey(1) & 0xFF) == 27:
                print("已取消")
                return 3
    finally:
        cap.release()
        engine.close()
        cv2.destroyAllWindows()

    if len(descriptors) < args.min_samples:
        if last_frame is not None:
            cv2.imwrite(str(LOG_DIR / "enroll_last.jpg"), last_frame)
        print(f"错误：有效样本只有 {len(descriptors)} 帧（至少需要 {args.min_samples} 帧）。")
        if reject_counts:
            summary = ", ".join(f"{k}={v}" for k, v in sorted(reject_counts.items(), key=lambda x: -x[1]))
            print(f"  拒帧统计：{summary}")
        return 4

    template = build_template(descriptors)
    save_template(template, args.template)
    if preview is not None:
        cv2.imwrite(str(LOG_DIR / "enroll_preview.jpg"), draw_observation(preview, last_obs, ["enrolled"]))

    st = template["stats"]
    print("录入成功")
    print(f"  原始 {template['raw_samples']} 帧，保留 {template['num_samples']} 帧，姿态模板 {len(template['centroids'])} 个")
    print(f"  阈值 = {template['threshold']:.4f}")
    print(f"  同人距离 mean={st['mean']:.4f} std={st['std']:.4f} p50={st['p50']:.4f} p90={st['p90']:.4f} max={st['max']:.4f}")
    print(f"  模板已保存到 {args.template}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
