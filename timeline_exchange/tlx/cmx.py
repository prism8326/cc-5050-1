"""CMX 3600 EDL importer/exporter.

The EDL is single video track (V), linear, with record timecodes.  Only ``C``
(cut), ``D`` (dissolve) and ``W`` (wipe) events are expressible; anything else
in the internal model becomes an entry in the :class:`LossReport` instead of
being silently removed.

Record timecodes in an EDL are labels on a timeline whose first frame is 01:00:00:00
by convention; the title form ``FCM: NON-DROP FRAME`` is required because drop
frame cannot be represented exactly in this tool.
"""

from __future__ import annotations

import re
from fractions import Fraction

from .timecode import FrameRate, parse_timecode, format_timecode
from .model import (
    Timeline,
    Track,
    Clip,
    Source,
    Gap,
    TRANSITION_CUT,
    TRANSITION_DISSOLVE,
    TRANSITION_WIPE,
    TRANSITION_FADE,
    CMX_EXPRESSIBLE_TRANSITIONS,
)
from .loss import LossReport, LossEntry
from .capabilities import fallback_transition

FORMAT_NAME = "cmx3600"
DEFAULT_ORIGIN_HOUR = 1  # 01:00:00:00 record origin convention
_TC = r"(\d{2}):(\d{2}):(\d{2}):(\d{2})"
_EVENT_RE = re.compile(
    rf"^(\d{{3,4}})\s+(\S+)\s+([VA])\s+(\w+)\s+(?:(\d+)\s+)?{_TC}\s+{_TC}\s+{_TC}\s+{_TC}\s*$"
)
_TITLE_RE = re.compile(r"^\*?\s*TITLE:\s*(.*)$", re.IGNORECASE)


class EDLParseError(ValueError):
    pass


def _record_origin(rate: FrameRate, start_hour: int) -> Fraction:
    return parse_timecode(f"{start_hour:02d}:00:00:00", rate)


def _read_event(line: str):
    m = _EVENT_RE.match(line)
    if not m:
        raise EDLParseError(f"unparseable EDL event: {line.rstrip()!r}")
    g = m.groups()
    event = int(g[0])
    reel = g[1]
    track = g[2]
    trans = g[3]
    aux = g[4]
    sin, sout, rin, rout = [
        ":".join(part)
        for part in (g[5:9], g[9:13], g[13:17], g[17:21])
    ]
    return {
        "event": event,
        "reel": reel,
        "track": track,
        "trans": trans,
        "aux": aux,
        "source_in": sin,
        "source_out": sout,
        "record_in": rin,
        "record_out": rout,
    }


def import_text(text: str, rate: FrameRate, sources: dict[str, Source] | None = None,
                start_hour: int = DEFAULT_ORIGIN_HOUR) -> Timeline:
    """Parse a CMX 3600 EDL.

    ``sources`` maps reel names to :class:`Source` objects so mixed frame rates
    can be resolved.  Without it, sources are synthesized at the record rate.
    """
    sources = dict(sources or {})
    origin = _record_origin(rate, start_hour)
    tl = Timeline(
        timeline_id="edl_import",
        rate=rate,
        start_time=origin,
    )
    track = Track(track_id="V1", kind="video", index=0)
    tl.tracks.append(track)

    seen_fcm = False
    events = []
    title = "EDL import"
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.startswith("*") and not _TITLE_RE.match(line):
            if line.upper().startswith("* TITLE"):
                t = _TITLE_RE.match(line)
                if t:
                    title = t.group(1).strip()
            continue
        upper = line.upper()
        if upper.startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
            continue
        if upper.startswith("FCM:"):
            seen_fcm = True
            if "DROP" in upper and "NON-DROP" not in upper:
                raise EDLParseError("drop-frame EDLs are not supported exactly")
            continue
        if line[0].isdigit():
            events.append(_read_event(line))

    if not seen_fcm:
        # Tolerate but remember: non-drop frame is the only exact mode.
        pass

    tl.metadata["title"] = title
    cursor = origin  # record cursor; gaps appear when events do not abut
    prev_event_no = 0

    for idx, ev in enumerate(events):
        if ev["track"] != "V":
            raise EDLParseError(
                f"event {ev['event']}: audio-only events ({ev['track']}) are not "
                f"mapped by this video exchange"
            )
        if ev["event"] <= prev_event_no:
            raise EDLParseError(f"event numbers must be ascending near {ev['event']}")
        prev_event_no = ev["event"]

        reel = ev["reel"]
        if reel not in sources:
            # Synthesize a source at the record rate; mark it as unresolved.
            src = Source(
                source_id=reel,
                path=None,
                rate=rate,
                duration=parse_timecode(ev["source_out"], rate),
                exists=False,
                reel=reel,
            )
            sources[reel] = src
        src = sources[reel]
        tl.sources[reel] = src

        rec_in = origin + parse_timecode(ev["record_in"], rate)
        rec_out = origin + parse_timecode(ev["record_out"], rate)
        # Source timecodes are labelled on the source's own clock, so they must
        # be parsed against the source frame rate, never the record rate, and
        # rebased by the media's first-frame timecode.
        src_in = parse_timecode(ev["source_in"], src.rate) - src.timecode_origin
        src_out = parse_timecode(ev["source_out"], src.rate) - src.timecode_origin
        duration = rec_out - rec_in

        # An explicit record gap between adjacent events becomes a real Gap.
        if rec_in > cursor:
            track.gaps.append(
                Gap(
                    gap_id=f"G{idx + 1:03d}",
                    start=cursor,
                    duration=rec_in - cursor,
                )
            )

        trans_in = TRANSITION_CUT
        trans_dur = Fraction(0)
        if ev["trans"] == "C":
            trans_in = TRANSITION_CUT
        elif ev["trans"] == "D":
            trans_in = TRANSITION_DISSOLVE
            trans_dur = Fraction(int(ev["aux"] or 0), 1) / rate.rate
            if trans_dur <= 0:
                raise EDLParseError(
                    f"event {ev['event']}: dissolve needs a frame count"
                )
        elif ev["trans"] == "W":
            trans_in = TRANSITION_WIPE
            trans_dur = Fraction(int(ev["aux"] or 0), 1) / rate.rate
        else:
            raise EDLParseError(
                f"event {ev['event']}: unsupported EDL transition {ev['trans']!r}"
            )

        track.clips.append(
            Clip(
                clip_id=f"C{ev['event']:04d}",
                source_id=reel,
                source_in=src_in,
                source_out=src_out,
                timeline_start=rec_in,
                duration=duration,
                transition_in=trans_in,
                transition_duration=trans_dur,
            )
        )
        cursor = rec_out

    tl.validate()
    return tl


def _reel_name(source_id: str, reel: str | None) -> str:
    name = (reel or source_id).replace(" ", "_")
    # CMX reel labels are limited to 8 alphanumeric chars in strict decoders.
    if len(name) > 8 or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        name = re.sub(r"[^A-Za-z0-9]", "", name)[:8] or "REEL"
    return name.upper()


def export_text(tl: Timeline, report: LossReport | None = None,
                profile=None) -> str:
    """Export to CMX 3600 EDL, recording every loss in ``report``.

    ``profile`` is a :class:`~tlx.capabilities.TargetProfile`; defaults to the
    full CMX profile.  A narrowed profile degrades exactly the capabilities it
    dropped and reports them, so the capability list and the exporter cannot
    drift apart.
    """
    if report is None:
        report = LossReport(FORMAT_NAME)
    if profile is None:
        from .capabilities import CMX_PROFILE
        profile = CMX_PROFILE
    origin = tl.start_time
    rate = tl.rate
    video = tl.video_tracks()
    if len(video) > 1 and not profile.multi_video_track:
        for extra in video[1:]:
            first_clip = min(
                extra.clips, key=lambda c: c.timeline_start, default=None
            )
            report.add(
                LossEntry(
                    code="track_not_expressible",
                    severity="error",
                    kind="track",
                    object_id=extra.track_id,
                    timeline_start=(
                        first_clip.timeline_start if first_clip else None
                    ),
                    timeline_end=(first_clip.timeline_end if first_clip else None),
                    message=(
                        "CMX 3600 holds a single video track; clips on "
                        f"{extra.track_id} cannot be exported"
                    ),
                    details={"fallback": "track_dropped"},
                )
            )
    if not video:
        report.add(
            LossEntry(
                code="no_video_track",
                severity="error",
                kind="track",
                object_id="-",
                timeline_start=None,
                timeline_end=None,
                message="timeline has no video track to export",
            )
        )
        return ""

    track = video[0]
    title = str(tl.metadata.get("title", tl.timeline_id))
    lines = [
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        "",
    ]
    event_no = 0
    reel_cache: dict[str, str] = {}

    items = track.sorted_items()
    for item in items:
        if isinstance(item, Gap):
            if not profile.explicit_gaps:
                report.add(
                    LossEntry(
                        code="gap_not_expressible",
                        severity="error",
                        kind="gap",
                        object_id=item.gap_id,
                        timeline_start=item.start,
                        timeline_end=item.end,
                        message=(
                            "CMX 3600 has no explicit gap construct; the empty "
                            "interval is represented only by the record-time jump"
                        ),
                        details={"fallback": "record_time_jump"},
                    )
                )
            continue

        clip = item
        event_no += 1
        src = tl.sources[clip.source_id]
        if clip.source_id not in reel_cache:
            reel_cache[clip.source_id] = _reel_name(clip.source_id, src.reel)
            if reel_cache[clip.source_id] != (src.reel or clip.source_id):
                report.add(
                    LossEntry(
                        code="reel_name_truncated",
                        severity="warning",
                        kind="source",
                        object_id=clip.source_id,
                        timeline_start=None,
                        timeline_end=None,
                        message=(
                            f"source id {clip.source_id!r} truncated to CMX reel "
                            f"{reel_cache[clip.source_id]!r}"
                        ),
                    )
                )
        reel = reel_cache[clip.source_id]

        if src.rate.rate != rate.rate:
            report.add(
                LossEntry(
                    code="mixed_rate_source",
                    severity="warning",
                    kind="source",
                    object_id=clip.source_id,
                    timeline_start=clip.timeline_start,
                    timeline_end=clip.timeline_end,
                    message=(
                        f"source rate {src.rate} differs from record rate {rate}; "
                        "EDL stores timecodes only, retiming metadata is lost"
                    ),
                    details={"fallback": "record_rate_timecode"},
                )
            )

        if clip.baked and not profile.baked_media:
            report.add(
                LossEntry(
                    code="baked_media_not_expressible",
                    severity="error",
                    kind="clip",
                    object_id=clip.clip_id,
                    timeline_start=clip.timeline_start,
                    timeline_end=clip.timeline_end,
                    message=(
                        "target profile cannot reference a pre-baked media asset; "
                        "the flattened transition cannot be relinked"
                    ),
                    details={"fallback": "baked_asset_dropped"},
                )
            )

        if clip.reverse and not profile.reverse:
            report.add(
                LossEntry(
                    code="reverse_not_expressible",
                    severity="error",
                    kind="clip",
                    object_id=clip.clip_id,
                    timeline_start=clip.timeline_start,
                    timeline_end=clip.timeline_end,
                    message=(
                        "target profile cannot express reverse playback; the "
                        "source range is exported forwards"
                    ),
                    details={"fallback": "forward_playback", "source_range_reversed": True},
                )
            )

        effective = clip.transition_in
        if effective not in profile.transitions:
            effective = fallback_transition(effective, profile)
            report.add(
                LossEntry(
                    code="transition_not_expressible",
                    severity="error",
                    kind="transition",
                    object_id=clip.clip_id,
                    timeline_start=clip.timeline_start,
                    timeline_end=min(
                        clip.timeline_end,
                        clip.timeline_start + clip.transition_duration,
                    ),
                    message=(
                        f"transition {clip.transition_in!r} has no CMX 3600 "
                        f"equivalent under {profile.profile_id}; "
                        f"degraded to {effective}"
                    ),
                    details={
                        "requested": clip.transition_in,
                        "fallback": effective,
                        "source_frames_unchanged": True,
                    },
                )
            )
        if effective == TRANSITION_CUT:
            trans_code, aux = "C", ""
        elif effective == TRANSITION_WIPE:
            frames = int(clip.transition_duration * rate.rate)
            trans_code, aux = "W", f"{frames:3d}"
        else:  # dissolve (fade is not distinguishable and falls back here)
            frames = int(clip.transition_duration * rate.rate)
            trans_code, aux = "D", f"{frames:3d}"

        if not clip.enabled:
            report.add(
                LossEntry(
                    code="disabled_clip_forced_enabled",
                    severity="warning",
                    kind="clip",
                    object_id=clip.clip_id,
                    timeline_start=clip.timeline_start,
                    timeline_end=clip.timeline_end,
                    message="CMX EDL cannot mark a clip disabled; it is exported active",
                )
            )

        sin = format_timecode(clip.source_in + src.timecode_origin, src.rate)
        sout = format_timecode(clip.source_out + src.timecode_origin, src.rate)
        rin = format_timecode(clip.timeline_start - origin, rate)
        rout = format_timecode(clip.timeline_end - origin, rate)
        lines.append(
            f"{event_no:04d}  {reel:<8} V  {trans_code:<3s} {aux}".rstrip()
            + f"  {sin} {sout} {rin} {rout}"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _fallback_for(transition: str, profile) -> str:
    from .capabilities import fallback_transition
    return fallback_transition(transition, profile)
