#!/usr/bin/env python
"""Train a query-grouped XGBoost learning-to-rank model from relevance annotations."""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from backend.app.ltr_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION  # noqa: E402
try:
    from .evaluate_rerankers import JudgedCourse, evaluate_ranker, read_annotations
except ImportError:  # Direct execution: python scripts/ltr/train_ltr_model.py
    from evaluate_rerankers import JudgedCourse, evaluate_ranker, read_annotations


DEFAULT_ANNOTATIONS_FILE = ROOT_DIR / "artifacts" / "human-ltr-annotations.json"
DEFAULT_MODEL_FILE = ROOT_DIR / "data" / "ltr" / "ltr-model.json"
DEFAULT_METADATA_FILE = ROOT_DIR / "data" / "ltr" / "ltr-model-metadata.json"
DEFAULT_TEST_SCORES_FILE = ROOT_DIR / "artifacts" / "ltr-test-scores.csv"
DEFAULT_CUTOFFS = (1, 3, 5, 10, 20, 25)
RANKING_OBJECTIVES = ("rank:ndcg", "rank:pairwise")
OBJECTIVE_DISPLAY_NAMES = {
    "rank:ndcg": "XGBoost NDCG ranker",
    "rank:pairwise": "XGBoost pairwise ranker",
}


def require_xgboost() -> Any:
    try:
        import xgboost
    except ImportError as exc:
        raise RuntimeError(
            "XGBoost is not installed. Install project dependencies with "
            "crs\\Scripts\\python.exe -m pip install -r requirements.txt."
        ) from exc
    return xgboost


def read_training_annotations(path: Path) -> dict[str, list[JudgedCourse]]:
    """Read either the native JSONL checkpoints or a browser-exported JSON array."""
    if path.suffix.lower() != ".json":
        return read_annotations(path)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Annotation file is not valid JSON: {path}") from exc
    if not isinstance(rows, list):
        raise RuntimeError("JSON annotation files must contain an array of rated courses.")

    by_query: dict[str, list[JudgedCourse]] = {}
    seen: set[tuple[str, str]] = set()
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise RuntimeError(f"Invalid annotation row {row_number}: expected an object.")
        query_id = str(row.get("query_id") or "").strip()
        document = row.get("document") or {}
        course_id = str(document.get("course_id") or "").strip()
        relevance = row.get("relevance")
        schema_version = str(row.get("feature_schema_version") or document.get("ltr_feature_schema_version") or "")
        if schema_version != FEATURE_SCHEMA_VERSION:
            raise RuntimeError(
                f"Invalid annotation row {row_number}: expected feature schema {FEATURE_SCHEMA_VERSION!r}, "
                f"got {schema_version!r}."
            )
        if not query_id or not course_id or isinstance(relevance, bool) or relevance not in range(4):
            raise RuntimeError(f"Invalid annotation row {row_number}: missing query, course ID, or relevance.")
        key = (query_id, course_id)
        if key in seen:
            continue
        seen.add(key)
        raw_features = row.get("features") or document.get("ltr_features") or {}
        if not isinstance(raw_features, dict):
            raw_features = {}
        missing_features = [name for name in FEATURE_NAMES if name not in raw_features]
        if missing_features:
            raise RuntimeError(
                f"Invalid annotation row {row_number}: missing LTR features {', '.join(missing_features)}."
            )
        by_query.setdefault(query_id, []).append(
            JudgedCourse(
                query_id=query_id,
                course_id=course_id,
                pool_rank=len(by_query[query_id]) + 1,
                relevance=int(relevance),
                features={name: float(value) for name, value in raw_features.items() if value is not None},
            )
        )
    if not by_query:
        raise RuntimeError(f"No annotation records found in {path}.")
    return by_query


def split_query_ids(query_ids: list[str], seed: int) -> tuple[list[str], list[str], list[str]]:
    if len(query_ids) < 20:
        raise RuntimeError("At least 20 annotated queries are required to make train/dev/test splits.")
    shuffled = list(query_ids)
    random.Random(seed).shuffle(shuffled)
    test_count = max(1, round(len(shuffled) * 0.15))
    dev_count = max(1, round(len(shuffled) * 0.15))
    train_count = len(shuffled) - dev_count - test_count
    if train_count < 1:
        raise RuntimeError("Not enough queries remaining for training after the requested splits.")
    return (
        sorted(shuffled[:train_count]),
        sorted(shuffled[train_count : train_count + dev_count]),
        sorted(shuffled[train_count + dev_count :]),
    )


def flatten_queries(
    query_courses: dict[str, list[JudgedCourse]],
    query_ids: list[str],
    feature_names: list[str],
) -> tuple[np.ndarray, np.ndarray, list[int], list[JudgedCourse]]:
    courses = [course for query_id in query_ids for course in query_courses[query_id]]
    matrix = np.asarray(
        [[float(course.features.get(feature_name, 0.0)) for feature_name in feature_names] for course in courses],
        dtype=np.float64,
    )
    labels = np.asarray([course.relevance for course in courses], dtype=np.int32)
    groups = [len(query_courses[query_id]) for query_id in query_ids]
    if not all(group > 0 for group in groups):
        raise RuntimeError("Every split query must contain at least one judged course.")
    return matrix, labels, groups, courses


def score_rows(courses: list[JudgedCourse], scores: np.ndarray) -> dict[tuple[str, str], float]:
    return {(course.query_id, course.course_id): float(score) for course, score in zip(courses, scores)}


def write_test_scores(path: Path, courses: list[JudgedCourse], scores: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "query_id": course.query_id,
            "course_id": course.course_id,
            "score": f"{float(score):.10f}",
            "relevance": course.relevance,
        }
        for course, score in zip(courses, scores)
    ]
    import csv

    with path.open("w", encoding="utf-8", newline="") as score_file:
        writer = csv.DictWriter(score_file, fieldnames=["query_id", "course_id", "score", "relevance"])
        writer.writeheader()
        writer.writerows(rows)


def train(args: argparse.Namespace) -> dict[str, Any]:
    xgboost = require_xgboost()
    query_courses = read_training_annotations(args.annotations)
    excluded_features = sorted(set(args.exclude_feature))
    unknown_features = sorted(set(excluded_features).difference(FEATURE_NAMES))
    if unknown_features:
        raise RuntimeError(f"Unknown LTR feature(s): {', '.join(unknown_features)}")
    feature_names = [feature_name for feature_name in FEATURE_NAMES if feature_name not in excluded_features]
    if not feature_names:
        raise RuntimeError("At least one LTR feature is required.")
    train_ids, dev_ids, test_ids = split_query_ids(sorted(query_courses), args.seed)
    train_matrix, train_labels, train_groups, _ = flatten_queries(query_courses, train_ids, feature_names)
    dev_matrix, dev_labels, dev_groups, _ = flatten_queries(query_courses, dev_ids, feature_names)
    test_matrix, _, _, test_courses = flatten_queries(query_courses, test_ids, feature_names)

    ranker = xgboost.XGBRanker(
        objective=args.objective,
        eval_metric=["ndcg@5", "ndcg@10", "ndcg@25"],
        n_estimators=args.n_estimators,
        learning_rate=args.learning_rate,
        max_depth=args.max_depth,
        min_child_weight=args.min_child_weight,
        reg_lambda=args.reg_lambda,
        random_state=args.seed,
        n_jobs=args.n_jobs,
        verbosity=0,
        early_stopping_rounds=args.early_stopping_rounds,
    )
    ranker.fit(
        train_matrix,
        train_labels,
        group=train_groups,
        eval_set=[(dev_matrix, dev_labels)],
        eval_group=[dev_groups],
        verbose=False,
    )
    test_scores = ranker.predict(test_matrix)
    score_by_pair = score_rows(test_courses, test_scores)
    test_query_courses = {query_id: query_courses[query_id] for query_id in test_ids}
    ltr_metrics = evaluate_ranker(
        test_query_courses,
        "ltr_model",
        lambda course: score_by_pair[(course.query_id, course.course_id)],
        DEFAULT_CUTOFFS,
        args.relevance_threshold,
    )
    baseline_metrics = [
        evaluate_ranker(
            test_query_courses,
            name,
            score,
            DEFAULT_CUTOFFS,
            args.relevance_threshold,
        )
        for name, score in (
            ("bm25_feature", lambda course: course.features.get("bm25_reciprocal_rank", 0.0)),
            ("dense_feature", lambda course: course.features.get("dense_reciprocal_rank", 0.0)),
            (
                "rrf_feature_sum",
                lambda course: course.features.get("bm25_reciprocal_rank", 0.0)
                + course.features.get("dense_reciprocal_rank", 0.0),
            ),
        )
    ]

    args.model_file.parent.mkdir(parents=True, exist_ok=True)
    ranker.get_booster().save_model(str(args.model_file))
    write_test_scores(args.test_scores, test_courses, test_scores)
    best_iteration = getattr(ranker, "best_iteration", None)
    if best_iteration is None:
        best_iteration = ranker.get_booster().num_boosted_rounds()
    else:
        best_iteration += 1  # XGBoost exposes a zero-based best tree index.
    metadata = {
        "schema_version": "ltr-model-v2",
        "model_format": "xgboost-json",
        "created_at": datetime.now(UTC).isoformat(),
        "annotations_file": str(args.annotations),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": feature_names,
        "excluded_features": excluded_features,
        "training": {
            "objective": args.objective,
            "algorithm": OBJECTIVE_DISPLAY_NAMES[args.objective],
            "seed": args.seed,
            "best_iteration": best_iteration,
            "parameters": {
                "n_estimators": args.n_estimators,
                "learning_rate": args.learning_rate,
                "max_depth": args.max_depth,
                "min_child_weight": args.min_child_weight,
                "reg_lambda": args.reg_lambda,
            },
            "query_splits": {"train": train_ids, "dev": dev_ids, "test": test_ids},
            "pair_counts": {
                "train": len(train_labels),
                "dev": len(dev_labels),
                "test": len(test_courses),
            },
        },
        "held_out_metrics": {"ltr_model": ltr_metrics, "baselines": baseline_metrics},
        "test_scores_file": str(args.test_scores),
    }
    args.metadata_file.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS_FILE)
    parser.add_argument("--model-file", type=Path, default=DEFAULT_MODEL_FILE)
    parser.add_argument("--metadata-file", type=Path, default=DEFAULT_METADATA_FILE)
    parser.add_argument("--test-scores", type=Path, default=DEFAULT_TEST_SCORES_FILE)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-child-weight", type=float, default=1.0)
    parser.add_argument("--reg-lambda", type=float, default=1.0)
    parser.add_argument(
        "--objective",
        choices=RANKING_OBJECTIVES,
        default="rank:ndcg",
        help="XGBoost ranking objective; rank:ndcg is the default.",
    )
    parser.add_argument("--early-stopping-rounds", type=int, default=50)
    parser.add_argument("--relevance-threshold", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--n-jobs", type=int, default=-1)
    parser.add_argument(
        "--exclude-feature",
        action="append",
        default=[],
        choices=FEATURE_NAMES,
        help="Exclude one feature. May be repeated; intended for ablation experiments.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        metadata = train(parse_args())
        metrics = metadata["held_out_metrics"]["ltr_model"]
        print(
            f"Trained {metadata['training']['algorithm']} LTR model. "
            f"Held-out NDCG@10={metrics['ndcg@10']:.4f}; "
            f"MRR@25={metrics['mrr@25']:.4f}; MAP@25={metrics['ap@25']:.4f}."
        )
    except RuntimeError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)
