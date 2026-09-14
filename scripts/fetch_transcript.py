#!/usr/bin/env python3
"""Fetch YouTube captions. Prints timestamped transcript to stdout."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VIDEO_ID_RE = re.compile(
    r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})"
)
BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
VTT_TS_RE = re.compile(
    r"(\d{2}:)?\d{2}:\d{2}\.\d{3}\s-->\s(\d{2}:)?\d{2}:\d{2}\.\d{3}"
)
TAG_RE = re.compile(r"<[^>]+>")


def video_id(raw: str) -> str:
    text = raw.strip()
    if BARE_ID_RE.match(text):
        return text
    match = VIDEO_ID_RE.search(text)
    if match:
        return match.group(1)
    raise SystemExit(f"Could not parse a YouTube video id from: {raw}")


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def fmt_secs(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


ENGLISH = ["en", "en-US", "en-GB"]


def pick_track(listing):
    """Best available track, plus a human label and a quality warning."""
    try:
        return listing.find_manually_created_transcript(ENGLISH), "manual en", None
    except Exception:
        pass
    try:
        return listing.find_generated_transcript(ENGLISH), "auto en", None
    except Exception:
        pass

    tracks = list(listing)
    if not tracks:
        return None, None, None

    for track in tracks:
        if track.is_translatable:
            label = f"{track.language_code} -> en translation"
            warn = None
            if track.is_generated:
                warn = (
                    f"Only an auto-generated '{track.language_code}' track exists. "
                    "Translating it to English, but auto-captions in the wrong language "
                    "are often nonsense. Treat this transcript as unreliable."
                )
            return track.translate("en"), label, warn

    track = tracks[0]
    return (
        track,
        f"{track.language_code} (untranslated)",
        f"No English track; falling back to '{track.language_code}' as-is.",
    )


def from_api(vid: str) -> tuple[list, str] | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print(
            "Install youtube-transcript-api: python3 -m pip install youtube-transcript-api",
            file=sys.stderr,
        )
        return None

    try:
        listing = YouTubeTranscriptApi().list(vid)
    except Exception as exc:
        print(f"Transcript API: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None

    try:
        track, label, warn = pick_track(listing)
    except Exception as exc:
        print(f"Transcript API: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None

    if track is None:
        print("Transcript API: video lists no caption tracks.", file=sys.stderr)
        return None
    if warn:
        print(f"WARNING: {warn}", file=sys.stderr)

    try:
        snippets = list(track.fetch())
    except Exception as exc:
        print(f"Transcript API: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None

    rows: list[tuple[float, str]] = []
    for item in snippets:
        if hasattr(item, "start") and hasattr(item, "text"):
            start = float(item.start)
            text = str(item.text)
        elif isinstance(item, dict):
            start = float(item.get("start", 0))
            text = str(item.get("text", ""))
        else:
            continue
        text = text.replace("\n", " ").strip()
        if text:
            rows.append((start, text))
    return (rows, label) if rows else None


def vtt_to_rows(path: Path) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    current_start: float | None = None
    texts: list[str] = []

    def flush() -> None:
        nonlocal current_start, texts
        if current_start is None:
            return
        line = " ".join(texts).strip()
        if line:
            rows.append((current_start, line))
        current_start = None
        texts = []

    def parse_clock(stamp: str) -> float:
        parts = stamp.split(":")
        if len(parts) == 3:
            h, m, rest = parts
            sec, ms = rest.split(".")
            return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
        m, rest = parts
        sec, ms = rest.split(".")
        return int(m) * 60 + int(sec) + int(ms) / 1000

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or line.isdigit():
            continue
        ts = VTT_TS_RE.match(line)
        if ts:
            flush()
            start_token = line.split("-->")[0].strip()
            current_start = parse_clock(start_token)
            continue
        cleaned = TAG_RE.sub("", line).replace("&nbsp;", " ").strip()
        if cleaned and current_start is not None:
            texts.append(cleaned)
    flush()

    deduped: list[tuple[float, str]] = []
    seen = ""
    for start, text in rows:
        if text == seen:
            continue
        deduped.append((start, text))
        seen = text
    return deduped


def from_ytdlp(vid: str) -> list[tuple[float, str]] | None:
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        print("yt-dlp not found on PATH (optional caption fallback).", file=sys.stderr)
        return None

    with tempfile.TemporaryDirectory(prefix="yt-captions-") as tmp:
        out_tmpl = str(Path(tmp) / "captions")
        cmd = [
            ytdlp,
            "--skip-download",
            "--write-auto-sub",
            "--write-sub",
            "--sub-langs",
            "en.*,en,en-US,en-GB",
            "--sub-format",
            "vtt",
            "--no-warnings",
            "-o",
            out_tmpl,
            watch_url(vid),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "yt-dlp failed").strip()
            print(f"yt-dlp: {err}", file=sys.stderr)
            return None
        vtts = sorted(Path(tmp).glob("*.vtt"))
        if not vtts:
            print("yt-dlp ran but wrote no .vtt captions.", file=sys.stderr)
            return None
        rows = vtt_to_rows(vtts[0])
        return rows or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a YouTube transcript to stdout.")
    parser.add_argument("url_or_id", help="YouTube URL or 11-character video id")
    parser.add_argument(
        "--require",
        action="store_true",
        help="Exit non-zero when no captions are available (default: warn and continue).",
    )
    args = parser.parse_args()
    vid = video_id(args.url_or_id)

    found = from_api(vid)
    if found:
        rows, source = found
        source = f"youtube-transcript-api ({source})"
    else:
        rows = from_ytdlp(vid)
        source = "yt-dlp auto/manual captions"

    if not rows:
        message = (
            "No usable captions. Design tutorials often have none, or only a "
            "wrong-language auto track. Continue with the frame-vision pipeline "
            "(capture_video.py); do not invent captions."
        )
        if args.require:
            raise SystemExit(message)
        print(f"WARNING: {message}", file=sys.stderr)
        print(f"# video_id: {vid}")
        print("# source: unavailable")
        print(f"# url: {watch_url(vid)}")
        return

    print(f"# video_id: {vid}")
    print(f"# source: {source}")
    print(f"# url: {watch_url(vid)}")
    print()
    for start, text in rows:
        print(f"[{fmt_secs(start)}] {text}")


if __name__ == "__main__":
    main()
