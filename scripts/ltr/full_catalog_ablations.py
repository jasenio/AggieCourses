#!/usr/bin/env python
"""Run six LTR/retrieval ablations over the complete course catalog.

Run this inside the app container (or another environment with the ParadeDB
database, cached models, and requirements-ltr installed):

    python scripts/ltr/full_catalog_ablations.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from backend.app import main as app  # noqa: E402
from backend.app import postgres  # noqa: E402
from backend.app.ltr_features import FEATURE_NAMES, features_for_course  # noqa: E402
from train_ltr_model import read_training_annotations  # noqa: E402


DEFAULT_ANNOTATIONS = ROOT_DIR / "artifacts" / "human-ltr-annotations-2026-07-26.json"
DEFAULT_OUTPUT = ROOT_DIR / "artifacts" / "ltr" / "full-catalog-ablation-results.json"
DEFAULT_SPLIT = ROOT_DIR / "artifacts" / "ltr" / "full-catalog-query-split.json"
DEFAULT_CACHE = ROOT_DIR / "artifacts" / "ltr" / "full-catalog-cache"
TEST_QUERY_COUNT = 37
METRICS = ("ndcg@10", "mrr@25", "map@25")
RRF_RANK_CONSTANT = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--split-file", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--test-queries", type=int, default=TEST_QUERY_COUNT)
    parser.add_argument("--cross-encoder-candidates", type=int, default=250)
    parser.add_argument("--n-estimators", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--relevance-threshold", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--force-recompute", action="store_true")
    return parser.parse_args()


def split_queries(query_ids: list[str], seed: int, test_count: int) -> tuple[list[str], list[str]]:
    if len(query_ids) <= test_count:
        raise RuntimeError("There must be more queries than the requested held-out test count.")
    shuffled = list(query_ids)
    random.Random(seed).shuffle(shuffled)
    return sorted(shuffled[test_count:]), sorted(shuffled[:test_count])


def ranks(hits: list[dict[str, Any]]) -> dict[str, int]:
    return {str(hit.get("_id") or ""): rank for rank, hit in enumerate(hits, 1) if hit.get("_id")}


def rrf_rank(lexical: list[dict[str, Any]], semantic: list[dict[str, Any]]) -> list[str]:
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    candidates: set[str] = set()
    sequence = 0
    for hits in (lexical, semantic):
        for rank, hit in enumerate(hits, 1):
            course_id = str(hit.get("_id") or "")
            if not course_id:
                continue
            candidates.add(course_id)
            first_seen.setdefault(course_id, sequence)
            sequence += 1
            scores[course_id] = scores.get(course_id, 0.0) + 1.0 / (RRF_RANK_CONSTANT + rank)
    return sorted(candidates, key=lambda course_id: (-scores[course_id], first_seen[course_id], course_id))


def cross_rank(query: str, rrf_ids: list[str], by_id: dict[str, dict[str, Any]], candidates: int) -> list[str]:
    head = rrf_ids[:candidates]
    if not head:
        return rrf_ids
    model = app.get_cross_encoder_model()
    pairs = [(query, "\n".join(app.course_text_fields(by_id[course_id]))) for course_id in head]
    scores = model.predict(pairs, batch_size=64, show_progress_bar=False)
    reranked = [course_id for course_id, _ in sorted(zip(head, scores), key=lambda pair: -float(pair[1]))]
    return reranked + rrf_ids[candidates:]


def metric_values(labels_by_id: dict[str, int], ranking: list[str], threshold: int) -> dict[str, float | None]:
    labels = [labels_by_id.get(course_id, 0) for course_id in ranking if course_id in labels_by_id]
    relevant = sum(label >= threshold for label in labels)
    if not relevant:
        return {metric: None for metric in METRICS}
    top10 = labels[:10]
    ideal = sorted(labels, reverse=True)[:10]

    def dcg(values: list[int]) -> float:
        return sum((2**value - 1) / np.log2(index + 2) for index, value in enumerate(values))

    first = next((index + 1 for index, label in enumerate(labels[:25]) if label >= threshold), None)
    ap_values = [
        sum(sum(label >= threshold for label in labels[:index]) / index for index, label in enumerate(labels[:25], 1) if label >= threshold)
        / relevant
    ]
    return {
        "ndcg@10": dcg(top10) / dcg(ideal) if dcg(ideal) else 0.0,
        "mrr@25": 1.0 / first if first else 0.0,
        "map@25": ap_values[0],
    }


def mean_metric(rows: list[dict[str, float | None]], metric: str) -> float | None:
    values = [float(row[metric]) for row in rows if row.get(metric) is not None]
    return round(sum(values) / len(values), 6) if values else None


def train_ltr(
    annotations: dict[str, list[Any]],
    train_ids: list[str],
    *,
    n_estimators: int,
    learning_rate: float,
    max_depth: int,
) -> Any:
    try:
        import xgboost
    except ImportError as exc:
        raise RuntimeError("Install project dependencies with: pip install -r requirements.txt") from exc
    rows = [course for query_id in train_ids for course in annotations[query_id]]
    matrix = np.asarray([[course.features.get(name, 0.0) for name in FEATURE_NAMES] for course in rows], dtype=np.float32)
    labels = np.asarray([course.relevance for course in rows], dtype=np.int32)
    groups = [len(annotations[query_id]) for query_id in train_ids]
    model = xgboost.XGBRanker(
        objective="rank:ndcg",
        eval_metric="ndcg@10",
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=max_depth,
        random_state=20260726,
        n_jobs=-1,
        verbosity=0,
    )
    model.fit(matrix, labels, group=groups, verbose=False)
    return model


def main(args: argparse.Namespace) -> None:
    annotations = read_training_annotations(args.annotations)
    train_ids, test_ids = split_queries(sorted(annotations), args.seed, args.test_queries)
    args.split_file.parent.mkdir(parents=True, exist_ok=True)
    args.split_file.write_text(
        json.dumps({"seed": args.seed, "train_query_ids": train_ids, "test_query_ids": test_ids}, indent=2),
        encoding="utf-8",
    )
    model = train_ltr(
        annotations,
        train_ids,
        n_estimators=args.n_estimators,
        learning_rate=args.learning_rate,
        max_depth=args.max_depth,
    )
    postgres.open_pool()
    try:
        raw_rows = json.loads(args.annotations.read_text(encoding="utf-8"))
        query_text_by_id = {
            str(row.get("query_id")): str(row.get("query") or "")
            for row in raw_rows
            if isinstance(row, dict)
        }
        catalog_hits, _ = app.paradedb.catalog_hits(lambda _: True)
        by_id = {str(hit["_id"]): dict(hit["_source"]) for hit in catalog_hits if hit.get("_id")}
        course_ids = sorted(by_id)
        statistics = postgres.catalog_rank_statistics(course_ids)
        document_count = len(course_ids)
        if document_count < 10000:
            raise RuntimeError(f"Expected the full catalog (about 11,000 courses), found {document_count}.")
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        all_results: dict[str, list[dict[str, Any]]] = {name: [] for name in ("bm25", "semantic", "rrf", "rrf_cross", "rrf_ltr", "rrf_cross_ltr")}
        started = time.perf_counter()
        for index, query_id in enumerate(test_ids, 1):
            query = query_text_by_id.get(query_id, "")
            if not query:
                raise RuntimeError(f"Annotation export is missing query text for {query_id}.")
            cache_file = args.cache_dir / f"{query_id}.json"
            if cache_file.exists() and not args.force_recompute:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                lexical_hits, _ = app.paradedb.lexical_hits(query, document_count, lambda _: True)
                vector = app.embed_query(query)
                semantic_hits, _ = (
                    app.paradedb.semantic_hits(vector, document_count, lambda _: True)
                    if vector is not None else ([], 0.0)
                )
                lexical_ids = [str(hit["_id"]) for hit in lexical_hits]
                semantic_ids = [str(hit["_id"]) for hit in semantic_hits]
                rrf_ids = rrf_rank(lexical_hits, semantic_hits)
                cross_ids = cross_rank(query, rrf_ids, by_id, args.cross_encoder_candidates)
                payload = {"query_id": query_id, "query": query, "bm25": lexical_ids, "semantic": semantic_ids, "rrf": rrf_ids, "rrf_cross": cross_ids}
                cache_file.write_text(json.dumps(payload), encoding="utf-8")
            rank_maps = {name: {course_id: rank for rank, course_id in enumerate(ids, 1)} for name, ids in payload.items() if name in ("bm25", "semantic", "rrf", "rrf_cross")}
            feature_matrix = np.asarray(
                [
                    [
                        *features_for_course(
                            payload["query"],
                            by_id[course_id],
                            bm25_rank=rank_maps["bm25"].get(course_id),
                            semantic_rank=rank_maps["semantic"].get(course_id),
                            current_section_count=statistics.get(course_id, {}).get("current_section_count", 0),
                            historical_enrollment=statistics.get(course_id, {}).get("historical_enrollment", 0),
                        ).values()
                    ]
                    for course_id in course_ids
                ],
                dtype=np.float32,
            )
            ltr_scores = model.predict(feature_matrix)
            ltr_order = [course_id for course_id, _ in sorted(zip(course_ids, ltr_scores), key=lambda pair: -float(pair[1]))]
            cross_set = set(payload["rrf_cross"][: args.cross_encoder_candidates])
            cross_ltr_order = sorted(cross_set, key=lambda course_id: -float(ltr_scores[course_ids.index(course_id)]))
            cross_ltr_order += [course_id for course_id in payload["rrf_cross"] if course_id not in cross_set]
            rankings = {
                "bm25": payload["bm25"],
                "semantic": payload["semantic"],
                "rrf": payload["rrf"],
                "rrf_cross": payload["rrf_cross"],
                "rrf_ltr": ltr_order,
                "rrf_cross_ltr": cross_ltr_order,
            }
            labels = {course.course_id: course.relevance for course in annotations[query_id]}
            for name, ranking in rankings.items():
                values = metric_values(labels, ranking, args.relevance_threshold)
                all_results[name].append({"query_id": query_id, **values})
            print(f"[{index}/{len(test_ids)}] {query_id} evaluated ({time.perf_counter() - started:.1f}s)", flush=True)
        summary = []
        for name, rows in all_results.items():
            summary.append({"ranker": name, "queries": len(rows), **{metric: mean_metric(rows, metric) for metric in METRICS}})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "schema_version": "full-catalog-ltr-ablations-v1",
                    "created_at": datetime.now(UTC).isoformat(),
                    "annotations": str(args.annotations),
                    "catalog_courses": document_count,
                    "train_queries": len(train_ids),
                    "test_queries": len(test_ids),
                    "metrics": list(METRICS),
                    "relevance_threshold": args.relevance_threshold,
                    "cross_encoder_candidates": args.cross_encoder_candidates,
                    "summary": summary,
                    "per_query": all_results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps(summary, indent=2))
    finally:
        postgres.close_pool()


if __name__ == "__main__":
    main(parse_args())
