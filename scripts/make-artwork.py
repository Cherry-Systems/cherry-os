#!/usr/bin/env python3
"""Draws Cherry OS artwork: logo, wallpapers, GRUB background, boot splash, installer images and
placeholder desktop previews.  Usage: make-artwork.py OUT_DIR

Real desktop screenshots in artwork/screens/<desktop>.png replace the drawn previews."""
import math
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop" / "lib"))
from cherryos.desktops import DESKTOPS  # noqa: E402

CHERRY = (200, 16, 64)
CHERRY_DARK = (122, 8, 40)
BLOSSOM = (255, 183, 206)
LEAF = (64, 160, 72)
STEM = (92, 58, 30)
NIGHT = (24, 10, 26)

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <defs>
    <radialGradient id="c" cx="0.35" cy="0.35" r="0.75">
      <stop offset="0" stop-color="#ff5a7e"/><stop offset="0.55" stop-color="#c81040"/>
      <stop offset="1" stop-color="#7a0828"/>
    </radialGradient>
  </defs>
  <path d="M128 36 C 118 80, 92 110, 78 150" fill="none" stroke="#5c3a1e" stroke-width="9" stroke-linecap="round"/>
  <path d="M128 36 C 140 84, 160 112, 178 156" fill="none" stroke="#5c3a1e" stroke-width="9" stroke-linecap="round"/>
  <path d="M128 38 C 150 10, 200 12, 214 34 C 186 52, 150 54, 128 38 Z" fill="#40a048"/>
  <path d="M132 37 C 160 30, 186 30, 210 34" fill="none" stroke="#2c7434" stroke-width="3"/>
  <circle cx="76" cy="176" r="48" fill="url(#c)"/>
  <circle cx="180" cy="182" r="48" fill="url(#c)"/>
  <ellipse cx="60" cy="158" rx="13" ry="8" fill="#ffffff" opacity="0.55" transform="rotate(-35 60 158)"/>
  <ellipse cx="164" cy="164" rx="13" ry="8" fill="#ffffff" opacity="0.55" transform="rotate(-35 164 164)"/>
</svg>
"""


def font(size, bold=True):
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    for d in ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF", "/usr/share/fonts/dejavu"]:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def bezier(p0, p1, p2, p3, steps=60):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def draw_logo(size):
    """The SVG above, drawn with Pillow at 4x and scaled down for smooth edges."""
    s = size * 4 / 256
    img = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    P = lambda x, y: (x * s, y * s)  # noqa: E731
    for stem in [((128, 36), (118, 80), (92, 110), (78, 150)), ((128, 36), (140, 84), (160, 112), (178, 156))]:
        r = 4.5 * s
        for x, y in (P(*p) for p in bezier(*stem, steps=400)):
            d.ellipse([x - r, y - r, x + r, y + r], fill=STEM)
    leaf = bezier((128, 38), (150, 10), (200, 12), (214, 34)) + bezier((214, 34), (186, 52), (150, 54), (128, 38))
    d.polygon([P(*p) for p in leaf], fill=LEAF)
    d.line([P(*p) for p in bezier((132, 37), (160, 30), (186, 30), (210, 34))], fill=(44, 116, 52), width=int(3 * s))
    for cx, cy in [(76, 176), (180, 182)]:
        r = 48
        for i in range(40, 0, -1):  # radial gradient, light from the top left
            t = i / 40
            col = tuple(int(a + (b - a) * (1 - t)) for a, b in zip(CHERRY_DARK, (255, 90, 126)))
            ox, oy = cx - 13 * (1 - t), cy - 13 * (1 - t)
            d.ellipse([P(ox - r * t, oy - r * t), P(ox + r * t, oy + r * t)], fill=col)
        hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        hd = ImageDraw.Draw(hl)
        hd.ellipse([P(cx - 29, cy - 26), P(cx - 3, cy - 10)], fill=(255, 255, 255, 140))
        hl = hl.rotate(0)
        img = Image.alpha_composite(img, hl)
        d = ImageDraw.Draw(img)
    return img.resize((size, size), Image.LANCZOS)


def gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def petal(size, color):
    p = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    d.ellipse([size * 0.4, size * 0.2, size * 1.6, size * 1.8], fill=color)
    d.polygon([(size, size * 0.15), (size * 0.85, size * 0.45), (size * 1.15, size * 0.45)], fill=(0, 0, 0, 0))
    return p


def wallpaper(w, h, top, bottom, seed, petal_color, bokeh_color, logo=False):
    rnd = random.Random(seed)
    img = gradient(w, h, top, bottom).convert("RGBA")
    bokeh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bokeh)
    for _ in range(36):
        r = rnd.randint(h // 30, h // 7)
        x, y = rnd.randint(-r, w + r), rnd.randint(-r, h + r)
        a = rnd.randint(18, 60)
        bd.ellipse([x - r, y - r, x + r, y + r], fill=bokeh_color + (a,))
    img = Image.alpha_composite(img, bokeh.filter(ImageFilter.GaussianBlur(h // 60)))
    petals = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for _ in range(140):
        s = rnd.randint(h // 120, h // 45)
        p = petal(s, petal_color + (rnd.randint(140, 235),)).rotate(rnd.randint(0, 359), expand=True,
                                                                   resample=Image.BICUBIC)
        # More petals drift down the right side, leaving the left calm for desktop icons.
        x = int(w * (0.35 + 0.65 * rnd.random() ** 0.7))
        petals.alpha_composite(p, (min(x, w - p.width), rnd.randint(0, h - p.height)))
    img = Image.alpha_composite(img, petals.filter(ImageFilter.GaussianBlur(1)))
    if logo:
        lg = draw_logo(h // 5)
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow.alpha_composite(lg, (w - lg.width - h // 10, h - lg.height - h // 10))
        img = Image.alpha_composite(img, glow)
    return img.convert("RGB")


def centered(d, w, y, text, f, fill):
    tw = d.textlength(text, font=f)
    d.text(((w - tw) / 2, y), text, font=f, fill=fill)


def desktop_preview(desktop, w=960, h=540):
    """A simple drawing of each desktop's layout, until real screenshots are added."""
    img = wallpaper(w, h, (60, 14, 40), (180, 60, 100), hash(desktop.id) & 0xFFFF, BLOSSOM, (255, 200, 220))
    d = ImageDraw.Draw(img, "RGBA")
    bar = (20, 20, 28, 230)
    if desktop.layout == "bottom-panel":
        d.rectangle([0, h - 34, w, h], fill=bar)
        d.ellipse([10, h - 28, 32, h - 6], fill=CHERRY)
        for i in range(5):
            d.rounded_rectangle([50 + i * 34, h - 28, 74 + i * 34, h - 6], 4, fill=(90, 90, 110, 255))
        d.text((w - 60, h - 26), "4:06", font=font(14), fill="white")
    elif desktop.layout == "top-bar-dock":
        d.rectangle([0, 0, w, 24], fill=(0, 0, 0, 240))
        centered(d, w, 4, "Sat 4:06 PM", font(13), "white")
        d.rounded_rectangle([w // 2 - 150, h - 56, w // 2 + 150, h - 10], 14, fill=(30, 30, 40, 200))
        for i in range(6):
            d.rounded_rectangle([w // 2 - 136 + i * 48, h - 48, w // 2 - 104 + i * 48, h - 18], 8,
                                fill=(140, 140, 170, 255))
    elif desktop.layout == "side-panel":
        d.rectangle([0, 0, w, 26], fill=bar)
        d.rectangle([w - 220, 26, w, h], fill=(28, 28, 36, 215))
        for i in range(4):
            d.rounded_rectangle([w - 206, 40 + i * 90, w - 14, 116 + i * 90], 10, fill=(60, 60, 75, 255))
    else:  # light top panel + dock
        d.rectangle([0, 0, w, 26], fill=bar)
        d.rounded_rectangle([w // 2 - 120, h - 50, w // 2 + 120, h - 8], 10, fill=(30, 30, 40, 200))
    d.rounded_rectangle([w * 0.18, h * 0.16, w * 0.62, h * 0.66], 10, fill=(250, 248, 250, 245))
    d.rectangle([w * 0.18, h * 0.16, w * 0.62, h * 0.16 + 26], fill=(230, 222, 228, 255))
    d.text((w * 0.18 + 12, h * 0.16 + 5), desktop.label, font=font(14), fill=(40, 30, 40))
    return img


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "art")
    (out / "wallpapers").mkdir(parents=True, exist_ok=True)
    (out / "screens").mkdir(parents=True, exist_ok=True)

    (out / "logo.svg").write_text(LOGO_SVG)
    draw_logo(512).save(out / "logo.png")
    draw_logo(256).save(out / "logo-256.png")

    W, H = 2560, 1440
    wallpaper(W, H, (38, 8, 30), (196, 50, 96), 1, BLOSSOM, (255, 170, 200), logo=True).save(
        out / "wallpapers" / "cherry-dusk.png")
    wallpaper(W, H, (8, 6, 20), (70, 12, 50), 2, (255, 150, 185), (200, 60, 120)).save(
        out / "wallpapers" / "cherry-night.png")
    wallpaper(W, H, (255, 236, 242), (255, 196, 214), 3, (230, 90, 130), (255, 255, 255)).save(
        out / "wallpapers" / "cherry-morning.png")

    grub = wallpaper(1024, 768, (20, 6, 18), (90, 16, 52), 4, BLOSSOM, (220, 90, 140))
    lg = draw_logo(150)
    grub.paste(lg, (1024 - 190, 40), lg)
    d = ImageDraw.Draw(grub)
    d.text((1024 - 330, 196), "Cherry OS", font=font(46), fill="white")
    grub.save(out / "grub.png")

    draw_logo(220).save(out / "plymouth-logo.png")
    ring = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
    ImageDraw.Draw(ring).arc([60, 60, 1140, 1140], 0, 100, fill=BLOSSOM + (255,), width=56)
    ring.resize((300, 300), Image.LANCZOS).save(out / "spinner.png")

    welcome = wallpaper(800, 450, (40, 8, 30), (180, 40, 90), 5, BLOSSOM, (255, 170, 200))
    lg = draw_logo(200)
    welcome.paste(lg, (300, 60), lg)
    centered(ImageDraw.Draw(welcome), 800, 290, "Welcome to Cherry OS", font(40), "white")
    welcome.save(out / "welcome.png")

    slides = [
        ("Every desktop, one OS", "KDE Plasma, GNOME, Cinnamon, XFCE and Budgie — switch any time in Cherry Center."),
        ("Windows apps just work", "Double-click a .exe or install Windows apps from the Cherry Store."),
        ("Ready to code", "Python, Java, JavaScript, C/C++, Rust, Go, Ruby and PHP are already installed."),
        ("Your files follow you", "Bring files from your old computer on a USB stick, your own cloud drive or a NAS."),
        ("Safe for years", "Security updates install themselves, for as long as Debian supports this release."),
    ]
    for i, (title, text) in enumerate(slides, 1):
        s = wallpaper(800, 440, (36, 8, 28), (150, 30, 80), 10 + i, BLOSSOM, (255, 170, 200))
        d = ImageDraw.Draw(s)
        lg = draw_logo(120)
        s.paste(lg, (340, 50), lg)
        centered(d, 800, 200, title, font(38), "white")
        words, line, y = text.split(), "", 270
        for wd in words:
            if d.textlength(line + " " + wd, font=font(22, False)) > 680:
                centered(d, 800, y, line.strip(), font(22, False), (255, 225, 235))
                line, y = "", y + 34
            line += " " + wd
        centered(d, 800, y, line.strip(), font(22, False), (255, 225, 235))
        s.save(out / f"slide{i}.png")

    for desk in DESKTOPS.values():
        real = ROOT / "artwork" / "screens" / f"{desk.id}.png"
        if real.exists():
            shutil.copy(real, out / "screens" / f"{desk.id}.png")
        else:
            desktop_preview(desk).save(out / "screens" / f"{desk.id}.png")
    print(f"artwork written to {out}")


if __name__ == "__main__":
    main()
