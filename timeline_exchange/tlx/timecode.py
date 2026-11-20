"""Exact rational time and timecode.

All times in the system are :class:`fractions.Fraction` seconds.  No float
arithmetic is ever used on time values, so a feature-length timeline cannot
accumulate rounding drift.
"""

from __future__ import annotations

from fractions import Fraction
from dataclasses import dataclass
import re

_TC_RE = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)[:;](\d+)$")


class TimecodeError(ValueError):
    pass


@dataclass(frozen=True)
class FrameRate:
    """A frame rate as an exact rational, e.g. 24000/1001, 25, 30000/1001."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("frame rate must be positive")
        # frozen dataclass + object.__setattr__ trick not needed: store Fraction
        object.__setattr__(self, "_rate", Fraction(self.numerator, self.denominator))

    @property
    def rate(self) -> Fraction:
        return self._rate  # type: ignore[attr-defined]

    @property
    def frame_duration(self) -> Fraction:
        return Fraction(1, 1) / self._rate  # type: ignore[attr-defined]

    @property
    def nominal_fps(self) -> int:
        """Integer frame count per second used by timecode labels (24, 25, 30)."""
        return int(self._rate + Fraction(1, 2))  # type: ignore[attr-defined]

    @classmethod
    def from_value(cls, value) -> "FrameRate":
        if isinstance(value, FrameRate):
            return value
        if isinstance(value, Fraction):
            return cls(value.numerator, value.denominator)
        if isinstance(value, float):
            # Only accept exact-value floats by routing through Fraction of repr.
            f = Fraction(value)
            return cls(f.numerator, f.denominator)
        if isinstance(value, int):
            return cls(value, 1)
        if isinstance(value, str):
            if "/" in value:
                n, d = value.split("/")
                return cls(int(n), int(d))
            return cls(int(value), 1)
        raise TypeError(f"cannot build FrameRate from {type(value)!r}")

    def __str__(self) -> str:
        if self.denominator == 1:
            return f"{self.numerator}fps"
        return f"{self.numerator}/{self.denominator}fps"


def frame_time(index: int, rate: FrameRate) -> Fraction:
    """Exact start time of frame ``index`` on a timeline running at ``rate``."""
    return Fraction(index, 1) / rate.rate


def time_at_frame(index: int, rate: FrameRate) -> Fraction:
    return frame_time(index, rate)


def frame_index_at(time: Fraction, rate: FrameRate) -> int:
    """Index of the frame whose display interval contains ``time``.

    Frame i covers the half-open interval [i/r, (i+1)/r).  The selection is an
    exact integer floor; mixed-rate sources use their own rate here.  All
    timeline times are non-negative, so plain floor division is exact.
    """
    scaled = time * rate.rate
    return scaled.numerator // scaled.denominator


def parse_timecode(tc: str, rate: FrameRate) -> Fraction:
    """Parse ``HH:MM:SS:FF`` (or ``;`` delimiter) into exact seconds."""
    m = _TC_RE.match(tc.strip())
    if not m:
        raise TimecodeError(f"malformed timecode {tc!r}")
    h, mnt, s, f = (int(g) for g in m.groups())
    if f >= rate.nominal_fps:
        raise TimecodeError(
            f"frame {f} out of range for {rate} (nominal {rate.nominal_fps}) in {tc!r}"
        )
    total_frames = ((h * 60 + mnt) * 60 + s) * rate.nominal_fps + f
    return Fraction(total_frames, 1) / rate.rate


def format_timecode(time: Fraction, rate: FrameRate) -> str:
    """Format an exact time as ``HH:MM:SS:FF`` using frame selection semantics."""
    idx = frame_index_at(time, rate)
    nominal = rate.nominal_fps
    h, rem = divmod(idx, nominal * 3600)
    mnt, rem = divmod(rem, nominal * 60)
    s, f = divmod(rem, nominal)
    return f"{h:02d}:{mnt:02d}:{s:02d}:{f:02d}"


def parse_timecode_relative(tc: str, rate: FrameRate, origin: Fraction) -> Fraction:
    """Parse a timecode that is labelled on a timeline with non-zero origin."""
    return origin + parse_timecode(tc, rate)
