"""Benchmark filtered ParadeDB semantic retrieval without HTTP overhead."""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app import main, paradedb, postgres


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--location", action="append", default=[])
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--runs", type=int, default=20)
    return parser.parse_args()


def main_cli() -> None:
    args = parse_args()
    vector = main.embed_query(args.query)
    if vector is None:
        raise RuntimeError("The semantic embedding model is unavailable.")
    filters = postgres.ActiveSectionFilterSpec(
        filter_terms=args.term,
        locations=args.location,
        instruction_types=[],
        core_attributes=[],
        graduation_requirements=[],
        attributes=[],
        major_aliases=[],
        department_aliases=[],
        college_aliases=[],
    )
    section_filters = filters if args.term or args.location else None
    semantic_call = lambda: paradedb.semantic_hits(
        vector,
        args.limit,
        lambda _: True,
        section_filters=section_filters,
    )
    semantic_call()
    samples = [semantic_call()[1] * 1000 for _ in range(args.runs)]
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    print(f"median_ms={statistics.median(samples):.1f}")
    print(f"p95_ms={ordered[p95_index]:.1f}")
    print(f"min_ms={min(samples):.1f}")
    print(f"max_ms={max(samples):.1f}")
    print("samples_ms=", [round(value, 1) for value in samples])
    postgres.close_pool()


if __name__ == "__main__":
    main_cli()
