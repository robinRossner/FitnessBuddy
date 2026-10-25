#!/usr/bin/env python3
"""Generate the FitnessBuddy app icon (iOS 18 single-size asset catalog: light, dark, tinted).

Design: three concentric activity-style rings on a deep navy field, in the app's domain colours
(outer lifting orange, middle running teal, inner coach purple). Purely geometric, no text,
no SF Symbols, no third-party assets.

Run from the repo root or ios/:
    uv run --with pillow python ios/scripts/make_app_icon.py [--preview]

Writes icon-1024.png, icon-1024-dark.png, icon-1024-tinted.png into
ios/FitnessBuddy/Resources/Assets.xcassets/AppIcon.appiconset/ and Contents.json next to them.
--preview also writes ios/docs/screenshots/app-icon-preview.png (light/dark/tinted at 1024, 180, 60).
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageChops

IOS = Path(__file__).resolve().parent.parent
ICONSET = IOS / "FitnessBuddy/Resources/Assets.xcassets/AppIcon.appiconset"
PREVIEW = IOS / "docs/screenshots/app-icon-preview.png"

SIZE = 1024
SS = 4  # supersampling factor
CENTER = SIZE / 2
STROKE = 98
# (centre-line radius, sweep as fraction of a full turn, colour, highlight colour, tinted grey)
RINGS = [  # keep radius + STROKE/2 of the outer ring well inside the 1024 canvas
    (344, 0.80, (255, 149, 0), (255, 196, 92), 255),     # lifting orange
    (236, 0.68, (0, 184, 158), (84, 232, 206), 205),     # running teal-green
    (128, 0.54, (175, 82, 222), (222, 150, 255), 160),   # coach purple
]


def _arc_mask(radius: float, sweep: float) -> Image.Image:
    """Supersampled 'L' mask of an arc starting at 12 o'clock, clockwise, with round caps."""
    n = SIZE * SS
    mask = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(mask)
    c, r, w = CENTER * SS, radius * SS, STROKE * SS
    ro = r + w / 2  # PIL strokes inward from the bounding box, so use the outer radius
    box = [c - ro, c - ro, c + ro, c + ro]
    start = -90.0
    end = start + 360.0 * sweep
    d.arc(box, start, end, fill=255, width=int(w))
    for ang in (start, end):
        x = c + r * math.cos(math.radians(ang))
        y = c + r * math.sin(math.radians(ang))
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=255)
    return mask


def _ring_track(radius: float) -> Image.Image:
    n = SIZE * SS
    mask = Image.new("L", (n, n), 0)
    c, r, w = CENTER * SS, radius * SS, STROKE * SS
    d = ImageDraw.Draw(mask)
    ro = r + w / 2
    d.ellipse([c - ro, c - ro, c + ro, c + ro], outline=255, width=int(w))
    return mask


def _gradient(top, bottom, size) -> Image.Image:
    """Vertical RGB gradient, built small and stretched for speed."""
    col = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        col.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return col.resize((size, size), Image.BILINEAR)


def _background() -> Image.Image:
    n = SIZE * SS
    bg = _gradient((22, 30, 58), (6, 9, 20), n)
    # soft glow behind the rings so the centre has some depth
    glow = Image.new("L", (n, n), 0)
    gd = ImageDraw.Draw(glow)
    r = int(n * 0.42)
    gd.ellipse([n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r], fill=70)
    glow = glow.resize((n // 8, n // 8), Image.BILINEAR)
    from PIL import ImageFilter
    glow = glow.filter(ImageFilter.GaussianBlur(14)).resize((n, n), Image.BICUBIC)
    lift = Image.new("RGB", (n, n), (52, 66, 120))
    return Image.composite(lift, bg, glow)


def render(variant: str) -> Image.Image:
    n = SIZE * SS
    if variant == "light":
        canvas = _background().convert("RGBA")
    elif variant == "dark":
        canvas = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    elif variant == "tinted":
        canvas = Image.new("RGBA", (n, n), (0, 0, 0, 255))
    else:
        raise ValueError(variant)

    for radius, sweep, color, light, grey in RINGS:
        track = _ring_track(radius)
        arc = _arc_mask(radius, sweep)
        if variant == "tinted":
            track_fill = Image.new("RGBA", (n, n), (255, 255, 255, 0))
            track_fill.putalpha(track.point(lambda v: v * 38 // 255))
            canvas = Image.alpha_composite(canvas, track_fill)
            fill = _gradient((grey,) * 3, (int(grey * 0.78),) * 3, n).convert("RGBA")
        else:
            track_fill = Image.new("RGBA", (n, n), color + (0,))
            track_fill.putalpha(track.point(lambda v: v * 56 // 255))
            canvas = Image.alpha_composite(canvas, track_fill)
            fill = _gradient(light, color, n).convert("RGBA")
        fill.putalpha(arc)
        canvas = Image.alpha_composite(canvas, fill)

    out = canvas.resize((SIZE, SIZE), Image.LANCZOS)
    if variant != "dark":
        out = out.convert("RGB")  # opaque, full-bleed; iOS applies the corner mask
    return out


def write_contents():
    entry = {"idiom": "universal", "platform": "ios", "size": "1024x1024"}
    images = [
        {**entry, "filename": "icon-1024.png"},
        {"appearances": [{"appearance": "luminosity", "value": "dark"}], **entry,
         "filename": "icon-1024-dark.png"},
        {"appearances": [{"appearance": "luminosity", "value": "tinted"}], **entry,
         "filename": "icon-1024-tinted.png"},
    ]
    (ICONSET / "Contents.json").write_text(
        json.dumps({"images": images, "info": {"author": "xcode", "version": 1}}, indent=2) + "\n")


def _masked(img: Image.Image, px: int, backdrop: tuple) -> Image.Image:
    """Icon at px size with the iOS corner mask on a backdrop (preview only)."""
    icon = img.resize((px, px), Image.LANCZOS).convert("RGBA")
    base = Image.new("RGBA", (px, px), backdrop + (255,))
    base = Image.alpha_composite(base, icon)
    m = Image.new("L", (px * 4, px * 4), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, px * 4 - 1, px * 4 - 1], radius=int(px * 4 * 0.2237), fill=255)
    m = m.resize((px, px), Image.LANCZOS)
    base.putalpha(m)
    return base


def preview(icons: dict):
    sizes = [("1024", 480), ("180", 180), ("60", 60)]  # 1024 shown scaled to fit the sheet
    pad, label_h = 40, 36
    cols = list(icons)
    col_w = 480 + pad
    sheet_w = pad + len(cols) * col_w
    sheet_h = pad + (480 + label_h + pad) + (180 + label_h + pad) + (60 + label_h + pad)
    sheet = Image.new("RGB", (sheet_w, sheet_h), (236, 238, 244))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:
        font = ImageFont.load_default()
    backdrops = {"light": (236, 238, 244), "dark": (18, 20, 28), "tinted": (60, 56, 72)}
    for ci, name in enumerate(cols):
        x0 = pad + ci * col_w
        y = pad
        for label, px in sizes:
            ic = _masked(icons[name], px, (24, 26, 36) if name == "dark" else (236, 238, 244))
            sheet.paste(ic, (x0 + (480 - px) // 2, y), ic)
            d.text((x0, y + px + 6), f"{name} @ {label if label != '1024' else '1024 (shown 480)'}", fill=(60, 64, 80), font=font)
            y += px + label_h + pad
    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(PREVIEW)
    print("wrote", PREVIEW)


def main():
    ICONSET.mkdir(parents=True, exist_ok=True)
    icons = {v: render(v) for v in ("light", "dark", "tinted")}
    names = {"light": "icon-1024.png", "dark": "icon-1024-dark.png", "tinted": "icon-1024-tinted.png"}
    for v, img in icons.items():
        img.save(ICONSET / names[v], optimize=True)
        print("wrote", ICONSET / names[v])
    write_contents()
    if "--preview" in sys.argv:
        preview(icons)


if __name__ == "__main__":
    main()
