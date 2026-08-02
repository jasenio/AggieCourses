#!/usr/bin/env python
"""Evaluate candidate rankings against LLM relevance labels.

By default this compares the saved annotation-pool order with rankings induced
by the logged BM25, dense, and combined RRF features. For a trained reranker,
pass a CSV containing query_id, course_id, and score.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ANNOTATIONS_FILE = ROOT_DIR / "artifacts" / "human-ltr-annotations.json"
DEFAULT_CUTOFFS = (1, 3, 5, 10, 20, 25)


@dataclass(frozen=True)
class JudgedCourse:
    query_id: str
    course_id: str
    pool_rank: int
    relevance: int
    features: dict[str, float]


def parse_cutoffs(value: str) -> tuple[int, ...]:
    try:
        cutoffs = tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Cutoffs must be comma-separated positive integers.") from exc
    if not cutoffs or any(cutoff < 1 for cutoff in cutoffs):
        raise argparse.ArgumentTypeError("Cutoffs must be comma-separated positive integers.")
    return cutoffs


def read_annotations(path: Path) -> dict[str, list[JudgedCourse]]:
    if not path.exists():
        raise RuntimeError(f"Annotation file does not exist: {path}")
    if path.suffix.lower() == ".json":
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Annotation file is not valid JSON: {path}") from exc
        if not isinstance(rows, list):
            raise RuntimeError("JSON annotation files must contain a flat array of judgments.")
        by_query: dict[str, list[JudgedCourse]] = {}
        seen: set[tuple[str, str]] = set()
        for row_number, row in enumerate(rows, 1):
            document = row.get("document") if isinstance(row, dict) else None
            query_id = str(row.get("query_id") or "").strip() if isinstance(row, dict) else ""
            course_id = str((document or {}).get("course_id") or "").strip()
            relevance = row.get("relevance") if isinstance(row, dict) else None
            raw_features = row.get("features") if isinstance(row, dict) else None
            if (
                not query_id
                or not course_id
                or isinstance(relevance, bool)
                or relevance not in range(4)
                or not isinstance(raw_features, dict)
            ):
                raise RuntimeError(f"Invalid flat annotation at {path}:{row_number}.")
            if (query_id, course_id) in seen:
                continue
            seen.add((query_id, course_id))
            by_query.setdefault(query_id, []).append(
                JudgedCourse(
                    query_id=query_id,
                    course_id=course_id,
                    pool_rank=len(by_query[query_id]) + 1,
                    relevance=int(relevance),
                    features={name: float(value) for name, value in raw_features.items()},
                )
            )
        if not by_query:
            raise RuntimeError(f"No annotation records found in {path}.")
        return by_query

    by_query: dict[str, list[JudgedCourse]] = {}
    with path.open("r", encoding="utf-8") as annotations_file:
        for line_number, line in enumerate(annotations_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                query_id = str(record["query_id"])
                ratings = {str(item["course_id"]): int(item["relevance"]) for item in record["ratings"]}
                feature_rows = record.get("annotations", [])
                features_by_course = {
                    str(item.get("document", {}).get("course_id", "")): {
                        name: float(value) for name, value in (item.get("features") or {}).items()
                    }
                    for item in feature_rows
                    if item.get("document", {}).get("course_id")
                }
                courses = record["courses"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Invalid annotation record at {path}:{line_number}.") from exc

            judged: list[JudgedCourse] = []
            for position, course in enumerate(courses, start=1):
                course_id = str(course.get("course_id") or "")
                relevance = ratings.get(course_id)
                if not course_id or relevance is None:
                    raise RuntimeError(f"Missing rating for a course at {path}:{line_number}.")
                if relevance not in range(4):
                    raise RuntimeError(f"Invalid relevance label at {path}:{line_number}: {relevance}.")
                judged.append(
                    JudgedCourse(
                        query_id=query_id,
                        course_id=course_id,
                        pool_rank=int(course.get("rank") or position),
                        relevance=relevance,
                        features=features_by_course.get(course_id, {}),
                    )
                )
            by_query[query_id] = judged  # Last checkpoint wins after --overwrite.
    if not by_query:
        raise RuntimeError(f"No annotation records found in {path}.")
    return by_query


def read_external_scores(path: Path) -> dict[tuple[str, str], float]:
    scores: dict[tuple[str, str], float] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as score_file:
        for line_number, row in enumerate(csv.DictReader(score_file), start=2):
            try:
                query_id = str(row["query_id"]).strip()
                course_id = str(row["course_id"]).strip()
                score = float(row["score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Invalid score row at {path}:{line_number}; expected query_id, course_id, score.") from exc
            if not query_id or not course_id or not math.isfinite(score):
                raise RuntimeError(f"Invalid score row at {path}:{line_number}.")
            scores[(query_id, course_id)] = score
    return scores


def sorted_courses(courses: list[JudgedCourse], score: Callable[[JudgedCourse], float]) -> list[JudgedCourse]:
    return sorted(courses, key=lambda course: (-score(course), course.pool_rank, course.course_id))


def dcg(relevances: list[int]) -> float:
    return sum(((2**relevance) - 1) / math.log2(position + 1) for position, relevance in enumerate(relevances, start=1))


def query_metrics(courses: list[JudgedCourse], cutoffs: tuple[int, ...], threshold: int) -> dict[str, Any]:
    labels = [course.relevance for course in courses]
    total_relevant = sum(label >= threshold for label in labels)
    first_relevant_rank = next((position for position, label in enumerate(labels, start=1) if label >= threshold), None)
    metrics: dict[str, Any] = {"relevant_count": total_relevant, "first_relevant_rank": first_relevant_rank}
    for cutoff in cutoffs:
        top_labels = labels[:cutoff]
        ideal_dcg = dcg(sorted(labels, reverse=True)[:cutoff])
        relevant_at_k = sum(label >= threshold for label in top_labels)
        metrics[f"ndcg@{cutoff}"] = dcg(top_labels) / ideal_dcg if ideal_dcg else None
        metrics[f"precision@{cutoff}"] = relevant_at_k / min(cutoff, len(labels)) if labels else 0.0
        metrics[f"recall@{cutoff}"] = relevant_at_k / total_relevant if total_relevant else None
        metrics[f"hit_rate@{cutoff}"] = float(relevant_at_k > 0) if total_relevant else None
        metrics[f"mrr@{cutoff}"] = (
            1.0 / first_relevant_rank if first_relevant_rank is not None and first_relevant_rank <= cutoff else 0.0
        ) if total_relevant else None
        precision_sum = sum(
            sum(label >= threshold for label in labels[:position]) / position
            for position, label in enumerate(top_labels, start=1)
            if label >= threshold
        )
        metrics[f"ap@{cutoff}"] = precision_sum / total_relevant if total_relevant else None
    return metrics


def mean(values: list[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return round(sum(valid) / len(valid), 6) if valid else None


def evaluate_ranker(
    query_courses: dict[str, list[JudgedCourse]],
    ranker_name: str,
    score: Callable[[JudgedCourse], float],
    cutoffs: tuple[int, ...],
    threshold: int,
) -> dict[str, Any]:
    per_query = [query_metrics(sorted_courses(courses, score), cutoffs, threshold) for courses in query_courses.values()]
    aggregate: dict[str, Any] = {
        "ranker": ranker_name,
        "queries": len(per_query),
        "queries_with_relevant": sum(metrics["relevant_count"] > 0 for metrics in per_query),
        "relevance_threshold": threshold,
    }
    for metric in ("ndcg", "precision", "recall", "hit_rate", "mrr", "ap"):
        for cutoff in cutoffs:
            aggregate[f"{metric}@{cutoff}"] = mean([metrics.get(f"{metric}@{cutoff}") for metrics in per_query])
    aggregate["mean_first_relevant_rank"] = mean(
        [float(metrics["first_relevant_rank"]) for metrics in per_query if metrics["first_relevant_rank"] is not None]
    )
    return aggregate


def binarize_relevance(
    query_courses: dict[str, list[JudgedCourse]],
    threshold: int,
) -> dict[str, list[JudgedCourse]]:
    """Map graded labels to 0/1 while preserving every candidate and feature."""
    return {
        query_id: [replace(course, relevance=int(course.relevance >= threshold)) for course in courses]
        for query_id, courses in query_courses.items()
    }


def print_results(results: list[dict[str, Any]], cutoffs: tuple[int, ...]) -> None:
    max_cutoff = max(cutoffs)
    columns = ["ranker", "queries", *(f"ndcg@{cutoff}" for cutoff in cutoffs), f"mrr@{max_cutoff}", f"map@{max_cutoff}", "mean_first_relevant_rank"]
    print("\t".join(columns))
    for result in results:
        row: list[str] = []
        for column in columns:
            key = f"ap@{max_cutoff}" if column == f"map@{max_cutoff}" else column
            value = result.get(key)
            row.append(f"{value:.4f}" if isinstance(value, float) else str(value if value is not None else "n/a"))
        print("\t".join(row))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS_FILE)
    parser.add_argument("--cutoffs", type=parse_cutoffs, default=DEFAULT_CUTOFFS)
    parser.add_argument("--relevance-threshold", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--binary-relevance", action="store_true", help="Convert labels at the relevance threshold to 0/1 before every metric, including NDCG.")
    parser.add_argument("--scores", type=Path, help="Optional CSV with query_id,course_id,score from a trained reranker.")
    parser.add_argument("--score-name", default="external_scores", help="Label for the optional external score ranker.")
    parser.add_argument("--output", type=Path, help="Optional JSON metrics output path.")
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    query_courses = read_annotations(args.annotations)
    evaluation_threshold = args.relevance_threshold
    if args.binary_relevance:
        query_courses = binarize_relevance(query_courses, args.relevance_threshold)
        evaluation_threshold = 1
    rankers: list[tuple[str, Callable[[JudgedCourse], float]]] = [
        ("annotation_pool_order", lambda course: -float(course.pool_rank)),
        ("bm25_feature", lambda course: course.features.get("bm25_reciprocal_rank", 0.0)),
        ("dense_feature", lambda course: course.features.get("dense_reciprocal_rank", 0.0)),
        ("rrf_feature_sum", lambda course: course.features.get("bm25_reciprocal_rank", 0.0) + course.features.get("dense_reciprocal_rank", 0.0)),
    ]
    if args.scores:
        external_scores = read_external_scores(args.scores)
        rankers.append((args.score_name, lambda course: external_scores.get((course.query_id, course.course_id), float("-inf"))))
    results = [evaluate_ranker(query_courses, name, score, args.cutoffs, evaluation_threshold) for name, score in rankers]
    print_results(results, args.cutoffs)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "annotations": str(args.annotations),
                    "cutoffs": args.cutoffs,
                    "binary_relevance": args.binary_relevance,
                    "relevance_threshold": args.relevance_threshold,
                    "results": results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Saved metrics: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except RuntimeError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)
