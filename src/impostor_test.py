import sys
from pathlib import Path

import cv2
import numpy as np

from face_engine import FaceEngine, USED_INDICES, descriptor_distance, load_template, TEMPLATE_PATH


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "impostors"
    tpl = load_template(TEMPLATE_PATH)
    cents = [np.asarray(c, dtype=np.float32) for c in tpl["centroids"]]
    engine = FaceEngine()
    print(f"threshold={tpl['threshold']:.4f} poses={len(cents)}")
    rows = []
    for p in sorted(folder.glob("*")):
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        img = cv2.imread(str(p))
        if img is None:
            print(f"{p.name}: unreadable")
            continue
        obs = engine.observe(img)
        if obs is None or not obs.ok:
            print(f"{p.name}: no usable face {obs.reasons if obs else ''}")
            continue
        desc = FaceEngine.descriptor(obs)
        ds = [descriptor_distance(desc, c) for c in cents]
        best = min(ds)
        rows.append(best)
        print(f"{p.name}: best={best:.4f} min={min(ds):.4f} all={['%.4f' % d for d in ds]}")
    engine.close()
    if rows:
        arr = np.array(rows)
        print(f"impostor best-dist: min={arr.min():.4f} median={np.median(arr):.4f} max={arr.max():.4f}")
        print(f"would reject at threshold {tpl['threshold']:.4f}: {int((arr >= tpl['threshold']).sum())}/{len(arr)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
