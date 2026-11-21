"""Internal timeline model shared by every importer and exporter.

The model is deliberately format-neutral.  Time fields are
:class:`fractions.Fraction` seconds so conversion never routes through float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from .timecode import FrameRate

#: Transitions the internal model can represent.
TRANSITION_CUT = "cut"
TRANSITION_DISSOLVE = "dissolve"
TRANSITION_FADE = "fade"
TRANSITION_WIPE = "wipe"
TRANSITION_PUSH = "push"
TRANSITION_SLIDE = "slide"

SUPPORTED_TRANSITIONS = frozenset(
    {
        TRANSITION_CUT,
        TRANSITION_DISSOLVE,
        TRANSITION_FADE,
        TRANSITION_WIPE,
        TRANSITION_PUSH,
        TRANSITION_SLIDE,
    }
)

#: Transitions expressible in a CMX 3600 EDL (cuts, dissolves, key/fade WIPEs).
CMX_EXPRESSIBLE_TRANSITIONS = frozenset(
    {TRANSITION_CUT, TRANSITION_DISSOLVE, TRANSITION_FADE, TRANSITION_WIPE}
)

#: Transition downgrades when the target cannot express it.  The value is the
#: semantics that replaces it, so a narrower target is a *decided* mapping.
CMX_TRANSITION_FALLBACK = {
    TRANSITION_PUSH: TRANSITION_CUT,
    TRANSITION_SLIDE: TRANSITION_CUT,
}


@dataclass
class Source:
    """A piece of source media.

    ``media_ref`` is the stable identity a relinker must follow.  A display
    name (``source_id`` / ``reel``) is *not* enough: two projects may reference
    different media under the same name.  ``content_id`` is a media fingerprint
    used to catch a same-duration stand-in, and ``timecode_origin`` records the
    timecode label of the media's first frame.
    """

    source_id: str
    path: Optional[str]
    rate: FrameRate
    duration: Fraction  # exact duration of the media
    exists: bool = True  # set False after a real media probe fails
    reel: Optional[str] = None  # CMX reel name, assigned at export if needed
    media_ref: Optional[str] = None  # stable cross-project media identity
    content_id: Optional[str] = None  # media fingerprint, not a display name
    timecode_origin: Fraction = Fraction(0)  # timecode of the first frame


@dataclass
class Gap:
    """An explicit empty interval on a track (never silently collapsed)."""

    gap_id: str
    start: Fraction
    duration: Fraction

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass
class Clip:
    clip_id: str
    source_id: str
    source_in: Fraction  # exact source time of the first selected frame
    source_out: Fraction  # exact source time one frame past the last selected frame
    timeline_start: Fraction
    duration: Fraction
    transition_in: str = TRANSITION_CUT
    transition_duration: Fraction = Fraction(0)
    enabled: bool = True
    reverse: bool = False  # play the source range backwards
    # True when this clip already carries a flattened (baked) transition and
    # therefore must be relinked against baked media, not the two source ranges.
    baked: bool = False

    @property
    def timeline_end(self) -> Fraction:
        return self.timeline_start + self.duration

    @property
    def source_out_last_frame(self) -> Fraction:
        return self.source_out

    def validate(self) -> None:
        if self.transition_in not in SUPPORTED_TRANSITIONS:
            raise ValueError(f"unsupported transition {self.transition_in!r}")
        if self.duration <= 0:
            raise ValueError(f"clip {self.clip_id} has non-positive duration")
        if self.source_out <= self.source_in:
            raise ValueError(f"clip {self.clip_id} source range is empty")
        if self.transition_duration < 0:
            raise ValueError(f"clip {self.clip_id} negative transition duration")
        if (
            self.transition_in == TRANSITION_CUT
            and self.transition_duration != 0
        ):
            raise ValueError(f"clip {self.clip_id} cut cannot carry a duration")
        if (
            self.transition_in != TRANSITION_CUT
            and self.transition_duration <= 0
        ):
            raise ValueError(
                f"clip {self.clip_id} {self.transition_in} needs a duration"
            )


@dataclass
class Track:
    track_id: str
    kind: str  # "video" or "audio"
    index: int
    clips: list[Clip] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)

    def sorted_items(self):
        items = [(c.timeline_start, 0, c) for c in self.clips]
        items += [(g.start, 1, g) for g in self.gaps]
        return [item for _, _, item in sorted(items, key=lambda t: (t[0], t[1]))]


@dataclass
class Timeline:
    timeline_id: str
    rate: FrameRate  # record/master rate
    start_time: Fraction = Fraction(0)  # non-zero start timecode support
    tracks: list[Track] = field(default_factory=list)
    sources: dict[str, Source] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    @property
    def duration(self) -> Fraction:
        ends = [Fraction(0)]
        for t in self.tracks:
            ends.extend(c.timeline_end for c in t.clips)
            ends.extend(g.end for g in t.gaps)
        return max(ends)

    def video_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "video"]

    def validate(self) -> None:
        for track in self.tracks:
            for clip in track.clips:
                clip.validate()
                if clip.source_id not in self.sources:
                    raise ValueError(
                        f"clip {clip.clip_id} references unknown source "
                        f"{clip.source_id!r}"
                    )
