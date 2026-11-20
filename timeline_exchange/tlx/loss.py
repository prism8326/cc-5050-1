"""Loss reports for lossy interchange.

Nothing is ever silently dropped: whenever an exporter cannot express part of
the timeline it appends a :class:`LossEntry` carrying the exact timeline
position, the object id, a code and a human-readable reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from fractions import Fraction
from typing import Optional

from .timecode import FrameRate, format_timecode


@dataclass
class LossEntry:
    code: str
    severity: str  # "error" blocks faithfulness, "warning" downgrades
    kind: str  # clip | gap | track | transition | source
    object_id: str
    timeline_start: Optional[Fraction]
    timeline_end: Optional[Fraction]
    message: str

    def to_jsonable(self, rate: FrameRate) -> dict:
        d = asdict(self)
        for key in ("timeline_start", "timeline_end"):
            v = d[key]
            d[key] = {
                "rational": str(v) if v is not None else None,
                "timecode": format_timecode(v, rate) if v is not None else None,
            }
        return d


@dataclass
class LossReport:
    target_format: str
    entries: list[LossEntry] = field(default_factory=list)

    def add(self, entry: LossEntry) -> None:
        self.entries.append(entry)

    @property
    def has_errors(self) -> bool:
        return any(e.severity == "error" for e in self.entries)

    def to_jsonable(self, rate: FrameRate) -> dict:
        return {
            "target_format": self.target_format,
            "entry_count": len(self.entries),
            "has_errors": self.has_errors,
            "entries": [e.to_jsonable(rate) for e in self.entries],
        }
