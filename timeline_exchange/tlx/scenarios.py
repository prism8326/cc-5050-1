"""Executable scenarios that stitch the contract samples to real behaviour.

Each scenario produces measured facts (not prose) and is safe to re-run:

``baked_standin``
    Replace one source range with a same-duration baked file, move the edit
    point, and prove the operation notices the stand-in instead of silently
    reselecting frames from it.

``three_capabilities``
    On one shot source: a reverse clip, a transition whose clip overlaps a
    neighbouring track, and a cross-rate cut.  Any output frame the author
    picks inside the exchange window resolves back to its source and range.

``shared_name_swap``
    Two projects use the same display name for different media (different
    source timecode).  Rebuild them with the two inputs in random order and
    check that the validator follows ``media_ref`` — never the name — and that
    a name-only lookup would have accepted a clip that merely plays.

``flatten_vs_editable``
    Import a project with identical pictures whose edit point is either baked
    or still editable; the report must separate pixel equality from structural
    fidelity.

``narrow_wipe``
    Narrow the target's capability set and confirm only the affected contract
    samples change, with the first lost edit-semantics position.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from fractions import Fraction

from .model import (
    Timeline, Track, Clip, Source, TRANSITION_CUT, TRANSITION_DISSOLVE,
    TRANSITION_PUSH,
)
from .timecode import FrameRate, frame_time, parse_timecode, format_timecode
from . import contracts, fidelity, preview, relink, trace, validate
from .capabilities import CMX_PROFILE, NARROW_WIPE_PROFILE, TargetProfile
from .media import MediaCatalog, MediaAsset

R24 = contracts.R24
R25 = contracts.R25
ORIGIN = contracts.ORIGIN


# ---------------------------------------------------------------------------
# Scenario 1: baked stand-in is caught by a move-edit-point operation
# ---------------------------------------------------------------------------

def _move_edit_point(tl: Timeline, clip_id: str, new_source_in: Fraction) -> dict:
    """Simulate moving an edit point and re-deriving the selected source frame.

    Returns before/after selections plus whether the destination is backed by
    the media the clip claimed.  This is the operation an author performs; it
    must not succeed by accident on a same-duration stand-in.
    """
    clip = next(c for t in tl.tracks for c in t.clips if c.clip_id == clip_id)
    src = tl.sources[clip.source_id]
    old_in = clip.source_in
    record = clip.timeline_start
    before = preview.describe_selection(preview.select_frame(tl, record), tl)
    clip.source_in = new_source_in
    clip.source_out = new_source_in + clip.duration
    after = preview.describe_selection(preview.select_frame(tl, record), tl)
    wanted_frame = preview.select_frame(tl, record).source_frame_index
    backed = (
        src.content_id is not None
        and src.content_id == _expected_content_for(tl, clip_id)
    )
    return {
        "clip_id": clip_id,
        "record_timecode": format_timecode(record - tl.start_time, tl.rate),
        "old_source_in": {"rational": str(old_in),
                          "timecode": format_timecode(old_in, src.rate)},
        "new_source_in": {"rational": str(new_source_in),
                          "timecode": format_timecode(new_source_in, src.rate)},
        "before_selection": {k: before.get(k) for k in
                             ("source_id", "source_frame_index")},
        "after_selection": {k: after.get(k) for k in
                            ("source_id", "source_frame_index")},
        "wanted_frame": wanted_frame,
        "media_ref": src.media_ref,
        "content_id": src.content_id,
        "backed_by_claimed_media": backed,
        "verdict": (
            "edit point moved onto the intended media"
            if backed else
            "edit point would resolve to a stand-in: same duration, wrong media"
        ),
    }


def _expected_content_for(tl: Timeline, clip_id: str) -> str | None:
    """The content id the project *intended* for the clip's source.

    A baked stand-in carries its own content id; the project's intended media
    is registered in metadata so a substitute can be detected.
    """
    intended = tl.metadata.get("intended_content_ids", {})
    return intended.get(clip_id)


def scenario_baked_standin(seed: int = 0) -> dict:
    tl = contracts.baked_stand_in()
    tl.metadata["intended_content_ids"] = {
        "C1": "sha256:aaaa",     # real CAM_A
        "C2": "sha256:aaaa",     # the edit *intended* CAM_A, but was baked in
    }
    move_c1 = _move_edit_point(tl, "C1", frame_time(4, R24))
    move_c2 = _move_edit_point(tl, "C2", frame_time(4, R24))
    catalog = MediaCatalog()
    # The intended media exists in the catalog under CAM_A's real identity.
    catalog.add(_asset(
        path="samples/CAM_A", media_ref="media:cam-a", content_id="sha256:aaaa",
        rate=R24, duration=frame_time(480, R24),
    ))
    # The catalog holds the project's *intended* media only.  The substituted
    # baked asset is not registered, so following media_ref locates C2 instead
    # of matching on the display name.
    validation = validate.validate(tl, catalog)
    caught = not move_c2["backed_by_claimed_media"]
    return {
        "scenario": "baked_standin",
        "seed": seed,
        "description": (
            "one source range replaced by a same-duration baked video; move an "
            "edit point and verify the stand-in is detected"
        ),
        "move_on_real_clip": move_c1,
        "move_on_baked_clip": move_c2,
        "stand_in_detected": caught,
        "validation": validation.to_jsonable(tl),
        "pass": bool(caught and move_c1["backed_by_claimed_media"]
                     and not validation.ok),
    }


def _asset(**kw) -> MediaAsset:
    return MediaAsset(**kw)


# ---------------------------------------------------------------------------
# Scenario 2: reverse + overlapping-track transition + cross-rate cut
# ---------------------------------------------------------------------------

def three_capability_timeline() -> Timeline:
    """One shot source with all three hard cases on it.

    * CAM_A: a reverse-played range on V1.
    * CAM_B: an incoming clip whose transition overlaps V2's clip in time.
    * CAM_C at 29.97: a cross-rate cut against the 23.976 record rate.
    """
    cam_a = contracts.A
    cam_b = contracts.B
    cam_c = contracts.SampleMedia(
        "CAM_C", "media:cam-c", "sha256:cccc", contracts.R30, 480, "samples/CAM_C"
    )
    tl = Timeline("three_capabilities", R24, ORIGIN)
    tl.sources = {
        "CAM_A": contracts._src(cam_a),
        "CAM_B": contracts._src(cam_b),
        "CAM_C": contracts._src(cam_c),
    }
    v1 = Track("V1", "video", 0)
    v1.clips = [
        # Reverse: source range 30..18 read backwards across 12 record frames.
        Clip("C1", "CAM_A", frame_time(18, R24), frame_time(30, R24),
             ORIGIN, frame_time(12, R24), reverse=True),
        # Cross-rate cut: CAM_C is 29.97; source out is on its own clock.
        Clip("C2", "CAM_C", frame_time(20, contracts.R30),
             frame_time(32, contracts.R30),
             ORIGIN + frame_time(12, R24), frame_time(12, R24)),
        # Transition clip that overlaps V2 in record time.
        Clip("C3", "CAM_B", frame_time(4, R25), frame_time(24, R25),
             ORIGIN + frame_time(24, R24), frame_time(20, R24),
             transition_in=TRANSITION_DISSOLVE,
             transition_duration=frame_time(6, R24)),
    ]
    # V2 overlaps only the tail of the C3 transition window [24, 30).  Frames
    # 24-25 show V1's live transition, frames 26+ show the V2 overlay: both an
    # overlapping transition and an any-frame resolution requirement.
    v2 = Track("V2", "video", 1)
    v2.clips = [
        Clip("O1", "CAM_A", frame_time(40, R24), frame_time(50, R24),
             ORIGIN + frame_time(26, R24), frame_time(10, R24)),
    ]
    tl.tracks = [v1, v2]
    tl.validate()
    return tl


def scenario_three_capabilities(seed: int = 0) -> dict:
    tl = three_capability_timeline()
    rate = tl.rate
    probes = {
        "reverse_mid": ORIGIN + frame_time(3, rate),
        "cross_rate_cut": ORIGIN + frame_time(12, rate),
        "overlap_transition": ORIGIN + frame_time(25, rate),
        "overlap_track_wins": ORIGIN + frame_time(27, rate),
    }
    resolved = {}
    for name, t in probes.items():
        res = trace.resolve(tl, t)
        resolved[name] = res.to_jsonable(tl)

    # The author must be able to pick *any* output frame and get a source back.
    count = preview.frame_index_at(tl.duration - tl.start_time, rate)
    unresolved = []
    for i in range(count):
        t = ORIGIN + frame_time(i, rate)
        res = trace.resolve(tl, t)
        if res.kind in ("gap", "black"):
            continue
        if not res.sources:
            unresolved.append(i)
        for s in res.sources:
            if s.source_frame_index < 0:
                unresolved.append(i)

    # Reverse must actually descend on the source clock.
    rev = resolved["reverse_mid"]["sources"][0]
    first = resolved["reverse_mid"]["sources"][0]["source_frame_index"]
    t0 = ORIGIN
    s0 = trace.resolve(tl, t0).sources[0].source_frame_index
    descending = s0 > first

    overlap = resolved["overlap_transition"]
    return {
        "scenario": "three_capabilities",
        "seed": seed,
        "description": (
            "reverse clip, transition overlapping a neighbouring track, and a "
            "cross-rate cut on one shot source; every output frame resolves"
        ),
        "resolved_frames": resolved,
        "frames_without_source": unresolved,
        "reverse_descends": descending,
        "overlap_shows_two_ranges": overlap["needs"] == "reconnect_two_ranges"
                                   and len(overlap["sources"]) == 2,
        "pass": bool(not unresolved and descending
                     and overlap["needs"] == "reconnect_two_ranges"),
    }


# ---------------------------------------------------------------------------
# Scenario 3: same display name, different media, random input order
# ---------------------------------------------------------------------------

def _catalog_for(project: Timeline, tag: str) -> MediaCatalog:
    catalog = MediaCatalog()
    src = project.sources["CAM_A"]
    catalog.add(
        MediaAsset(
            path=src.path,
            media_ref=src.media_ref,
            content_id=src.content_id,
            rate=src.rate,
            duration=src.duration,
            timecode_origin=src.timecode_origin,
            display_name="CAM_A",
        )
    )
    return catalog


def scenario_shared_name_swap(seed: int | None = None) -> dict:
    left, right = contracts.shared_name_projects()
    order = [("left", left), ("right", right)]
    s = random.Random(seed)
    s.shuffle(order)
    rebuild_order = [name for name, _ in order]

    combined = MediaCatalog()
    catalogs = {"left": _catalog_for(left, "left"),
                "right": _catalog_for(right, "right")}
    for cat in catalogs.values():
        for a in cat.assets:
            combined.add(a)

    results = {}
    for name, project in order:
        res = validate.validate(project, combined)
        results[name] = res.to_jsonable(project)

    # A name-only lookup would find exactly one asset per project and accept
    # it, even though the source timecode origin differs.
    name_only_accepts = {}
    for name, project in order:
        src = project.sources["CAM_A"]
        hits = combined.by_name("CAM_A")
        accepted = hits[0] if len(hits) == 1 else None
        # With both projects in the catalog there are two CAM_A assets: a naive
        # resolver that ignores media_ref cannot tell which belongs here.
        name_only_accepts[name] = {
            "candidate_count": len(hits),
            "ambiguous": len(hits) > 1,
            "would_accept_by_name": bool(hits),
            "correct_ref": src.media_ref,
            "candidate_refs": [h.media_ref for h in hits],
            "candidate_origins": [h.timecode_origin_label() for h in hits],
        }

    # Wrong-project catalog: give each project the *other* project's asset and
    # confirm that ref-following rejects it while a name match would not.
    crossed = {}
    for name, project in order:
        other = "right" if name == "left" else "left"
        cat = _catalog_for({"left": left, "right": right}[other], other)
        # Keep the display name identical so a name lookup would match.
        for a in cat.assets:
            a.display_name = "CAM_A"
        res = validate.validate(project, cat)
        crossed[name] = {
            "ok": res.ok,
            "issues": [i.to_jsonable(project) for i in res.issues],
        }

    passed = all(r["ok"] for r in results.values()) and all(
        v["ambiguous"] for v in name_only_accepts.values()
    ) and all(not v["ok"] for v in crossed.values())
    return {
        "scenario": "shared_name_swap",
        "description": (
            "two projects share the display name CAM_A but use different source "
            "timecodes; rebuild in random order and validate by ref"
        ),
        "seed": seed,
        "rebuild_order": rebuild_order,
        "validation": results,
        "name_only_lookup": name_only_accepts,
        "cross_project_catalog": crossed,
        "pass": bool(passed),
    }


# ---------------------------------------------------------------------------
# Scenario 4: identical picture, different edit structure
# ---------------------------------------------------------------------------

def scenario_flatten_vs_editable(seed: int = 0) -> dict:
    original = contracts.baked_stand_in()
    # Remove the baked marker and give C2 the real source range/identity: this
    # is the *editable* project with the same picture.
    editable = contracts.baked_stand_in()
    editable.tracks[0].clips[1].baked = False
    editable.sources["BAKED"] = contracts._src(contracts.A)
    editable.sources["BAKED"].source_id = "CAM_A"
    editable.tracks[0].clips[1].source_id = "CAM_A"
    editable.sources["CAM_A"] = editable.sources.pop("BAKED")
    # The baked copy is a pixel-identical flatten: same content fingerprint and
    # timecode origin, only the editability differs.
    original.sources["BAKED"].content_id = "sha256:aaaa"

    # Force the editable project's second range to start at the same frame the
    # baked asset represents, so decoded frames are identical.
    for tl in (original, editable):
        c2 = tl.tracks[0].clips[1]
        c2.source_in = frame_time(0, c2.duration and R24 or R24)
        c2.source_out = frame_time(20, R24)

    report = fidelity.compare(editable, original)
    return {
        "scenario": "flatten_vs_editable",
        "seed": seed,
        "description": (
            "identical decoded pictures; one project keeps an editable cut, the "
            "other is a baked asset — verdicts must stay separate"
        ),
        "pixel_match": report.pixel_match,
        "edit_structure_faithful": report.edit_structure_faithful,
        "report": report.to_jsonable(),
        "pass": bool(report.pixel_match and not report.edit_structure_faithful),
    }


# ---------------------------------------------------------------------------
# Scenario 5: narrowing a capability only changes affected samples
# ---------------------------------------------------------------------------

def scenario_narrow_wipe(seed: int = 0) -> dict:
    diff = contracts.compare_profiles(CMX_PROFILE, NARROW_WIPE_PROFILE)
    affected = {row["sample"] for row in diff["changed_samples"]}
    wipe_only = affected == {"wipe"}
    return {
        "scenario": "narrow_wipe",
        "seed": seed,
        "description": (
            "remove wipe from the target capability list; only the wipe sample "
            "may change, and it must name the first lost edit position"
        ),
        "diff": diff,
        "affected_samples": sorted(affected),
        "pass": bool(wipe_only and diff["only_affected_samples_changed"]
                     and all(r["first_loss_record_timecode"]
                             for r in diff["changed_samples"])),
    }


# ---------------------------------------------------------------------------
# Scenario 6: relink against real disk media, including a baked stand-in
# ---------------------------------------------------------------------------

def scenario_relink_disk_media(seed: int = 0) -> dict:
    """Relink the three hard cases against the real frame bundles.

    The timeline references CAM_A (reverse), CAM_B (transition) and CAM_C
    (cross-rate cut).  Candidates are read from the real bundles, so identity
    is the true on-disk fingerprint.  A same-duration baked copy of CAM_A under
    a different ref and a same-name CAM_B alternative with a different source
    timecode are both offered: neither may be accepted for the real clip.
    """
    tl = three_capability_timeline()
    sources_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "test_project", "sources",
    )

    real = {
        "CAM_A": MediaAsset.from_bundle(
            os.path.join(sources_root, "CAM_A"), "media:cam-a"),
        "CAM_B": MediaAsset.from_bundle(
            os.path.join(sources_root, "CAM_B"), "media:cam-b"),
        "CAM_C": MediaAsset.from_bundle(
            os.path.join(sources_root, "CAM_C"), "media:cam-c"),
    }
    baked = MediaAsset.from_bundle(
        os.path.join(sources_root, "CAM_A_BAKED"), "media:cam-a-baked")
    alt = MediaAsset.from_bundle(
        os.path.join(sources_root, "CAM_B_ALT"), "media:cam-b-alt",
        display_name="CAM_B")

    # Give the timeline the real media identity, then drop every path so the
    # relinker has to reconnect.
    for sid, asset in real.items():
        src = tl.sources[sid]
        src.media_ref = asset.media_ref
        src.content_id = asset.content_id
        src.timecode_origin = asset.timecode_origin
        src.path = None
        src.exists = False

    def cand(asset: MediaAsset) -> relink.MediaCandidate:
        return relink.MediaCandidate(
            path=asset.path,
            media_ref=asset.media_ref,
            content_id=asset.content_id,
            reel="CAM_B" if asset.display_name == "CAM_B" else asset.display_name,
            rate=asset.rate,
            duration=asset.duration,
            timecode_origin=asset.timecode_origin,
        )

    # Real relink with the decoys shuffled in.  Order must not change which
    # media is accepted.
    candidates = [cand(baked), cand(alt)] + [cand(a) for a in real.values()]
    random.Random(seed).shuffle(candidates)
    result = relink.relink(tl, candidates)
    accepted_bundles = sorted({
        os.path.basename(a["candidate_path"].rstrip("/"))
        for a in result.accepted
    })

    # Decoy-only relink: neither the same-duration baked copy nor the same-name
    # alternative may be accepted for anything, regardless of order.
    decoy_tl = three_capability_timeline()
    for sid, asset in real.items():
        src = decoy_tl.sources[sid]
        src.media_ref = asset.media_ref
        src.content_id = asset.content_id
        src.timecode_origin = asset.timecode_origin
        src.path = None
        src.exists = False
    decoy_candidates = [cand(baked), cand(alt)]
    random.Random(seed + 1).shuffle(decoy_candidates)
    decoy_result = relink.relink(decoy_tl, decoy_candidates)

    rejected_bundles = sorted({
        os.path.basename(r["candidate_path"].rstrip("/"))
        for r in decoy_result.rejected
    })
    passed = (
        accepted_bundles == ["CAM_A", "CAM_B", "CAM_C"]
        and result.missing_requests == []
        and decoy_result.accepted == []
        and rejected_bundles == ["CAM_A_BAKED", "CAM_B_ALT"]
    )
    return {
        "scenario": "relink_disk_media",
        "seed": seed,
        "description": (
            "relink reverse + transition + cross-rate cut against real bundles; "
            "a same-duration baked stand-in and a same-name alternative must be "
            "refused by media identity, independent of input order"
        ),
        "candidate_order": [os.path.basename(c.path) for c in candidates],
        "accepted": result.accepted,
        "rejected": result.rejected,
        "missing_requests": result.missing_requests,
        "accepted_bundles": accepted_bundles,
        "decoy_only": {
            "candidate_order": [os.path.basename(c.path)
                                for c in decoy_candidates],
            "accepted": decoy_result.accepted,
            "rejected": decoy_result.rejected,
            "missing_requests": decoy_result.missing_requests,
        },
        "decoy_rejected_bundles": rejected_bundles,
        "pass": bool(passed),
    }


SCENARIOS = {
    "baked_standin": scenario_baked_standin,
    "three_capabilities": scenario_three_capabilities,
    "shared_name_swap": scenario_shared_name_swap,
    "flatten_vs_editable": scenario_flatten_vs_editable,
    "narrow_wipe": scenario_narrow_wipe,
    "relink_disk_media": scenario_relink_disk_media,
}


@dataclass
class ScenarioRun:
    name: str
    result: dict

    @property
    def passed(self) -> bool:
        return bool(self.result.get("pass"))


def run_scenario(name: str, seed: int | None = None) -> ScenarioRun:
    fn = SCENARIOS[name]
    result = fn(seed) if seed is not None else fn()
    return ScenarioRun(name, result)


def run_all(seed: int | None = None) -> list[ScenarioRun]:
    return [run_scenario(name, seed) for name in SCENARIOS]
