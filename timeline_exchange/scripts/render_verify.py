"""Render before/after preview strips and verify them frame by frame.

Outputs (under test_project/renders/):
  before_strip.png  - selections from the original TL-JSON timeline
  after_strip.png   - selections from the re-imported CMX EDL timeline
  missing_slate.png - a deliberately broken reference, rendered as a slate
  frame_audit.json  - per-record-frame comparison of the two strips

The two strips share one record-frame grid, so any visual difference lines up
at the same tile index.
"""

from __future__ import annotations

import json
import os
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tlx import tl_json, cmx, exchange, render, preview, fidelity  # noqa: E402
from tlx.loss import LossReport  # noqa: E402
from tlx.timecode import frame_time, frame_index_at, format_timecode  # noqa: E402

PROJECT = os.path.join(ROOT, "test_project")
OUT = os.path.join(PROJECT, "renders")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(PROJECT, "project.tl.json"), encoding="utf-8") as fh:
        original = tl_json.import_text(fh.read())

    report = LossReport(cmx.FORMAT_NAME)
    edl = cmx.export_text(original, report)
    reimported = cmx.import_text(edl, original.rate, sources=dict(original.sources))

    # Pixel fidelity and structural fidelity are separate verdicts even for a
    # plain CMX round trip; record both so a reviewer sees which axis moved.
    fid = fidelity.compare(original, reimported)

    count = frame_index_at(original.duration - original.start_time, original.rate)
    times = [original.start_time + frame_time(i, original.rate)
             for i in range(count)]

    before = render.render_strip_png(
        original, times, base_dir=PROJECT,
        out_path=os.path.join(OUT, "before_strip.png"),
    )
    after = render.render_strip_png(
        reimported, times, base_dir=PROJECT,
        out_path=os.path.join(OUT, "after_strip.png"),
    )

    audit = []
    for i, (b, a) in enumerate(zip(before["tiles"], after["tiles"])):
        audit.append({
            "record_frame": i,
            "record_timecode": b["record_timecode"],
            "before": {
                "kind": b["kind"],
                "source_id": b.get("source_id"),
                "source_frame_index": b.get("source_frame_index"),
                "transition_in": b.get("transition_in"),
            },
            "after": {
                "kind": a["kind"],
                "source_id": a.get("source_id"),
                "source_frame_index": a.get("source_frame_index"),
                "transition_in": a.get("transition_in"),
            },
            "frame_identity_preserved": (
                b.get("kind") == a.get("kind")
                and b.get("source_id") == a.get("source_id")
                and b.get("source_frame_index") == a.get("source_frame_index")
            ),
        })
    with open(os.path.join(OUT, "frame_audit.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "frames": audit,
                "fidelity": fid.to_jsonable(),
            },
            fh, indent=2,
        )

    preserved = sum(1 for row in audit if row["frame_identity_preserved"])
    changed = [row for row in audit if not row["frame_identity_preserved"]]
    print(f"rendered {count} record frames each for before/after")
    print(f"source frame identity preserved on {preserved}/{count} frames")
    print(f"loss entries: {len(report.entries)} "
          f"(errors={sum(e.severity == 'error' for e in report.entries)})")
    print(f"pixel match={fid.pixel_match} "
          f"edit_structure_faithful={fid.edit_structure_faithful} "
          f"(structure deltas={len(fid.structure_deltas)})")
    # Every non-preserved frame must map to a reported loss position/cause.
    loss_codes = {e.code for e in report.entries}
    assert "track_not_expressible" in loss_codes
    assert "transition_not_expressible" in loss_codes
    # Collect the record-frame ranges covered by each reported loss.
    rate = original.rate
    v2_frames = set()
    for tr in original.video_tracks():
        if tr.index < 1:
            continue
        for c in tr.clips:
            start = frame_index_at(c.timeline_start - original.start_time, rate)
            end = frame_index_at(c.timeline_end - original.start_time, rate)
            v2_frames.update(range(start, end))
    for row in changed:
        b, a = row["before"], row["after"]
        if row["record_frame"] in v2_frames:
            # Second video track is not expressible: the overlay is removed and
            # V1 underneath shows through. This is the track loss report.
            assert b["kind"] == "frame" and a["kind"] == "frame", row
            continue
        # Every other difference must be the push->cut downgrade on C0004,
        # where the selected source frame itself remains identical.
        assert b["kind"] == "frame" and a["kind"] == "frame", row
        assert b["source_id"] == a["source_id"], row
        assert b["source_frame_index"] == a["source_frame_index"], row
    print(f"{len(changed)} changed tiles all correspond to reported losses")

    # Deliberately break one reference to prove the slate path locates it.
    broken = tl_json.import_text(tl_json.export_text(original))
    broken.sources["CAM_B"].path = "sources/MISSING_CAM_B"
    broken_missing = preview.probe_sources(broken, PROJECT)
    assert broken_missing == ["CAM_B"], broken_missing
    render.render_strip_png(
        broken, [broken.start_time + frame_time(13, broken.rate)],
        base_dir=PROJECT,
        out_path=os.path.join(OUT, "missing_slate.png"),
    )
    print("wrote diagnostic slate for missing CAM_B -> missing_slate.png")
    print("outputs in", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
