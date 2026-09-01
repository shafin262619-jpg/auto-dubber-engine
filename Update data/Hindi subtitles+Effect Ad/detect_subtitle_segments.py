"""
detect_subtitle_segments.py
----------------------------
Finds every distinct burned-in-subtitle "line" in a video, with start/end
timestamps, WITHOUT leaving gaps. This is step 1 of the subtitle-localization
pipeline. Read subtitle_localization_playbook.md before using this.

WHY THIS EXISTS / LESSON LEARNED:
A naive approach (sample video at low fps, threshold brightness in a wide
band, split on gaps) silently DROPS real subtitle lines whenever a nearby
bright light source or bright background pushes the "no subtitle" frames
above the presence threshold in a way that hides a genuine text-content
change, OR drops short lines as "noise". This happened in production and a
2.9s line ("信不信") was completely missed. The fix that worked reliably:
  1. Use a TIGHT y-band (just the text row, not a generous margin).
  2. Use 8 fps (0.125s resolution) minimum -- 4 fps is not enough to catch
     short lines cleanly.
  3. Never rely on "presence" alone to find gaps -- in a densely-narrated
     video, subtitles can be present 100% of the time with zero true gaps.
     Segment boundaries should come from CONTENT CHANGE (mask diff), not
     from presence/absence.
  4. After generating segments, you MUST spot-check by extracting a frame
     from the middle of every segment and actually reading it (Claude's own
     vision), not trust the algorithm blindly.

USAGE:
  1. First, manually find the subtitle's tight y-band once per video:
     - Extract 2-3 frames that contain subtitle text (different backgrounds:
       one dark scene, one bright scene).
     - Crop the bottom ~20% of the frame and overlay a pixel grid (see
       make_grid() below) so you can read exact coordinates.
     - View the grid images and note the y0/y1 where the text glyphs
       actually sit (typically a ~40-50px tall band in a 720p video).
     - Set BAND_Y0 / BAND_Y1 below to that (tight, not generous).
  2. Run this script. It prints segments as (start_sec, end_sec).
  3. Manually verify: sum of segment durations should look right, and there
     should be NO unexplained multi-second gaps unless you have actually
     confirmed (by viewing a frame in that gap) that there is really no
     subtitle on screen there.
"""

import cv2
import numpy as np
import subprocess
import glob
import os
import sys

# ------------- CONFIGURE THESE FOR YOUR VIDEO -------------
VIDEO_PATH = "/mnt/user-data/uploads/your_video.mp4"
WORKDIR = "/home/claude/work/detect_tmp"
ANALYZE_FPS = 8            # >=8 recommended; 4 is too coarse, will miss short lines
BAND_Y0, BAND_Y1 = 605, 668   # TIGHT crop around the text row only (1280x720 video).
                               # Re-measure this per video with make_grid().
WHITE_THRESH = 205            # grayscale threshold to call a pixel "text-white"
PRESENCE_MIN_RATIO = 0.015    # fraction of band that must be white to count as "on"
CONTENT_CHANGE_DIFF = 0.50    # xor/union ratio between consecutive masks that
                               # signals a NEW line (not just the same line held)
MIN_ANALYZE_SECONDS = None    # None = whole video; or set e.g. 90 to do it in chunks
START_OFFSET = 0.0            # seconds into the video to start analysis (for chunking)
# ------------------------------------------------------------


def make_grid(frame_path, y0, y1, out_path, x_start=0, x_end=None):
    """Overlay a pixel coordinate grid on a crop so a human (or Claude's vision)
    can read exact bbox coordinates directly off the image. ALWAYS use this
    instead of trusting an automated bbox -- automated brightness bboxes are
    unreliable on bright backgrounds and can massively over- or under-size
    the box (this happened in production: a box came out ~10x too wide)."""
    img = cv2.imread(frame_path)
    x_end = x_end or img.shape[1]
    crop = img[y0:y1, x_start:x_end].copy()
    for x in range(0, crop.shape[1], 50):
        cv2.line(crop, (x, 0), (x, crop.shape[0]), (0, 255, 0), 1)
        cv2.putText(crop, str(x_start + x), (x + 2, 15), cv2.FONT_HERSHEY_SIMPLEX,
                     0.4, (0, 255, 0), 1)
    for y in range(0, crop.shape[0], 20):
        cv2.line(crop, (0, y), (crop.shape[1], y), (0, 200, 255), 1)
        cv2.putText(crop, str(y0 + y), (2, y + 12), cv2.FONT_HERSHEY_SIMPLEX,
                     0.4, (0, 200, 255), 1)
    cv2.imwrite(out_path, crop)
    return out_path


def extract_frames(video_path, out_dir, fps, start=0.0, duration=None):
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", video_path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vf", f"fps={fps}", os.path.join(out_dir, "f_%05d.jpg"),
            "-hide_banner", "-loglevel", "error"]
    subprocess.run(cmd, check=True)


def band_mask(img, y0, y1):
    band = img[y0:y1, :]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    _, white = cv2.threshold(gray, WHITE_THRESH, 255, cv2.THRESH_BINARY)
    return white


def mask_diff(a, b):
    xor = cv2.bitwise_xor(a, b)
    union = cv2.bitwise_or(a, b)
    s = int(union.sum())
    return 0.0 if s == 0 else xor.sum() / s


def segment(video_path, y0, y1, fps, start=0.0, duration=None):
    frame_dir = os.path.join(WORKDIR, "frames")
    extract_frames(video_path, frame_dir, fps, start, duration)
    files = sorted(glob.glob(os.path.join(frame_dir, "f_*.jpg")))
    masks = [band_mask(cv2.imread(f), y0, y1) for f in files]
    presence = [(m.mean() / 255.0) > PRESENCE_MIN_RATIO for m in masks]
    diffs = [0.0] + [mask_diff(masks[i - 1], masks[i]) for i in range(1, len(masks))]

    segments = []
    i = 0
    N = len(masks)
    while i < N:
        if not presence[i]:
            i += 1
            continue
        s = i
        j = i + 1
        while j < N and presence[j] and diffs[j] < CONTENT_CHANGE_DIFF:
            j += 1
        e = j - 1
        segments.append((start + s / fps, start + (e + 1) / fps))
        i = j

    # report gaps (periods with no subtitle) so you can manually confirm them
    gaps = []
    prev_end = start
    for s, e in segments:
        if s - prev_end > 1.0 / fps + 0.01:
            gaps.append((prev_end, s))
        prev_end = e
    return segments, gaps


if __name__ == "__main__":
    os.makedirs(WORKDIR, exist_ok=True)
    segs, gaps = segment(VIDEO_PATH, BAND_Y0, BAND_Y1, ANALYZE_FPS,
                          START_OFFSET, MIN_ANALYZE_SECONDS)
    print(f"Found {len(segs)} segments:")
    for s, e in segs:
        print(f"  ({s:.2f}, {e:.2f})  dur={e-s:.2f}")
    print(f"\n{len(gaps)} gap(s) with NO subtitle detected -- verify each one by")
    print("extracting a frame in the middle of the gap and viewing it:")
    for g in gaps:
        mid = (g[0] + g[1]) / 2
        print(f"  gap ({g[0]:.2f}, {g[1]:.2f})  -> check frame at t={mid:.2f}")
