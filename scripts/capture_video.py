#!/usr/bin/env python3
"""Download a tutorial video and sample frames for visual reading.

Design tutorials carry their instructions on screen, not in the captions, so the
agent needs pixels. This writes <work>/video.mp4 plus <work>/frames/*.jpg and an
index.md mapping every frame to its timestamp.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_ID_RE = re.compile(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})")
BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PTS_RE = re.compile(r"pts_time:([0-9.]+)")

# YouTube breaks extractor clients often; the first one that works wins.
CLIENTS = ["default", "android_vr", "web_safari", "tv", "mweb", "ios"]


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


def require(tool: str, hint: str) -> str:
    found = shutil.which(tool)
    if not found:
        raise SystemExit(f"{tool} not found on PATH. Install it with: {hint}")
    return found


def fmt_ts(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def client_args(client: str) -> list:
    if client == "default":
        return []
    return ["--extractor-args", f"youtube:player_client={client}"]


def probe(ytdlp: str, url: str, height: int) -> tuple:
    """Return (client, metadata dict) for the first extractor client that works."""
    fmt = f"bv*[height<={height}][ext=mp4]/bv*[height<={height}]/b[height<={height}]"
    errors = []
    for client in CLIENTS:
        cmd = [
            ytdlp, "--no-warnings", "--quiet", "--simulate",
            "--print", "%(id)s\t%(title)s\t%(duration)s\t%(height)s",
            "-f", fmt, *client_args(client), url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        line = proc.stdout.strip().splitlines()
        if proc.returncode == 0 and line:
            parts = line[-1].split("\t")
            if len(parts) == 4:
                print(f"extractor client: {client}", file=sys.stderr)
                return client, {
                    "id": parts[0],
                    "title": parts[1],
                    "duration": float(parts[2] or 0),
                    "height": parts[3],
                }
        errors.append(f"  {client}: {(proc.stderr or '').strip().splitlines()[-1:] or ['no output']}")
    raise SystemExit("yt-dlp could not read the video with any client:\n" + "\n".join(errors))


def download(ytdlp: str, url: str, client: str, height: int, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"reusing existing {dest}", file=sys.stderr)
        return
    fmt = f"bv*[height<={height}][ext=mp4]/bv*[height<={height}]/b[height<={height}]"
    cmd = [
        ytdlp, "--no-warnings", "--no-playlist",
        "-f", fmt, *client_args(client),
        "--merge-output-format", "mp4",
        "-o", str(dest), url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        raise SystemExit(f"yt-dlp download failed:\n{proc.stderr or proc.stdout}")


def scene_frames(ffmpeg: str, video: Path, out_dir: Path, threshold: float, quality: int) -> list:
    pattern = str(out_dir / "scene_%04d.jpg")
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-fps_mode", "vfr", "-q:v", str(quality), pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg scene detection failed:\n{proc.stderr[-2000:]}")
    stamps = [float(m) for m in PTS_RE.findall(proc.stderr)]
    files = sorted(out_dir.glob("scene_*.jpg"))
    return list(zip(files, stamps + [0.0] * (len(files) - len(stamps))))


def interval_frames(ffmpeg: str, video: Path, out_dir: Path, every: float, quality: int) -> list:
    pattern = str(out_dir / "t_%04d.jpg")
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(video),
        "-vf", f"fps=1/{every}", "-q:v", str(quality), pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg interval sampling failed:\n{proc.stderr[-2000:]}")
    files = sorted(out_dir.glob("t_*.jpg"))
    return [(f, i * every) for i, f in enumerate(files)]


def thin(items: list, limit: int) -> list:
    """Keep at most `limit` items, evenly spaced across the video."""
    if limit <= 0 or len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def grab_one(ffmpeg: str, video: Path, at: float, dest: Path) -> None:
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-ss", str(at), "-i", str(video), "-frames:v", "1", str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg frame grab failed:\n{proc.stderr[-2000:]}")


def write_index(out_dir: Path, meta: dict, frames: list) -> Path:
    index = out_dir / "index.md"
    lines = [
        f"# Frames: {meta.get('title', '')}",
        "",
        f"- source: {meta.get('url', '')}",
        f"- duration: {fmt_ts(meta.get('duration', 0))}",
        f"- frames: {len(frames)}",
        "",
        "| frame | time | kind |",
        "| --- | --- | --- |",
    ]
    for path, stamp, kind in frames:
        lines.append(f"| {path.name} | {fmt_ts(stamp)} | {kind} |")
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a tutorial and sample frames.")
    parser.add_argument("url_or_id", help="YouTube URL or 11-character video id")
    parser.add_argument("--work", default="work", help="Working directory (default: work)")
    parser.add_argument("--height", type=int, default=720, help="Max video height")
    parser.add_argument("--scene", type=float, default=0.25, help="Scene-change threshold 0-1")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between grid frames")
    parser.add_argument("--max-frames", type=int, default=120, help="Cap on total frames")
    parser.add_argument("--quality", type=int, default=2, help="ffmpeg JPEG quality (2=best)")
    parser.add_argument("--at", type=float, help="Also grab one lossless PNG at this timestamp")
    args = parser.parse_args()

    ytdlp = require("yt-dlp", "python3 -m pip install -U yt-dlp")
    ffmpeg = require("ffmpeg", "brew install ffmpeg")

    vid = video_id(args.url_or_id)
    url = watch_url(vid)
    work = Path(args.work)
    frames_dir = work / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    client, meta = probe(ytdlp, url, args.height)
    meta["url"] = url
    video = work / "video.mp4"
    download(ytdlp, url, client, args.height, video)

    if args.at is not None:
        dest = work / f"frame_{args.at:g}s.png"
        grab_one(ffmpeg, video, args.at, dest)
        print(dest)
        return

    for stale in frames_dir.glob("*.jpg"):
        stale.unlink()

    scenes = scene_frames(ffmpeg, video, frames_dir, args.scene, args.quality)
    grid = interval_frames(ffmpeg, video, frames_dir, args.interval, args.quality)

    half = max(1, args.max_frames // 2)
    kept = [(p, t, "scene") for p, t in thin(scenes, half)]
    kept += [(p, t, "grid") for p, t in thin(grid, args.max_frames - len(kept))]
    keep_names = {p.name for p, _, _ in kept}
    for path in frames_dir.glob("*.jpg"):
        if path.name not in keep_names:
            path.unlink()

    kept.sort(key=lambda row: row[1])
    index = write_index(frames_dir, meta, kept)

    print(json.dumps({
        "video": str(video),
        "frames_dir": str(frames_dir),
        "index": str(index),
        "frame_count": len(kept),
        "title": meta.get("title"),
        "duration": meta.get("duration"),
    }, indent=2))


if __name__ == "__main__":
    main()
