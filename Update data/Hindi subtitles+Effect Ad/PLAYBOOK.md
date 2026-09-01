# Playbook: Replace burned-in video subtitles with a translated language + subtle per-shot zoom

Give this whole file (plus the three companion `.py` files:
`detect_subtitle_segments.py`, `render_video.py`, `lines_data_TEMPLATE.py`)
to Claude at the start of a new conversation, along with the source video.
Follow it exactly, in order. It was written after building and correcting
this exact pipeline on a real video, including fixing two real mistakes —
those fixes are load-bearing, not optional polish. Do not "simplify" this
process.

Target quality bar: **the final output must be correct on the first delivery
to the user, with no back-and-forth correction round needed.** Everything
below exists to make that possible — mainly the mandatory self-QA steps.
Do not skip them to save time.

---

## 0. What this task is

Input: a video with burned-in (hardcoded, not a separate subtitle track)
on-screen text in a source language — dialogue subtitles, and sometimes
recurring background graphics/signs that also contain source-language text.

Output: a video, same length, same audio, where:
- every instance of the source-language subtitle text is covered by a
  precisely-fitted white box (or blur, if the user prefers — confirm) sized
  to the actual text, with the translated text placed on top of it, and
- every camera shot has a light zoom-in OR zoom-out (never both on the same
  shot) applied for a subtle cinematic feel.

This is fundamentally a **manual, vision-driven transcription and
translation task with automated assistance for timing/positioning** — not a
fully automatic OCR/MT pipeline. There is no reliable offline OCR or
translation API in this environment (no internet access). Claude must read
every line of source text itself (using its vision) and translate it itself.
Budget time accordingly — do not attempt to shortcut this with brightness
thresholding alone (see the pitfalls in §5).

---

## 1. Inputs to collect from the user BEFORE starting

Ask for (or confirm you already have) all of the following. Do not guess if
missing:

1. **The video file.**
2. **Source and target language.** (This playbook was built for Chinese →
   Hindi, but the method is language-agnostic.)
3. **Scope**: the whole video, or a specific time range? If the video is
   long (say, over ~2 minutes), tell the user you will process it in
   checkpointed batches (see §6) and confirm the batch size is fine.
4. **Box style preference**: white box with translated text on top
   (default / validated), or blur instead. If blur is requested, warn the
   user that blurring text at video resolution often still leaves it
   partially legible, and white-box is more reliable at hiding it — get an
   explicit choice.
5. **Zoom effect**: on by default, ~6% subtle Ken Burns, alternating
   in/out per shot. Confirm intensity is acceptable, or ask if they want a
   demo first.
6. **Tone/register for translation**: literal vs. natural/colloquial. This
   playbook defaults to natural, meaning-preserving translation (not
   word-for-word), matching spoken/casual tone of the source. Confirm.
7. If a checkpoint file (like this playbook's §7, "project state") is
   included, read it — it tells you exactly what has already been
   validated and where to resume.

Once you have these, tell the user your plan in 2-3 sentences and proceed —
do not ask more than necessary; use sensible defaults from this playbook and
state them.

---

## 2. Environment checks (do this once, first)

Run and confirm before anything else:

```bash
ffmpeg -version; ffprobe -version
python3 -c "import cv2, PIL; print('ok')"
fc-list | grep -i -E "freesans|freeserif"   # need a font covering the target script
```

`/usr/share/fonts/truetype/freefont/FreeSansBold.ttf` covers Hindi
(Devanagari), Bengali, and most other scripts in this environment and is
available **offline**. Verify by rendering a short test string in the
target script with PIL and viewing the image — do not assume, confirm.

There is **no internet access** in this environment. That means: no
installable OCR language packs, no translation API, no font downloads.
Read source text with Claude's own vision. Render target text with a
locally-available font that has been confirmed (by rendering and viewing)
to support the target script.

Get basic video facts:
```bash
ffprobe -v error -show_format -show_streams your_video.mp4
```
Note resolution, fps, duration — you'll need these as constants in the
scripts below.

---

## 3. Step-by-step pipeline

### 3.1 Find the subtitle text's tight y-band (once per video)

Extract a couple of frames that contain subtitle text — pick one with a
dark background and one with a bright background, since brightness-based
detection behaves very differently on each.

```bash
ffmpeg -y -ss <t> -i your_video.mp4 -frames:v 1 -q:v 2 frame.jpg
```

Use `make_grid()` from `detect_subtitle_segments.py` to overlay pixel
coordinates on the bottom ~20% of each frame, view the result, and read off
the y-range where the actual glyphs sit. Keep it **tight** — in the video
this playbook was built on it was a ~47px band (y=613 to y=660 in a 720px-
tall frame), not the generous ~85px band a first attempt produced.

### 3.2 Segment the subtitle lines, with zero unexplained gaps

Use `detect_subtitle_segments.py`. Set `BAND_Y0/BAND_Y1` from §3.1, run it.

**Do not trust "presence" alone to define gaps.** In a densely-narrated
video subtitles can be on screen ~100% of the time; a "no subtitle" gap the
algorithm reports might really be a **missed line**, not real silence — this
happened in production (a ~2.9s line was silently dropped this way).
Segment boundaries must come from **content change** (mask diff between
consecutive frames), and every reported gap must be spot-checked: extract
and *view* a frame from the middle of the gap. If there's text on screen,
your band/threshold missed it — fix and re-run, don't just add the missing
segment by hand and move on, since there may be others.

Use **8fps minimum** for the analysis pass. 4fps (0.25s resolution) is
demonstrably too coarse and can merge or skip short lines.

### 3.3 Read and translate every single line

For every segment from §3.2:

1. Grab the middle timestamp, extract the full-resolution frame.
2. Crop tightly around the text using `make_grid()` and **view the grid
   image** (this is mandatory — do not estimate bbox from the automated
   mask alone; brightness thresholding fails badly on bright backgrounds,
   verified failure mode: a bbox came out nearly full-frame-width when the
   real text was a third of that).
3. Read off the tight `(x0, y0, x1, y1)` bbox directly from the grid's
   coordinate labels.
4. Read the source text yourself and translate it for meaning/tone (not
   literal word-for-word), keeping it close in length to the original so it
   fits comfortably in the same box.
5. Record the tuple `(start, end, bbox, translated_text)` in your data file
   (see `lines_data_TEMPLATE.py`).

Do this for **every** segment — do not sample/skip lines to save time, and
do not batch-guess translations from context without reading each frame.

### 3.4 Find recurring background graphics/titles

Separately from the dialogue subtitle band, scan wide/establishing shots for
any other burned-in source-language text — signs, on-screen titles, UI
labels that are part of the scenery. **These often recur across multiple,
non-adjacent shots that reuse the same background** — a title that appears
in shot 1 can reappear identically in shot 12 of the same video. Do not
assume a single occurrence. When you find one:
- Confirm every shot in which it's clearly legible (skip
  blurred/out-of-focus background occurrences in close-up shots — covering
  those looks worse than leaving them, since they're not really readable
  anyway; use judgment, but be consistent).
- Get a tight bbox the same way as §3.3 (grid overlay, view it, read
  coordinates) for **each** occurrence — camera framing can differ between
  occurrences even if the graphic itself is identical, so the bbox may
  differ.
- Record as `(start, end, bbox, translated_text)` in the `titles` list.

### 3.5 Determine shots for the zoom effect

```bash
ffmpeg -i your_video.mp4 -vf "select='gt(scene,0.15)',showinfo" -f null - 2>&1 \
  | grep -oP 'pts_time:\K[0-9.]+'
```
This gives camera-cut timestamps. Build a `shots` list of
`(start, end, 'in'|'out')`, alternating direction per shot (or vary
otherwise, per the user's brief — the constraint is *one* effect per shot,
never both zoom-in and zoom-out on the same shot).

### 3.6 Render

Use `render_video.py`'s `render()` function with your `lines`, `titles`,
`shots`. Key implementation details already handled correctly in that
script — do not change them without good reason:
- Overlays are composited **before** the zoom crop, so boxes/text move
  naturally with the zoomed background instead of floating independently.
- Padding around each bbox is **asymmetric**: more on the bottom than the
  top (glyph anti-aliasing / descenders bleed low). Symmetric small padding
  left a visible sliver of source text in production — verified failure
  mode, now fixed with `PAD_TOP=4, PAD_BOTTOM=12, PAD_SIDE=8`. Re-tune only
  if your spot-checks (§4) show a problem, and re-check afterward.
- Text is centered using the font's **ink bounding box**
  (`draw.textbbox`), not `box_height/2`. Naive centering visibly pushed
  text a few px too high in production — verified failure mode, now fixed.
- Original audio is muxed in from the source, untouched.

---

## 4. Mandatory self-QA — do ALL of this before showing the user anything

This is what makes "no review needed" possible. Do not skip steps to save
time; each one exists because it caught a real bug during development.

1. **Coverage check**: list your `lines` segments end-to-end and confirm
   there is no gap you haven't explicitly verified (§3.2).
2. **Recurrence check**: confirm you scanned for the background
   title/graphic (§3.4) across the *entire* processed range, not just the
   first shot it appeared in.
3. **Bbox sanity check**: for every line, the bbox width should roughly
   match the visual length of the source text you read — if a bbox looks
   like it spans almost the full frame width for a short line, that's a red
   flag; re-derive it with the grid method.
4. **Render spot-check**: after rendering, extract and *view* several
   frames per shot (at least: one at the very start of each subtitle line,
   one at its midpoint) via:
   ```bash
   ffmpeg -y -ss <t> -i output.mp4 -frames:v 1 -q:v 2 check.jpg
   ```
   For each, visually confirm:
   - No sliver of source-language text is visible anywhere near the box
     edges.
   - The translated text is vertically and horizontally centered in the
     box, not touching the edges, not truncated/overflowing.
   - The white box does not cover meaningfully more of the frame than the
     original text needed (oversized boxes look sloppy and were a real bug
     — verify against §3.3's grid readings).
   - The zoom looks subtle and smooth, not jarring, and matches the
     declared direction for that shot.
5. **Duration/audio check**: confirm output duration matches the intended
   range and audio is present and in sync (spot check by ear if possible,
   or at minimum confirm the audio stream muxed correctly via `ffprobe`).

Only after all five pass, present the output to the user.

---

## 5. Pitfalls already hit once — do not repeat them

These are not hypothetical; each one produced a visibly wrong output during
development that had to be corrected after the user caught it. All fixes
are already implemented in the companion scripts — this list exists so a
future Claude understands *why* the scripts are written the way they are,
and doesn't "clean up" a fix back into a bug.

1. **Oversized subtitle boxes.** Cause: deriving bbox from a wide
   brightness-threshold band (~85px) instead of the tight actual text
   height (~47px), and not accounting for unrelated bright pixels
   (clothing highlights, UI icons, light fixtures) inside that band
   inflating the detected width to near full-frame. Fix: always read bbox
   manually off a pixel-grid overlay (§3.3), never trust the raw threshold
   mask's bounding box.
2. **A real subtitle line silently dropped.** Cause: treating "presence
   ratio below threshold" as proof of "no subtitle here", in a video where
   subtitles are in fact continuously present with no true gaps. Fix:
   segment on content-change (mask diff) at ≥8fps, and manually verify every
   reported gap by viewing a frame in it (§3.2).
3. **Recurring background title treated as one-off.** Cause: assuming a
   background graphic seen in the opening shot wouldn't reappear later,
   when in fact the same set/background was reused in a later shot with the
   same graphic. Fix: explicitly scan for recurrence (§3.4), don't stop
   looking after the first hit.
4. **Translated text sitting slightly too high in its box.** Cause:
   centering with `box_height/2` using the font's advance height, ignoring
   the font's internal ascender offset. Fix: center using the actual ink
   bounding box (`draw.textbbox`) — implemented in `render_video.py`,
   `draw_box_text()`.

---

## 6. Batching a long video

For anything longer than ~2 minutes, don't try to do §3.1-3.6 for the whole
video in one uninterrupted pass — checkpoint your `lines`/`titles`/`shots`
data as you go (e.g. one Python data file per ~60-120s chunk, or one growing
file), so partial progress survives if the session is interrupted. The final
render in §3.6, however, should still be run as **one continuous pass**
over the full target range in a single `render()` call so the audio stays
in sync — don't render separate chunk-videos and concatenate them unless you
have no other option, since concatenation is an extra place sync bugs can
creep in.

---

## 7. Project state (this specific video), if resuming

If the user hands you this file together with the *same* source video they
originally gave another Claude, check whether a "project state" note is
attached (either appended below, or as a separate file) describing what
range has already been validated (segments, translations, bboxes, output
so far). Resume from there rather than re-deriving already-validated work —
but still run the §4 QA checklist over the *previously* validated range too
before assuming it's still correct, in case the checkpoint data is stale or
incomplete.
