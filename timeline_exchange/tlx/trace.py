"""Edit-point resolution against source references.

The exchange window lets an author click any output frame.  Given that record
time, this module reports which source range(s) back it and what to ask the
relinker for.  Two situations must be kept apart:

* a **live** edit point (cut, dissolve, wipe, ...) still has an editable cut:
  both source ranges are addressable and must be reconnected by the relinker.
* a **flattened** transition (``clip.baked``) has no editable cut any more: the
  picture is one asset, so the resolver asks for the baked media and says the
  two source ranges are gone.

Reverse clips and mixed-rate clips are resolved on their own clock, so the
answer is an exact source time and frame even for a cross-rate cut.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from .model import Timeline, Clip, TRANSITION_CUT
from .timecode import format_timecode, frame_index_at
from . import preview


@dataclass
class ResolvedSource:
    role: str  # "single" | "outgoing" | "incoming" | "baked"
    clip_id: str
    source_id: str
    media_ref: Optional[str]
    source_time: Fraction
    source_frame_index: int
    source_in: Fraction
    source_out: Fraction
    rate: str
    reverse: bool
    baked: bool

    def to_jsonable(self, tl: Timeline) -> dict:
        src = tl.sources[self.source_id]
        return {
            "role": self.role,
            "clip_id": self.clip_id,
            "source_id": self.source_id,
            "media_ref": self.media_ref,
            "source_time_rational": str(self.source_time),
            "source_timecode": format_timecode(
                self.source_time - src.timecode_origin, src.rate
            ),
            "source_frame_index": self.source_frame_index,
            "source_in_rational": str(self.source_in),
            "source_out_rational": str(self.source_out),
            "source_in_timecode": format_timecode(
                self.source_in - src.timecode_origin, src.rate
            ),
            "source_out_timecode": format_timecode(
                self.source_out - src.timecode_origin, src.rate
            ),
            "rate": self.rate,
            "reverse": self.reverse,
            "baked": self.baked,
        }


@dataclass
class EditPointResolution:
    record_time: Fraction
    record_timecode: str
    kind: str  # "frame" | "transition_in" | "baked" | "gap" | "black" | "missing"
    clip_id: Optional[str]
    transition_in: Optional[str]
    transition_progress: Optional[Fraction]
    needs: str  # "reconnect_one_range" | "reconnect_two_ranges" | "baked_media" | "none"
    sources: list[ResolvedSource]
    note: str

    def to_jsonable(self, tl: Timeline) -> dict:
        return {
            "record_time_rational": str(self.record_time),
            "record_timecode": self.record_timecode,
            "kind": self.kind,
            "clip_id": self.clip_id,
            "transition_in": self.transition_in,
            "transition_progress_rational": (
                str(self.transition_progress)
                if self.transition_progress is not None
                else None
            ),
            "needs": self.needs,
            "sources": [s.to_jsonable(tl) for s in self.sources],
            "note": self.note,
        }


def _resolved_from_clip(tl: Timeline, clip: Clip, role: str,
                        source_time: Fraction) -> ResolvedSource:
    src = tl.sources[clip.source_id]
    return ResolvedSource(
        role=role,
        clip_id=clip.clip_id,
        source_id=clip.source_id,
        media_ref=src.media_ref,
        source_time=source_time,
        source_frame_index=frame_index_at(source_time, src.rate),
        source_in=clip.source_in,
        source_out=clip.source_out,
        rate=str(src.rate),
        reverse=clip.reverse,
        baked=clip.baked,
    )


def resolve(tl: Timeline, time: Fraction) -> EditPointResolution:
    sel = preview.select_frame(tl, time)
    tc = format_timecode(time - tl.start_time, tl.rate)
    if sel.kind in ("gap", "black", "missing", "frame") and sel.clip_id is None:
        return EditPointResolution(
            record_time=time, record_timecode=tc, kind=sel.kind,
            clip_id=None, transition_in=None, transition_progress=None,
            needs="none", sources=[], note=sel.detail,
        )

    clip = next(
        c for t in tl.tracks for c in t.clips if c.clip_id == sel.clip_id
    )

    if clip.baked:
        # One baked asset backs the frame; the two original ranges are gone.
        res = _resolved_from_clip(tl, clip, "baked", sel.source_time)
        return EditPointResolution(
            record_time=time, record_timecode=tc, kind="baked",
            clip_id=clip.clip_id, transition_in=clip.transition_in,
            transition_progress=sel.transition_progress,
            needs="baked_media", sources=[res],
            note=(
                "flattened transition: request the baked media; the outgoing and "
                "incoming source ranges are no longer separately addressable"
            ),
        )

    in_transition = (
        clip.transition_in != TRANSITION_CUT
        and clip.transition_duration > 0
        and (time - clip.timeline_start) < clip.transition_duration
    )
    if in_transition:
        sources = []
        prev = _abutting_previous(tl, clip)
        if prev is not None:
            # Outgoing side: advance its source on its own clock to this frame.
            src = tl.sources[prev.source_id]
            outgoing_time = prev.source_in + (time - prev.timeline_start)
            if prev.reverse:
                outgoing_time = prev.source_out - (time - prev.timeline_start)
            sources.append(_resolved_from_clip(tl, prev, "outgoing", outgoing_time))
        sources.append(
            _resolved_from_clip(tl, clip, "incoming", sel.source_time)
        )
        if prev is None:
            # The outgoing side is a gap/black, not another clip: only the
            # incoming range is addressable, even though the transition is live.
            return EditPointResolution(
                record_time=time, record_timecode=tc, kind="transition_in",
                clip_id=clip.clip_id, transition_in=clip.transition_in,
                transition_progress=sel.transition_progress,
                needs="reconnect_one_range", sources=sources,
                note=(
                    f"live {clip.transition_in} over an empty outgoing side; "
                    "only the incoming source range is addressable"
                ),
            )
        return EditPointResolution(
            record_time=time, record_timecode=tc, kind="transition_in",
            clip_id=clip.clip_id, transition_in=clip.transition_in,
            transition_progress=sel.transition_progress,
            needs="reconnect_two_ranges", sources=sources,
            note=(
                f"live {clip.transition_in}: edit point is still cuttable; "
                "relink both the outgoing and incoming source ranges"
            ),
        )

    res = _resolved_from_clip(tl, clip, "single", sel.source_time)
    return EditPointResolution(
        record_time=time, record_timecode=tc, kind="frame",
        clip_id=clip.clip_id, transition_in=None, transition_progress=None,
        needs="reconnect_one_range", sources=[res],
        note="plain cut: a single source range backs this frame",
    )


def _abutting_previous(tl: Timeline, clip: Clip) -> Optional[Clip]:
    for track in tl.tracks:
        if clip not in track.clips:
            continue
        ordered = sorted(track.clips, key=lambda c: c.timeline_start)
        idx = ordered.index(clip)
        if idx == 0:
            return None
        prev = ordered[idx - 1]
        if prev.timeline_end == clip.timeline_start:
            return prev
    return None
