import sys
import cv2

def open_camera():
    for backend, name in ((cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF"), (cv2.CAP_ANY, "ANY")):
        cap = cv2.VideoCapture(0, backend)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                print(f"camera opened via {name}: {frame.shape[1]}x{frame.shape[0]}")
                cap.release()
                return True
            cap.release()
        print(f"{name} failed")
    return False

if __name__ == "__main__":
    sys.exit(0 if open_camera() else 1)
