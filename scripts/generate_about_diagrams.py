"""Generate PNG pipeline diagrams for the Angular about page.

Run from repo root:
  .\\venv\\Scripts\\python.exe scripts\\generate_about_diagrams.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "web" / "public" / "about"

# Gigsorooni palette (matches web/src/styles/variables.css)
CREAM = (245, 240, 230)
CHARCOAL = (31, 27, 24)
ORANGE = (217, 108, 45)
MUSTARD = (219, 169, 40)
TEAL = (45, 127, 122)
WHITE = (255, 255, 255)
MUTED = (74, 68, 63)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Pick a readable font; fall back to Pillow default if TTF missing."""
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    width: int = 2,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple[int, int, int],
) -> None:
    draw.line([start, end], fill=color, width=3)
    ex, ey = end
    sx, sy = start
    if abs(ex - sx) >= abs(ey - sy):
        tip = (-8, -5) if ex > sx else (8, -5)
        draw.polygon([(ex, ey), (ex + tip[0], ey + tip[1]), (ex + tip[0], ey - tip[1])], fill=color)
    else:
        tip = (-5, -8) if ey > sy else (-5, 8)
        draw.polygon([(ex, ey), (ex + tip[0], ey + tip[1]), (ex - tip[0], ey + tip[1])], fill=color)


def _box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    label: str,
    fill: tuple[int, int, int],
    *,
    llm: bool = False,
) -> None:
    _rounded_rect(draw, xy, 14, fill, outline=CHARCOAL, width=2)
    if llm:
        badge = "AI"
        bx1, by1, bx2, by2 = xy[0] + 8, xy[1] + 8, xy[0] + 38, xy[1] + 28
        _rounded_rect(draw, (bx1, by1, bx2, by2), 8, ORANGE, outline=CHARCOAL, width=1)
        draw.text((bx1 + 7, by1 + 3), badge, fill=WHITE, font=_font(12, bold=True))
    font = _font(15, bold=True)
    tw = draw.textlength(label, font=font)
    cx = (xy[0] + xy[2]) / 2 - tw / 2
    cy = (xy[1] + xy[3]) / 2 - 8
    draw.text((cx, cy), label, fill=CHARCOAL, font=font)


def pipeline_overview() -> None:
    """Full LangGraph flow — LLM steps highlighted."""
    w, h = 920, 280
    img = Image.new("RGB", (w, h), CREAM)
    draw = ImageDraw.Draw(img)

    draw.text((24, 18), "Research pipeline (runs about every hour)", fill=CHARCOAL, font=_font(20, bold=True))
    draw.text((24, 48), "Orange AI badges = steps that call a language model", fill=MUTED, font=_font(14))

    y = 110
    boxes = [
        ("Plan", ORANGE, True),
        ("Search", TEAL, False),
        ("Crawl", TEAL, False),
        ("Curate", ORANGE, True),
        ("Enrich", MUSTARD, False),
        ("Save", MUSTARD, False),
    ]
    x = 24
    bw, bh, gap = 118, 56, 18
    for i, (label, color, llm) in enumerate(boxes):
        rect = (x, y, x + bw, y + bh)
        _box(draw, rect, label, color, llm=llm)
        if i < len(boxes) - 1:
            _draw_arrow(draw, (x + bw + 4, y + bh // 2), (x + bw + gap - 4, y + bh // 2), CHARCOAL)
        x += bw + gap

    draw.text((24, 210), "MongoDB stores events, posters, venues, and run reports", fill=TEAL, font=_font(15, bold=True))
    _draw_arrow(draw, (520, 166), (520, 198), TEAL)

    img.save(OUT_DIR / "pipeline-overview.png", optimize=True)


def planner_diagram() -> None:
    """Planner LLM inventing diverse search queries."""
    w, h = 640, 360
    img = Image.new("RGB", (w, h), CREAM)
    draw = ImageDraw.Draw(img)

    draw.text((24, 20), "Step 1 — Plan (uses AI)", fill=CHARCOAL, font=_font(22, bold=True))
    draw.text(
        (24, 54),
        "The planner LLM reads topic prompts and recent searches,\nthen returns fresh DuckDuckGo query strings.",
        fill=MUTED,
        font=_font(14),
    )

    _box(draw, (40, 120, 220, 190), "Topic prompts", TEAL)
    _box(draw, (260, 105, 380, 205), "Planner LLM", ORANGE, llm=True)
    _draw_arrow(draw, (220, 155), (258, 155), CHARCOAL)
    _draw_arrow(draw, (380, 155), (418, 155), CHARCOAL)
    _box(draw, (420, 120, 600, 190), "Search queries", MUSTARD)

    queries = [
        '"gigs Fortitude Valley June"',
        '"live music Gold Coast whats on"',
        '"concerts Brisbane this month"',
    ]
    y = 240
    for q in queries:
        _rounded_rect(draw, (40, y, 600, y + 32), 10, WHITE, outline=TEAL, width=2)
        draw.text((52, y + 7), q, fill=CHARCOAL, font=_font(13))
        y += 40

    img.save(OUT_DIR / "step-planner.png", optimize=True)


def curator_diagram() -> None:
    """Curator LLM turning messy web text into structured rows."""
    w, h = 640, 380
    img = Image.new("RGB", (w, h), CREAM)
    draw = ImageDraw.Draw(img)

    draw.text((24, 20), "Step 4 — Curate (uses AI)", fill=CHARCOAL, font=_font(22, bold=True))
    draw.text(
        (24, 54),
        "Search snippets + crawled HTML are noisy. The curator LLM\nextracts act, venue, date, and poster hints per gig.",
        fill=MUTED,
        font=_font(14),
    )

    _rounded_rect(draw, (32, 110, 300, 220), 12, WHITE, outline=CHARCOAL, width=2)
    draw.text((44, 120), "Messy web text", fill=CHARCOAL, font=_font(14, bold=True))
    draw.text(
        (44, 148),
        "…Powderfinger LIVE\nSat 12 Jul · Tivoli…\n[IMG alt=poster src=…]",
        fill=MUTED,
        font=_font(12),
    )

    _box(draw, (330, 125, 450, 205), "Curator LLM", ORANGE, llm=True)
    _draw_arrow(draw, (300, 165), (328, 165), CHARCOAL)
    _draw_arrow(draw, (450, 165), (478, 165), CHARCOAL)

    _rounded_rect(draw, (480, 110, 608, 220), 12, WHITE, outline=TEAL, width=2)
    draw.text((492, 120), "Structured rows", fill=TEAL, font=_font(14, bold=True))
    draw.text(
        (492, 148),
        "Event · Venue · Date\nPoster URL · Summary",
        fill=CHARCOAL,
        font=_font(12),
    )

    draw.text(
        (32, 260),
        "After the LLM: plain code dedupes, caches posters, and saves to MongoDB.",
        fill=MUTED,
        font=_font(13),
    )

    img.save(OUT_DIR / "step-curator.png", optimize=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pipeline_overview()
    planner_diagram()
    curator_diagram()
    print(f"Wrote diagrams to {OUT_DIR}")


if __name__ == "__main__":
    main()
