#!/usr/bin/env python
"""Measure the deployed ParadeDB search path with a small fixed query suite."""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app import main as app


DEFAULT_QUERIES = ("accounting", "ACCT 200", "calculus", "computer science")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", help="Query to benchmark; repeat to provide multiple queries.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--location", action="append", default=[])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--disable-gc", action="store_true")
    parser.add_argument("--freeze-gc", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "artifacts" / "search-benchmark.json")
    args = parser.parse_args()
    if args.limit < 1:
        raise ValueError("--limit must be positive.")
    if args.repetitions < 1:
        raise ValueError("--repetitions must be positive.")

    queries = args.query or list(DEFAULT_QUERIES)
    rows = []
    if args.disable_gc:
        gc.disable()
    with TestClient(app.app) as client:
        health = client.get("/health")
        health.raise_for_status()
        if args.freeze_gc:
            warmup = client.get("/search", params={
                "q": queries[0],
                "rank": "relevance",
                "limit": args.limit,
                "term": args.term,
                "location": args.location,
            })
            warmup.raise_for_status()
            gc.collect()
            gc.freeze()
        for repetition in range(1, args.repetitions + 1):
            for query in queries:
                started = time.perf_counter()
                response = client.get("/search", params={
                    "q": query,
                    "rank": "relevance",
                    "limit": args.limit,
                    "term": args.term,
                    "location": args.location,
                })
                wall_seconds = time.perf_counter() - started
                response.raise_for_status()
                payload = response.json()
                rows.append(
                    {
                        "query": query,
                        "repetition": repetition,
                        "wall_seconds": wall_seconds,
                        "result_count": len(payload["results"]),
                        "timings": payload["timings"],
                    }
                )

    wall_seconds = [row["wall_seconds"] for row in rows]
    report = {
        "search_backend": "paradedb",
        "rank": "relevance",
        "query_count": len(rows),
        "repetitions": args.repetitions,
        "health": health.json(),
        "wall_seconds": {
            "mean": statistics.fmean(wall_seconds),
            "p50": percentile(wall_seconds, 0.50),
            "p95": percentile(wall_seconds, 0.95),
        },
        "queries": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["wall_seconds"], indent=2))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
