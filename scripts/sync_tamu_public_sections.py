#!/usr/bin/env python
"""Incrementally refresh the active Howdy term without rebuilding embeddings.

Run this from a Linux systemd timer, cron, or Windows Task Scheduler. With
DATABASE_URL set, PostgreSQL is updated first and the CSV files are not
touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.main import refresh_and_sync_current_sections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--term",
        help="Howdy term code; defaults to the newest active PostgreSQL term.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report changes without replacing snapshots or updating ParadeDB.",
    )
    parser.add_argument(
        "--show_changed",
        action="store_true",
        help="Include each changed section plus added, removed, and open/closed-status categories in the JSON output.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the complete JSON report to this text file as well as stdout.",
    )
    parser.add_argument(
        "--refresh-all-restrictions",
        action="store_true",
        help=(
            "Re-fetch restrictions for every active CRN. Without this flag, "
            "only new or changed sections are checked."
        ),
    )
    parser.add_argument(
        "--restriction-workers",
        type=int,
        default=12,
        help="Concurrent Howdy restriction workers (default: 12).",
    )
    args = parser.parse_args()

    report = refresh_and_sync_current_sections(
        dry_run=args.dry_run,
        show_changed=args.show_changed,
        term_code=args.term,
        refresh_all_restrictions=args.refresh_all_restrictions,
        restriction_workers=args.restriction_workers,
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{output}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
