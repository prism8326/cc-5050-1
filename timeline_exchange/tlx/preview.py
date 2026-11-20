"""Media probing and exact frame selection.

The preview never guesses content.  A source either exists on disk (and frames
are rendered from the real media bundle) or it is flagged missing, in which
case the renderer draws a diagnostic slate naming the exact broken reference.
"""

from __future__ import annotations

import os
from fractions import Fraction
from dataclasses import dataclass

from .timecode import frame_index_at, frame_time, format_timecode
from .model import Timeline, Clip, Gap, TRANSITION_CUT


class MediaError(RuntimeError):
    pass


@dataclass
class FrameSelection:
    """Exactly what the timeline shows at one record time."""

    timeline_time: Fraction
    kind: str  # "frame" | "gap" | "missing" | "black"
    clip_id: str | None
    source_id: str | None
    source_time: Fraction | None
    source_frame_index: int | None
    transition_in: str | None
    transition_progress: Fraction | None  # exact 0..1 during a transition
    detail: str = ""


def probe_sources(tl: Timeline, base_dir: str | None = None) -> list[str]:
    """Mark every source missing/existing on disk. Returns missing source ids."""
    missing = []
    for src in tl.sources.values():
        if not src.path:
            src.exists = False
            missing.append(src.source_id)
            continue
        path = src.path
        if base_dir and not os.path.isabs(path):
            path = os.path.join(base_dir, path)
        src.exists = os.path.exists(path)
        if not src.exists:
            missing.append(src.source_id)
    return missing


def _covering_clip(tl: Timeline, time: Fraction) -> tuple[Clip | None, Gap | None]:
    # Highest video track wins when tracks overlap.
    for track in sorted(tl.video_tracks(), key=lambda t: -t.index):
        for clip in track.clips:
            if clip.enabled and clip.timeline_start <= time < clip.timeline_end:
                return clip, None
        for gap in track.gaps:
            if gap.start <= time < gap.end:
                return None, gap
    return None, None


def select_frame(tl: Timeline, time: Fraction) -> FrameSelection:
    """Resolve the exact source frame selected at record time ``time``."""
    clip, gap = _covering_clip(tl, time)
    if gap is not None:
        return FrameSelection(
            timeline_time=time,
            kind="gap",
            clip_id=None,
            source_id=None,
            source_time=None,
            source_frame_index=None,
            transition_in=None,
            transition_progress=None,
            detail=f"explicit gap {gap.gap_id}",
        )
    if clip is None:
        return FrameSelection(
            timeline_time=time,
            kind="black",
            clip_id=None,
            source_id=None,
            source_time=None,
            source_frame_index=None,
            transition_in=None,
            transition_progress=None,
            detail="no item covers this record time",
        )

    src = tl.sources[clip.source_id]
    # Exact source time: clip offset plus (record time minus clip start).
    source_time = clip.source_in + (time - clip.timeline_start)
    idx = frame_index_at(source_time, src.rate)

    progress = None
    trans_in = None
    if clip.transition_in != TRANSITION_CUT and clip.transition_duration > 0:
        into = time - clip.timeline_start
        if into < clip.transition_duration:
            trans_in = clip.transition_in
            progress = into / clip.transition_duration

    if not src.exists:
        return FrameSelection(
            timeline_time=time,
            kind="missing",
            clip_id=clip.clip_id,
            source_id=clip.source_id,
            source_time=source_time,
            source_frame_index=idx,
            transition_in=trans_in,
            transition_progress=progress,
            detail=(
                f"missing media reference {clip.source_id!r} "
                f"path={src.path!r} wanted source frame {idx}"
            ),
        )

    return FrameSelection(
        timeline_time=time,
        kind="frame",
        clip_id=clip.clip_id,
        source_id=clip.source_id,
        source_time=source_time,
        source_frame_index=idx,
        transition_in=trans_in,
        transition_progress=progress,
    )


def timeline_frame_times(tl: Timeline) -> list[Fraction]:
    """One exact record time per record frame across the whole timeline."""
    rate = tl.rate
    count = frame_index_at(tl.duration - tl.start_time, rate)
    return [tl.start_time + frame_time(i, rate) for i in range(count)]


def describe_selection(sel: FrameSelection, tl: Timeline) -> dict:
    """JSON-able description including exact time and display timecodes."""
    rate = tl.rate
    out = {
        "timeline_time_rational": str(sel.timeline_time),
        "record_timecode": format_timecode(
            sel.timeline_time - tl.start_time, rate
        ),
        "kind": sel.kind,
        "clip_id": sel.clip_id,
        "source_id": sel.source_id,
        "detail": sel.detail,
    }
    if sel.source_time is not None and sel.source_id is not None:
        src_rate = tl.sources[sel.source_id].rate
        out["source_time_rational"] = str(sel.source_time)
        out["source_timecode"] = format_timecode(sel.source_time, src_rate)
        out["source_frame_index"] = sel.source_frame_index
        out["source_rate"] = str(src_rate)
    if sel.transition_in:
        out["transition_in"] = sel.transition_in
        out["transition_progress_rational"] = str(sel.transition_progress)
    return out
