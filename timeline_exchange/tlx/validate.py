"""Source-reference provenance validator.

A timeline may reference media that plays perfectly yet belongs to a different
arsenal: same display name, same duration, different source timecode.  A
name-based lookup would accept it because the file opens.  This validator
therefore resolves every source reference the way the media catalog defines it
— by ``media_ref`` — and only then checks the content fingerprint and the media
timecode origin.

The output separates two axes that must never be conflated:

* ``pixel_match`` — would the pixels look the same?  A same-duration stand-in
  can still be pixel-identical, so this alone is not enough to accept an edit.
* ``edit_structure_faithful`` — are the source references, source ranges and
  timecode origins the same edit?  This is the one that decides acceptance.

References that cannot be resolved by ``media_ref`` are returned as
``needs_human`` with the exact clip and record position, so an operator fixes
the cut rather than letting a name collision pick another project's clip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from .model import Timeline, Clip
from .media import MediaCatalog, MediaAsset
from .timecode import format_timecode, frame_index_at


@dataclass
class ReferenceIssue:
    clip_id: str
    source_id: str
    media_ref: Optional[str]
    timeline_start: Fraction
    kind: str  # missing_ref | unresolved_ref | content_mismatch | timecode_mismatch | name_collision
    needs_human: bool
    message: str
    name_only_candidate: Optional[str] = None
    pixel_match: Optional[bool] = None

    def to_jsonable(self, tl: Timeline) -> dict:
        return {
            "clip_id": self.clip_id,
            "source_id": self.source_id,
            "media_ref": self.media_ref,
            "record_timecode": format_timecode(
                self.timeline_start - tl.start_time, tl.rate
            ),
            "kind": self.kind,
            "needs_human": self.needs_human,
            "message": self.message,
            "name_only_candidate": self.name_only_candidate,
            "pixel_match": self.pixel_match,
        }


@dataclass
class ValidationResult:
    issues: list[ReferenceIssue]
    resolved: list[dict]

    @property
    def needs_human(self) -> list[ReferenceIssue]:
        return [i for i in self.issues if i.needs_human]

    @property
    def ok(self) -> bool:
        return not self.needs_human

    def to_jsonable(self, tl: Timeline) -> dict:
        return {
            "ok": self.ok,
            "needs_human_count": len(self.needs_human),
            "resolved": self.resolved,
            "issues": [i.to_jsonable(tl) for i in self.issues],
            "needs_human": [i.to_jsonable(tl) for i in self.needs_human],
        }


def _clips(tl: Timeline):
    for track in tl.tracks:
        for clip in track.clips:
            yield track, clip


def validate(tl: Timeline, catalog: MediaCatalog) -> ValidationResult:
    issues: list[ReferenceIssue] = []
    resolved: list[dict] = []

    for track, clip in _clips(tl):
        src = tl.sources[clip.source_id]

        if not src.media_ref:
            issues.append(
                ReferenceIssue(
                    clip_id=clip.clip_id,
                    source_id=clip.source_id,
                    media_ref=None,
                    timeline_start=clip.timeline_start,
                    kind="missing_ref",
                    needs_human=True,
                    message=(
                        "source has no stable media_ref; a display name cannot "
                        "be used to pick media safely"
                    ),
                )
            )
            continue

        asset: Optional[MediaAsset] = catalog.by_ref(src.media_ref)
        if asset is None:
            # A name match here is exactly the trap: it may be another
            # project's media that happens to play.
            name_hits = catalog.by_name(src.reel or src.source_id)
            issues.append(
                ReferenceIssue(
                    clip_id=clip.clip_id,
                    source_id=clip.source_id,
                    media_ref=src.media_ref,
                    timeline_start=clip.timeline_start,
                    kind="unresolved_ref",
                    needs_human=True,
                    message=(
                        f"media_ref {src.media_ref!r} is not in the catalog; "
                        f"{len(name_hits)} asset(s) share the display name but "
                        "must not be substituted"
                    ),
                    name_only_candidate=name_hits[0].path if name_hits else None,
                )
            )
            continue

        content_ok = src.content_id is None or src.content_id == asset.content_id
        origin_ok = src.timecode_origin == asset.timecode_origin

        if not content_ok:
            issues.append(
                ReferenceIssue(
                    clip_id=clip.clip_id,
                    source_id=clip.source_id,
                    media_ref=src.media_ref,
                    timeline_start=clip.timeline_start,
                    kind="content_mismatch",
                    needs_human=True,
                    message=(
                        f"content fingerprint differs: timeline {src.content_id!r} "
                        f"vs media {asset.content_id!r}"
                    ),
                    pixel_match=False,
                )
            )
        if not origin_ok:
            issues.append(
                ReferenceIssue(
                    clip_id=clip.clip_id,
                    source_id=clip.source_id,
                    media_ref=src.media_ref,
                    timeline_start=clip.timeline_start,
                    kind="timecode_mismatch",
                    needs_human=True,
                    message=(
                        "media source timecode origin differs: timeline "
                        f"{format_timecode(src.timecode_origin, src.rate)} vs media "
                        f"{asset.timecode_origin_label()}; source range now points "
                        "at different content"
                    ),
                    pixel_match=content_ok,
                )
            )
        if content_ok and origin_ok:
            resolved.append(
                {
                    "clip_id": clip.clip_id,
                    "track_id": track.track_id,
                    "source_id": clip.source_id,
                    "media_ref": src.media_ref,
                    "content_id": asset.content_id,
                    "timecode_origin": asset.timecode_origin_label(),
                }
            )
    return ValidationResult(issues, resolved)


def first_source_frame(tl: Timeline, clip: Clip) -> int:
    """Frame index the clip's first record frame selects, for diagnostics."""
    src = tl.sources[clip.source_id]
    return frame_index_at(clip.source_in, src.rate)
