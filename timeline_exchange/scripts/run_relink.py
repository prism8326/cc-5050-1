"""Relink a project's sources against the real media catalog and validate refs.

Usage:
  python3 -I scripts/run_relink.py --in test_project/project.tl.json \
      --catalog-dir test_project/sources --seed 7 \
      --out test_project/contracts/relink.json

The command plans every clip (baked media vs. one/two source ranges), offers
the catalog assets as candidates in a shuffled order, and reports accepted and
rejected media by identity.  It then validates every source reference by
``media_ref`` and prints the clips a human must fix.  Exit code is non-zero if
a source cannot be resolved or a reference needs human attention.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tlx import relink, tl_json, validate  # noqa: E402
from tlx.media import MediaCatalog  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=os.path.join(
        ROOT, "test_project", "project.tl.json"))
    ap.add_argument("--catalog-dir", action="append", default=None,
                    help="directory containing catalog.json (repeatable)")
    ap.add_argument("--seed", type=int, default=None,
                    help="shuffle candidate order to prove order independence")
    ap.add_argument("--out", default=None, help="write JSON evidence here")
    args = ap.parse_args()

    with open(args.inp, encoding="utf-8") as fh:
        tl = tl_json.import_text(fh.read())

    catalog_dirs = args.catalog_dir or [
        os.path.join(ROOT, "test_project", "sources"),
        os.path.join(ROOT, "test_project"),
    ]
    catalog = MediaCatalog.from_directories(catalog_dirs)
    if not catalog.assets:
        print("no catalog assets found in", catalog_dirs)
        return 1

    candidates = [
        relink.MediaCandidate(
            path=a.path, media_ref=a.media_ref, content_id=a.content_id,
            reel=a.display_name, rate=a.rate, duration=a.duration,
            timecode_origin=a.timecode_origin,
        )
        for a in catalog.assets
    ]
    if args.seed is not None:
        random.Random(args.seed).shuffle(candidates)

    relinked = relink.relink(tl, candidates)
    validation = validate.validate(tl, catalog)

    evidence = {
        "input": os.path.abspath(args.inp),
        "seed": args.seed,
        "catalog": catalog.to_jsonable(),
        "relink": relinked.to_jsonable(tl),
        "validation": validation.to_jsonable(tl),
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, indent=2, ensure_ascii=False)
        print("wrote", args.out)

    accepted = {a["source_id"] for a in relinked.accepted}
    print(f"relinked {len(accepted)} source(s); "
          f"rejected {len(relinked.rejected)} candidate(s)")
    for sid in sorted(accepted):
        print(f"  {sid}: accepted")
    if relinked.missing_requests:
        print("  unresolved:", ", ".join(relinked.missing_requests))
    if validation.ok:
        print(f"validation: all {len(validation.resolved)} reference(s) "
              "resolve by media_ref")
    else:
        print("validation: human attention required")
        for issue in validation.needs_human:
            print(f"  {issue.clip_id} {issue.source_id} "
                  f"{issue.kind}: {issue.message}")
    return 0 if validation.ok and not relinked.missing_requests else 1


if __name__ == "__main__":
    raise SystemExit(main())
