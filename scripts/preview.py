#!/usr/bin/env python3
"""Render design.json to a PNG so layout can be checked against tutorial frames.

This approximates PowerPoint (fonts and shadows differ); it is a layout check,
not a fidelity check. Open the .pptx itself to judge the real thing.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    sys.exit("Install pillow: python3 -m pip install pillow")

FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
]

# Tutorial fonts are often unavailable locally; map to the closest system face.
FALLBACKS = {
    "tw cen mt": ["Futura.ttc", "AvenirNext.ttc", "Helvetica.ttc"],
    "century gothic": ["Futura.ttc", "AvenirNext.ttc", "Helvetica.ttc"],
    "montserrat": ["AvenirNext.ttc", "Helvetica.ttc"],
    "calibri": ["Helvetica.ttc", "Arial.ttf"],
}
GENERIC = ["Helvetica.ttc", "Arial.ttf", "AvenirNext.ttc", "Supplemental/Arial.ttf"]


def find_font(name: str, bold: bool) -> Path | None:
    candidates = []
    stem = name.replace(" ", "")
    candidates += [f"{stem}.ttf", f"{stem}.ttc", f"{stem}.otf"]
    candidates += FALLBACKS.get(name.lower(), [])
    candidates += GENERIC
    for candidate in candidates:
        for folder in FONT_DIRS:
            path = folder / candidate
            if path.exists():
                return path
    return None


_FONT_CACHE: dict = {}


def load_font(name: str, size_pt: float, bold: bool, scale: float):
    key = (name, round(size_pt, 2), bold, scale)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    px = max(6, int(round(size_pt * scale / 72.0)))
    path = find_font(name, bold)
    font = None
    if path:
        try:
            index = 0
            if path.suffix == ".ttc" and bold:
                index = 1
            font = ImageFont.truetype(str(path), px, index=index)
        except Exception:
            try:
                font = ImageFont.truetype(str(path), px)
            except Exception:
                font = None
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def rgb(value) -> tuple:
    text = str(value).lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def fill_color(spec):
    if spec is None:
        return None
    if isinstance(spec, str):
        return rgb(spec)
    if spec.get("stops"):
        # flat approximation: the preview checks layout, not gradients
        return rgb(spec["stops"][0][0])
    return rgb(spec.get("color", "FFFFFF"))


class Canvas:
    def __init__(self, w_in: float, h_in: float, scale: float, bg) -> None:
        self.scale = scale
        self.size = (int(w_in * scale), int(h_in * scale))
        self.base = Image.new("RGBA", self.size, (fill_color(bg) or (255, 255, 255)) + (255,))
        self.shadows = Image.new("RGBA", self.size, (0, 0, 0, 0))

    def px(self, inches: float) -> float:
        return inches * self.scale

    def box(self, spec) -> tuple:
        x, y = self.px(spec["x"]), self.px(spec["y"])
        return [x, y, x + self.px(spec["w"]), y + self.px(spec["h"])]


def draw_shape_on(layer, canvas: Canvas, spec: dict, color, outline=None, width=0) -> None:
    kind = spec.get("type", "rect")
    draw = ImageDraw.Draw(layer)
    box = canvas.box(spec) if kind != "freeform" else None

    if kind in ("rect", "line"):
        draw.rectangle(box, fill=color, outline=outline, width=width)
    elif kind in ("roundRect", "round1Rect", "round2SameRect", "plaque"):
        adj = spec.get("adj", 0.16)
        adj = adj[0] if isinstance(adj, (list, tuple)) else adj
        radius = min(box[2] - box[0], box[3] - box[1]) * float(adj)
        draw.rounded_rectangle(box, radius=max(1, radius), fill=color, outline=outline, width=width)
    elif kind in ("oval", "donut"):
        draw.ellipse(box, fill=color, outline=outline, width=width)
    elif kind == "pie":
        adj = spec.get("adj", [0, 162])
        values = adj if isinstance(adj, (list, tuple)) else [0, adj]
        sweep = float(values[1] if len(values) > 1 else 162) / 54.0 * 90.0
        draw.pieslice(box, start=0, end=max(1, sweep), fill=color, outline=outline, width=width)
    elif kind == "chord":
        draw.chord(box, start=0, end=180, fill=color, outline=outline, width=width)
    elif kind == "arc":
        draw.arc(box, start=180, end=360, fill=outline or color, width=max(1, width))
    elif kind in ("triangle", "rightTriangle"):
        x0, y0, x1, y1 = box
        draw.polygon([(x0 + (x1 - x0) / 2, y0), (x1, y1), (x0, y1)],
                     fill=color, outline=outline)
    elif kind in ("arrowRight", "chevron"):
        x0, y0, x1, y1 = box
        mid = (y0 + y1) / 2
        head = x0 + (x1 - x0) * 0.55
        draw.polygon(
            [(x0, y0 + (y1 - y0) * 0.18), (head, y0 + (y1 - y0) * 0.18),
             (head, y0), (x1, mid), (head, y1),
             (head, y1 - (y1 - y0) * 0.18), (x0, y1 - (y1 - y0) * 0.18)],
            fill=color, outline=outline,
        )
    elif kind == "teardrop":
        x0, y0, x1, y1 = box
        draw.ellipse([x0, y0, x1, y0 + (y1 - y0) * 0.72], fill=color, outline=outline)
        cx = (x0 + x1) / 2
        draw.polygon([(x0 + (x1 - x0) * 0.22, y0 + (y1 - y0) * 0.55),
                      (x1 - (x1 - x0) * 0.22, y0 + (y1 - y0) * 0.55),
                      (cx, y1)], fill=color, outline=outline)
    elif kind == "diamond":
        x0, y0, x1, y1 = box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        draw.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], fill=color, outline=outline)
    elif kind == "freeform":
        points = [(canvas.px(x), canvas.px(y)) for x, y in spec.get("points", [])]
        if len(points) >= 2:
            if spec.get("close", True) and color:
                draw.polygon(points, fill=color, outline=outline)
            else:
                draw.line(points, fill=outline or color, width=max(1, width))
    elif kind in ("text", "picture"):
        if kind == "picture" and color:
            draw.rectangle(box, fill=color)
    else:
        draw.rectangle(box, fill=color, outline=outline, width=width)


def draw_text(layer, canvas: Canvas, spec: dict, defaults: dict) -> None:
    text = spec.get("text")
    runs = spec.get("runs")
    if text is None and not runs:
        return
    font_spec = {**defaults, **(spec.get("font") or {})}
    blocks = runs if runs else [{"text": text, **font_spec}]

    lines = []
    for block in blocks:
        merged = {**font_spec, **block}
        for piece in str(merged.get("text", "")).split("\n"):
            lines.append({**merged, "text": piece})

    draw = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = canvas.box(spec)
    heights = []
    for line in lines:
        font = load_font(line.get("name", "Helvetica"), line.get("size", 18),
                         line.get("bold", False), canvas.scale)
        spacing = float(line.get("line_spacing", 1.0) or 1.0)
        heights.append(font.size * 1.25 * spacing)
    total = sum(heights)

    anchor = font_spec.get("anchor", "middle")
    if anchor == "top":
        cursor = y0
    elif anchor == "bottom":
        cursor = y1 - total
    else:
        cursor = y0 + ((y1 - y0) - total) / 2

    for line, height in zip(lines, heights):
        font = load_font(line.get("name", "Helvetica"), line.get("size", 18),
                         line.get("bold", False), canvas.scale)
        content = line["text"]
        if line.get("caps"):
            content = content.upper()
        spacing_px = float(line.get("spacing", 0)) * canvas.scale / 72.0
        width = draw.textlength(content, font=font) + spacing_px * max(0, len(content) - 1)
        align = line.get("align", "center")
        if align == "left":
            tx = x0
        elif align == "right":
            tx = x1 - width
        else:
            tx = x0 + ((x1 - x0) - width) / 2
        color = rgb(line.get("color", "000000"))
        if spacing_px:
            for char in content:
                draw.text((tx, cursor), char, font=font, fill=color)
                tx += draw.textlength(char, font=font) + spacing_px
        else:
            draw.text((tx, cursor), content, font=font, fill=color)
        cursor += height


def render(spec: dict, dest: Path, scale: float, index: int) -> Path:
    canvas_spec = spec.get("canvas", {})
    slides = spec.get("slides")
    if slides is None:
        slides = [{"shapes": spec.get("shapes", []), "bg": canvas_spec.get("bg")}]
    slide = slides[index]
    bg = slide.get("bg", canvas_spec.get("bg"))

    canvas = Canvas(canvas_spec.get("w_in", 13.333), canvas_spec.get("h_in", 7.5), scale, bg)
    defaults = {"name": "Calibri", "size": 18, "color": "000000", **(spec.get("defaults") or {})}

    shapes = slide.get("shapes", [])
    for shape_spec in shapes:
        shadow_spec = shape_spec.get("shadow")
        if not shadow_spec:
            continue
        for shadow in shadow_spec if isinstance(shadow_spec, list) else [shadow_spec]:
            if str(shadow.get("type", "outer")).lower() == "inner":
                continue  # layout check only; Pillow has no inner-shadow equivalent
            dist = shadow.get("dist", 4) / 72.0
            angle = math.radians(shadow.get("dir", 5400000) / 60000.0)
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            moved = dict(shape_spec)
            moved["x"] = shape_spec.get("x", 0) + dist * math.cos(angle)
            moved["y"] = shape_spec.get("y", 0) + dist * math.sin(angle)
            alpha = int(float(shadow.get("alpha", 0.25)) * 255)
            draw_shape_on(layer, canvas, moved, rgb(shadow.get("color", "000000")) + (alpha,))
            blur = max(1, shadow.get("blur", 12) * canvas.scale / 72.0 / 2)
            canvas.shadows.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))

    canvas.base.alpha_composite(canvas.shadows)

    for shape_spec in shapes:
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        line = shape_spec.get("line") or {}
        outline = rgb(line["color"]) if line.get("color") else None
        width = int(round(float(line.get("width", 0)) * canvas.scale / 72.0)) if line else 0
        color = fill_color(shape_spec.get("fill", "FFFFFF")) if shape_spec.get("type") != "text" else None
        draw_shape_on(layer, canvas, shape_spec, color, outline, max(0, width))
        draw_text(layer, canvas, shape_spec, defaults)
        rotation = shape_spec.get("rotation")
        if rotation:
            box = canvas.box(shape_spec)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            layer = layer.rotate(-float(rotation), center=(cx, cy), resample=Image.BICUBIC)
        canvas.base.alpha_composite(layer)

    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.base.convert("RGB").save(dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Render design.json to a PNG preview.")
    parser.add_argument("spec")
    parser.add_argument("out")
    parser.add_argument("--scale", type=float, default=96.0, help="Pixels per inch")
    parser.add_argument("--slide", type=int, default=0, help="Slide index to render")
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    print(render(spec, Path(args.out), args.scale, args.slide))


if __name__ == "__main__":
    main()
