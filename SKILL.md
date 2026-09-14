---
name: Video-Tutorial-to-PPT
description: >-
  Turns a YouTube video into a real PowerPoint file. For design tutorials it
  watches the video frame by frame and rebuilds the slide that is being taught,
  animations included; for lecture-style videos it builds an outline deck from
  the transcript. Use when the user pastes a YouTube URL and wants slides,
  PowerPoint, pptx, a presentation, or a deck from a video.
---

# YouTube to PowerPoint

Personal skill. Scripts live next to this file.

`SKILL_DIR` = directory containing this `SKILL.md` (`~/.cursor/skills/Video-Tutorial-to-PPT`).

**Rights:** rebuild the *technique* a tutorial teaches, using shapes you construct
yourself. Never extract media or XML out of someone else's template and never
redistribute their file as your output. If the user has no right to reuse the
material, stop and say so.

## Dependencies

Once per machine:

```bash
python3 -m pip install -r "$SKILL_DIR/requirements.txt"
brew install ffmpeg yt-dlp
```

Install `yt-dlp` from Homebrew, not pip: the pip build often lags behind
YouTube's extractor changes and fails to download.

## Pick a track

| Video | Track |
| --- | --- |
| A design tutorial: the host builds a slide on screen (shapes, colors, animations) | **Design track** |
| A talk or lesson that teaches a topic | **Outline track** |
| Both | Design track for the slide that is demonstrated, outline track for the rest |

Say which track you chose in one sentence before you start.

---

# Design track

The instructions live in the pixels, not the captions. Read frames.

```
Task Progress:
- [ ] Capture frames
- [ ] Read the finished slide
- [ ] Measure geometry
- [ ] Write design.json
- [ ] Preview and iterate
- [ ] Build pptx
- [ ] Report path
```

### 1. Capture frames

```bash
python3 "$SKILL_DIR/scripts/capture_video.py" "<url>" --work work
```

Writes `work/video.mp4`, `work/frames/*.jpg`, and `work/frames/index.md` mapping
every frame to its timestamp. Captions are optional here; run
`scripts/fetch_transcript.py` only if you want the narration as a hint. It exits
cleanly when there are none.

### 2. Read the finished slide

Design tutorials show the result in the first 15 seconds and again at the end.
Find those frames first: they are worth more than the whole build sequence.
The video thumbnail is often a clean, full-bleed render of the final slide, which
makes it the single best reference:

```bash
curl -s -o work/target.jpg "https://i.ytimg.com/vi/<video-id>/maxresdefault.jpg"
```

Then scan the build for what the still cannot tell you: shape types, the
Animation Pane (effect names, Start mode, Duration, Delay), and the font name in
the ribbon.

### 3. Measure, do not guess

A 1280x720 frame of a 16:9 slide is exactly 96 px per inch, so pixel coordinates
convert straight to slide inches. Sample the image with Pillow rather than
eyeballing it: scan rows and columns for color runs to recover edges, widths, and
exact hex fills. Then round to a regular grid; designers use even spacing, and
matching that looks better than matching your measurement noise.

### 4. Write design.json

Follow [design-reference.md](design-reference.md). For a repeated layout such as
four matching cards, write a short throwaway Python script that emits the JSON;
hand-writing near-identical blocks invites drift.

Quality:

- Reuse one palette and one type scale across the slide.
- Give every animated shape an `id`.
- Group shapes that should move together with `targets`.
- Prefer fonts that ship with Office so the deck travels.

### 5. Preview and iterate

```bash
python3 "$SKILL_DIR/scripts/preview.py" work/design.json work/preview.png
```

Read the PNG back, compare it against the target frame, and adjust. Comparing
measured feature positions between the two images beats eyeballing. Expect one
or two rounds. The preview approximates fonts and shadows; it is a layout check.

### 6. Build

```bash
python3 "$SKILL_DIR/scripts/build_design.py" work/design.json "<dest>.pptx"
```

Confirm the animations survived:

```bash
unzip -p "<dest>.pptx" ppt/slides/slide1.xml | grep -c "<p:timing>"
```

---

# Outline track

```bash
python3 "$SKILL_DIR/scripts/fetch_transcript.py" "<url>"
```

Save a copy to `slides/transcript.txt`, then write `slides/slides.json` per
[reference.md](reference.md) and build:

```bash
python3 "$SKILL_DIR/scripts/build_pptx.py" slides/slides.json "slides/<name>.pptx"
```

Quality:

- Short titles, about 8 words.
- At most 8 bullets per slide, one line each.
- 8-18 slides unless the video specifies a count.
- Speaker asides go in `notes`.
- Title slide first, recap last.

---

## Scripts

| Script | Purpose |
| --- | --- |
| `capture_video.py` | Download the video and sample frames. `--at SECONDS` grabs one lossless PNG. |
| `fetch_transcript.py` | Captions when they exist. Warns and exits 0 when they do not; `--require` to fail instead. |
| `build_design.py` | `design.json` to `.pptx`, animations included. |
| `preview.py` | `design.json` to PNG for layout checking. |
| `pptx_anim.py` | Timing-XML engine. `python-pptx` has no animation API. |
| `build_pptx.py` | Outline-track bullet decks. |

`examples/infographic-plan-cards.design.json` is a full worked design-track slide.
`examples/solar-system.design.json` is a second one.

## Report

Give the `.pptx` path, the track you used, the slide count, and the animation
step count. Tell the user to open it in PowerPoint and run the slideshow, since
neither the PNG preview nor Cursor's file preview plays animations.

## Do not

- Build a bullet-list summary when the video is a design tutorial.
- Copy shapes, media, or XML out of an existing template.
- Dump the raw transcript onto slides.
- Trust auto-captions in a language the speaker is not speaking.
