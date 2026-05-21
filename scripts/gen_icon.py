"""Generate memory-dump app icon. 1024x1024 PNG, no alpha (iOS adds mask).

Concept: links fall from above into a central memory hub, which radiates a small
knowledge graph beneath. Brand: violet gradient + crisp white glyph.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
OUT = Path(__file__).resolve().parents[1] / "apps" / "mobile" / "assets" / "icon.png"
ADAPTIVE_OUT = OUT.with_name("adaptive-icon.png")


def diagonal_gradient(
    size: int,
    top_left: tuple[int, int, int],
    bottom_right: tuple[int, int, int],
) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    t = (x + y) / (2.0 * (size - 1))
    t = t[..., None]
    a = np.array(top_left, dtype=np.float32)
    b = np.array(bottom_right, dtype=np.float32)
    arr = (a * (1.0 - t) + b * t).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def radial_glow(
    size: int,
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int],
    strength: float = 0.45,
) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    dx = x - center[0]
    dy = y - center[1]
    d = np.sqrt(dx * dx + dy * dy)
    a = np.clip(1.0 - d / radius, 0.0, 1.0) ** 2 * strength
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., 0] = color[0]
    rgba[..., 1] = color[1]
    rgba[..., 2] = color[2]
    rgba[..., 3] = (a * 255.0).astype(np.float32)
    return Image.fromarray(rgba.astype(np.uint8), mode="RGBA")


def draw_glyph(canvas: Image.Image) -> Image.Image:
    """White line+dot composition centered on the canvas."""
    size = canvas.width
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    white = (245, 245, 247, 255)
    white_soft = (245, 245, 247, 200)
    white_faint = (245, 245, 247, 130)

    cx = size / 2
    # Hub sits a touch below center to leave room for the falling links above.
    hub_y = size * 0.62
    hub_r = size * 0.075

    # --- 3 falling "link" particles above ---
    drops = [
        (cx - size * 0.16, size * 0.16, size * 0.018),
        (cx + size * 0.04, size * 0.10, size * 0.024),
        (cx + size * 0.18, size * 0.20, size * 0.018),
    ]
    for x, y, r in drops:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=white)
        # tiny streak below each drop
        draw.line([(x, y + r * 1.2), (x, y + r * 3.2)], fill=white_faint, width=4)

    # --- Funnel chevron ---
    chev_top = size * 0.28
    chev_bot = size * 0.46
    chev_half = size * 0.16
    draw.line(
        [(cx - chev_half, chev_top), (cx, chev_bot)],
        fill=white,
        width=18,
        joint="curve",
    )
    draw.line(
        [(cx, chev_bot), (cx + chev_half, chev_top)],
        fill=white,
        width=18,
        joint="curve",
    )

    # --- Memory hub (central node) ---
    draw.ellipse(
        (cx - hub_r, hub_y - hub_r, cx + hub_r, hub_y + hub_r),
        fill=white,
    )
    # subtle ring around the hub
    ring_r = hub_r * 1.55
    draw.ellipse(
        (cx - ring_r, hub_y - ring_r, cx + ring_r, hub_y + ring_r),
        outline=white_soft,
        width=4,
    )

    # --- Knowledge graph: 5 satellites around the hub ---
    sat_distance = size * 0.20
    sat_r = size * 0.028
    # Angles in degrees, measured from +x axis, but we render in screen coords (y down).
    # Place 5 satellites in a fan beneath/around the hub for the "second brain" web.
    angles_deg = [200, 245, 295, 340, 30]
    positions: list[tuple[float, float]] = []
    for deg in angles_deg:
        rad = math.radians(deg)
        sx = cx + sat_distance * math.cos(rad)
        sy = hub_y - sat_distance * math.sin(rad)  # subtract because screen y is down
        positions.append((sx, sy))

    # Connect satellites to hub
    for sx, sy in positions:
        # vector from hub edge → satellite (skip overlap with hub)
        vx, vy = sx - cx, sy - hub_y
        length = math.sqrt(vx * vx + vy * vy)
        ux, uy = vx / length, vy / length
        start = (cx + ux * hub_r * 1.05, hub_y + uy * hub_r * 1.05)
        end = (sx - ux * sat_r * 1.05, sy - uy * sat_r * 1.05)
        draw.line([start, end], fill=white_soft, width=6)

    # Sparse cross-links between satellites for "wiki backlinks" feel
    cross_pairs = [(0, 1), (1, 2), (2, 3), (3, 4)]
    for i, j in cross_pairs:
        sx1, sy1 = positions[i]
        sx2, sy2 = positions[j]
        vx, vy = sx2 - sx1, sy2 - sy1
        length = math.sqrt(vx * vx + vy * vy)
        ux, uy = vx / length, vy / length
        start = (sx1 + ux * sat_r * 1.05, sy1 + uy * sat_r * 1.05)
        end = (sx2 - ux * sat_r * 1.05, sy2 - uy * sat_r * 1.05)
        draw.line([start, end], fill=white_faint, width=4)

    # Draw satellite dots last so they sit on top of the lines
    for sx, sy in positions:
        draw.ellipse(
            (sx - sat_r, sy - sat_r, sx + sat_r, sy + sat_r),
            fill=white,
        )

    # Slight blur on a copy then composite for soft glow on the white shapes
    glow = overlay.filter(ImageFilter.GaussianBlur(radius=6))
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba = Image.alpha_composite(canvas_rgba, glow)
    canvas_rgba = Image.alpha_composite(canvas_rgba, overlay)
    return canvas_rgba.convert("RGB")


def main() -> None:
    # Brand violet → deep indigo diagonal
    base = diagonal_gradient(SIZE, top_left=(124, 92, 255), bottom_right=(20, 14, 56))

    # Add a soft glow up top-left to suggest light source
    glow = radial_glow(
        SIZE,
        center=(SIZE * 0.20, SIZE * 0.15),
        radius=SIZE * 0.70,
        color=(180, 150, 255),
        strength=0.55,
    )
    base_rgba = base.convert("RGBA")
    base_rgba = Image.alpha_composite(base_rgba, glow)
    base = base_rgba.convert("RGB")

    final = draw_glyph(base)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    final.save(OUT, format="PNG", optimize=True)
    # Adaptive-icon (Android): just reuse the same square art.
    final.save(ADAPTIVE_OUT, format="PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"wrote {ADAPTIVE_OUT} ({ADAPTIVE_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
