"""Generate real frame-bundle test media with ImageMagick.

Each bundle is a directory of numbered PNGs plus ``manifest.json``.  Every frame
is rendered with its source id, frame number, rate and timecode burned in, and
gets a distinct hue, so a wrong frame selection is immediately visible and can
also be detected programmatically via the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tlx.timecode import (  # noqa: E402
    FrameRate, frame_time, format_timecode, parse_timecode,
)


def _tc_seconds(label: str, rate: FrameRate) -> Fraction:
    return parse_timecode(label, rate)

PALETTES = {
    # hue range per source so frames are visually distinct
    "CAM_A": (0, 90),       # red->yellow
    "CAM_B": (95, 180),     # green->cyan
    "CAM_C": (185, 270),    # blue->magenta
    "CAM_A_BAKED": (330, 360),  # same frames as CAM_A, distinct name
    "CAM_B_ALT": (95, 180),     # same palette as CAM_B, different content
}

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _hsl(h: float, s: int = 55, l: int = 38) -> str:
    return f"hsl({h:.1f},{s}%,{l}%)"


def make_bundle(out_dir: str, source_id: str, rate: FrameRate,
                frame_count: int, size: str = "320x180",
                content_seed: str | None = None,
                timecode_origin: str | None = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    # Palette follows the *content*, not the bundle name, so a baked copy is
    # truly pixel-identical to its source and only its identity differs.
    h0, h1 = PALETTES[content_seed or source_id]
    manifest = {
        "source_id": source_id,
        "rate": f"{rate.numerator}/{rate.denominator}",
        "frame_count": frame_count,
        "size": size,
        "content_seed": content_seed or source_id,
        "frames": [],
    }
    if timecode_origin:
        manifest["timecode_origin"] = timecode_origin
        manifest["timecode_origin_rational"] = str(_tc_seconds(timecode_origin, rate))
    for i in range(frame_count):
        name = f"frame_{i:06d}.png"
        t = frame_time(i, rate)
        tc = format_timecode(t, rate)
        hue = h0 + (h1 - h0) * (i / max(1, frame_count - 1))
        bg = _hsl(hue)
        label1 = f"{content_seed or source_id}  f{i:04d}"
        label2 = f"{rate}  {tc}"
        label3 = f"t={str(t)}s  seed={content_seed or source_id}"
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
            # Deterministic bytes: strip date/timestamp chunks so a pixel-
            # identical copy hashes identically.
            "-strip",
            dst,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        frames.append(name)
    manifest["frames"] = frames
    manifest["duration_rational"] = str(frame_time(frame_count, rate))
    # Fingerprint the real pixel bytes so a same-duration stand-in cannot match.
    digest = hashlib.sha256()
    for name in frames:
        with open(os.path.join(out_dir, name), "rb") as fh:
            digest.update(fh.read())
    manifest["content_id"] = "sha256:" + digest.hexdigest()[:24]
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"built {source_id}: {frame_count} frames at {rate} -> {out_dir}")


def write_catalog(out_dir: str) -> str:
    """Write a catalog index mapping stable media_refs to bundles.

    Two bundles can be pixel-identical (CAM_A / CAM_A_BAKED) and still be
    distinct media; two bundles can share a display name (CAM_B / CAM_B_ALT)
    and be different media.  The catalog records identity explicitly rather
    than inferring it from a path or a name.
    """
    def entry(bundle: str, media_ref: str, display_name: str | None = None,
              timecode_origin: str | None = None) -> dict:
        manifest = json.load(open(os.path.join(out_dir, bundle, "manifest.json"),
                                  encoding="utf-8"))
        row = {
            "media_ref": media_ref,
            "path": bundle,
            "display_name": display_name or bundle,
        }
        if timecode_origin:
            row["timecode_origin_rational"] = timecode_origin
        return row

    def origin_of(bundle: str) -> str | None:
        m = json.load(open(os.path.join(out_dir, bundle, "manifest.json"),
                           encoding="utf-8"))
        return m.get("timecode_origin_rational")

    assets = [
        entry("CAM_A", "media:cam-a"),
        entry("CAM_B", "media:cam-b"),
        entry("CAM_C", "media:cam-c"),
        entry("CAM_A_BAKED", "media:cam-a-baked"),
        # Same display name as CAM_B but a different asset and timecode origin.
        entry("CAM_B_ALT", "media:cam-b-alt", display_name="CAM_B",
              timecode_origin=origin_of("CAM_B_ALT")),
    ]
    path = os.path.join(out_dir, "catalog.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": "tlx-media-catalog-v1", "assets": assets},
                  fh, indent=2)
    print("wrote", path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "test_project", "sources"))
    args = ap.parse_args()
    specs = [
        ("CAM_A", FrameRate(24000, 1001), 60),
        ("CAM_B", FrameRate(25, 1), 48),
        ("CAM_C", FrameRate(30000, 1001), 72),
        # A pixel-identical, *different name* copy of CAM_A to model a baked
        # stand-in: same frames, distinct media identity and timecode origin.
        ("CAM_A_BAKED", FrameRate(24000, 1001), 60),
        # A same-name, different-content asset used to prove that a source
        # reference validator must follow media_ref, not the display name.
        ("CAM_B_ALT", FrameRate(25, 1), 48),
    ]
    for source_id, rate, count in specs:
        seed = "CAM_A" if source_id == "CAM_A_BAKED" else source_id
        make_bundle(
            os.path.join(args.out, source_id), source_id, rate, count,
            content_seed=seed,
            timecode_origin="02:00:00:00" if source_id == "CAM_B_ALT" else None,
        )
    write_catalog(args.out)


if __name__ == "__main__":
    main()
