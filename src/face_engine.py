from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "face_landmarker.task"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
TEMPLATE_PATH = DATA_DIR / "face_template.json"
SETTINGS_PATH = DATA_DIR / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def camera_index() -> int:
    try:
        return int(load_settings().get("camera_index", 0))
    except Exception:
        return 0

FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
             397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
             172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
LEFT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
RIGHT_EYE = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466]
LEFT_BROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
RIGHT_BROW = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
NOSE = [168, 6, 197, 195, 5, 4, 1, 2, 98, 327, 326, 294, 19, 94]
LIPS_OUTER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409,
              270, 269, 267, 0, 37, 39, 40, 185]

USED_INDICES = sorted(set(FACE_OVAL + LEFT_EYE + RIGHT_EYE + LEFT_BROW + RIGHT_BROW + NOSE + LIPS_OUTER))

MAX_ROLL_DEG = 38.0
MAX_YAW = 0.55
MIN_INTEROCULAR_PX = 30.0
MAX_INTEROCULAR_PX = 340.0


@dataclass
class FaceObservation:
    points: np.ndarray
    rotation_deg: float
    interocular: float
    eyes: np.ndarray
    mouth_width: float
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _rot2d(deg: float) -> np.ndarray:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s], [s, c]], dtype=np.float32)


class FaceEngine:
    def __init__(self, model_path: Path | str = MODEL_PATH, num_faces: int = 1):
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=num_faces,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)

    def close(self) -> None:
        try:
            self.landmarker.close()
        except Exception:
            pass

    def observe(self, bgr: np.ndarray) -> FaceObservation | None:
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        result = self.landmarker.detect(mp_image)
        if not result.face_landmarks:
            return None
        lm = result.face_landmarks[0]
        pts = np.array([[p.x * w, p.y * h] for p in lm], dtype=np.float32)
        if len(pts) < 468:
            return None

        left_eye = pts[LEFT_EYE].mean(axis=0)
        right_eye = pts[RIGHT_EYE].mean(axis=0)
        eye_vec = right_eye - left_eye
        interocular = float(np.linalg.norm(eye_vec))
        rotation_deg = float(np.degrees(math.atan2(eye_vec[1], eye_vec[0])))

        eye_mid = (left_eye + right_eye) * 0.5
        if len(pts) >= 478:
            nose_tip = pts[4]
        else:
            nose_tip = pts[NOSE].mean(axis=0)
        yaw = float((nose_tip[0] - eye_mid[0]) / max(interocular, 1e-6))

        mouth_left = pts[61]
        mouth_right = pts[291]
        mouth_width = float(np.linalg.norm(mouth_right - mouth_left))

        reasons: list[str] = []
        if abs(rotation_deg) > MAX_ROLL_DEG:
            reasons.append("roll")
        if abs(yaw) > MAX_YAW:
            reasons.append("yaw")
        if interocular < MIN_INTEROCULAR_PX:
            reasons.append("too_far")
        if interocular > MAX_INTEROCULAR_PX:
            reasons.append("too_close")

        return FaceObservation(
            points=pts,
            rotation_deg=rotation_deg,
            interocular=interocular,
            eyes=np.vstack([left_eye, right_eye]),
            mouth_width=mouth_width,
            ok=not reasons,
            reasons=reasons,
        )

    @staticmethod
    def descriptor(obs: FaceObservation) -> np.ndarray:
        pts = obs.points[USED_INDICES, :2].astype(np.float64)
        diff = pts[:, None, :] - pts[None, :, :]
        dist = np.sqrt((diff * diff).sum(axis=2))
        scale = float(dist.mean())
        if scale < 1e-6:
            return np.zeros((len(pts) * (len(pts) - 1)) // 2, dtype=np.float32)
        dist /= scale
        iu = np.triu_indices(len(pts), k=1)
        return dist[iu].astype(np.float32)


def descriptor_distance(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    return float(np.abs(a - b).mean())


def _l1_matrix(stack: np.ndarray) -> np.ndarray:
    n = len(stack)
    out = np.empty((n, n), dtype=np.float32)
    for i in range(n):
        out[i] = np.abs(stack - stack[i]).mean(axis=1)
    return out


def build_template(descriptors: list[np.ndarray], max_poses: int = 8) -> dict:
    if len(descriptors) < 10:
        raise ValueError(f"not enough samples: {len(descriptors)}")
    stack = np.stack(descriptors, axis=0)
    center = np.median(stack, axis=0)
    rough = np.abs(stack - center).mean(axis=1)
    keep = rough <= np.percentile(rough, 95)
    if int(keep.sum()) >= 10:
        stack = stack[keep]

    n = len(stack)
    k = int(min(max_poses, max(3, n // 10)))
    dm = _l1_matrix(stack)
    rep_idx = [int(np.argmin(dm.sum(axis=1)))]
    for _ in range(k - 1):
        nearest = dm[:, rep_idx].min(axis=1)
        nxt = int(np.argmax(nearest))
        if nxt in rep_idx:
            break
        rep_idx.append(nxt)
    reps = stack[rep_idx]

    nonrep = [i for i in range(n) if i not in set(rep_idx)]
    if len(nonrep) >= 10:
        calib = dm[np.ix_(nonrep, rep_idx)].min(axis=1)
    else:
        calib = dm[:, rep_idx].min(axis=1)
    p50, p90 = np.percentile(calib, [50, 90])
    std = float(calib.std())
    threshold = float(min(max(p90 * 3.0, p50 * 2.0, 0.022), 0.052))

    return {
        "version": 2,
        "descriptor": "edm",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "num_samples": n,
        "raw_samples": len(descriptors),
        "used_indices": USED_INDICES,
        "centroids": [r.tolist() for r in reps],
        "threshold": threshold,
        "stats": {
            "mean": float(calib.mean()),
            "std": std,
            "p50": float(p50),
            "p90": float(p90),
            "max": float(calib.max()),
        },
    }


def template_centroids(template: dict) -> list[np.ndarray]:
    if "centroids" in template:
        return [np.asarray(c, dtype=np.float32) for c in template["centroids"]]
    return [np.asarray(template["template"], dtype=np.float32)]


def template_distance(desc: np.ndarray, template: dict) -> float:
    return min(descriptor_distance(desc, c) for c in template_centroids(template))


def save_template(template: dict, path: Path = TEMPLATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")


def load_template(path: Path = TEMPLATE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def draw_observation(bgr: np.ndarray, obs: FaceObservation | None, lines: list[str],
                     matched: bool | None = None) -> np.ndarray:
    out = bgr.copy()
    color = (0, 200, 0) if obs is not None and obs.ok else (0, 0, 255)
    if obs is not None:
        for idx in USED_INDICES:
            x, y = int(obs.points[idx][0]), int(obs.points[idx][1])
            cv2.circle(out, (x, y), 1, color, -1)
        lx, ly = obs.eyes[0]
        rx, ry = obs.eyes[1]
        cv2.line(out, (int(lx), int(ly)), (int(rx), int(ry)), (255, 200, 0), 1)

    y = 24
    for line in lines:
        cv2.putText(out, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        y += 26

    if matched is not None:
        banner = "FACE OK" if matched else "NO MATCH"
        color = (0, 180, 0) if matched else (0, 0, 200)
        h, w = out.shape[:2]
        cv2.rectangle(out, (0, 0), (w, 6), color, -1)
        cv2.putText(out, banner, (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
    return out


_FONT_PATHS = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]
_font_cache: dict = {}


def _get_font(size: int):
    if size not in _font_cache:
        for path in _FONT_PATHS:
            try:
                _font_cache[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def draw_text_cn(bgr: np.ndarray, lines: list[str], origin: tuple[int, int] = (20, 20),
                 size: int = 34, color: tuple[int, int, int] = (255, 255, 255),
                 stroke: int = 2) -> np.ndarray:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    font = _get_font(size)
    x, y = origin
    for line in lines:
        draw.text((x, y), line, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += int(size * 1.45)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def open_camera(index=None, width: int = 1280, height: int = 720) -> cv2.VideoCapture:
    if index is None:
        index = camera_index()
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap
