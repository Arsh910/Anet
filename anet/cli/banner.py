"""banner.py — animated ANET startup banner.

Hand-authored block art (NOT figlet — no figlet font reproduces the target
gothic-pixel logo). The word ANET is drawn on a pixel grid using the full-block
character █, two block chars per logical pixel so the glyphs read as square in a
terminal (cells are ~2:1 tall). A faint binary-noise field is laid down behind
the logo and the white logo pixels are stamped on top. A green→cyan vertical
gradient is painted over the logo with a top-down reveal animation, and a dim
tagline is centered beneath it.

Degrades gracefully: any failure (or a name with undrawn letters) makes
show_banner return False so the caller falls back to a plain rule.
"""

import random
import time

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.text import Text

# Vertical gradient stops — Anet's house palette: green (top) → cyan (bottom).
_GRADIENT = ["#22c55e", "#10b981", "#06b6d4", "#0891b2"]
# Bright leading edge of the reveal sweep.
_HIGHLIGHT = "#ecfdf5"
# Dim style for the binary-noise background.
_BG_STYLE = "grey37"
# Tagline shown under the logo (dim, centered).
_TAGLINE = "MULTI-AGENT AI ASSISTANT · RESEARCH · CODE · DESKTOP · MEMORY"

_BLOCK = "█"          # "on" pixel glyph
_PX_CHARS = 2         # chars per logical pixel (horizontal) → square-looking
_GAP = 1              # blank pixels between letters
_BG_DENSITY = 0.22    # fraction of background cells that get a 0/1

# ── Hand-authored 6×7 block glyphs ('#' = on pixel) ───────────────────────────
# Heavy, chunky strokes to match the reference weight. The A is a bold standout
# glyph in the same pixel style rather than an attempt at the ornate blackletter.
_LETTERS: dict[str, list[str]] = {
    "A": [
        " #### ",
        "##  ##",
        "##  ##",
        "######",
        "##  ##",
        "##  ##",
        "##  ##",
    ],
    "N": [
        "##  ##",
        "### ##",
        "######",
        "######",
        "## ###",
        "##  ##",
        "##  ##",
    ],
    "E": [
        "######",
        "##    ",
        "##    ",
        "##### ",
        "##    ",
        "##    ",
        "######",
    ],
    "T": [
        "######",
        "  ##  ",
        "  ##  ",
        "  ##  ",
        "  ##  ",
        "  ##  ",
        "  ##  ",
    ],
}
_PIX_H = 7            # glyph height in pixels


def _hex_to_rgb(h: str) -> tuple[int, int, int] | None:
    """Parse '#rrggbb' → (r,g,b). Returns None for anything that isn't a hex color
    (e.g. a named rich color), so the gradient can fall back instead of crashing."""
    h = h.lstrip("#")
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _interp(stops: list[str], t: float) -> str:
    """Color at position t (0..1) along the gradient stops."""
    if t <= 0:
        return stops[0]
    if t >= 1:
        return stops[-1]
    seg = t * (len(stops) - 1)
    i = int(seg)
    f = seg - i
    a, b = _hex_to_rgb(stops[i]), _hex_to_rgb(stops[i + 1])
    if a is None or b is None:
        # A stop isn't hex (named color) — can't interpolate; use the nearer stop.
        return stops[i] if f < 0.5 else stops[i + 1]
    r, g, bl = (round(a[k] + (b[k] - a[k]) * f) for k in range(3))
    return f"#{r:02x}{g:02x}{bl:02x}"


def _logo_pixels(word: str) -> list[str]:
    """Compose the word's glyphs into _PIX_H pixel rows of '#'/' '."""
    rows = [""] * _PIX_H
    for idx, ch in enumerate(word):
        glyph = _LETTERS[ch]
        for r in range(_PIX_H):
            rows[r] += glyph[r]
            if idx != len(word) - 1:
                rows[r] += " " * _GAP
    return rows


def _build_grid(console: Console, word: str):
    """Build the composite character grid: a binary-noise background with the
    logo stamped on top. Returns layers needed to render any animation frame."""
    logo = _logo_pixels(word)
    logo_px_w = len(logo[0])
    logo_char_w = logo_px_w * _PX_CHARS

    term_w = console.width or 80
    field_w = max(logo_char_w, min(term_w, 80))

    top_pad, bot_pad = 1, 1
    height = _PIX_H + top_pad + bot_pad

    # Background binary noise (generated once so it doesn't flicker between frames).
    bg = [
        [(random.choice("01") if random.random() < _BG_DENSITY else " ")
         for _ in range(field_w)]
        for _ in range(height)
    ]

    # Stamp logo pixels (2 chars wide each), centered, recording their pixel-row
    # so the gradient/reveal can color them per row.
    is_logo = [[False] * field_w for _ in range(height)]
    prow = [[-1] * field_w for _ in range(height)]
    start_col = (field_w - logo_char_w) // 2
    for r in range(_PIX_H):
        gr = r + top_pad
        for c in range(logo_px_w):
            if logo[r][c] != " ":
                base = start_col + c * _PX_CHARS
                for k in range(_PX_CHARS):
                    cc = base + k
                    if 0 <= cc < field_w:
                        is_logo[gr][cc] = True
                        prow[gr][cc] = r
    return bg, is_logo, prow, height, field_w


def _render(bg, is_logo, prow, height, field_w, row_colors, revealed: int) -> Text:
    """Render one frame. Logo pixel-rows < `revealed` show as gradient blocks
    (bright leading edge); everything else shows the dim binary background."""
    out = Text()
    for gr in range(height):
        for c in range(field_w):
            if is_logo[gr][c] and prow[gr][c] < revealed:
                pr = prow[gr][c]
                color = _HIGHLIGHT if pr == revealed - 1 else row_colors[pr]
                out.append(_BLOCK, style=f"bold {color}")
            else:
                ch = bg[gr][c]
                if ch == " ":
                    out.append(" ")
                else:
                    out.append(ch, style=_BG_STYLE)
        out.append("\n")
    return out


def show_banner(
    console: Console,
    name: str = "ANET",
    animate: bool = True,
    tagline: str | None = _TAGLINE,
    gradient: list[str] | None = None,
) -> bool:
    """Render the block-art logo with binary background + tagline. Returns True
    on success, False if it can't render (caller falls back to a plain rule).

    `gradient` overrides the vertical color stops (the active theme passes its own)."""
    try:
        word = name.upper()
        if not word or any(ch not in _LETTERS for ch in word):
            return False  # only ANET's letters are hand-drawn

        stops = gradient or _GRADIENT
        bg, is_logo, prow, height, field_w = _build_grid(console, word)
        row_colors = [_interp(stops, r / max(1, _PIX_H - 1)) for r in range(_PIX_H)]

        console.print()
        if animate and console.is_terminal:
            delay = min(0.09, 0.6 / _PIX_H)
            with Live(console=console, refresh_per_second=60, transient=False) as live:
                for revealed in range(1, _PIX_H + 1):
                    live.update(Align.center(_render(bg, is_logo, prow, height, field_w, row_colors, revealed)))
                    time.sleep(delay)
                # Settled frame: all rows colored, no bright leading edge.
                live.update(Align.center(_render(bg, is_logo, prow, height, field_w, row_colors, _PIX_H + 1)))
        else:
            console.print(Align.center(_render(bg, is_logo, prow, height, field_w, row_colors, _PIX_H + 1)))

        if tagline:
            console.print(Align.center(Text(tagline, style="dim cyan")))
        console.print()
        return True
    except Exception:
        return False


# ── High-resolution image export (for README) ─────────────────────────────────

def _load_font(size: int):
    from PIL import ImageFont
    candidates = (
        "C:/Windows/Fonts/consola.ttf",   # Consolas (Windows)
        "C:/Windows/Fonts/cour.ttf",      # Courier New
        "DejaVuSansMono.ttf",
        "consola.ttf",
    )
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def save_image(
    path: str,
    name: str = "ANET",
    tagline: str | None = _TAGLINE,
    scale: int = 3,
    mono: bool = False,
    transparent: bool = False,
    binary: bool = True,
) -> str:
    """Render the block-art logo (binary background, green→cyan gradient or a
    flat white `mono` logo, tagline) to a crisp image for the README.

    path        : output file; format inferred from extension (.png / .jpg).
    scale       : resolution multiplier (3 ≈ 3500px wide).
    mono        : draw the logo in flat white instead of the gradient.
    transparent : transparent background (PNG only; ignored for JPEG).
    binary      : draw the faint 0/1 noise field (ignored when transparent).
    Returns the path written.
    """
    from PIL import Image, ImageDraw

    is_jpeg = path.lower().endswith((".jpg", ".jpeg"))
    transparent = transparent and not is_jpeg   # JPEG has no alpha

    word = name.upper()
    logo = _logo_pixels(word)
    logo_px_w = len(logo[0])

    px       = 36 * scale          # size of one logical pixel block
    margin_x = 110 * scale
    top      = 90 * scale
    tag_gap  = 60 * scale
    tag_h    = 76 * scale
    bottom   = 64 * scale

    logo_w = logo_px_w * px
    logo_h = _PIX_H * px
    W = logo_w + 2 * margin_x
    H = top + logo_h + tag_gap + tag_h + bottom

    if transparent:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    else:
        img = Image.new("RGB", (W, H), (8, 8, 10))   # near-black background
    draw = ImageDraw.Draw(img)

    # Faint binary-noise background (skip when transparent or binary=False).
    if binary and not transparent:
        bin_font = _load_font(int(20 * scale))
        cw, ch = int(14 * scale), int(24 * scale)
        for y in range(0, H, ch):
            for x in range(0, W, cw):
                if random.random() < 0.16:
                    draw.text((x, y), random.choice("01"), font=bin_font, fill=(58, 62, 72))

    # Logo pixels — flat white (mono) or the green→cyan vertical gradient.
    x0, y0 = margin_x, top
    for r in range(_PIX_H):
        color = (245, 245, 245) if mono else _hex_to_rgb(_interp(_GRADIENT, r / max(1, _PIX_H - 1)))
        for c in range(logo_px_w):
            if logo[r][c] != " ":
                X, Y = x0 + c * px, y0 + r * px
                draw.rectangle([X, Y, X + px, Y + px], fill=color)

    # Centered tagline beneath the logo.
    if tagline:
        tag_font = _load_font(int(22 * scale))
        tw = draw.textlength(tagline, font=tag_font)
        draw.text(((W - tw) / 2, top + logo_h + tag_gap), tagline,
                  font=tag_font, fill=(150, 162, 168))

    if is_jpeg:
        img.save(path, quality=95)
    else:
        img.save(path)
    return path
