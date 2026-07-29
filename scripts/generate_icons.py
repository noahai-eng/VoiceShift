#!/usr/bin/env python3
"""
Generator für Menüleisten-Icon (Symbol ohne Schrift) + Animationsframes.

Output:
  assets/icon_symbol.svg          – statisches Symbol-Icon
  assets/icon_symbol.png          – 44 px PNG (retina-fähig)
  assets/animation/frame_NN.png   – 8 Animationsframes (44 px)
  assets/animation/frame_NN.svg   – Quell-SVGs der Frames
"""
import math
import os
import subprocess
import sys

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS     = os.path.join(ROOT, "assets")
ANIM_DIR   = os.path.join(ASSETS, "animation")

SIZE       = 512
# Schwarz mit transparentem Hintergrund → macOS behandelt das PNG als
# Template-Bild und tintet es automatisch passend zum Menüleisten-Style.
FG         = "black"

N_BARS     = 9
BAR_W      = 36
GAP        = 24
MIN_H      = 120
MAX_H      = 460
Y_CENTER   = SIZE // 2

N_FRAMES   = 8
PNG_SIZE   = 44     # Menüleiste retina-Größe

# Statische Höhen (asymmetrisch wie im Vollogo, nur ohne Schriftbereich)
STATIC_HEIGHTS = [180, 300, 430, 240, 460, 350, 200, 380, 260]

TOTAL_W = N_BARS * BAR_W + (N_BARS - 1) * GAP
START_X = (SIZE - TOTAL_W) // 2


def make_svg(heights):
    bars = []
    for i, h in enumerate(heights):
        x = START_X + i * (BAR_W + GAP)
        y = Y_CENTER - h / 2
        bars.append(
            f'    <rect x="{x}" y="{y:.1f}" width="{BAR_W}" '
            f'height="{h:.1f}" rx="{BAR_W // 2}"/>'
        )
    bars_str = "\n".join(bars)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}" '
        f'xmlns="http://www.w3.org/2000/svg">\n'
        f'  <g fill="{FG}">\n{bars_str}\n  </g>\n'
        f'</svg>\n'
    )


def frame_heights(frame_idx):
    """Sinus-basierte Höhen mit Phase pro Balken – wirkt wie echte Audio-Pegel."""
    heights = []
    for bar_idx in range(N_BARS):
        # Jeder Balken hat eigene Phasenverschiebung (Welle wandert)
        phase_offset = bar_idx * 0.85
        phase = (frame_idx / N_FRAMES) * 2 * math.pi + phase_offset
        norm = (math.sin(phase) + 1) / 2          # 0..1
        # Leichte Asymmetrie pro Balken (manche bleiben kleiner)
        scale = 0.75 + 0.25 * math.sin(bar_idx * 1.3)
        h = MIN_H + norm * (MAX_H - MIN_H) * scale
        heights.append(h)
    return heights


def render_png(svg_path, png_path, size):
    subprocess.run(
        ["/opt/homebrew/bin/rsvg-convert", "-w", str(size), "-h", str(size),
         svg_path, "-o", png_path],
        check=True,
    )


def main():
    os.makedirs(ANIM_DIR, exist_ok=True)

    # Statisches Symbol
    static_svg = os.path.join(ASSETS, "icon_symbol.svg")
    static_png = os.path.join(ASSETS, "icon_symbol.png")
    with open(static_svg, "w") as f:
        f.write(make_svg(STATIC_HEIGHTS))
    render_png(static_svg, static_png, PNG_SIZE)
    print(f"✅ {os.path.relpath(static_svg, ROOT)}")
    print(f"✅ {os.path.relpath(static_png, ROOT)}")

    # Animationsframes
    for frame in range(N_FRAMES):
        svg_path = os.path.join(ANIM_DIR, f"frame_{frame:02d}.svg")
        png_path = os.path.join(ANIM_DIR, f"frame_{frame:02d}.png")
        with open(svg_path, "w") as f:
            f.write(make_svg(frame_heights(frame)))
        render_png(svg_path, png_path, PNG_SIZE)
        print(f"✅ {os.path.relpath(png_path, ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
