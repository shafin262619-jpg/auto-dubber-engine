"""
render_video.py
----------------
Step 3 of the pipeline: burns the translated (e.g. Hindi) text onto a white
box that exactly covers the original subtitle / title-graphic area, and
applies a subtle per-shot Ken-Burns zoom, while keeping the original audio
untouched.

Import your validated `lines`, `titles`, and `shots` lists from a data file
(see lines_data_TEMPLATE.py) and call render().

KEY LESSONS BAKED INTO THIS SCRIPT (do not "simplify" these away):
  1. Overlays are drawn on the ORIGINAL frame BEFORE the zoom crop is
     applied, not after. This keeps the box glued to the background/subject
     naturally as the frame zooms, instead of floating separately.
  2. Padding around the detected text bbox must be ASYMMETRIC and generous
     on the BOTTOM (subtitle glyphs / anti-aliasing can bleed a few px below
     the tightest bbox). Symmetric small padding left a sliver of the
     original-language text visible on some frames in production.
  3. Text must be centered using the font's actual ink bounding box
     (draw.textbbox), NOT box_height/2. Naive centering using textbbox
     height alone ignores the font's internal ascender offset and visibly
     pushes the text a few px too high. See draw_box_text().
  4. Font must support the target script. FreeSansBold
     (/usr/share/fonts/truetype/freefont/FreeSansBold.ttf) covers Hindi
     (Devanagari), Bengali, and most scripts and is available offline in
     this environment -- confirm with fc-list before assuming any other
     font supports your target script.
  5. ALWAYS spot check rendered output by extracting frames (ffmpeg -ss ..
     -frames:v 1) from the start/mid/end of every shot and viewing them
     before presenting to the user. Do not skip this step.
"""

import cv2
import numpy as np
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

# ------------- CONFIGURE THESE -------------
FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
PAD_TOP = 4
PAD_BOTTOM = 12   # generous -- see lesson #2 above
PAD_SIDE = 8
ZOOM_AMT = 0.06   # 6% subtle zoom over the shot's duration
TEXT_COLOR = (20, 20, 20)
BOX_COLOR = (255, 255, 255)
# --------------------------------------------


def fit_font(draw, text, max_w, max_h, start_size=48, min_size=14):
    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        if (r - l) <= max_w and (b - t) <= max_h:
            return font
        size -= 2
    return ImageFont.truetype(FONT_PATH, min_size)


def draw_box_text(draw, bbox, text, W, H, radius=8,
                   pad_top=PAD_TOP, pad_bottom=PAD_BOTTOM, pad_side=PAD_SIDE):
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - pad_side); y0 = max(0, y0 - pad_top)
    x1 = min(W, x1 + pad_side); y1 = min(H, y1 + pad_bottom)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=BOX_COLOR)
    box_w, box_h = (x1 - x0 - 16), (y1 - y0 - 10)
    font = fit_font(draw, text, box_w, box_h)
    # ink-centered placement -- see lesson #3
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    tx = cx - (r - l) / 2 - l
    ty = cy - (b - t) / 2 - t
    draw.text((tx, ty), text, font=font, fill=TEXT_COLOR)


def get_active(t, items):
    """items: list of (start, end, bbox, text). Returns (bbox, text) or None."""
    for (s, e, bbox, text) in items:
        if s <= t < e:
            return bbox, text
    return None


def get_scale(t, shots, zoom_amt=ZOOM_AMT):
    for (s, e, d) in shots:
        if s <= t < e:
            prog = (t - s) / (e - s) if e > s else 0
            return 1.0 + zoom_amt * prog if d == 'in' else (1.0 + zoom_amt) - zoom_amt * prog
    return 1.0


def zoom_frame(frame_bgr, scale, W, H):
    if abs(scale - 1.0) < 1e-4:
        return frame_bgr
    new_w, new_h = int(W / scale), int(H / scale)
    x0, y0 = (W - new_w) // 2, (H - new_h) // 2
    crop = frame_bgr[y0:y0 + new_h, x0:x0 + new_w]
    return cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR)


def render(src_video, out_video, lines, titles, shots,
           start=0.0, duration=None, W=1280, H=720, fps=25):
    """
    lines / titles: list of (start_sec, end_sec, (x0,y0,x1,y1), "translated text")
    shots: list of (start_sec, end_sec, 'in'|'out')
    start/duration: process a sub-range of the source video (for batching a
      long video into chunks). duration=None means "to the end".
    """
    cap = cv2.VideoCapture(src_video)
    if start:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    nframes = int(duration * fps) if duration else int(1e9)

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
        "-i", "pipe:0",
        "-ss", str(start)] + (["-t", str(duration)] if duration else []) + [
        "-i", src_video,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "160k",
        "-shortest", out_video,
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    idx = 0
    while idx < nframes:
        ret, frame = cap.read()
        if not ret:
            break
        t = start + idx / fps
        active_line = get_active(t, lines)
        active_title = get_active(t, titles)
        if active_line or active_title:
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img)
            if active_title:
                draw_box_text(draw, active_title[0], active_title[1], W, H,
                               radius=6, pad_top=4, pad_bottom=6, pad_side=6)
            if active_line:
                draw_box_text(draw, active_line[0], active_line[1], W, H, radius=8)
            frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        scale = get_scale(t, shots)
        frame = zoom_frame(frame, scale, W, H)
        proc.stdin.write(frame.tobytes())
        idx += 1
        if idx % 250 == 0:
            print(f"{idx} frames processed (t={t:.1f}s)", file=sys.stderr)

    proc.stdin.close()
    proc.wait()
    cap.release()
    print(f"DONE: {idx} frames -> {out_video}")


if __name__ == "__main__":
    print("Import this module and call render(...) with your lines_data. "
          "See lines_data_TEMPLATE.py and the playbook markdown.")
