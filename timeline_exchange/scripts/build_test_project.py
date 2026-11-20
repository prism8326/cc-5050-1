"""Build the mixed-rate, non-zero-start, gap-and-transition test project."""

from __future__ import annotations

import json
import os
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tlx.timecode import FrameRate, frame_time, parse_timecode  # noqa: E402
from tlx.model import (  # noqa: E402
    Timeline, Track, Clip, Gap, Source,
    TRANSITION_CUT, TRANSITION_DISSOLVE, TRANSITION_PUSH,
)
from tlx import tl_json, cmx  # noqa: E402

PROJECT_DIR = os.path.join(ROOT, "test_project")
SOURCE_DIR = os.path.join(PROJECT_DIR, "sources")
MASTER = FrameRate(24000, 1001)
ORIGIN = parse_timecode("01:00:00:00", MASTER)


def _load_source(source_id: str) -> Source:
    manifest_path = os.path.join(SOURCE_DIR, source_id, "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return Source(
        source_id=source_id,
        path=os.path.join("sources", source_id),
        rate=FrameRate.from_value(manifest["rate"]),
        duration=Fraction(manifest["duration_rational"]),
        exists=True,
    )


def build() -> Timeline:
    sources = {sid: _load_source(sid) for sid in ("CAM_A", "CAM_B", "CAM_C")}
    tl = Timeline(
        timeline_id="mixed_rate_demo",
        rate=MASTER,
        start_time=ORIGIN,
        metadata={"title": "Mixed rate gap/transition demo"},
    )
    tl.sources = sources

    v1 = Track(track_id="V1", kind="video", index=0)
    v2 = Track(track_id="V2", kind="video", index=1)

    m = MASTER
    def F(frames: int) -> Fraction:
        return frame_time(frames, m)

    # Clip 1: CAM_A (23.976 source) first 12 record frames, starts at origin.
    c1 = Clip(
        clip_id="C0001", source_id="CAM_A",
        source_in=frame_time(0, sources["CAM_A"].rate),
        source_out=frame_time(12, sources["CAM_A"].rate),
        timeline_start=ORIGIN,
        duration=F(12),
    )
    # Clip 2 abuts C1 exactly (adjacent cut), CAM_B is 25fps -> mixed rate.
    c2 = Clip(
        clip_id="C0002", source_id="CAM_B",
        source_in=frame_time(4, sources["CAM_B"].rate),
        source_out=frame_time(16, sources["CAM_B"].rate),
        timeline_start=ORIGIN + F(12),
        duration=F(12),
    )
    # Explicit 6-frame gap on the record timeline.
    gap = Gap(gap_id="G0001", start=ORIGIN + F(24), duration=F(6))
    # Clip 3 after the gap, CAM_C (29.97), with a dissolve at its head.
    c3 = Clip(
        clip_id="C0003", source_id="CAM_C",
        source_in=frame_time(10, sources["CAM_C"].rate),
        source_out=frame_time(22, sources["CAM_C"].rate),
        timeline_start=ORIGIN + F(30),
        duration=F(12),
        transition_in=TRANSITION_DISSOLVE,
        transition_duration=F(6),
    )
    # Clip 4 abuts C3: a push transition that CMX cannot express.
    c4 = Clip(
        clip_id="C0004", source_id="CAM_A",
        source_in=frame_time(20, sources["CAM_A"].rate),
        source_out=frame_time(30, sources["CAM_A"].rate),
        timeline_start=ORIGIN + F(42),
        duration=F(10),
        transition_in=TRANSITION_PUSH,
        transition_duration=F(4),
    )
    v1.clips = [c1, c2, c3, c4]
    v1.gaps = [gap]

    # A second video track: legal in TL-JSON, impossible in CMX -> loss report.
    overlay = Clip(
        clip_id="C0101", source_id="CAM_B",
        source_in=frame_time(0, sources["CAM_B"].rate),
        source_out=frame_time(6, sources["CAM_B"].rate),
        timeline_start=ORIGIN + F(32),
        duration=F(6),
    )
    v2.clips = [overlay]

    tl.tracks = [v1, v2]
    tl.validate()
    return tl


def main() -> None:
    os.makedirs(PROJECT_DIR, exist_ok=True)
    tl = build()
    tljson_path = os.path.join(PROJECT_DIR, "project.tl.json")
    with open(tljson_path, "w", encoding="utf-8") as fh:
        fh.write(tl_json.export_text(tl))

    from tlx.loss import LossReport
    report = LossReport(cmx.FORMAT_NAME)
    edl = cmx.export_text(tl, report)
    edl_path = os.path.join(PROJECT_DIR, "project.edl")
    with open(edl_path, "w", encoding="utf-8") as fh:
        fh.write(edl)
    report_path = os.path.join(PROJECT_DIR, "expected_edl_loss.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report.to_jsonable(tl.rate), fh, indent=2)
    print("wrote", tljson_path)
    print("wrote", edl_path)
    print("wrote", report_path, f"({len(report.entries)} loss entries)")


if __name__ == "__main__":
    main()
