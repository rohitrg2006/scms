"""
face_recognition_module.py — EduTrack Pro face recognition handler
Uses OpenCV (LBPH algorithm) — no dlib, no cmake required.
Install: pip install opencv-contrib-python numpy
"""

import base64
import io
import json
import os
import numpy as np
from PIL import Image

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# OpenCV ships a Haar cascade for face detection
CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml" if CV2_AVAILABLE else ""


# ── HELPERS ───────────────────────────────────────────────────────────────────

def check_available() -> tuple:
    if not CV2_AVAILABLE:
        return False, "opencv-contrib-python is not installed. Run: pip install opencv-contrib-python"
    return True, ""


def _b64_to_bgr(b64_string: str):
    """Convert base64 data-URL to a BGR numpy array (OpenCV format)."""
    try:
        if "," in b64_string:
            b64_string = b64_string.split(",", 1)[1]
        raw = base64.b64decode(b64_string)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        arr = np.array(img)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _detect_face_roi(bgr_img):
    """
    Detect the largest face in the image.
    Returns a grayscale 200x200 ROI, or None if no face found.
    """
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(CASCADE_PATH)
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
    )
    if len(faces) == 0:
        return None, "No face detected. Please look directly at the camera."
    if len(faces) > 1:
        return None, "Multiple faces detected. Please ensure only one person is in frame."

    x, y, w, h = faces[0]
    roi = gray[y:y+h, x:x+w]
    roi = cv2.resize(roi, (200, 200))
    return roi, ""


def _encoding_to_json(roi: np.ndarray) -> str:
    """Flatten the face ROI to a JSON list (our 'encoding')."""
    return json.dumps(roi.flatten().tolist())


def _json_to_encoding(s: str) -> np.ndarray:
    arr = np.array(json.loads(s), dtype=np.uint8)
    return arr.reshape((200, 200))


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def encode_face_from_b64(b64_image: str) -> tuple:
    """
    Given a base64 image, return (encoding_ndarray, error_message).
    encoding is None on failure.
    """
    ok, err = check_available()
    if not ok:
        return None, err

    bgr = _b64_to_bgr(b64_image)
    if bgr is None:
        return None, "Could not decode image."

    roi, err = _detect_face_roi(bgr)
    if roi is None:
        return None, err

    return roi, ""


def match_face_from_b64(b64_image: str, candidates: list, tolerance: float = 70.0) -> tuple:
    """
    Match a base64 image against stored encodings using LBPH histogram comparison.
    tolerance: lower = stricter (0-100 range, recommended 60-80)
    Returns (student_id, error_message).
    """
    ok, err = check_available()
    if not ok:
        return None, err

    bgr = _b64_to_bgr(b64_image)
    if bgr is None:
        return None, "Could not decode image."

    roi, err = _detect_face_roi(bgr)
    if roi is None:
        return None, err

    # Compute LBP histogram for the captured face
    lbph = cv2.face.LBPHFaceRecognizer_create()

    best_sid  = None
    best_dist = float("inf")

    for cand in candidates:
        try:
            known_roi = _json_to_encoding(cand["encoding_json"])
            # Train on single sample, then predict
            lbph.train([known_roi], np.array([0]))
            label, confidence = lbph.predict(roi)
            if confidence < best_dist:
                best_dist = confidence
                best_sid  = cand["student_id"]
        except Exception:
            continue

    if best_dist <= tolerance:
        return best_sid, ""

    return None, f"Face not recognised (confidence: {best_dist:.1f}, threshold: {tolerance})."
