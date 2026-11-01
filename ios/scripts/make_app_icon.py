#!/usr/bin/env python3
"""Generate the FitnessBuddy app icon (iOS 18 single-size asset catalog: light, dark, tinted).

Design ("Pulse Lift"): a dumbbell whose handle is a heartbeat trace, cream plates and a yellow trace
with a long shadow on a teal field. Purely geometric, no text, no SF Symbols, no third-party assets.

Run from the repo root or ios/:
    uv run --with pillow python ios/scripts/make_app_icon.py [--preview]

Writes icon-1024.png, icon-1024-dark.png, icon-1024-tinted.png into
ios/FitnessBuddy/Resources/Assets.xcassets/AppIcon.appiconset/ and Contents.json next to them.
--preview also writes ios/docs/screenshots/app-icon-preview.png (light/dark/tinted at 1024, 180, 60).
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IOS = Path(__file__).resolve().parent.parent
ICONSET = IOS / "FitnessBuddy/Resources/Assets.xcassets/AppIcon.appiconset"
PREVIEW = IOS / "docs/screenshots/app-icon-preview.png"

SIZE = 1024
SS = 4  # supersampling factor
# (x, y, width, height, corner radius) in 1024-canvas points
PLATES = [
    (150, 302, 110, 420, 38),
    (764, 302, 110, 420, 38),
    (86, 392, 52, 240, 22),
    (886, 392, 52, 240, 22),
]
TRACE = [(240, 512), (372, 512), (432, 404), (512, 668), (596, 300), (656, 560), (692, 512), (784, 512)]
TRACE_WIDTH = 60
SHADOW_LENGTH = 900

# variant: (plate colour, trace colour)
COLOURS = {
    "light": ((255, 246, 229), (255, 225, 77)),
    "dark": ((94, 234, 212), (255, 225, 77)),
    "tinted": ((170, 170, 170), (255, 255, 255)),
}
BG_TOP, BG_BOTTOM = (18, 194, 176), (6, 122, 128)
SHADOW = (4, 90, 96)


def _draw_plates(d: ImageDraw.ImageDraw, fill, offset: int = 0):
    for x, y, w, h, r in PLATES:
        d.rounded_rectangle([(x + offset) * SS, (y + offset) * SS, (x + w + offset) * SS, (y + h + offset) * SS],
                            radius=r * SS, fill=fill)


def _draw_trace(d: ImageDraw.ImageDraw, fill, offset: int = 0):
    """Heartbeat polyline with round caps and joins."""
    w = TRACE_WIDTH * SS
    pts = [((x + offset) * SS, (y + offset) * SS) for x, y in TRACE]
    d.line(pts, fill=fill, width=w)
    for x, y in pts:
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=fill)


def _gradient(top, bottom, size) -> Image.Image:
    """Vertical RGB gradient, built small and stretched for speed."""
    col = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        col.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return col.resize((size, size), Image.BILINEAR)


def render(variant: str) -> Image.Image:
    n = SIZE * SS
    if variant == "light":
        canvas = _gradient(BG_TOP, BG_BOTTOM, n)
    elif variant == "dark":
        canvas = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    elif variant == "tinted":
        canvas = Image.new("RGB", (n, n), (0, 0, 0))
    else:
        raise ValueError(variant)

    d = ImageDraw.Draw(canvas)
    if variant == "light":
        for offset in range(SHADOW_LENGTH, 0, -1):
            _draw_trace(d, SHADOW, offset)
            _draw_plates(d, SHADOW, offset)
    plate, trace = COLOURS[variant]
    _draw_trace(d, trace)  # under the plates, so the handle runs into them cleanly
    _draw_plates(d, plate)

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
