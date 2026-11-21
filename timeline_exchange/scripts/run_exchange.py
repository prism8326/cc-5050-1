"""Command-line entry point for a real import/export/re-import exchange.

Usage:
  python3 -I scripts/run_exchange.py --in test_project/project.tl.json \
      --from tl-json --to cmx3600 --rate 24000/1001 \
      --media-dir test_project --out /tmp/out.edl --report /tmp/loss.json

The command performs the full kernel pipeline (parse, convert, export,
re-import, per-record-frame verification) and prints a summary. It never treats
"file written" as success; the frame comparison and loss report are the result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tlx import (  # noqa: E402
    exchange, tl_json, capabilities, contracts, relink, trace, validate,
)
from tlx.media import MediaCatalog  # noqa: E402
from tlx.timecode import FrameRate, frame_time, frame_index_at  # noqa: E402


def _load_source_registry(path: str | None):
    """Resolve reel names to real sources via a reference TL-JSON project."""
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return dict(tl_json.import_text(fh.read()).sources)


def main() -> int:
    ap = argparse.ArgumentParser(description="Clip timeline exchange runner")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--from", dest="src", required=True, choices=exchange.FORMATS)
    ap.add_argument("--to", dest="tgt", required=True, choices=exchange.FORMATS)
    ap.add_argument("--rate", default=None, help="record rate, required for EDL input")
    ap.add_argument("--media-dir", default=None)
    ap.add_argument(
        "--source-registry", default=None,
        help="reference TL-JSON project resolving EDL reel names to real sources",
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument(
        "--target-profile", default=None,
        choices=sorted(capabilities.PROFILES),
        help="narrow the target capability set (e.g. cmx3600-narrow-wipe)",
    )
    ap.add_argument("--contracts", action="store_true",
                    help="also print per-capability contract outcomes")
    args = ap.parse_args()

    with open(args.inp, encoding="utf-8") as fh:
        text = fh.read()
    rate = FrameRate.from_value(args.rate) if args.rate else None
    sources = _load_source_registry(args.source_registry)

    result = exchange.run_exchange(
        text, args.src, args.tgt, rate=rate, sources=sources,
        media_base_dir=args.media_dir, profile_id=args.target_profile,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(result.exported_text)

    summary = result.verify_summary()
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)

    print(f"exchange {args.src} -> {args.tgt}")
    print(f"  exported: {args.out}")
    print(f"  record frames compared: {summary['compared_frames']}")
    print(f"  frame mismatches: {summary['mismatch_count']}")
    print(f"  loss entries: {summary['loss']['entry_count']} "
          f"(errors={sum(1 for e in summary['loss']['entries'] if e['severity'] == 'error')})")
    print(f"  missing media: {summary['missing_sources'] or 'none'}")
    first = summary.get("first_loss_position")
    if first:
        print(f"  first lost edit semantics: {first['timecode']} "
              f"({first['code']} on {first['object_id']})")
    if args.report:
        print(f"  verification report: {args.report}")
    if args.contracts:
        profile = capabilities.get_profile(
            args.target_profile, args.tgt
        )
        for outcome in contracts.run_all(profile):
            print(f"  contract {outcome.sample:<12} {outcome.outcome:<11} "
                  f"pixel={outcome.pixel_match} structure="
                  f"{outcome.edit_structure_faithful} "
                  f"first={outcome.first_loss_record_timecode}")
    # Non-zero exit only when a loss is an error, so CI can gate strict targets.
    return 2 if result.loss_report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
