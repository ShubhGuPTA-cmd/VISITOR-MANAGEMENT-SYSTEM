"""
Face detection, quality gating, embedding and matching.

Pipeline (much stronger than a classic Haar-cascade + LBPH setup):
  RetinaFace/SCRFD detector  ->  5-point alignment  ->  ArcFace (ResNet-50, WebFace600K) 512-d embedding
  ->  cosine matching against a *gallery of several embeddings per person*
  ->  multi-frame fusion + runner-up margin test before anything is auto-approved.
"""
import threading
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from . import config, db

_REASON_TEXT = {
    "no_face": "No face detected",
    "too_far": "Move closer",
    "blurry": "Hold still - image is blurry",
    "dark": "Too dark - improve lighting",
    "bright": "Too bright / backlit",
    "turned": "Look straight at the camera",
    "tilted": "Keep your head level",
    "low_conf": "Face not clear enough",
    "cut_off": "Move fully into the frame",
    "multiple": "One person at a time, please",
}


@dataclass
class FaceInfo:
    ok: bool
    reasons: list = field(default_factory=list)
    bbox: Optional[list] = None          # [x1,y1,x2,y2] normalised 0..1
    det_score: float = 0.0
    size_px: int = 0
    embedding: Optional[np.ndarray] = None
    quality: float = 0.0                 # 0..1 composite (used to pick the best snapshot)

    def hint(self) -> str:
        return _REASON_TEXT.get(self.reasons[0], "") if self.reasons else ""


def decode_image(data: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    m = max(h, w)
    if m > 1280:  # keep latency predictable
        s = 1280 / m
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


class FaceEngine:
    def __init__(self):
        from insightface.app import FaceAnalysis
        config.MODEL_ROOT.mkdir(parents=True, exist_ok=True)
        self.app = FaceAnalysis(name=config.MODEL_PACK, root=str(config.MODEL_ROOT),
                                allowed_modules=["detection", "recognition"],
                                providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.5)
        self._lock = threading.Lock()          # ONNX sessions are not guaranteed re-entrant
        self._glock = threading.Lock()
        self.gallery = np.zeros((0, 512), dtype=np.float32)
        self.owner = np.zeros((0,), dtype=np.int64)
        self.reload_gallery()

    # ---------------------------------------------------------------- gallery
    def reload_gallery(self):
        with db.conn() as c:
            rows = c.execute(
                "SELECT e.person_id, e.vec FROM embeddings e JOIN people p ON p.id=e.person_id").fetchall()
        if rows:
            g = np.stack([db.blob_to_vec(r["vec"]) for r in rows])
            g /= np.linalg.norm(g, axis=1, keepdims=True) + 1e-9
            o = np.array([r["person_id"] for r in rows], dtype=np.int64)
        else:
            g, o = np.zeros((0, 512), np.float32), np.zeros((0,), np.int64)
        with self._glock:
            self.gallery, self.owner = g, o

    # -------------------------------------------------------------- analysis
    def analyse(self, img: np.ndarray, s: dict, strict: bool = True, want_embedding: bool = True) -> FaceInfo:
        with self._lock:
            if want_embedding:
                faces = self.app.get(img)
            else:  # fast path for the live preview: detector only, no embedding network
                from types import SimpleNamespace
                bboxes, kpss = self.app.det_model.detect(img, max_num=0, metric="default")
                faces = [SimpleNamespace(bbox=b[:4], det_score=float(b[4]), normed_embedding=None,
                                         kps=None if kpss is None else kpss[i])
                         for i, b in enumerate(bboxes)]
        if not faces:
            return FaceInfo(False, ["no_face"])
        faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
        f = faces[0]
        H, W = img.shape[:2]
        x1, y1, x2, y2 = [float(v) for v in f.bbox]
        w, h = x2 - x1, y2 - y1
        reasons = []

        if len(faces) > 1:
            a2 = (faces[1].bbox[2] - faces[1].bbox[0]) * (faces[1].bbox[3] - faces[1].bbox[1])
            if a2 > 0.45 * w * h and strict:
                reasons.append("multiple")
        if min(w, h) < s["min_face_px"]:
            reasons.append("too_far")
        if f.det_score < (0.6 if strict else 0.5):
            reasons.append("low_conf")
        margin = 0.01 * max(W, H)
        if x1 < -margin or y1 < -margin or x2 > W + margin or y2 > H + margin:
            reasons.append("cut_off")

        # sharpness on a scale-normalised crop, so a big sharp face and a small sharp face score alike
        cx1, cy1, cx2, cy2 = max(0, int(x1)), max(0, int(y1)), min(W, int(x2)), min(H, int(y2))
        crop = img[cy1:cy2, cx1:cx2]
        blur = 0.0
        bright = 128.0
        if crop.size:
            g = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
            blur = float(cv2.Laplacian(g, cv2.CV_64F).var())
            bright = float(g.mean())
            if blur < s["blur_min"]:
                reasons.append("blurry")
            if bright < 55:
                reasons.append("dark")
            elif bright > 205:
                reasons.append("bright")

        # head pose from the 5 landmarks (eyes, nose, mouth corners)
        yaw = roll = 0.0
        if f.kps is not None:
            le, re, nose = f.kps[0], f.kps[1], f.kps[2]
            eye_d = np.linalg.norm(re - le) + 1e-6
            yaw = float((nose[0] - (le[0] + re[0]) / 2) / eye_d)
            roll = float(np.degrees(np.arctan2(re[1] - le[1], re[0] - le[0])))
            if abs(yaw) > (0.38 if strict else 0.7):
                reasons.append("turned")
            if abs(roll) > (22 if strict else 35):
                reasons.append("tilted")

        quality = float(np.clip(
            0.35 * min(1.0, min(w, h) / 220) + 0.25 * min(1.0, blur / 200) +
            0.25 * (1 - min(1.0, abs(yaw) / 0.6)) + 0.15 * (1 - abs(bright - 125) / 125), 0, 1))

        emb = None
        if want_embedding and f.normed_embedding is not None:
            emb = f.normed_embedding.astype(np.float32)
        return FaceInfo(ok=not reasons, reasons=reasons,
                        bbox=[x1 / W, y1 / H, x2 / W, y2 / H], det_score=float(f.det_score),
                        size_px=int(min(w, h)), embedding=emb, quality=quality)

    # ------------------------------------------------------------ fusion/match
    @staticmethod
    def fuse(embs: list, min_agree: float = 0.55):
        """Combine per-frame embeddings. Returns (fused, n_used) or (None, 0) if frames disagree
        (different people in the burst, or a face swapped mid-capture)."""
        if not embs:
            return None, 0
        E = np.stack(embs)
        if len(E) == 1:
            return E[0], 1
        sims = E @ E.T
        # keep the largest group that mutually agrees
        best = max(range(len(E)), key=lambda i: int((sims[i] >= min_agree).sum()))
        keep = [j for j in range(len(E)) if sims[best, j] >= min_agree]
        if len(keep) < 2:
            return None, 0
        m = E[keep].mean(axis=0)
        return m / (np.linalg.norm(m) + 1e-9), len(keep)

    def match(self, emb: np.ndarray, s: dict, exclude_person: Optional[int] = None) -> dict:
        """Identify against the gallery. Each person's score is the mean of their two best
        embedding similarities (robust to one odd enrolment photo)."""
        with self._glock:
            G, O = self.gallery, self.owner
        if len(G) == 0:
            return {"person_id": None, "score": 0.0, "second": 0.0, "decision": "unknown", "top": []}
        sims = G @ emb
        per = {}
        for pid in np.unique(O):
            if exclude_person is not None and pid == exclude_person:
                continue
            v = np.sort(sims[O == pid])[::-1]
            per[int(pid)] = float(v[:2].mean())
        if not per:
            return {"person_id": None, "score": 0.0, "second": 0.0, "decision": "unknown", "top": []}
        ranked = sorted(per.items(), key=lambda kv: kv[1], reverse=True)
        (best_id, best), second = ranked[0], (ranked[1][1] if len(ranked) > 1 else 0.0)
        thr, margin = s["match_threshold"], s["match_margin"]
        if best >= thr and (best - second >= margin or second < thr - 0.10):
            decision = "match"
        elif best >= thr - 0.08:
            decision = "uncertain"      # close call -> never auto-approve, ask a human
        else:
            decision = "unknown"
        return {"person_id": best_id if decision == "match" else None, "score": best, "second": second,
                "decision": decision, "candidate": best_id, "top": ranked[:3]}


def encode_jpeg(img: np.ndarray, quality: int = 88, max_side: int = 720) -> bytes:
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def face_thumbnail(img: np.ndarray, bbox_norm, size: int = 320) -> bytes:
    H, W = img.shape[:2]
    x1, y1, x2, y2 = bbox_norm[0] * W, bbox_norm[1] * H, bbox_norm[2] * W, bbox_norm[3] * H
    cx, cy, side = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 1.7
    a, b = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    c, d = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
    crop = img[b:d, a:c]
    if crop.size == 0:
        crop = img
    return encode_jpeg(cv2.resize(crop, (size, size)), 85, size)
