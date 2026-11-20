"""Exchange orchestration: import, convert, export, re-import and verify.

Verification compares, for every record frame, the exact source frame selected
before and after the round trip, so "the file opens" is never confused with
"the content survived".
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from . import tl_json, cmx
from .timecode import FrameRate, frame_time, frame_index_at, format_timecode
from .model import Timeline
from .loss import LossReport
from . import preview


FORMATS = (tl_json.FORMAT_NAME, cmx.FORMAT_NAME)


@dataclass
class FrameMismatch:
    record_time: Fraction
    record_timecode: str
    before: dict
    after: dict
    reason: str

    def to_jsonable(self, rate: FrameRate) -> dict:
        return {
            "record_time_rational": str(self.record_time),
            "record_timecode": self.record_timecode,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
        }


@dataclass
class ExchangeResult:
    source_format: str
    target_format: str
    exported_text: str
    loss_report: LossReport
    reimported: Timeline
    original: Timeline
    mismatches: list[FrameMismatch]
    compared_frames: int
    missing_sources: list[str]

    def verify_summary(self) -> dict:
        rate = self.original.rate
        return {
            "source_format": self.source_format,
            "target_format": self.target_format,
            "compared_frames": self.compared_frames,
            "mismatch_count": len(self.mismatches),
            "loss": self.loss_report.to_jsonable(rate),
            "missing_sources": self.missing_sources,
            "mismatches": [m.to_jsonable(rate) for m in self.mismatches],
        }


def import_document(fmt: str, text: str, rate: FrameRate | None = None,
                    sources: dict | None = None, start_hour: int = 1) -> Timeline:
    if fmt == tl_json.FORMAT_NAME:
        return tl_json.import_text(text)
    if fmt == cmx.FORMAT_NAME:
        if rate is None:
            raise ValueError("a record FrameRate is required to import CMX EDL")
        return cmx.import_text(text, rate, sources=sources, start_hour=start_hour)
    raise ValueError(f"unknown format {fmt!r}; supported: {FORMATS}")


def export_document(fmt: str, tl: Timeline) -> tuple[str, LossReport]:
    report = LossReport(fmt)
    if fmt == tl_json.FORMAT_NAME:
        return tl_json.export_text(tl), report
    if fmt == cmx.FORMAT_NAME:
        return cmx.export_text(tl, report), report
    raise ValueError(f"unknown format {fmt!r}; supported: {FORMATS}")


def _selection_key(sel_desc: dict) -> tuple:
    """Identity of what is shown: kind/source/source frame/transition."""
    return (
        sel_desc.get("kind"),
        sel_desc.get("source_id"),
        sel_desc.get("source_frame_index"),
        sel_desc.get("transition_in"),
    )


def compare_timelines(before: Timeline, after: Timeline) -> list[FrameMismatch]:
    """Walk every record frame and compare the exact selected source frame."""
    rate = before.rate
    origin = before.start_time
    frame_count = frame_index_at(before.duration - origin, rate)
    mismatches: list[FrameMismatch] = []
    for i in range(frame_count):
        t = origin + frame_time(i, rate)
        b = preview.describe_selection(preview.select_frame(before, t), before)
        a = preview.describe_selection(preview.select_frame(after, t), after)
        if _selection_key(b) != _selection_key(a):
            reason = _explain_mismatch(b, a)
            mismatches.append(
                FrameMismatch(
                    record_time=t,
                    record_timecode=format_timecode(t - before.start_time, rate),
                    before=b,
                    after=a,
                    reason=reason,
                )
            )
    return mismatches


def _explain_mismatch(b: dict, a: dict) -> str:
    if b["kind"] != a["kind"]:
        return f"content kind changed: {b['kind']} -> {a['kind']}"
    if b.get("source_id") != a.get("source_id"):
        return f"source changed: {b.get('source_id')} -> {a.get('source_id')}"
    if b.get("source_frame_index") != a.get("source_frame_index"):
        return (
            f"selected source frame changed: "
            f"{b.get('source_frame_index')} -> {a.get('source_frame_index')}"
        )
    if b.get("transition_in") != a.get("transition_in"):
        return (
            f"transition changed: {b.get('transition_in')} -> "
            f"{a.get('transition_in')}"
        )
    return "frame selection differs"


def run_exchange(text: str, source_format: str, target_format: str,
                 rate: FrameRate | None = None, sources: dict | None = None,
                 media_base_dir: str | None = None,
                 start_hour: int = 1) -> ExchangeResult:
    original = import_document(
        source_format, text, rate=rate, sources=sources, start_hour=start_hour
    )
    missing = preview.probe_sources(original, media_base_dir)
    exported, report = export_document(target_format, original)

    # Re-import using the same source registry so reel names resolve back.
    re_sources = dict(original.sources)
    reimported = import_document(
        target_format,
        exported,
        rate=original.rate,
        sources=re_sources,
        start_hour=start_hour,
    )
    frame_count = frame_index_at(
        original.duration - original.start_time, original.rate
    )
    mismatches = compare_timelines(original, reimported)
    return ExchangeResult(
        source_format=source_format,
        target_format=target_format,
        exported_text=exported,
        loss_report=report,
        reimported=reimported,
        original=original,
        mismatches=mismatches,
        compared_frames=frame_count,
        missing_sources=missing,
    )
