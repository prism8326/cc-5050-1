"""Run every contract sample and scenario and freeze the measured outcomes.

Writes a single JSON artifact you can diff in review:

  test_project/contracts/capability_outcomes.json
      per profile: the capability manifest joined to the executable samples,
      with each measured outcome (reversible / flattened / rejected) and the
      first record position where edit semantics is lost.

  test_project/contracts/narrowing.json
      the wipe-narrowed profile diff: only affected samples change.

  test_project/contracts/scenarios.json
      baked stand-in, three-capability trace, shared-name rebuild, flatten vs
      editable — each with its raw evidence and a pass flag.

The command exits non-zero if any scenario fails, so this is a gate, not a
report that is allowed to go stale silently.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tlx import contracts, scenarios  # noqa: E402
from tlx import capabilities  # noqa: E402

OUT_DIR = os.path.join(ROOT, "test_project", "contracts")


def _write(name: str, obj) -> str:
    path = os.path.join(OUT_DIR, name)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
    return path


def build_outcomes() -> dict:
    per_profile = {}
    for profile_id, profile in capabilities.PROFILES.items():
        outcomes = contracts.run_all(profile)
        manifest = capabilities.manifest(
            profile, contracts.outcomes_jsonable(outcomes)
        )
        per_profile[profile_id] = {
            "manifest": manifest,
            "samples": contracts.outcomes_jsonable(outcomes),
        }
    return {"profiles": per_profile}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7,
                    help="seed for the random input order in scenarios")
    args = ap.parse_args()

    outcomes = build_outcomes()
    p1 = _write("capability_outcomes.json", outcomes)

    narrowing = contracts.compare_profiles(
        capabilities.CMX_PROFILE, capabilities.NARROW_WIPE_PROFILE
    )
    p2 = _write("narrowing.json", narrowing)

    runs = scenarios.run_all(seed=args.seed)
    scenario_doc = {
        "seed": args.seed,
        "scenarios": {r.name: r.result for r in runs},
        "failed": [r.name for r in runs if not r.passed],
    }
    p3 = _write("scenarios.json", scenario_doc)

    print("wrote", p1)
    print("wrote", p2)
    print("wrote", p3)
    for profile_id, data in outcomes["profiles"].items():
        counts = {}
        for row in data["samples"]:
            counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        print(f"  {profile_id}: {counts}")
    print(f"  narrowing wipe: {narrowing['changed_count']} sample(s) changed, "
          f"only affected={narrowing['only_affected_samples_changed']}")
    for r in runs:
        print(f"  scenario {r.name}: {'PASS' if r.passed else 'FAIL'}")
    return 0 if not scenario_doc["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
