"""TL-JSON: the rich, lossless interchange format (schema v1).

Time values may be written either as exact rational strings (``"1001/24000"``)
or as timecodes interpreted against the relevant frame rate.  Exporters always
emit both representations so a round trip never needs floating point.
"""

from __future__ import annotations

import json
from fractions import Fraction

from .timecode import FrameRate, parse_timecode, format_timecode
from .model import (
    Timeline,
    Track,
    Clip,
    Gap,
    Source,
    SUPPORTED_TRANSITIONS,
    TRANSITION_CUT,
)

FORMAT_NAME = "tl-json"
SCHEMA_VERSION = 1


class TLJSONError(ValueError):
    pass


def _fr(rate) -> FrameRate:
    return FrameRate.from_value(rate)


def _parse_time(value, rate: FrameRate, origin: Fraction) -> Fraction:
    """Accept an exact rational object/string or a timecode string."""
    if isinstance(value, dict):
        if "rational" in value and value["rational"] is not None:
            return Fraction(value["rational"])
        if "timecode" in value and value["timecode"] is not None:
            return origin + parse_timecode(value["timecode"], rate)
        raise TLJSONError(f"time object without rational/timecode: {value!r}")
    if isinstance(value, str):
        s = value.strip()
        if ":" in s:
            return origin + parse_timecode(s, rate)
        return Fraction(s)
    if isinstance(value, (int, float)):
        raise TLJSONError(
            f"bare numeric time {value!r} is forbidden; use a rational string "
            f"or timecode to keep the representation exact"
        )
    raise TLJSONError(f"cannot parse time value {value!r}")


def _parse_source_time(value, rate: FrameRate) -> Fraction:
    # Source times have no timeline origin.
    return _parse_time(value, rate, Fraction(0))


def import_text(text: str) -> Timeline:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TLJSONError("top-level TL-JSON document must be an object")
    if data.get("schema") != FORMAT_NAME or data.get("version") != SCHEMA_VERSION:
        raise TLJSONError("not a TL-JSON v1 document")

    master = _fr(data["rate"])
    origin = Fraction(0)
    if data.get("start_timecode"):
        origin = parse_timecode(data["start_timecode"], master)
    elif data.get("start_time"):
        origin = _parse_time(data["start_time"], master, Fraction(0))

    tl = Timeline(
        timeline_id=str(data.get("timeline_id", "timeline")),
        rate=master,
        start_time=origin,
        metadata=dict(data.get("metadata", {})),
    )

    for sid, sdata in data.get("sources", {}).items():
        rate = _fr(sdata["rate"])
        tl.sources[sid] = Source(
            source_id=sid,
            path=sdata.get("path"),
            rate=rate,
            duration=_parse_source_time(sdata["duration"], rate),
            exists=bool(sdata.get("exists", True)),
            reel=sdata.get("reel"),
        )

    for tdata in data.get("tracks", []):
        kind = tdata.get("kind", "video")
        if kind not in ("video", "audio"):
            raise TLJSONError(f"track {tdata.get('track_id')} bad kind {kind!r}")
        track = Track(
            track_id=str(tdata["track_id"]),
            kind=kind,
            index=int(tdata.get("index", 0)),
        )
        for cdata in tdata.get("clips", []):
            src_id = cdata["source_id"]
            if src_id not in tl.sources:
                raise TLJSONError(
                    f"clip {cdata.get('clip_id')} references unknown source {src_id!r}"
                )
            src_rate = tl.sources[src_id].rate
            trans = cdata.get("transition_in", TRANSITION_CUT)
            if trans not in SUPPORTED_TRANSITIONS:
                raise TLJSONError(
                    f"clip {cdata.get('clip_id')} unknown transition {trans!r}"
                )
            tdur = _parse_time(
                cdata.get("transition_duration", "0"), master, origin
            )
            clip = Clip(
                clip_id=str(cdata["clip_id"]),
                source_id=src_id,
                source_in=_parse_source_time(cdata["source_in"], src_rate),
                source_out=_parse_source_time(cdata["source_out"], src_rate),
                timeline_start=_parse_time(
                    cdata["timeline_start"], master, origin
                ),
                duration=_parse_time(cdata["duration"], master, origin),
                transition_in=trans,
                transition_duration=tdur,
                enabled=bool(cdata.get("enabled", True)),
            )
            clip.validate()
            track.clips.append(clip)
        for gdata in tdata.get("gaps", []):
            track.gaps.append(
                Gap(
                    gap_id=str(gdata["gap_id"]),
                    start=_parse_time(gdata["start"], master, origin),
                    duration=_parse_time(gdata["duration"], master, origin),
                )
            )
        tl.tracks.append(track)

    tl.validate()
    return tl


def _time_obj(time: Fraction, rate: FrameRate, origin: Fraction) -> dict:
    return {
        "rational": str(time),
        "timecode": format_timecode(time - origin, rate),
    }


def _source_time_obj(time: Fraction, rate: FrameRate) -> dict:
    return {
        "rational": str(time),
        "timecode": format_timecode(time, rate),
    }


def export_text(tl: Timeline) -> str:
    origin = tl.start_time
    doc = {
        "schema": FORMAT_NAME,
        "version": SCHEMA_VERSION,
        "timeline_id": tl.timeline_id,
        "rate": f"{tl.rate.numerator}/{tl.rate.denominator}",
        "start_timecode": format_timecode(origin, tl.rate),
        "start_time": _time_obj(origin, tl.rate, Fraction(0)),
        "metadata": tl.metadata,
        "sources": {},
        "tracks": [],
    }
    for sid, src in tl.sources.items():
        doc["sources"][sid] = {
            "path": src.path,
            "reel": src.reel,
            "rate": f"{src.rate.numerator}/{src.rate.denominator}",
            "duration": _source_time_obj(src.duration, src.rate),
            "exists": src.exists,
        }
    for track in tl.tracks:
        tdoc = {
            "track_id": track.track_id,
            "kind": track.kind,
            "index": track.index,
            "clips": [],
            "gaps": [],
        }
        for clip in track.clips:
            src = tl.sources[clip.source_id]
            tdoc["clips"].append(
                {
                    "clip_id": clip.clip_id,
                    "source_id": clip.source_id,
                    "source_in": _source_time_obj(clip.source_in, src.rate),
                    "source_out": _source_time_obj(clip.source_out, src.rate),
                    "timeline_start": _time_obj(
                        clip.timeline_start, tl.rate, origin
                    ),
                    "duration": _time_obj(clip.duration, tl.rate, origin),
                    "transition_in": clip.transition_in,
                    "transition_duration": _time_obj(
                        clip.transition_duration, tl.rate, origin
                    ),
                    "enabled": clip.enabled,
                }
            )
        for gap in track.gaps:
            tdoc["gaps"].append(
                {
                    "gap_id": gap.gap_id,
                    "start": _time_obj(gap.start, tl.rate, origin),
                    "duration": _time_obj(gap.duration, tl.rate, origin),
                }
            )
        doc["tracks"].append(tdoc)
    return json.dumps(doc, indent=2, ensure_ascii=False)
