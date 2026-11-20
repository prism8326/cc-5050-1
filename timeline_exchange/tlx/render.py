"""Render preview tiles and contact-sheet strips.

Two outputs share one exact frame-selection kernel:

* :func:`render_tile_svg` returns a self-contained SVG the web UI shows live.
  Real frames are embedded by absolute file path; missing media becomes a red
  diagnostic slate naming the broken reference.
* :func:`render_strip_png` composites a horizontal PNG strip with ImageMagick
  for offline verification of the same selections.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction

from .model import Timeline
from .timecode import format_timecode
from . import preview

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _svg_escape(text: str) -> str:
    return (
        str(text).replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _slate_svg(width, height, title, lines, color):
    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{color}"/>',
        f'<rect x="6" y="6" width="{width - 12}" height="{height - 12}" '
        f'fill="none" stroke="#fff" stroke-width="2"/>',
        f'<text x="{width // 2}" y="32" fill="#fff" font-family="monospace" '
        f'font-size="19" text-anchor="middle" font-weight="bold">'
        f"{_svg_escape(title)}</text>",
    ]
    y = 60
    for line in lines:
        body.append(
            f'<text x="12" y="{y}" fill="#fff" font-family="monospace" font-size="14">'
            f"{_svg_escape(line)}</text>"
        )
        y += 21
    body.append("</svg>")
    return "".join(body)


def _frame_bundle(tl: Timeline, source_id: str, base_dir: str | None):
    src = tl.sources[source_id]
    path = src.path or ""
    if base_dir and not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    manifest_path = os.path.join(path, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(path)
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return path, manifest


def _missing_slate_svg(tl, sel, width, height, reason):
    return _slate_svg(
        width, height, "MEDIA MISSING",
        [
            f"record {format_timecode(sel.timeline_time - tl.start_time, tl.rate)}",
            f"clip {sel.clip_id} source {sel.source_id}",
            f"path {tl.sources[sel.source_id].path}",
            f"wanted source frame {sel.source_frame_index}",
            reason,
        ],
        "#7a1212",
    )


def render_tile_svg(tl: Timeline, time: Fraction, base_dir: str | None,
                    width: int = 320, height: int = 180) -> str:
    sel = preview.select_frame(tl, time)
    tc = format_timecode(sel.timeline_time - tl.start_time, tl.rate)

    if sel.kind == "gap":
        return _slate_svg(width, height, "GAP", [f"record {tc}", sel.detail], "#15314b")
    if sel.kind == "black":
        return _slate_svg(width, height, "NO SIGNAL", [f"record {tc}", sel.detail], "#000")
    if sel.kind == "missing":
        return _missing_slate_svg(tl, sel, width, height, "source marked missing")

    try:
        bundle, manifest = _frame_bundle(tl, sel.source_id, base_dir)
    except FileNotFoundError:
        return _missing_slate_svg(tl, sel, width, height, "frame bundle not found")

    frame_name = manifest["frames"][sel.source_frame_index]
    frame_path = os.path.abspath(os.path.join(bundle, frame_name))
    if not os.path.exists(frame_path):
        return _slate_svg(
            width, height, "FRAME MISSING",
            [f"record {tc}", f"source {sel.source_id} frame {sel.source_frame_index}",
             f"expected {frame_name}"],
            "#7a1212",
        )

    overlay = ""
    if sel.transition_in:
        pct = int(float(sel.transition_progress) * 100)
        overlay = (
            f'<rect x="0" y="0" width="{width}" height="20" fill="rgba(0,0,0,0.55)"/>'
            f'<text x="8" y="15" fill="#ffd34d" font-family="monospace" font-size="13">'
            f"{_svg_escape(sel.transition_in)} {pct}%</text>"
        )
    label = (
        f'<rect x="0" y="{height - 24}" width="{width}" height="24" '
        f'fill="rgba(0,0,0,0.62)"/>'
        f'<text x="8" y="{height - 7}" fill="#fff" font-family="monospace" font-size="13">'
        f"{_svg_escape(tc)} | {_svg_escape(sel.source_id)} f{sel.source_frame_index}</text>"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<image href="file://{frame_path}" width="{width}" height="{height}" '
        f'preserveAspectRatio="xMidYMid meet"/>'
        f"{overlay}{label}</svg>"
    )


def _imagemagick() -> str:
    magick = shutil.which("convert")
    if not magick:
        raise RuntimeError("ImageMagick 'convert' is required to render PNG strips")
    return magick


def _png_tile(tl, time, base_dir, tmpdir, index, tile_w, tile_h):
    """Render one tile directly with ImageMagick (no SVG font dependency)."""
    sel = preview.select_frame(tl, time)
    tc = format_timecode(sel.timeline_time - tl.start_time, tl.rate)
    out = os.path.join(tmpdir, f"tile_{index:04d}.png")
    convert = _imagemagick()

    if sel.kind in ("gap", "black"):
        bg = "#15314b" if sel.kind == "gap" else "#000000"
        title = "GAP" if sel.kind == "gap" else "NO SIGNAL"
        subprocess.run([
            convert, "-font", FONT, "-size", f"{tile_w}x{tile_h}", f"xc:{bg}",
            "-fill", "white", "-gravity", "north", "-pointsize", "22",
            "-annotate", "+0+14", title,
            "-pointsize", "14", "-gravity", "south", "-annotate", "+0+10", tc,
            out,
        ], check=True, capture_output=True)
        return out, sel

    if sel.kind == "missing":
        path = tl.sources[sel.source_id].path
        subprocess.run([
            convert, "-font", FONT, "-size", f"{tile_w}x{tile_h}", "xc:#7a1212",
            "-fill", "white", "-pointsize", "18", "-gravity", "north",
            "-annotate", "+0+10", "MEDIA MISSING",
            "-pointsize", "13", "-gravity", "center",
            "-annotate", "+0-12",
            f"{sel.source_id} f{sel.source_frame_index}",
            "-annotate", "+0+8", tc,
            "-pointsize", "11", "-annotate", "+0+28", path,
            out,
        ], check=True, capture_output=True)
        return out, sel

    bundle, manifest = _frame_bundle(tl, sel.source_id, base_dir)
    frame_path = os.path.join(bundle, manifest["frames"][sel.source_frame_index])
    badge = f"{tc} {sel.source_id} f{sel.source_frame_index}"
    if sel.transition_in:
        badge = f"{sel.transition_in} {int(float(sel.transition_progress)*100)}%  {badge}"
    subprocess.run([
        convert, frame_path,
        "-resize", f"{tile_w}x{tile_h}",
        "-background", "black", "-gravity", "center",
        "-extent", f"{tile_w}x{tile_h}",
        "-font", FONT, "-fill", "white",
        "-undercolor", "rgba(0,0,0,0.62)",
        "-pointsize", "13", "-gravity", "south",
        "-annotate", "+4+3", badge,
        out,
    ], check=True, capture_output=True)
    return out, sel


def render_strip_png(tl: Timeline, times: list[Fraction], base_dir: str | None,
                     out_path: str, tile_w: int = 256, tile_h: int = 144) -> dict:
    """Composite a horizontal contact-sheet PNG of real frame selections."""
    convert = _imagemagick()
    tmpdir = tempfile.mkdtemp(prefix="tlx_render_")
    tile_paths, selections = [], []
    try:
        for i, t in enumerate(times):
            path, sel = _png_tile(tl, t, base_dir, tmpdir, i, tile_w, tile_h)
            tile_paths.append(path)
            selections.append(preview.describe_selection(sel, tl))
        subprocess.run(
            [convert] + tile_paths + ["+append", out_path],
            check=True, capture_output=True,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {"out_path": out_path, "tiles": selections, "tile_count": len(times)}
