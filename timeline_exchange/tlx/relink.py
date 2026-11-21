"""Media relinker: reconnect an edit's source references to real media.

The relinker operates on the internal model, not on display names.  For each
clip it consults two independent facts:

* whether the clip is already a *flattened* transition (``clip.baked``): the
  picture of two source ranges committed into one asset.  Such a clip must ask
  for the **baked media**, and can no longer expose the two original ranges.
* otherwise the edit point is still live.  A transition clip needs **both**
  source ranges reconnected (incoming and outgoing sides); a plain cut needs
  its single range.

A candidate is only accepted after its fingerprint (``media_ref`` +
``content_id``) matches.  A stand-in with the same duration but different
content is rejected and reported, so a relink cannot silently replace a shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from .model import Timeline, Clip, TRANSITION_CUT
from .timecode import format_timecode


@dataclass
class MediaCandidate:
    """A piece of media offered to the relinker."""

    path: str
    media_ref: Optional[str] = None
    content_id: Optional[str] = None
    rate: Optional[object] = None
    duration: Optional[Fraction] = None
    reel: Optional[str] = None
    timecode_origin: Fraction = Fraction(0)


@dataclass
class RangeRequest:
    clip_id: str
    role: str  # "single" | "incoming" | "outgoing" | "baked"
    source_id: str
    source_in: Fraction
    source_out: Fraction
    transition_in: str

    def to_jsonable(self, tl: Timeline) -> dict:
        src = tl.sources[self.source_id]
        return {
            "clip_id": self.clip_id,
            "role": self.role,
            "source_id": self.source_id,
            "media_ref": src.media_ref,
            "source_in": {
                "rational": str(self.source_in),
                "timecode": format_timecode(
                    self.source_in - src.timecode_origin, src.rate
                ),
            },
            "source_out": {
                "rational": str(self.source_out),
                "timecode": format_timecode(
                    self.source_out - src.timecode_origin, src.rate
                ),
            },
            "transition_in": self.transition_in,
        }


@dataclass
class RelinkDecision:
    clip_id: str
    request: str  # "baked_media" | "reconnect_two_ranges" | "reconnect_range"
    ranges: list[RangeRequest] = field(default_factory=list)
    reason: str = ""

    def to_jsonable(self, tl: Timeline) -> dict:
        return {
            "clip_id": self.clip_id,
            "request": self.request,
            "reason": self.reason,
            "ranges": [r.to_jsonable(tl) for r in self.ranges],
        }


@dataclass
class RelinkResult:
    decisions: list[RelinkDecision]
    accepted: list[dict]
    rejected: list[dict]
    missing_requests: list[str]

    def to_jsonable(self, tl: Timeline) -> dict:
        return {
            "decisions": [d.to_jsonable(tl) for d in self.decisions],
            "accepted": self.accepted,
            "rejected": self.rejected,
            "missing_requests": self.missing_requests,
        }


def _outgoing_neighbour(tl: Timeline, clip: Clip) -> Clip | None:
    """The clip immediately before ``clip`` on the same track, if it abuts."""
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


def plan(tl: Timeline) -> list[RelinkDecision]:
    """Decide what each clip needs.  No media is touched here."""
    decisions: list[RelinkDecision] = []
    for track in tl.tracks:
        for clip in sorted(track.clips, key=lambda c: c.timeline_start):
            if not clip.enabled:
                continue
            if clip.baked:
                decisions.append(
                    RelinkDecision(
                        clip_id=clip.clip_id,
                        request="baked_media",
                        ranges=[
                            RangeRequest(
                                clip.clip_id, "baked", clip.source_id,
                                clip.source_in, clip.source_out, clip.transition_in,
                            )
                        ],
                        reason=(
                            "clip carries a flattened transition; it must be "
                            "relinked against the baked asset, not two ranges"
                        ),
                    )
                )
                continue
            if clip.transition_in != TRANSITION_CUT and clip.transition_duration > 0:
                ranges = [
                    RangeRequest(
                        clip.clip_id, "incoming", clip.source_id,
                        clip.source_in, clip.source_out, clip.transition_in,
                    )
                ]
                prev = _outgoing_neighbour(tl, clip)
                if prev is not None:
                    ranges.insert(
                        0,
                        RangeRequest(
                            clip.clip_id, "outgoing", prev.source_id,
                            prev.source_in, prev.source_out, prev.transition_in,
                        ),
                    )
                decisions.append(
                    RelinkDecision(
                        clip_id=clip.clip_id,
                        request="reconnect_two_ranges",
                        ranges=ranges,
                        reason=(
                            "edit point has a clip: both the outgoing and "
                            "incoming source ranges must reconnect"
                        ),
                    )
                )
                continue
            decisions.append(
                RelinkDecision(
                    clip_id=clip.clip_id,
                    request="reconnect_range",
                    ranges=[
                        RangeRequest(
                            clip.clip_id, "single", clip.source_id,
                            clip.source_in, clip.source_out, clip.transition_in,
                        )
                    ],
                    reason="plain cut: reconnect the single source range",
                )
            )
    return decisions


def _candidate_matches(src, candidate: MediaCandidate) -> tuple[bool, str]:
    """A candidate must match the media fingerprint, never just its name.

    Media identity has three parts: the stable ``media_ref``, the pixel
    ``content_id``, and the source timecode origin.  Any mismatch means the
    candidate is different media even if its display name and duration match.
    """
    if src.media_ref is not None and candidate.media_ref != src.media_ref:
        return False, (
            f"media_ref mismatch: wanted {src.media_ref!r}, "
            f"candidate {candidate.media_ref!r}"
        )
    if src.content_id is not None and candidate.content_id != src.content_id:
        return False, (
            f"content_id mismatch (same-duration stand-in rejected): "
            f"wanted {src.content_id!r}, candidate {candidate.content_id!r}"
        )
    if candidate.timecode_origin != src.timecode_origin:
        return False, (
            "timecode_origin mismatch (same-name candidate rejected): wanted "
            f"{src.timecode_origin}, candidate {candidate.timecode_origin}"
        )
    return True, "identity matched: media_ref + content_id + timecode_origin"


def relink(tl: Timeline, candidates: list[MediaCandidate],
           accept_same_name_only: bool = False) -> RelinkResult:
    """Reconnect every clip's source references to a verified candidate.

    Returns the plan alongside accepted and rejected candidates.  ``tl`` is
    given candidate paths for accepted sources; nothing is done to a source
    whose candidate fails the fingerprint check.
    """
    decisions = plan(tl)
    accepted: list[dict] = []
    rejected: list[dict] = []
    resolved_sources: dict[str, str] = {}  # source_id -> candidate path

    for decision in decisions:
        for req in decision.ranges:
            src = tl.sources[req.source_id]
            if req.source_id in resolved_sources:
                # Two ranges of the same source share one media asset.
                accepted.append(
                    {
                        "clip_id": req.clip_id,
                        "role": req.role,
                        "source_id": req.source_id,
                        "request": decision.request,
                        "candidate_path": resolved_sources[req.source_id],
                        "media_ref": src.media_ref,
                        "content_id": src.content_id,
                        "reason": "same source already relinked",
                    }
                )
                continue
            match = None
            for cand in candidates:
                if accept_same_name_only:
                    ok = cand.reel is None or cand.reel == src.reel
                    reason = "display name match requested"
                else:
                    ok, reason = _candidate_matches(src, cand)
                if ok:
                    match = (cand, reason)
                    break
                rejected.append(
                    {
                        "clip_id": req.clip_id,
                        "role": req.role,
                        "source_id": req.source_id,
                        "candidate_path": cand.path,
                        "reason": reason,
                    }
                )
            if match is None:
                continue
            cand, reason = match
            resolved_sources[req.source_id] = cand.path
            src.path = cand.path
            src.exists = True
            if cand.media_ref is not None:
                src.media_ref = cand.media_ref
            if cand.content_id is not None:
                src.content_id = cand.content_id
            if cand.timecode_origin:
                src.timecode_origin = cand.timecode_origin
            accepted.append(
                {
                    "clip_id": req.clip_id,
                    "role": req.role,
                    "source_id": req.source_id,
                    "request": decision.request,
                    "candidate_path": cand.path,
                    "media_ref": src.media_ref,
                    "content_id": src.content_id,
                    "reason": reason,
                }
            )

    covered = {
        r.source_id for d in decisions for r in d.ranges
    }
    missing = sorted(
        sid for sid in covered if not tl.sources[sid].path
    )
    return RelinkResult(decisions, accepted, rejected, missing)


def serialise_candidates(candidates: list[MediaCandidate]) -> list[dict]:
    return [
        {
            "path": c.path,
            "media_ref": c.media_ref,
            "content_id": c.content_id,
            "reel": c.reel,
            "duration_rational": str(c.duration) if c.duration is not None else None,
            "timecode_origin_rational": str(c.timecode_origin),
        }
        for c in candidates
    ]


def candidates_from_jsonable(rows: list[dict]) -> list[MediaCandidate]:
    return [
        MediaCandidate(
            path=row["path"],
            media_ref=row.get("media_ref"),
            content_id=row.get("content_id"),
            reel=row.get("reel"),
            duration=(
                Fraction(row["duration_rational"])
                if row.get("duration_rational")
                else None
            ),
            timecode_origin=Fraction(row.get("timecode_origin_rational", "0")),
        )
        for row in rows
    ]
