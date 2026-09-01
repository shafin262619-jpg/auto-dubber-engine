# Project state — video_synced-3.mp4 (Chinese → Hindi subtitle localization)

Source video: 1280x720, 25fps, ~581.6s (9m41.6s) total, uploaded by the user
as `video_synced-3.mp4`.

## Validated and approved by the user: 0.00s – 65.00s

- Subtitle text y-band established: `y0=613, y1=660` (tight glyph band, 720p
  frame). Reuse this for the rest of the video unless a shot uses a visibly
  different subtitle style/position.
- 19 dialogue-line segments transcribed, translated, and bbox-verified.
- 2 occurrences of a recurring background title graphic ("直播算命现场" neon
  sign) found and handled: 0.00-5.44s and 41.38-44.25s. **Do not assume
  these are the only two occurrences in the full video — re-scan every wide/
  establishing shot for the rest of the runtime (§3.4 of PLAYBOOK.md).**
- Shot/zoom list built from scene-cut detection for 0-65s, alternating
  zoom-in/zoom-out per shot, 6% amplitude.
- Rendered, spot-checked (§4 of PLAYBOOK.md), and approved by the user as
  "সম্পূর্ণ ঠিকঠাক আছে" (fully correct) after two rounds of correction. The
  approved output is `demo_65s_v3_fixed.mp4`.

The exact validated data for this range is in
`lines_data_CHECKPOINT_0-65s.py` (same schema as `lines_data_TEMPLATE.py`).
Reuse it as-is for 0-65s — do not re-derive it.

## Two real mistakes made and fixed during this range (see PLAYBOOK.md §5)

1. First pass: subtitle boxes were ~85px tall and sometimes near
   full-frame-width (way oversized vs. the actual text) — root cause was
   trusting an automated brightness-threshold bbox instead of manually
   reading a pixel-grid overlay. Fixed for all 19 lines.
2. Second pass: the line at 41.38-44.25s ("信不信" → "विश्वास करो या न करो")
   was completely missing from the first two render attempts — root cause
   was a presence-detection gap that was actually a missed line, not real
   silence. The user caught this via a screenshot. Fixed by re-segmenting
   with a tighter band and content-change-based splitting (§3.2), which
   also surfaced the second title occurrence at the same timestamp.
   Also fixed in the same pass: text vertical centering was ~4px too high
   (naive box_height/2 centering); switched to ink-bbox centering.

## Remaining work: 65.00s – 581.6s (~8.6 minutes)

Not yet started. Follow PLAYBOOK.md §3 in full for this range — do not
assume the pacing/frequency of dialogue lines or title recurrences from the
first 65s generalizes; verify fresh with the same rigor.

Recommended batching: process and checkpoint in ~90-120s chunks (§6 of
PLAYBOOK.md), but do the final render for the *whole* 65s-581.6s range (or
even the whole video, 0-581.6s, re-rendering the already-approved part too
for a single consistent output file) in one continuous `render()` call so
audio stays in sync throughout.

Before presenting the completed full video to the user, run the full §4 QA
checklist over the **entire** output, including re-spot-checking the
already-approved 0-65s section in the final combined file (a re-encode or
concat step could in principle reintroduce sync drift — verify it didn't).
