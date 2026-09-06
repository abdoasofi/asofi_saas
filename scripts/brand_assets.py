"""Derive every brand asset size from the three master lockups.

    python3 scripts/brand_assets.py

A script rather than a one-off, because a logo gets redrawn and the twelve
sizes that hang off it then have to follow. Needs only Pillow — no Frappe
context, so it runs before a bench exists.

What it does NOT do is invent artwork. Everything here is a crop or a resample
of a master file; a genuinely new mark has to be designed, not computed.
"""

import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent / "asofi_saas" / "public" / "images"

#: The masters, and where the graphic ends and the baked-in text begins.
#:
#: Each lockup is an app icon: mark on top, then the Arabic name, the Latin
#: transliteration, a tagline, and a row of feature glyphs. A top bar needs the
#: mark alone — at 38px the text inside the full lockup is about four pixels
#: tall. These fractions were measured off the masters by row ink density, not
#: eyeballed; re-measure if a logo is redrawn.
BRANDS = {
    # Measured off the masters by row ink density, then used as measured. An
    # earlier pass padded these outward "to be safe" and did the opposite: the
    # extra 4% at the bottom clipped the top of the Arabic name into the icon.
    "asofisaas": {"graphic": (0.160, 0.543)},
    "rased":     {"graphic": (0.130, 0.508)},
    "edupulse":  {"graphic": (0.050, 0.500)},
    # دكّان's master is authored, not photographed: scripts/dukkan_lockup.html
    # lays the mark out by fraction of height, so these two numbers are the
    # layout itself rather than a reading taken off a finished picture. The
    # measured ink confirms them — graphic 0.174–0.447, first letter of the
    # name at 0.506 — and redrawing the lockup means revisiting both places.
    "dukkan":    {"graphic": (0.100, 0.490)},
}

#: 16–48 favicons · 64–128 in-page · 180 apple-touch · 192/512 PWA manifest.
ICON_SIZES = (16, 32, 48, 64, 96, 128, 180, 192, 256, 512)

OG_SIZE = (1200, 630)


def trimmed(im, ink_below=210):
    """Crop to the artwork's ink, ignoring the card it is drawn on.

    A plain difference-from-corner trim locks onto the rounded white card and
    its drop shadow — both are *nearly* white, so they register as content and
    the icon ends up as a graphic floating inside a visible card edge.
    Thresholding to real ink skips the card and finds the mark. 232 was still
    loose enough to catch the corner — the box only settles on the artwork at
    215 and below, so the bound is set well inside that.

    This is also why the bands above are cut generously at the TOP and exactly
    at the bottom: slack above costs nothing once this trims it, while slack
    below pulls the baked-in name into the icon.
    """
    mask = im.convert("L").point(lambda v: 255 if v < ink_below else 0)
    box = mask.getbbox()
    return im.crop(box) if box else im


def squared(im, pad=0.06):
    """Centre on a square white canvas with breathing room.

    Icons are placed by their box, not their ink. Without a consistent margin
    the three marks would each sit at a different apparent size in the same
    32px slot.
    """
    side = int(max(im.size) * (1 + pad * 2))
    canvas = Image.new("RGB", (side, side), "white")
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return canvas


def gradient(size, start=(23, 168, 155), end=(29, 78, 216)):
    """The brand gradient, drawn diagonally — sampled from the marks."""
    w, h = size
    grad = Image.new("RGB", (w, h))
    px = grad.load()
    for y in range(h):
        for x in range(0, w):
            t = (x / w + y / h) / 2
            px[x, y] = tuple(int(s + (e - s) * t) for s, e in zip(start, end))
    return grad


def build():
    if not ROOT.is_dir():
        sys.exit(f"لم أجد مجلد الصور: {ROOT}")

    made = []
    (ROOT / "icon").mkdir(exist_ok=True)
    (ROOT / "og").mkdir(exist_ok=True)

    for name, spec in BRANDS.items():
        master = ROOT / f"{name}.png"
        if not master.is_file():
            print(f"  ⚠ {name}.png غير موجود — تخطّي")
            continue

        im = Image.open(master).convert("RGB")
        w, h = im.size
        top, bottom = spec["graphic"]
        icon = squared(trimmed(im.crop((0, int(h * top), w, int(h * bottom)))))

        icon.resize((512, 512), Image.LANCZOS).save(
            ROOT / f"{name}-icon.png", "PNG", optimize=True
        )
        made.append(f"{name}-icon.png")

        for s in ICON_SIZES:
            out = ROOT / "icon" / f"{name}-{s}.png"
            icon.resize((s, s), Image.LANCZOS).save(out, "PNG", optimize=True)
            made.append(f"icon/{name}-{s}.png")

        # Social card: the full lockup on the brand gradient. Whatever a link
        # preview crops to, the mark stays centred and legible.
        card = gradient(OG_SIZE)
        lock = im.copy()
        lock.thumbnail((int(OG_SIZE[1] * 0.78),) * 2, Image.LANCZOS)
        plate = Image.new("RGB", (lock.width + 48, lock.height + 48), "white")
        plate.paste(lock, (24, 24))
        card.paste(plate, ((OG_SIZE[0] - plate.width) // 2, (OG_SIZE[1] - plate.height) // 2))
        card.save(ROOT / "og" / f"{name}-og.png", "PNG", optimize=True)
        made.append(f"og/{name}-og.png")

    # One multi-resolution favicon for the platform itself.
    plat = ROOT / "asofisaas-icon.png"
    if plat.is_file():
        Image.open(plat).save(
            ROOT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)]
        )
        made.append("favicon.ico")

    total = sum((ROOT / m).stat().st_size for m in made)
    print(f"\n  أُنتج {len(made)} ملفاً · {total // 1024} كيلوبايت إجمالاً")
    return made


if __name__ == "__main__":
    build()
