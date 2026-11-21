"""Tests for the executable contracts: capabilities, relinking, validation.

These tests bind documentation to behaviour.  Each capability in the manifest
must be exercised by a sample, each narrowing must touch only affected samples,
and the relinker/validator must follow media identity rather than a display
name.
"""

from __future__ import annotations

import os
import random
import sys
import unittest
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tlx import capabilities, contracts, fidelity, relink, scenarios, trace  # noqa
from tlx import preview, tl_json  # noqa: E402
from tlx.media import MediaAsset, MediaCatalog  # noqa: E402
from tlx.timecode import frame_time, parse_timecode  # noqa: E402
from tlx import validate  # noqa: E402

R24 = contracts.R24
R25 = contracts.R25
ORIGIN = contracts.ORIGIN
SOURCES = os.path.join(ROOT, "test_project", "sources")


class CapabilityManifestTests(unittest.TestCase):
    def test_every_capability_has_an_executable_sample(self):
        for profile in capabilities.PROFILES.values():
            outcomes = contracts.outcomes_jsonable(contracts.run_all(profile))
            manifest = capabilities.manifest(profile, outcomes)
            self.assertEqual(
                manifest["uncovered_capabilities"], [],
                f"{profile.profile_id} has capabilities with no sample: "
                f"{manifest['uncovered_capabilities']}",
            )

    def test_tl_json_is_reversible_for_every_sample(self):
        for outcome in contracts.run_all(capabilities.TL_JSON_PROFILE):
            self.assertEqual(
                outcome.outcome, "reversible",
                f"{outcome.sample}: {outcome.to_jsonable()}",
            )
            self.assertTrue(outcome.pixel_match)
            self.assertTrue(outcome.edit_structure_faithful)

    def test_cmx_outcomes_are_measured_not_assumed(self):
        outcomes = {o.sample: o for o in contracts.run_all(capabilities.CMX_PROFILE)}
        # Push has no CMX equivalent: same picture, edit no longer editable.
        self.assertEqual(outcomes["push"].outcome, "flattened")
        self.assertTrue(outcomes["push"].pixel_match)
        self.assertFalse(outcomes["push"].edit_structure_faithful)
        self.assertEqual(outcomes["push"].first_loss_record_timecode,
                         "00:00:00:20")
        # A fade re-reads as a dissolve: picture survives, semantic does not.
        self.assertEqual(outcomes["fade"].outcome, "flattened")
        # Reverse cannot be expressed and the picture changes.
        self.assertEqual(outcomes["reverse"].outcome, "rejected")
        # A gap is reported but reconstructed from record-time jumps.
        self.assertEqual(outcomes["gap"].outcome, "reversible")
        # The second video track is a genuine rejection.
        self.assertEqual(outcomes["multi_track"].outcome, "rejected")

    def test_narrowing_only_changes_the_affected_samples(self):
        diff = contracts.compare_profiles(
            capabilities.CMX_PROFILE, capabilities.NARROW_WIPE_PROFILE
        )
        self.assertEqual(diff["removed_capabilities"], ["transition:wipe"])
        self.assertEqual([c["sample"] for c in diff["changed_samples"]], ["wipe"])
        row = diff["changed_samples"][0]
        self.assertEqual(row["before_outcome"], "reversible")
        self.assertEqual(row["after_outcome"], "flattened")
        # The first lost edit-semantics position is named, not just a count.
        self.assertEqual(row["first_loss_record_timecode"], "00:00:00:20")
        self.assertIsNotNone(row["first_loss"])
        self.assertTrue(diff["only_affected_samples_changed"])

    def test_claimed_support_matches_measured_outcome(self):
        # "supported" must never disagree with what the samples measured.
        for profile in capabilities.PROFILES.values():
            outcomes = {o.sample: o for o in contracts.run_all(profile)}
            for sample in contracts.SAMPLES:
                cap = sample.capabilities[0]
                measured = outcomes[sample.sample_id].outcome
                if profile.supports(cap):
                    self.assertEqual(
                        measured, "reversible",
                        f"{profile.profile_id} claims {cap} but sample "
                        f"{sample.sample_id} measured {measured}",
                    )
                elif cap != "gap:explicit":
                    # A gap is reported as inexpressible by the loss report but
                    # is reconstructed from the record-time jump, so it can
                    # still measure reversible.  Every other disclaimed
                    # capability must actually degrade.
                    self.assertNotEqual(
                        measured, "reversible",
                        f"{profile.profile_id} disclaims {cap} but sample "
                        f"{sample.sample_id} measured reversible",
                    )

    def test_narrow_gap_profile_changes_no_sample(self):
        # Dropping explicit gaps does not change any measured outcome: CMX
        # already lacks gaps in the full profile, so the samples are identical.
        diff = contracts.compare_profiles(
            capabilities.CMX_PROFILE, capabilities.NARROW_GAP_PROFILE
        )
        self.assertEqual(diff["changed_count"], 0)


class FidelitySplitTests(unittest.TestCase):
    def test_flattened_project_matches_pixels_but_not_structure(self):
        original = contracts.baked_stand_in()
        editable = contracts.baked_stand_in()
        editable.tracks[0].clips[1].baked = False
        editable.sources["BAKED"] = contracts._src(contracts.A)
        editable.sources["BAKED"].source_id = "CAM_A"
        editable.tracks[0].clips[1].source_id = "CAM_A"
        editable.sources["CAM_A"] = editable.sources.pop("BAKED")
        original.sources["BAKED"].content_id = "sha256:aaaa"
        for tl in (original, editable):
            c2 = tl.tracks[0].clips[1]
            c2.source_in = frame_time(0, R24)
            c2.source_out = frame_time(20, R24)

        report = fidelity.compare(editable, original)
        self.assertTrue(report.pixel_match,
                        [d.to_jsonable() for d in report.frame_deltas])
        self.assertFalse(report.edit_structure_faithful)
        payload = report.to_jsonable()
        self.assertTrue(payload["separate_verdicts"])
        self.assertIn("identical pixels", " ".join(payload["notes"]))

    def test_differently_named_but_identical_media_is_a_pixel_match(self):
        # Content id is what identifies pixels; the display name is not.
        a = contracts.baked_stand_in()
        b = contracts.baked_stand_in()
        b.sources["BAKED"].content_id = a.sources["BAKED"].content_id
        report = fidelity.compare(a, b)
        self.assertTrue(report.pixel_match)

    def test_structure_delta_names_the_position(self):
        original = contracts.baked_stand_in()
        baked = contracts.baked_stand_in()
        baked.tracks[0].clips[1].baked = True
        original.tracks[0].clips[1].baked = False
        original.sources["BAKED"] = contracts._src(contracts.A)
        original.tracks[0].clips[1].source_id = "CAM_A"
        report = fidelity.compare(original, baked)
        self.assertFalse(report.edit_structure_faithful)
        delta = report.structure_deltas[0]
        self.assertEqual(delta.kind, "cut_changed")
        self.assertTrue(delta.record_timecode)


class TraceTests(unittest.TestCase):
    def test_any_output_frame_resolves_back_to_a_source(self):
        tl = scenarios.three_capability_timeline()
        count = preview.frame_index_at(tl.duration - tl.start_time, tl.rate)
        for i in range(count):
            t = ORIGIN + frame_time(i, tl.rate)
            res = trace.resolve(tl, t)
            if res.kind in ("gap", "black"):
                continue
            self.assertTrue(res.sources, f"frame {i} has no source")
            for s in res.sources:
                self.assertGreaterEqual(s.source_frame_index, 0)

    def test_reverse_clip_descends_on_source_clock(self):
        tl = scenarios.three_capability_timeline()
        first = trace.resolve(tl, ORIGIN).sources[0]
        later = trace.resolve(tl, ORIGIN + frame_time(3, tl.rate)).sources[0]
        self.assertGreater(first.source_frame_index, later.source_frame_index)
        self.assertTrue(later.reverse)

    def test_live_transition_returns_two_ranges(self):
        tl = scenarios.three_capability_timeline()
        res = trace.resolve(tl, ORIGIN + frame_time(25, tl.rate))
        self.assertEqual(res.kind, "transition_in")
        self.assertEqual(res.needs, "reconnect_two_ranges")
        roles = {s.role for s in res.sources}
        self.assertEqual(roles, {"outgoing", "incoming"})

    def test_overlapping_track_wins_but_transition_stays_addressable(self):
        tl = scenarios.three_capability_timeline()
        # Frame 27: the V2 overlay wins the picture ...
        overlay = trace.resolve(tl, ORIGIN + frame_time(27, tl.rate))
        self.assertEqual(overlay.clip_id, "O1")
        # ... while frame 25 inside the transition still returns both ranges.
        trans = trace.resolve(tl, ORIGIN + frame_time(25, tl.rate))
        self.assertEqual(trans.clip_id, "C3")

    def test_cross_rate_cut_resolves_on_source_clock(self):
        tl = scenarios.three_capability_timeline()
        res = trace.resolve(tl, ORIGIN + frame_time(12, tl.rate))
        src = res.sources[0]
        self.assertEqual(src.source_id, "CAM_C")
        self.assertEqual(src.rate, str(contracts.R30))
        # Source frame index is computed at 29.97, not the record rate.
        self.assertEqual(src.source_frame_index, 20)

    def test_baked_clip_requests_baked_media(self):
        tl = contracts.baked_stand_in()
        res = trace.resolve(tl, ORIGIN + frame_time(25, tl.rate))
        self.assertEqual(res.kind, "baked")
        self.assertEqual(res.needs, "baked_media")
        self.assertEqual([s.role for s in res.sources], ["baked"])


class RelinkTests(unittest.TestCase):
    def _real_assets(self):
        return {
            sid: MediaAsset.from_bundle(
                os.path.join(SOURCES, bundle), ref
            )
            for sid, bundle, ref in (
                ("CAM_A", "CAM_A", "media:cam-a"),
                ("CAM_B", "CAM_B", "media:cam-b"),
                ("CAM_C", "CAM_C", "media:cam-c"),
            )
        }

    def test_transition_without_baked_requests_two_ranges(self):
        tl = scenarios.three_capability_timeline()
        decisions = {d.clip_id: d for d in relink.plan(tl)}
        c3 = decisions["C3"]
        self.assertEqual(c3.request, "reconnect_two_ranges")
        self.assertEqual({r.role for r in c3.ranges}, {"outgoing", "incoming"})

    def test_baked_transition_requests_baked_media(self):
        tl = contracts.baked_stand_in()
        decisions = {d.clip_id: d for d in relink.plan(tl)}
        self.assertEqual(decisions["C2"].request, "baked_media")
        self.assertEqual([r.role for r in decisions["C2"].ranges], ["baked"])

    def test_same_name_different_timecode_is_rejected(self):
        tl = scenarios.three_capability_timeline()
        real = self._real_assets()
        for sid, asset in real.items():
            src = tl.sources[sid]
            src.media_ref = asset.media_ref
            src.content_id = asset.content_id
            src.timecode_origin = asset.timecode_origin
            src.path = None
        alt = MediaAsset.from_bundle(
            os.path.join(SOURCES, "CAM_B_ALT"), "media:cam-b-alt",
            display_name="CAM_B")
        result = relink.relink(tl, [
            relink.MediaCandidate(
                path=alt.path, media_ref=alt.media_ref,
                content_id=alt.content_id, reel="CAM_B",
                timecode_origin=alt.timecode_origin,
            )
        ] + [
            relink.MediaCandidate(
                path=a.path, media_ref=a.media_ref, content_id=a.content_id,
                reel=os.path.basename(a.path), timecode_origin=a.timecode_origin,
            )
            for a in real.values()
        ])
        rejected_paths = {r["candidate_path"] for r in result.rejected}
        self.assertIn(alt.path, rejected_paths)
        accepted_bundles = {
            os.path.basename(a["candidate_path"]) for a in result.accepted
        }
        self.assertEqual(accepted_bundles, {"CAM_A", "CAM_B", "CAM_C"})

    def test_same_duration_baked_standin_is_rejected(self):
        # CAM_A_BAKED is pixel-identical to CAM_A but carries a different
        # media_ref; duration and picture alone must not be enough.
        a = MediaAsset.from_bundle(os.path.join(SOURCES, "CAM_A"), "media:cam-a")
        baked = MediaAsset.from_bundle(
            os.path.join(SOURCES, "CAM_A_BAKED"), "media:cam-a-baked")
        self.assertEqual(a.content_id, baked.content_id)  # pixel-identical
        self.assertEqual(a.duration, baked.duration)     # same duration
        tl = contracts.baked_stand_in()
        tl.sources["BAKED"].media_ref = "media:cam-a"
        tl.sources["BAKED"].content_id = a.content_id
        tl.sources["BAKED"].path = None
        result = relink.relink(tl, [
            relink.MediaCandidate(
                path=baked.path, media_ref=baked.media_ref,
                content_id=baked.content_id, reel="CAM_A_BAKED",
                timecode_origin=baked.timecode_origin,
            )
        ])
        # No candidate can satisfy media:cam-a, so nothing is accepted for it.
        self.assertEqual(result.accepted, [])


class ValidatorTests(unittest.TestCase):
    def _catalog(self):
        return MediaCatalog.from_directories([SOURCES])

    def test_project_sources_resolve_by_ref(self):
        with open(os.path.join(ROOT, "test_project", "project.tl.json"),
                  encoding="utf-8") as fh:
            tl = tl_json.import_text(fh.read())
        for sid, ref in (("CAM_A", "media:cam-a"), ("CAM_B", "media:cam-b"),
                         ("CAM_C", "media:cam-c")):
            tl.sources[sid].media_ref = ref
        result = validate.validate(tl, self._catalog())
        self.assertTrue(result.ok, [i.to_jsonable(tl) for i in result.issues])

    def test_same_name_different_timecode_requires_human(self):
        # Two projects: same display name CAM_A, different source timecode.
        left, right = contracts.shared_name_projects()
        catalog = MediaCatalog()
        for project in (left, right):
            src = project.sources["CAM_A"]
            catalog.add(MediaAsset(
                path=src.path, media_ref=src.media_ref,
                content_id=src.content_id, rate=src.rate,
                duration=src.duration, timecode_origin=src.timecode_origin,
                display_name="CAM_A",
            ))
        # Both resolve correctly because the ref is unique.
        for project in (left, right):
            self.assertTrue(validate.validate(project, catalog).ok)

        # Cross the catalogs: each project now only has the other's media.
        crossed_left = MediaCatalog()
        right_src = right.sources["CAM_A"]
        crossed_left.add(MediaAsset(
            path=right_src.path, media_ref=right_src.media_ref,
            content_id=right_src.content_id, rate=right_src.rate,
            duration=right_src.duration,
            timecode_origin=right_src.timecode_origin, display_name="CAM_A",
        ))
        result = validate.validate(left, crossed_left)
        self.assertFalse(result.ok)
        issue = result.needs_human[0]
        self.assertEqual(issue.clip_id, "C1")
        self.assertEqual(issue.kind, "unresolved_ref")
        # It names the name-only candidate rather than silently using it.
        self.assertIsNotNone(issue.name_only_candidate)

    def test_same_ref_different_timecode_is_flagged(self):
        left, _right = contracts.shared_name_projects()
        src = left.sources["CAM_A"]
        catalog = MediaCatalog()
        catalog.add(MediaAsset(
            path=src.path, media_ref=src.media_ref, content_id=src.content_id,
            rate=src.rate, duration=src.duration,
            timecode_origin=parse_timecode("02:00:00:00", R24),
            display_name="CAM_A",
        ))
        result = validate.validate(left, catalog)
        self.assertFalse(result.ok)
        kinds = {i.kind for i in result.needs_human}
        self.assertIn("timecode_mismatch", kinds)
        # Pixel matching is reported separately from the structural problem.
        issue = next(i for i in result.needs_human
                     if i.kind == "timecode_mismatch")
        self.assertTrue(issue.pixel_match)


class RandomOrderStabilityTests(unittest.TestCase):
    def test_shared_name_swap_is_order_independent(self):
        for seed in range(8):
            run = scenarios.run_scenario("shared_name_swap", seed)
            self.assertTrue(run.passed, run.result)
            # Both projects validate by ref regardless of rebuild order.
            for name, payload in run.result["validation"].items():
                self.assertTrue(payload["ok"], (seed, name, payload))
            # Name-only lookup is genuinely ambiguous.
            for name, payload in run.result["name_only_lookup"].items():
                self.assertTrue(payload["ambiguous"], (seed, name, payload))

    def test_relink_disk_media_is_order_independent(self):
        for seed in range(6):
            run = scenarios.run_scenario("relink_disk_media", seed)
            self.assertTrue(run.passed, (seed, run.result))
            self.assertEqual(
                run.result["accepted_bundles"], ["CAM_A", "CAM_B", "CAM_C"]
            )


class ArtifactTests(unittest.TestCase):
    def test_generated_artifacts_are_current(self):
        path = os.path.join(ROOT, "test_project", "contracts",
                            "capability_outcomes.json")
        self.assertTrue(os.path.exists(path), "run scripts/run_contracts.py")
        import json
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for profile_id, payload in data["profiles"].items():
            self.assertEqual(payload["manifest"]["uncovered_capabilities"], [],
                             profile_id)
            for row in payload["samples"]:
                self.assertIn(row["outcome"],
                              ("reversible", "flattened", "rejected"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
