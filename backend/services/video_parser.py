"""Dash cam video ingestion — Groq's vision API (llama-4-scout) only accepts
still images, there is no video content type on any Groq tier. So a dashcam
clip is converted to a handful of evenly-spaced still frames here, which then
flow through the existing image pipeline (ask_with_images) unchanged.
"""

import glob
import os

import cv2

# Same size cap as storage.py's photo downscaling — keeps each frame well
# under Groq's vision request-size limit.
_MAX_FRAME_DIM = 1600
_JPEG_QUALITY = 85


def extract_frames(claim_id: str, video_path: str, max_frames: int = 4) -> list[str]:
    """Extract up to `max_frames` evenly-spaced frames from a dashcam video.

    Returns a list of saved JPEG frame paths, in chronological order, under
    data/claims/{claim_id}/dashcam_frames/. Returns [] if the video can't be
    read (corrupt file, unsupported codec, etc.) — callers treat that the same
    as "no dashcam evidence provided".
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        return []

    n = min(max_frames, total_frames)
    # Evenly-spaced frame indices, including the first and last frame.
    indices = [int(i * (total_frames - 1) / max(n - 1, 1)) for i in range(n)]

    # video_path is .../{claim_id}/docs/dashcam/<file> — climb to the claim dir
    # so frames land in a sibling .../{claim_id}/dashcam_frames/ folder.
    claim_dir = os.path.dirname(os.path.dirname(os.path.dirname(video_path)))
    out_dir = os.path.join(claim_dir, "dashcam_frames")
    os.makedirs(out_dir, exist_ok=True)
    # Clear frames from a prior extraction (e.g. a re-investigation) so stale
    # frames never linger alongside — or get mistaken for — the current run's.
    for stale in glob.glob(os.path.join(out_dir, "frame_*.jpg")):
        os.remove(stale)

    saved_paths: list[str] = []
    for seq, frame_idx in enumerate(indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        scale = min(1.0, _MAX_FRAME_DIM / max(w, h))
        if scale < 1.0:
            frame = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
        out_path = os.path.join(out_dir, f"frame_{seq:02d}.jpg")
        cv2.imwrite(out_path, frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
        saved_paths.append(out_path)

    cap.release()
    return saved_paths
