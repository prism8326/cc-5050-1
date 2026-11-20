"""End-to-end tests for the timeline exchange kernel.

Run with:  python3 -I -m unittest -v tests.test_exchange
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tlx.timecode import (  # noqa: E402
    FrameRate, parse_timecode, format_timecode, frame_time, frame_index_at,
)
from tlx.model import (  # noqa: E402
    Timeline, Track, Clip, Gap, Source,
    TRANSITION_DISSOLVE, TRANSITION_PUSH, TRANSITION_CUT,
)
from tlx import tl_json, cmx, exchange, preview  # noqa: E402
from tlx.loss import LossReport  # noqa: E402

R24 = FrameRate(24000, 1001)
R25 = FrameRate(25, 1)
R30 = FrameRate(30000, 1001)


def make_source(sid, rate, frames):
    return Source(
        source_id=sid, path=f"sources/{sid}", rate=rate,
        duration=frame_time(frames, rate), exists=True,
    )


class TimecodeTests(unittest.TestCase):
    def test_exact_ntsc_rational(self):
        # 23.976 fps: one frame is exactly 1001/24000 s, never a float.
        self.assertEqual(frame_time(1, R24), Fraction(1001, 24000))
        self.assertIsInstance(frame_time(1, R24), Fraction)

    def test_roundtrip_timecode(self):
        for rate in (R24, R25, R30):
            for label in ("00:00:00:00", "00:12:34:15", "01:02:03:07"):
                t = parse_timecode(label, rate)
                self.assertEqual(format_timecode(t, rate), label)

    def test_nonzero_origin(self):
        origin = parse_timecode("01:00:00:00", R24)
        t = origin + parse_timecode("00:00:01:00", R24)
        self.assertEqual(
            format_timecode(t - origin, R24), "00:00:01:00"
        )
        self.assertEqual(t, origin + Fraction(1001, 1000))

    def test_frame_selection_semantics(self):
        # Half-open interval: a time exactly on a boundary selects the new frame.
        t = frame_time(5, R25)
        self.assertEqual(frame_index_at(t, R25), 5)
        self.assertEqual(frame_index_at(t - Fraction(1, 10**9), R25), 4)

    def test_no_float_drift_over_feature_length(self):
        # A two-hour NTSC timeline: summing exact frame durations must land on
        # the exact expected rational, with zero accumulated float error.
        frames = 2 * 60 * 60 * 24
        total = sum((frame_time(1, R24) for _ in range(frames)), Fraction(0))
        expected = Fraction(frames, 1) / R24.rate
        self.assertEqual(total, expected)
        # And the timecode label is exact, not off by a frame.
        self.assertEqual(format_timecode(total, R24), "02:00:00:00")


class TLJSONRoundTripTests(unittest.TestCase):
    def _build(self):
        tl = Timeline(timeline_id="t", rate=R24,
                      start_time=parse_timecode("01:00:00:00", R24))
        tl.sources = {"A": make_source("A", R25, 48), "B": make_source("B", R30, 72)}
        v1 = Track("V1", "video", 0)
        v1.clips.append(Clip(
            "C1", "A", frame_time(0, R25), frame_time(10, R25),
            tl.start_time, frame_time(10, R24)))
        v1.gaps.append(Gap("G1", tl.start_time + frame_time(10, R24),
                           frame_time(4, R24)))
        v1.clips.append(Clip(
            "C2", "B", frame_time(3, R30), frame_time(9, R30),
            tl.start_time + frame_time(14, R24), frame_time(6, R24),
            transition_in=TRANSITION_DISSOLVE,
            transition_duration=frame_time(3, R24)))
        v2 = Track("V2", "video", 1)
        v2.clips.append(Clip("C3", "A", frame_time(1, R25), frame_time(4, R25),
                             tl.start_time + frame_time(2, R24),
                             frame_time(3, R24),
                             transition_in=TRANSITION_PUSH,
                             transition_duration=frame_time(2, R24)))
        tl.tracks = [v1, v2]
        tl.validate()
        return tl

    def test_lossless_roundtrip_matches_every_frame(self):
        tl = self._build()
        text = tl_json.export_text(tl)
        again = tl_json.import_text(text)
        mismatches = exchange.compare_timelines(tl, again)
        self.assertEqual(mismatches, [],
                         [m.reason for m in mismatches])
        # Exact rational times survive, including the non-zero origin.
        self.assertEqual(again.start_time, tl.start_time)
        self.assertEqual(again.sources["A"].rate.rate, R25.rate)

    def test_bare_numeric_time_rejected(self):
        tl = self._build()
        text = tl_json.export_text(tl).replace('"rational": "', '"rational": 0 and "x": "', 1)
        # Build a deliberately invalid document instead of fragile string surgery.
        bad = json.loads(tl_json.export_text(tl))
        bad["tracks"][0]["clips"][0]["timeline_start"] = 1.5
        with self.assertRaises(tl_json.TLJSONError):
            tl_json.import_text(json.dumps(bad))


class CMXTests(unittest.TestCase):
    def setUp(self):
        proj = os.path.join(ROOT, "test_project", "project.tl.json")
        with open(proj, encoding="utf-8") as fh:
            self.tl = tl_json.import_text(fh.read())

    def test_cmx_export_reports_all_losses_with_positions(self):
        report = LossReport(cmx.FORMAT_NAME)
        edl = cmx.export_text(self.tl, report)
        codes = {e.code for e in report.entries}
        self.assertIn("track_not_expressible", codes)
        self.assertIn("gap_not_expressible", codes)
        self.assertIn("transition_not_expressible", codes)
        # Position-bearing losses carry exact rational positions.
        gap_loss = next(e for e in report.entries if e.code == "gap_not_expressible")
        self.assertIsInstance(gap_loss.timeline_start, Fraction)
        self.assertGreater(gap_loss.timeline_end, gap_loss.timeline_start)
        # Nothing was silently deleted: all four V1 clips remain in the EDL.
        self.assertEqual(edl.count(" V  "), 4)

    def test_cmx_roundtrip_preserves_source_frames(self):
        report = LossReport(cmx.FORMAT_NAME)
        edl = cmx.export_text(self.tl, report)
        again = cmx.import_text(edl, self.tl.rate, sources=dict(self.tl.sources))
        # Compare V1 frame selections only; V2 is a documented loss.
        v1_before = Timeline(
            timeline_id="b", rate=self.tl.rate, start_time=self.tl.start_time,
            tracks=[t for t in self.tl.tracks if t.track_id == "V1"],
            sources=dict(self.tl.sources),
        )
        mismatches = exchange.compare_timelines(v1_before, again)
        # The only V1 difference is the push->cut downgrade, which is exactly
        # what the loss report already flagged on C0004. Source frame identity
        # (source id + frame index) is preserved throughout.
        for m in mismatches:
            self.assertEqual(m.reason, "transition changed: push -> None")
            self.assertEqual(m.before["clip_id"], "C0004")
            self.assertEqual(m.before["source_id"], m.after["source_id"])
            self.assertEqual(
                m.before["source_frame_index"], m.after["source_frame_index"]
            )
        self.assertTrue(mismatches)
        self.assertTrue(
            any(e.code == "transition_not_expressible"
                and e.object_id == "C0004" for e in report.entries)
        )

    def test_dissolve_preserved_but_push_becomes_cut(self):
        report = LossReport(cmx.FORMAT_NAME)
        edl = cmx.export_text(self.tl, report)
        self.assertIn(" V  D  ", edl)       # dissolve survives
        push_loss = next(e for e in report.entries
                         if e.code == "transition_not_expressible")
        self.assertEqual(push_loss.object_id, "C0004")
        again = cmx.import_text(edl, self.tl.rate, sources=dict(self.tl.sources))
        c4 = next(c for c in again.tracks[0].clips if c.clip_id == "C0004")
        self.assertEqual(c4.transition_in, TRANSITION_CUT)

    def test_record_gap_reconstructed_on_import(self):
        report = LossReport(cmx.FORMAT_NAME)
        edl = cmx.export_text(self.tl, report)
        again = cmx.import_text(edl, self.tl.rate, sources=dict(self.tl.sources))
        gaps = again.tracks[0].gaps
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].duration, frame_time(6, self.tl.rate))


class FrameSelectionTests(unittest.TestCase):
    def setUp(self):
        proj = os.path.join(ROOT, "test_project", "project.tl.json")
        with open(proj, encoding="utf-8") as fh:
            self.tl = tl_json.import_text(fh.read())

    def test_adjacent_cut_boundary_selects_incoming_clip(self):
        tl = self.tl
        boundary = tl.start_time + frame_time(12, tl.rate)
        before = preview.select_frame(tl, boundary - Fraction(1, 10**9))
        at = preview.select_frame(tl, boundary)
        self.assertEqual(before.clip_id, "C0001")
        self.assertEqual(at.clip_id, "C0002")
        # Incoming mixed-rate clip starts exactly at its source_in frame.
        self.assertEqual(at.source_frame_index, 4)

    def test_mixed_rate_source_frame_advances_on_own_clock(self):
        tl = self.tl
        c2 = next(c for c in tl.tracks[0].clips if c.clip_id == "C0002")
        t0 = c2.timeline_start
        sel0 = preview.select_frame(tl, t0)
        sel5 = preview.select_frame(tl, t0 + frame_time(5, tl.rate))
        self.assertEqual(sel0.source_id, "CAM_B")
        # Exact source time must be source_in + record offset on the 25fps clock.
        self.assertEqual(sel0.source_time, c2.source_in)
        self.assertEqual(sel5.source_frame_index,
                         frame_index_at(sel5.source_time, R25))

    def test_gap_and_overlay_priority(self):
        tl = self.tl
        gap_t = tl.start_time + frame_time(26, tl.rate)
        self.assertEqual(preview.select_frame(tl, gap_t).kind, "gap")
        # V2 overlay at record frame 32 must win over V1.
        ov_t = tl.start_time + frame_time(33, tl.rate)
        sel = preview.select_frame(tl, ov_t)
        self.assertEqual(sel.clip_id, "C0101")

    def test_every_record_frame_resolves_to_real_selection(self):
        tl = self.tl
        count = frame_index_at(tl.duration - tl.start_time, tl.rate)
        kinds = set()
        for i in range(count):
            sel = preview.select_frame(tl, tl.start_time + frame_time(i, tl.rate))
            kinds.add(sel.kind)
            if sel.kind == "frame":
                self.assertGreaterEqual(sel.source_frame_index, 0)
        self.assertIn("frame", kinds)
        self.assertIn("gap", kinds)


class MissingMediaTests(unittest.TestCase):
    def test_missing_reference_is_located_not_placeholder(self):
        proj = os.path.join(ROOT, "test_project", "project.tl.json")
        with open(proj, encoding="utf-8") as fh:
            tl = tl_json.import_text(fh.read())
        # Point a source at a nonexistent path.
        tl.sources["CAM_A"].path = "sources/DOES_NOT_EXIST"
        missing = preview.probe_sources(tl, base_dir=os.path.join(ROOT, "test_project"))
        self.assertIn("CAM_A", missing)
        sel = preview.select_frame(tl, tl.start_time)
        self.assertEqual(sel.kind, "missing")
        self.assertEqual(sel.source_id, "CAM_A")
        self.assertIn("DOES_NOT_EXIST", sel.detail)
        self.assertIn(str(sel.source_frame_index), sel.detail)


class ExchangePipelineTests(unittest.TestCase):
    def test_full_tljson_to_cmx_pipeline_and_verify(self):
        proj = os.path.join(ROOT, "test_project", "project.tl.json")
        with open(proj, encoding="utf-8") as fh:
            text = fh.read()
        result = exchange.run_exchange(
            text, tl_json.FORMAT_NAME, cmx.FORMAT_NAME,
            media_base_dir=os.path.join(ROOT, "test_project"),
        )
        summary = result.verify_summary()
        self.assertEqual(summary["loss"]["entry_count"], 5)
        self.assertTrue(summary["loss"]["has_errors"])
        self.assertGreater(summary["compared_frames"], 40)
        # Every mismatch must correspond to an explicitly reported loss:
        #  - V2 overlay (C0101): single-video-track limitation
        #  - C0004 push downgrade: transition limitation (frame identity intact)
        reported = {
            (e.kind, e.object_id) for e in result.loss_report.entries
            if e.severity == "error"
        }
        self.assertIn(("track", "V2"), reported)
        self.assertIn(("transition", "C0004"), reported)
        for m in result.mismatches:
            if m.before.get("clip_id") == "C0101":
                continue  # documented second-video-track loss
            self.assertEqual(m.before.get("clip_id"), "C0004")
            self.assertIn("transition changed", m.reason)
            self.assertEqual(
                m.before.get("source_id"), m.after.get("source_id")
            )
            self.assertEqual(
                m.before.get("source_frame_index"),
                m.after.get("source_frame_index"),
            )
        self.assertEqual(result.missing_sources, [])


class ReverseDirectionTests(unittest.TestCase):
    def test_edl_to_tljson_roundtrip(self):
        # Start from the generated EDL, import at the master rate with the real
        # source registry, then export rich TL-JSON and re-read it.
        edl_path = os.path.join(ROOT, "test_project", "project.edl")
        with open(edl_path, encoding="utf-8") as fh:
            edl_text = fh.read()
        with open(os.path.join(ROOT, "test_project", "project.tl.json"),
                  encoding="utf-8") as fh:
            source_registry = tl_json.import_text(fh.read()).sources
        result = exchange.run_exchange(
            edl_text, cmx.FORMAT_NAME, tl_json.FORMAT_NAME,
            rate=R24, sources=dict(source_registry),
            media_base_dir=os.path.join(ROOT, "test_project"),
        )
        # Rich target format: no loss entries expected.
        self.assertEqual(result.loss_report.entries, [])
        self.assertFalse(result.loss_report.has_errors)
        # Re-read TL-JSON and confirm every record frame selects identical content.
        reread = tl_json.import_text(result.exported_text)
        self.assertEqual(exchange.compare_timelines(result.original, reread), [])
        # The gap and dissolve carried by the EDL survive into the rich model.
        v1 = reread.tracks[0]
        self.assertEqual(len(v1.gaps), 1)
        self.assertTrue(any(
            c.transition_in == "dissolve" for c in v1.clips
        ))

    def test_tljson_to_tljson_is_lossless(self):
        path = os.path.join(ROOT, "test_project", "project.tl.json")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        result = exchange.run_exchange(
            text, tl_json.FORMAT_NAME, tl_json.FORMAT_NAME,
            media_base_dir=os.path.join(ROOT, "test_project"),
        )
        self.assertEqual(result.loss_report.entries, [])
        self.assertEqual(result.mismatches, [])
        # The exported document re-parses to an equal timeline (exact rationals).
        again = tl_json.import_text(result.exported_text)
        original = tl_json.import_text(text)
        self.assertEqual(again.start_time, original.start_time)
        self.assertEqual(again.duration, original.duration)


if __name__ == "__main__":
    unittest.main(verbosity=2)
