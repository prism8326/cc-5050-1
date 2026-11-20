"""Generate real frame-bundle test media with ImageMagick.

Each bundle is a directory of numbered PNGs plus ``manifest.json``.  Every frame
is rendered with its source id, frame number, rate and timecode burned in, and
gets a distinct hue, so a wrong frame selection is immediately visible and can
also be detected programmatically via the manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tlx.timecode import FrameRate, frame_time, format_timecode  # noqa: E402

PALETTES = {
    # hue range per source so frames are visually distinct
    "CAM_A": (0, 90),       # red->yellow
    "CAM_B": (95, 180),     # green->cyan
    "CAM_C": (185, 270),    # blue->magenta
}

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _hsl(h: float, s: int = 55, l: int = 38) -> str:
    return f"hsl({h:.1f},{s}%,{l}%)"


def make_bundle(out_dir: str, source_id: str, rate: FrameRate,
                frame_count: int, size: str = "320x180") -> None:
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    h0, h1 = PALETTES[source_id]
    manifest = {
        "source_id": source_id,
        "rate": f"{rate.numerator}/{rate.denominator}",
        "frame_count": frame_count,
        "size": size,
        "frames": [],
    }
    for i in range(frame_count):
        name = f"frame_{i:06d}.png"
        t = frame_time(i, rate)
        tc = format_timecode(t, rate)
        hue = h0 + (h1 - h0) * (i / max(1, frame_count - 1))
        bg = _hsl(hue)
        label1 = f"{source_id}  f{i:04d}"
        label2 = f"{rate}  {tc}"
        label3 = f"t={str(t)}s"
        dst = os.path.join(out_dir, name)
        cmd = [
            "convert",
            "-font", FONT,
            "-size", size,
            f"xc:{bg}",
            "-fill", "white",
            "-pointsize", "26",
            "-gravity", "north",
            "-annotate", "+0+12", label1,
            "-pointsize", "18",
            "-gravity", "center",
            "-annotate", "+0+6", label2,
            "-pointsize", "14",
            "-gravity", "south",
            "-annotate", "+0+10", label3,
            "-bordercolor", "white",
            "-border", "2",
            dst,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        frames.append(name)
    manifest["frames"] = frames
    manifest["duration_rational"] = str(frame_time(frame_count, rate))
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"built {source_id}: {frame_count} frames at {rate} -> {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "test_project", "sources"))
    args = ap.parse_args()
    specs = [
        ("CAM_A", FrameRate(24000, 1001), 60),
        ("CAM_B", FrameRate(25, 1), 48),
        ("CAM_C", FrameRate(30000, 1001), 72),
    ]
    for source_id, rate, count in specs:
        make_bundle(os.path.join(args.out, source_id), source_id, rate, count)


if __name__ == "__main__":
    main()
