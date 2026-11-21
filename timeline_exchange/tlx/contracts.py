"""Executable contract samples.

Each sample is a small, self-contained timeline plus the capability it
exercises.  Running a sample against a :class:`TargetProfile` performs the real
import → export → re-import → frame comparison pipeline and records one of:

``reversible``
    the target expresses the capability losslessly; every record frame decodes
    the same source frame and the edit structure is unchanged.
``flattened``
    the picture survives but the edit is no longer editable (a baked/flattened
    transition, or a stand-in the tool must treat as baked media).
``rejected``
    the target cannot express it and the tool refuses or degrades it while
    naming the exact loss; it is never silently dropped.

The samples also feed the capability manifest, so "supported" in the docs is
the same thing the runner observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable

from .model import (
    Timeline, Track, Clip, Source, Gap,
    TRANSITION_CUT, TRANSITION_DISSOLVE, TRANSITION_WIPE,
    TRANSITION_PUSH, TRANSITION_SLIDE,
)
from .timecode import FrameRate, frame_time, parse_timecode, format_timecode
from . import tl_json, cmx, exchange, fidelity, trace
from .capabilities import TargetProfile, get_profile

R24 = FrameRate(24000, 1001)
R25 = FrameRate(25, 1)
R30 = FrameRate(30000, 1001)
ORIGIN = parse_timecode("01:00:00:00", R24)


@dataclass
class SampleMedia:
    """A deterministic media bundle description (no pixels needed)."""

    source_id: str
    media_ref: str
    content_id: str
    rate: FrameRate
    frames: int
    path: str
    timecode_origin: Fraction = Fraction(0)


def _src(m: SampleMedia, path: str | None = None) -> Source:
    return Source(
        source_id=m.source_id,
        path=path or m.path,
        rate=m.rate,
        duration=frame_time(m.frames, m.rate),
        exists=True,
        media_ref=m.media_ref,
        content_id=m.content_id,
        timecode_origin=m.timecode_origin,
    )


A = SampleMedia("CAM_A", "media:cam-a", "sha256:aaaa", R24, 480, "samples/CAM_A")
B = SampleMedia("CAM_B", "media:cam-b", "sha256:bbbb", R25, 480, "samples/CAM_B")


@dataclass
class ContractSample:
    sample_id: str
    title: str
    capabilities: tuple[str, ...]
    build: Callable[[], Timeline]
    probe_times: Callable[[Timeline], list[Fraction]] = field(
        default=lambda tl: [tl.start_time + frame_time(0, tl.rate)]
    )
    # The expected outcome for a *fully capable* target.  A narrower target may
    # differ, which is how the manifest stays honest.
    expected_for_full: str = "reversible"
    exercises: str = ""

    def build_timeline(self) -> Timeline:
        tl = self.build()
        tl.validate()
        return tl


def _single_clip(dissolve: bool = False, transition: str = TRANSITION_CUT,
                 duration_frames: int = 4, reverse: bool = False,
                 baked: bool = False) -> Timeline:
    tl = Timeline("contract", R24, ORIGIN)
    tl.sources = {"CAM_A": _src(A)}
    v1 = Track("V1", "video", 0)
    v1.clips.append(
        Clip(
            "C1", "CAM_A", frame_time(10, R24), frame_time(40, R24),
            ORIGIN, frame_time(30, R24),
            transition_in=transition if dissolve else TRANSITION_CUT,
            transition_duration=(
                frame_time(duration_frames, R24) if dissolve else Fraction(0)
            ),
            reverse=reverse,
            baked=baked,
        )
    )
    tl.tracks = [v1]
    return tl


def _two_clip(transition: str, duration_frames: int = 4,
              outgoing_baked: bool = False) -> Timeline:
    tl = Timeline("contract", R24, ORIGIN)
    tl.sources = {"CAM_A": _src(A), "CAM_B": _src(B)}
    v1 = Track("V1", "video", 0)
    v1.clips = [
        Clip("C1", "CAM_A", frame_time(0, R24), frame_time(20, R24),
             ORIGIN, frame_time(20, R24),
             baked=outgoing_baked),
        Clip("C2", "CAM_B", frame_time(5, R25), frame_time(25, R25),
             ORIGIN + frame_time(20, R24), frame_time(20, R24),
             transition_in=transition,
             transition_duration=frame_time(duration_frames, R24)),
    ]
    tl.tracks = [v1]
    return tl


def _multi_track() -> Timeline:
    tl = Timeline("contract", R24, ORIGIN)
    tl.sources = {"CAM_A": _src(A), "CAM_B": _src(B)}
    v1 = Track("V1", "video", 0)
    v1.clips = [Clip("C1", "CAM_A", frame_time(0, R24), frame_time(20, R24),
                     ORIGIN, frame_time(20, R24))]
    v2 = Track("V2", "video", 1)
    v2.clips = [Clip("C2", "CAM_B", frame_time(0, R25), frame_time(10, R25),
                     ORIGIN + frame_time(4, R24), frame_time(10, R24))]
    tl.tracks = [v1, v2]
    return tl


def _gap() -> Timeline:
    tl = Timeline("contract", R24, ORIGIN)
    tl.sources = {"CAM_A": _src(A)}
    v1 = Track("V1", "video", 0)
    v1.clips = [Clip("C1", "CAM_A", frame_time(0, R24), frame_time(10, R24),
                     ORIGIN, frame_time(10, R24))]
    v1.gaps = [Gap("G1", ORIGIN + frame_time(10, R24), frame_time(6, R24))]
    v1.clips.append(Clip("C2", "CAM_A", frame_time(20, R24), frame_time(26, R24),
                         ORIGIN + frame_time(16, R24), frame_time(6, R24)))
    tl.tracks = [v1]
    return tl


def _mixed_rate() -> Timeline:
    tl = Timeline("contract", R24, ORIGIN)
    tl.sources = {"CAM_A": _src(A), "CAM_B": _src(B)}
    v1 = Track("V1", "video", 0)
    v1.clips = [
        Clip("C1", "CAM_A", frame_time(0, R24), frame_time(10, R24),
             ORIGIN, frame_time(10, R24)),
        Clip("C2", "CAM_B", frame_time(2, R25), frame_time(14, R25),
             ORIGIN + frame_time(10, R24), frame_time(12, R24)),
    ]
    tl.tracks = [v1]
    return tl


def _reverse() -> Timeline:
    return _single_clip(reverse=True)


def _baked() -> Timeline:
    return _single_clip(dissolve=True, transition=TRANSITION_DISSOLVE, baked=True)


def _push() -> Timeline:
    return _two_clip(TRANSITION_PUSH)


def _slide() -> Timeline:
    return _two_clip(TRANSITION_SLIDE)


def _wipe() -> Timeline:
    return _two_clip(TRANSITION_WIPE)


def _dissolve() -> Timeline:
    return _two_clip(TRANSITION_DISSOLVE)


SAMPLES: tuple[ContractSample, ...] = (
    ContractSample(
        "cut", "adjacent hard cut, editable edit point", ("transition:cut",),
        lambda: _single_clip(), expected_for_full="reversible",
    ),
    ContractSample(
        "dissolve", "dissolve keeps both source ranges editable",
        ("transition:dissolve",), _dissolve,
    ),
    ContractSample(
        "fade", "fade keeps both source ranges editable",
        ("transition:fade",),
        lambda: _two_clip("fade"), expected_for_full="reversible",
    ),
    ContractSample(
        "wipe", "wipe keeps both source ranges editable",
        ("transition:wipe",), _wipe,
    ),
    ContractSample(
        "push", "push transition between two ranges",
        ("transition:push",), _push,
    ),
    ContractSample(
        "slide", "slide transition between two ranges",
        ("transition:slide",), _slide,
    ),
    ContractSample(
        "multi_track", "second simultaneous video track",
        ("track:multi_video",), _multi_track,
    ),
    ContractSample(
        "gap", "explicit empty interval object",
        ("gap:explicit",), _gap,
    ),
    ContractSample(
        "mixed_rate", "mixed source rates on one record timeline",
        ("source:mixed_rate",), _mixed_rate,
    ),
    ContractSample(
        "reverse", "reverse-played source range",
        ("clip:reverse",), _reverse,
    ),
    ContractSample(
        "baked", "pre-flattened transition media asset",
        ("media:baked",), _baked, expected_for_full="reversible",
    ),
)


SAMPLES_BY_ID = {s.sample_id: s for s in SAMPLES}


@dataclass
class SampleOutcome:
    sample: str
    title: str
    capabilities: tuple[str, ...]
    profile_id: str
    outcome: str  # reversible | flattened | rejected
    expected_for_full: str
    loss_codes: list[str]
    first_loss: dict | None
    first_loss_record_timecode: str | None
    frame_deltas: int
    structure_deltas: int
    pixel_match: bool
    edit_structure_faithful: bool
    trace_window: dict | None = None

    def to_jsonable(self) -> dict:
        return {
            "sample": self.sample,
            "title": self.title,
            "capabilities": list(self.capabilities),
            "profile_id": self.profile_id,
            "outcome": self.outcome,
            "expected_for_full": self.expected_for_full,
            "loss_codes": self.loss_codes,
            "first_loss": self.first_loss,
            "first_loss_record_timecode": self.first_loss_record_timecode,
            "pixel_match": self.pixel_match,
            "edit_structure_faithful": self.edit_structure_faithful,
            "frame_deltas": self.frame_deltas,
            "structure_deltas": self.structure_deltas,
            "trace_window": self.trace_window,
        }


def _classify(profile: TargetProfile, sample: ContractSample,
              tl: Timeline, report, fid: fidelity.FidelityReport) -> str:
    # Outcome is what was *measured* after the real round trip, not what the
    # loss report predicted.  A loss entry can be conservative: a gap reported
    # as inexpressible is still reconstructed from the record-time jump, so the
    # sample is reversible if pixels and structure actually survive.
    if fid.pixel_match and fid.edit_structure_faithful:
        return "reversible"
    # Same decoded picture, but an edit semantic (transition, source range,
    # editable cut) was flattened away.
    if fid.pixel_match:
        return "flattened"
    return "rejected"


def run_sample(sample: ContractSample, profile: TargetProfile) -> SampleOutcome:
    tl = sample.build_timeline()
    text = (
        cmx.export_text(tl, profile=profile)
        if profile.format == cmx.FORMAT_NAME
        else tl_json.export_text(tl)
    )
    exported, report = exchange.export_document(profile.format, tl, profile=profile)
    reimported = exchange.import_document(
        profile.format, exported, rate=tl.rate, sources=dict(tl.sources)
    )
    # Contract samples describe their media abstractly; they are not probed
    # against disk, so the comparison is purely about edit semantics.
    fid = fidelity.compare(tl, reimported)
    outcome = _classify(profile, sample, tl, report, fid)

    first = report.first_positioned()
    first_tc = None
    if first is not None and first.timeline_start is not None:
        first_tc = format_timecode(first.timeline_start - tl.start_time, tl.rate)

    trace_window = None
    try:
        probe_time = _probe_time(sample, tl)
        trace_window = trace.resolve(tl, probe_time).to_jsonable(tl)
    except Exception:
        trace_window = None

    return SampleOutcome(
        sample=sample.sample_id,
        title=sample.title,
        capabilities=sample.capabilities,
        profile_id=profile.profile_id,
        outcome=outcome,
        expected_for_full=sample.expected_for_full,
        loss_codes=sorted({e.code for e in report.entries}),
        first_loss=report.first_loss_jsonable(tl.rate),
        first_loss_record_timecode=first_tc,
        frame_deltas=len(fid.frame_deltas),
        structure_deltas=len(fid.structure_deltas),
        pixel_match=fid.pixel_match,
        edit_structure_faithful=fid.edit_structure_faithful,
        trace_window=trace_window,
    )


def _probe_time(sample: ContractSample, tl: Timeline) -> Fraction:
    """A record time inside the sample's own capability window."""
    sid = sample.sample_id
    if sid in ("dissolve", "fade", "wipe", "push", "slide"):
        return ORIGIN + frame_time(21, R24)  # inside the 4-frame transition
    if sid == "baked":
        return ORIGIN + frame_time(5, R24)
    if sid == "gap":
        return ORIGIN + frame_time(12, R24)  # inside the explicit gap
    if sid == "multi_track":
        return ORIGIN + frame_time(6, R24)  # overlay on V2
    if sid == "mixed_rate":
        return ORIGIN + frame_time(11, R24)
    return ORIGIN + frame_time(5, R24)


def run_all(profile: TargetProfile | None = None,
            profile_id: str | None = None) -> list[SampleOutcome]:
    profile = profile or get_profile(profile_id)
    return [run_sample(s, profile) for s in SAMPLES]


def outcomes_jsonable(outcomes: list[SampleOutcome]) -> list[dict]:
    return [o.to_jsonable() for o in outcomes]


def compare_profiles(base: TargetProfile, narrowed: TargetProfile) -> dict:
    """Run both profiles and report exactly which samples a narrowing affects.

    This is the audit trail behind "narrowing a capability only changes the
    samples that use it": no sample is re-labelled by hand.
    """
    from .capabilities import CAPABILITY_KEYS

    base_outcomes = {o.sample: o for o in run_all(base)}
    narrow_outcomes = {o.sample: o for o in run_all(narrowed)}
    removed = [
        key for key in CAPABILITY_KEYS
        if base.supports(key) and not narrowed.supports(key)
    ]
    changed = []
    unchanged = []
    for sample_id, bo in base_outcomes.items():
        no = narrow_outcomes[sample_id]
        if (
            bo.outcome != no.outcome
            or bo.edit_structure_faithful != no.edit_structure_faithful
            or bo.pixel_match != no.pixel_match
        ):
            changed.append(
                {
                    "sample": sample_id,
                    "capabilities": list(no.capabilities),
                    "before_outcome": bo.outcome,
                    "after_outcome": no.outcome,
                    "first_loss_record_timecode": no.first_loss_record_timecode,
                    "first_loss": no.first_loss,
                    "explanation": (
                        f"sample uses {', '.join(no.capabilities)}, which "
                        f"{narrowed.profile_id} no longer expresses; edit "
                        "semantics first lost at "
                        f"{no.first_loss_record_timecode}"
                    ),
                }
            )
        else:
            unchanged.append(sample_id)
    return {
        "base_profile": base.profile_id,
        "narrowed_profile": narrowed.profile_id,
        "removed_capabilities": removed,
        "changed_samples": changed,
        "unchanged_samples": unchanged,
        "changed_count": len(changed),
        "only_affected_samples_changed": all(
            any(cap in removed for cap in row["capabilities"])
            for row in changed
        ),
    }


# ---------------------------------------------------------------------------
# Scenario fixtures used by the scenario runner
# ---------------------------------------------------------------------------

def baked_stand_in() -> Timeline:
    """A timeline whose second clip was replaced by a same-duration baked file.

    The first clip is a real, editable source range.  The second clip points at
    a baked asset that has the same duration but *different* content id and
    source-timecode origin: the picture plays, but a move of the edit point is
    no longer backed by the intended frames.
    """
    tl = Timeline("baked_stand_in", R24, ORIGIN)
    tl.sources = {
        "CAM_A": _src(A),
        "BAKED": Source(
            source_id="BAKED",
            path="samples/BAKED",
            rate=R24,
            duration=frame_time(20, R24),
            exists=True,
            media_ref="media:baked-standin",
            content_id="sha256:baked",
            timecode_origin=parse_timecode("00:00:00:00", R24),
        ),
    }
    v1 = Track("V1", "video", 0)
    v1.clips = [
        Clip("C1", "CAM_A", frame_time(0, R24), frame_time(20, R24),
             ORIGIN, frame_time(20, R24)),
        Clip("C2", "BAKED", frame_time(0, R24), frame_time(20, R24),
             ORIGIN + frame_time(20, R24), frame_time(20, R24), baked=True),
    ]
    tl.tracks = [v1]
    return tl


def shared_name_projects() -> tuple[Timeline, Timeline]:
    """Two projects that use the same display name for different media.

    Both call their source ``CAM_A`` and both durations match, but the two
    media have distinct ``media_ref`` / ``content_id`` and different source
    timecode origins.  A name-based resolver would swap them happily.
    """
    def build(tag: str, origin_label: str, frame_offset: int,
              reverse: bool = False) -> Timeline:
        rate = R24
        origin_tc = parse_timecode(origin_label, rate)
        media = SampleMedia(
            "CAM_A", f"media:{tag}", f"sha256:{tag}", rate, 480,
            f"projects/{tag}/CAM_A", timecode_origin=origin_tc,
        )
        tl = Timeline(f"project_{tag}", rate, ORIGIN)
        tl.sources = {"CAM_A": _src(media)}
        v1 = Track("V1", "video", 0)
        v1.clips = [
            Clip(
                "C1", "CAM_A",
                frame_time(frame_offset, rate),
                frame_time(frame_offset + 12, rate),
                ORIGIN, frame_time(12, rate), reverse=reverse,
            )
        ]
        tl.tracks = [v1]
        return tl

    left = build("left", "01:00:00:00", 0)
    right = build("right", "02:00:00:00", 24)
    return left, right


def shared_name_baked_projects() -> tuple[Timeline, Timeline]:
    """Same as :func:`shared_name_projects`, but the shared shot is baked."""
    left, right = shared_name_projects()
    right.tracks[0].clips[0].baked = True
    return left, right
