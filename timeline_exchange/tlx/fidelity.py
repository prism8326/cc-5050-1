"""Fidelity report: pixels and edit structure are separate verdicts.

Two projects can render identical pixels while one of them has lost its edit.
The report therefore never collapses to a single "lossless" boolean:

* ``pixel`` — for every record frame, does the decoded source frame have the
  same identity (source media + frame index)?  This can stay true even when a
  transition has been baked away.
* ``structure`` — are the edit points still editable: are the clips still
  present, do their source ranges match, is the transition still a transition,
  and does a cut remain a cut?

A flattened transition is the canonical case: pixels match, structure does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from .model import Timeline, TRANSITION_CUT
from .timecode import format_timecode, frame_index_at, frame_time
from . import preview


def _frame_identity(sel_desc: dict) -> tuple:
    """Pixel identity: decoded media content + frame index.

    The display ``source_id`` is deliberately excluded.  Two projects can show
    the *same picture* through differently named sources (a baked copy vs the
    original range); that is a pixel match.  Whether the edit point is still
    editable is a separate question answered by the structure axis.
    """
    media = sel_desc.get("source_content_id") or sel_desc.get("source_id")
    return (
        sel_desc.get("kind"),
        media,
        sel_desc.get("source_frame_index"),
    )


@dataclass
class FrameDelta:
    record_timecode: str
    reason: str
    before: dict
    after: dict

    def to_jsonable(self) -> dict:
        return {
            "record_timecode": self.record_timecode,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
        }


@dataclass
class StructureDelta:
    kind: str  # clip_missing | range_changed | transition_changed | cut_changed
    clip_id: str
    record_timecode: str
    message: str

    def to_jsonable(self) -> dict:
        return {
            "kind": self.kind,
            "clip_id": self.clip_id,
            "record_timecode": self.record_timecode,
            "message": self.message,
        }


@dataclass
class FidelityReport:
    compared_frames: int
    frame_deltas: list[FrameDelta]
    structure_deltas: list[StructureDelta]
    notes: list[str] = field(default_factory=list)

    @property
    def pixel_match(self) -> bool:
        return not self.frame_deltas

    @property
    def edit_structure_faithful(self) -> bool:
        return not self.structure_deltas

    def to_jsonable(self) -> dict:
        return {
            "pixel": {
                "match": self.pixel_match,
                "compared_frames": self.compared_frames,
                "differing_frames": len(self.frame_deltas),
                "deltas": [d.to_jsonable() for d in self.frame_deltas],
            },
            "structure": {
                "faithful": self.edit_structure_faithful,
                "deltas": [d.to_jsonable() for d in self.structure_deltas],
            },
            "separate_verdicts": True,
            "notes": self.notes,
        }


def _clips_by_position(tl: Timeline) -> dict[tuple, tuple]:
    """Match clips by edit location, not by an id the target may renumber."""
    out = {}
    for track in tl.tracks:
        for clip in track.clips:
            out[(track.index, clip.timeline_start)] = (track, clip)
    return out


def _gaps_by_position(tl: Timeline) -> dict[tuple, tuple]:
    out = {}
    for track in tl.tracks:
        for gap in track.gaps:
            out[(track.index, gap.start)] = (track, gap)
    return out


def _tc(tl: Timeline, time: Fraction) -> str:
    return format_timecode(time - tl.start_time, tl.rate)


def compare(before: Timeline, after: Timeline,
            pixel_source_only: bool = False) -> FidelityReport:
    """Compare two timelines on both axes.

    ``pixel_source_only`` compares decoded source frames only (ignoring the
    transition label), which is exactly the "same picture, different edit"
    case.  Structure is compared independently and always.
    """
    rate = before.rate
    count = frame_index_at(before.duration - before.start_time, rate)
    frame_deltas: list[FrameDelta] = []
    for i in range(count):
        t = before.start_time + frame_time(i, rate)
        b = preview.describe_selection(preview.select_frame(before, t), before)
        a = preview.describe_selection(preview.select_frame(after, t), after)
        if _frame_identity(b) != _frame_identity(a):
            reason = "source frame identity changed"
            if b.get("source_id") != a.get("source_id"):
                reason = "source media changed"
            elif b.get("source_frame_index") != a.get("source_frame_index"):
                reason = "source frame index changed"
            frame_deltas.append(
                FrameDelta(_tc(before, t), reason,
                           {k: b.get(k) for k in ("kind", "source_id",
                                                  "source_frame_index")},
                           {k: a.get(k) for k in ("kind", "source_id",
                                                  "source_frame_index")})
            )

    structure_deltas = _compare_structure(before, after)
    notes = []
    if not frame_deltas and structure_deltas:
        notes.append(
            "decoded source frames match exactly, but the edit structure is "
            "not faithful: identical pixels do not imply an editable cut"
        )
    return FidelityReport(count, frame_deltas, structure_deltas, notes)


def _compare_structure(before: Timeline, after: Timeline) -> list[StructureDelta]:
    deltas: list[StructureDelta] = []
    b_clips = _clips_by_position(before)
    a_clips = _clips_by_position(after)

    for key, (track, bclip) in b_clips.items():
        if key not in a_clips:
            deltas.append(
                StructureDelta(
                    "clip_missing", bclip.clip_id, _tc(before, bclip.timeline_start),
                    f"clip is absent after exchange (was on {track.track_id} at "
                    f"{_tc(before, bclip.timeline_start)})",
                )
            )
            continue
        _atrack, aclip = a_clips[key]
        if (bclip.source_in, bclip.source_out) != (aclip.source_in, aclip.source_out):
            deltas.append(
                StructureDelta(
                    "range_changed", bclip.clip_id,
                    _tc(before, bclip.timeline_start),
                    "source range changed: "
                    f"[{bclip.source_in}, {bclip.source_out}) -> "
                    f"[{aclip.source_in}, {aclip.source_out})",
                )
            )
        if bclip.transition_in != aclip.transition_in:
            deltas.append(
                StructureDelta(
                    "transition_changed", bclip.clip_id,
                    _tc(before, bclip.timeline_start),
                    f"transition {bclip.transition_in!r} -> {aclip.transition_in!r}",
                )
            )
        if bclip.reverse != aclip.reverse:
            deltas.append(
                StructureDelta(
                    "range_changed", bclip.clip_id,
                    _tc(before, bclip.timeline_start),
                    f"reverse playback {bclip.reverse} -> {aclip.reverse}",
                )
            )
        if bclip.baked != aclip.baked:
            # Baked/flattened state decides what a later relink asks for:
            # one baked asset, or two editable source ranges.
            deltas.append(
                StructureDelta(
                    "cut_changed", bclip.clip_id,
                    _tc(before, bclip.timeline_start),
                    "flattened/baked state changed: the edit point is "
                    f"{'no longer' if aclip.baked else 'now'} editable",
                )
            )

    for key, (_track, aclip) in a_clips.items():
        if key not in b_clips:
            deltas.append(
                StructureDelta(
                    "clip_missing", aclip.clip_id, _tc(after, aclip.timeline_start),
                    "clip exists only after exchange",
                )
            )

    # Gaps are edit semantics, not merely absence of clips: a target with no
    # explicit gap construct keeps the record-time jump but loses the object.
    b_gaps = _gaps_by_position(before)
    a_gaps = _gaps_by_position(after)
    for key, (track, gap) in b_gaps.items():
        if key not in a_gaps:
            deltas.append(
                StructureDelta(
                    "gap_flattened", gap.gap_id,
                    _tc(before, gap.start),
                    f"explicit gap {gap.gap_id} on {track.track_id} no longer "
                    "exists as an object; only the record-time jump remains",
                )
            )
    for key, (_track, gap) in a_gaps.items():
        if key not in b_gaps:
            deltas.append(
                StructureDelta(
                    "gap_added", gap.gap_id, _tc(after, gap.start),
                    "gap exists only after exchange",
                )
            )
    return deltas


def flattened_vs_editable(original: Timeline, baked: Timeline) -> dict:
    """Report pixel equality alongside structural loss for a flattened copy."""
    report = compare(original, baked)
    return {
        "pixel_match": report.pixel_match,
        "edit_structure_faithful": report.edit_structure_faithful,
        "report": report.to_jsonable(),
    }
