from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOG_DIR = _ROOT / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
if sys.stdout is None:
    sys.stdout = open(_LOG_DIR / "verify.log", "a", encoding="utf-8", buffering=1)
if sys.stderr is None:
    sys.stderr = sys.stdout

import cv2
import numpy as np

from face_engine import (
    LOG_DIR,
    TEMPLATE_PATH,
    FaceEngine,
    draw_observation,
    draw_text_cn,
    load_template,
    open_camera,
    template_distance,
)

WINDOW = "人脸验证"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="实时人脸轮廓验证")
    p.add_argument("--camera", type=int, default=None)
    p.add_argument("--duration", type=float, default=12.0)
    p.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    p.add_argument("--threshold", type=float, default=None)
    p.add_argument("--window", type=int, default=10)
    p.add_argument("--need", type=int, default=6)
    p.add_argument("--smooth", type=int, default=7)
    p.add_argument("--no-window", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tpl = load_template(args.template)
    threshold = float(args.threshold if args.threshold is not None else tpl["threshold"])
    poses = len(tpl.get("centroids", [tpl.get("template")]))
    print(f"模板：{tpl['num_samples']} 帧 / {poses} 个姿态，阈值 {threshold:.4f}，录入时间 {tpl['created']}")

    cap = open_camera(args.camera)
    if not cap.isOpened():
        print("错误：无法打开摄像头")
        return 2
    engine = FaceEngine()

    dists: list[float] = []
    log_rows: list[str] = []
    desc_buf: deque[np.ndarray] = deque(maxlen=max(args.smooth, 1))
    matched_flags: deque[bool] = deque(maxlen=args.window)
    valid = 0
    frames = 0
    unlocked = False
    unlocked_at = None
    start = time.time()
    preview = None

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            now = time.time()
            if now - start > args.duration:
                break
            frames += 1

            obs = engine.observe(frame)
            status = "未检测到人脸"
            dist = float("nan")
            is_match = False
            if obs is not None and obs.ok:
                valid += 1
                desc = FaceEngine.descriptor(obs)
                desc_buf.append(desc)
                rep = np.median(np.stack(desc_buf, axis=0), axis=0) if len(desc_buf) > 1 else desc
                dist = template_distance(rep, tpl)
                dists.append(dist)
                is_match = dist < threshold
                matched_flags.append(is_match)
                status = "匹配" if is_match else "不匹配"
                log_rows.append(f"{now - start:.3f},{dist:.6f},{int(is_match)}")
                if not unlocked and len(matched_flags) >= args.need and sum(matched_flags) >= args.need:
                    unlocked = True
                    unlocked_at = now - start
                    print(f"解锁成功：用时 {unlocked_at:.2f} 秒，距离 {dist:.4f}")
                preview = frame.copy()
            elif obs is not None:
                status = "请调整： " + ",".join(obs.reasons)
                desc_buf.clear()
            else:
                matched_flags.append(False)
                desc_buf.clear()

            lines = [
                f"状态：{status}",
                f"距离 {dist:.4f}   阈值 {threshold:.4f}",
                f"连续匹配 {sum(matched_flags)}/{len(matched_flags)}",
            ]
            annotated = draw_observation(frame, obs, [])
            annotated = draw_text_cn(annotated, lines, origin=(20, 14), size=30)
            if unlocked:
                annotated = draw_text_cn(annotated, ["解锁成功"], origin=(20, 150), size=44, color=(0, 255, 0))
            if not args.no_window:
                cv2.imshow(WINDOW, annotated)
                if (cv2.waitKey(1) & 0xFF) == 27:
                    break
    finally:
        cap.release()
        engine.close()
        if not args.no_window:
            cv2.destroyAllWindows()

    if dists:
        arr = np.array(dists)
        p10, p50, p90 = np.percentile(arr, [10, 50, 90])
        ratio = float((arr < threshold).mean())
        (LOG_DIR / "verify_dists.csv").write_text("t,dist,match\n" + "\n".join(log_rows) + "\n", encoding="utf-8")
        print(f"帧数={frames} 有效={valid} 匹配率={ratio:.3f}")
        print(f"距离 p10={p10:.4f} p50={p50:.4f} p90={p90:.4f} 最小={arr.min():.4f} 最大={arr.max():.4f}")
        if preview is not None:
            cv2.imwrite(str(LOG_DIR / "verify_preview.jpg"), preview)
    else:
        print(f"帧数={frames} 有效=0（没有可用人脸）")

    print("结果：" + ("解锁" if unlocked else "未解锁"))
    return 0 if unlocked else 1


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
