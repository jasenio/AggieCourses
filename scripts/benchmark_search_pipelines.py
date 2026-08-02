#!/usr/bin/env python
"""Benchmark full-catalog retrieval quality and warm pipeline latency together.

This is the canonical search-system benchmark.  Every selected pipeline runs
against the same held-out queries, the same full catalog, and the same
relevance judgments.  Latency repetitions are interleaved in rotating pipeline
order so cache warmth does not consistently favor one pipeline.

The benchmark intentionally uses the committed production LTR model.  It never
trains or replaces a model and never writes to PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app import main as app  # noqa: E402
from backend.app import paradedb, postgres  # noqa: E402


DEFAULT_ANNOTATIONS = ROOT_DIR / "artifacts" / "human-ltr-annotations-2026-07-26-2.json"
DEFAULT_SPLIT = ROOT_DIR / "artifacts" / "ltr" / "full-catalog-query-split-updated.json"
DEFAULT_OUTPUT = ROOT_DIR / "artifacts" / "search-pipeline-benchmark.json"
DEFAULT_PIPELINES = (
    "bm25",
    "semantic",
    "rrf",
    "rrf_ltr",
    "rrf_cross_encoder",
)
QUALITY_CUTOFF = 10
BINARY_CUTOFF = 25


@dataclass(frozen=True)
class QueryJudgments:
    query_id: str
    query: str
    relevance_by_course: dict[str, int]


@dataclass(frozen=True)
class PipelineResult:
    ranking: list[str]
    total_seconds: float
    stages: dict[str, float]


def parse_pipeline_names(value: str) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(name.strip() for name in value.split(",") if name.strip()))
    unknown = sorted(set(names) - set(DEFAULT_PIPELINES))
    if not names:
        raise argparse.ArgumentTypeError("At least one pipeline is required.")
    if unknown:
        raise argparse.ArgumentTypeError(
            f"Unknown pipelines: {', '.join(unknown)}. "
            f"Choose from: {', '.join(DEFAULT_PIPELINES)}."
        )
    return names


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if not 0 <= fraction <= 1:
        raise ValueError("Percentile fraction must be between zero and one.")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def latency_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    samples = [float(value) for value in values]
    return {
        "samples": len(samples),
        "mean": statistics.fmean(samples) if samples else None,
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "min": min(samples) if samples else None,
        "max": max(samples) if samples else None,
    }


def dcg(relevances: list[int]) -> float:
    return sum(
        ((2**relevance) - 1) / math.log2(position + 1)
        for position, relevance in enumerate(relevances, start=1)
    )


def ranking_quality(
    ranking: list[str],
    relevance_by_course: dict[str, int],
    *,
    relevance_threshold: int,
) -> dict[str, float | int | None]:
    """Score a full-catalog ranking, treating unjudged documents as nonrelevant.

    Judgment coverage is reported alongside quality so pool incompleteness is
    visible rather than silently filtering unjudged results out of the rank
    positions.
    """
    labels = [int(relevance_by_course.get(course_id, 0)) for course_id in ranking]
    total_relevant = sum(
        relevance >= relevance_threshold for relevance in relevance_by_course.values()
    )
    top_quality = labels[:QUALITY_CUTOFF]
    ideal_quality = sorted(relevance_by_course.values(), reverse=True)[:QUALITY_CUTOFF]
    relevant_positions = [
        position
        for position, relevance in enumerate(labels[:BINARY_CUTOFF], start=1)
        if relevance >= relevance_threshold
    ]
    precision_sum = sum(
        sum(
            label >= relevance_threshold
            for label in labels[:position]
        )
        / position
        for position in relevant_positions
    )
    judged_top10 = sum(course_id in relevance_by_course for course_id in ranking[:10])
    judged_top25 = sum(course_id in relevance_by_course for course_id in ranking[:25])
    relevant_at_25 = len(relevant_positions)
    ideal_dcg = dcg(ideal_quality)
    return {
        "ndcg@10": dcg(top_quality) / ideal_dcg if ideal_dcg else None,
        "mrr@25": 1.0 / relevant_positions[0] if relevant_positions else 0.0,
        "map@25": precision_sum / total_relevant if total_relevant else None,
        "recall@25": relevant_at_25 / total_relevant if total_relevant else None,
        "hit_rate@25": float(bool(relevant_positions)) if total_relevant else None,
        "judged_coverage@10": judged_top10 / min(10, len(ranking)) if ranking else 0.0,
        "judged_coverage@25": judged_top25 / min(25, len(ranking)) if ranking else 0.0,
        "judged_relevant": total_relevant,
    }


def mean(values: Iterable[float | int | None]) -> float | None:
    valid = [float(value) for value in values if value is not None]
    return statistics.fmean(valid) if valid else None


def load_judgments(path: Path) -> dict[str, QueryJudgments]:
    if not path.exists():
        raise RuntimeError(f"Annotation file does not exist: {path}")
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Annotation file is not valid JSON: {path}") from exc
    if not isinstance(rows, list):
        raise RuntimeError("Annotation JSON must contain a flat array of judgments.")

    queries: dict[str, str] = {}
    relevance: dict[str, dict[str, int]] = {}
    for row_number, row in enumerate(rows, start=1):
        document = row.get("document") if isinstance(row, dict) else None
        query_id = str(row.get("query_id") or "").strip() if isinstance(row, dict) else ""
        query = str(row.get("query") or "").strip() if isinstance(row, dict) else ""
        course_id = str((document or {}).get("course_id") or "").strip()
        label = row.get("relevance") if isinstance(row, dict) else None
        if (
            not query_id
            or not query
            or not course_id
            or isinstance(label, bool)
            or label not in range(4)
        ):
            raise RuntimeError(f"Invalid annotation row {row_number} in {path}.")
        if query_id in queries and queries[query_id] != query:
            raise RuntimeError(f"Query text changed within annotation group {query_id}.")
        existing = relevance.setdefault(query_id, {}).get(course_id)
        if existing is not None and existing != int(label):
            raise RuntimeError(f"Conflicting labels for {query_id}/{course_id}.")
        queries[query_id] = query
        relevance[query_id][course_id] = int(label)

    return {
        query_id: QueryJudgments(query_id, queries[query_id], labels)
        for query_id, labels in relevance.items()
    }


def held_out_query_ids(
    judgments: dict[str, QueryJudgments],
    split_path: Path,
    *,
    all_queries: bool,
) -> list[str]:
    if all_queries:
        return sorted(judgments)
    if not split_path.exists():
        raise RuntimeError(
            f"Held-out split does not exist: {split_path}. "
            "Provide --split-file or explicitly use --all-queries."
        )
    try:
        split = json.loads(split_path.read_text(encoding="utf-8"))
        query_ids = [str(value) for value in split["test_query_ids"]]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid held-out split: {split_path}") from exc
    missing = sorted(set(query_ids) - set(judgments))
    if missing:
        raise RuntimeError(
            f"Held-out split references queries missing from annotations: {', '.join(missing)}"
        )
    return query_ids


class PipelineRunner:
    def __init__(self, *, candidate_count: int, cross_encoder_candidates: int) -> None:
        self.candidate_count = candidate_count
        self.cross_encoder_candidates = cross_encoder_candidates

    @staticmethod
    def ranking(hits: list[dict[str, Any]]) -> list[str]:
        return [
            str(hit["_id"])
            for hit in hits
            if hit.get("_id")
        ]

    @staticmethod
    def ranks(hits: list[dict[str, Any]]) -> dict[str, int]:
        return {
            str(hit["_id"]): rank
            for rank, hit in enumerate(hits, start=1)
            if hit.get("_id")
        }

    def lexical(self, query: str) -> tuple[list[dict[str, Any]], float]:
        return paradedb.lexical_hits(query, self.candidate_count, lambda _: True)

    def embed(self, query: str) -> tuple[list[float], float]:
        started = time.perf_counter()
        vector = app.embed_query(query)
        elapsed = time.perf_counter() - started
        if vector is None:
            raise RuntimeError("The embedding model is unavailable.")
        return vector, elapsed

    def semantic(
        self,
        vector: list[float],
    ) -> tuple[list[dict[str, Any]], float]:
        return paradedb.semantic_hits(vector, self.candidate_count, lambda _: True)

    def hybrid_retrieval(
        self,
        query: str,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, float],
    ]:
        vector, embedding_seconds = self.embed(query)
        retrieval_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline-benchmark") as executor:
            lexical_future = executor.submit(self.lexical, query)
            semantic_future = executor.submit(self.semantic, vector)
            lexical_hits, lexical_seconds = lexical_future.result()
            semantic_hits, semantic_seconds = semantic_future.result()
        retrieval_wall_seconds = time.perf_counter() - retrieval_started
        fusion_started = time.perf_counter()
        fused = app.rrf_fuse_hit_lists([lexical_hits, semantic_hits])
        fusion_seconds = time.perf_counter() - fusion_started
        return lexical_hits, semantic_hits, fused, {
            "embedding_seconds": embedding_seconds,
            "lexical_seconds": lexical_seconds,
            "semantic_seconds": semantic_seconds,
            "hybrid_retrieval_wall_seconds": retrieval_wall_seconds,
            "fusion_seconds": fusion_seconds,
        }

    def production_ltr(
        self,
        query: str,
        fused: list[dict[str, Any]],
        lexical_hits: list[dict[str, Any]],
        semantic_hits: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        candidates = [dict(hit) for hit in fused[: self.candidate_count]]
        ltr_candidates = candidates[: app.LTR_CANDIDATE_COUNT]
        course_ids = [str(hit.get("_id") or "") for hit in ltr_candidates]
        statistics = postgres.catalog_rank_statistics(course_ids)
        for hit in ltr_candidates:
            course_id = str(hit.get("_id") or "")
            source = dict(hit.get("_source") or {})
            source["current_section_count"] = int(
                statistics.get(course_id, {}).get("current_section_count", 0)
            )
            hit["_source"] = source
        selected_metrics = {
            course_id: {
                "total_enrollment": int(values.get("historical_enrollment", 0))
            }
            for course_id, values in statistics.items()
        }
        reranked, applied = app.rerank_with_ltr(
            query,
            candidates,
            self.ranks(lexical_hits),
            self.ranks(semantic_hits),
            selected_metrics,
        )
        if not applied:
            raise RuntimeError("Production LTR model could not be applied.")
        return reranked, time.perf_counter() - started

    def run(self, pipeline: str, query: str) -> PipelineResult:
        started = time.perf_counter()
        if pipeline == "bm25":
            hits, lexical_seconds = self.lexical(query)
            stages = {"lexical_seconds": lexical_seconds}
        elif pipeline == "semantic":
            vector, embedding_seconds = self.embed(query)
            hits, semantic_seconds = self.semantic(vector)
            stages = {
                "embedding_seconds": embedding_seconds,
                "semantic_seconds": semantic_seconds,
            }
        else:
            lexical_hits, semantic_hits, fused, stages = self.hybrid_retrieval(query)
            hits = fused[: self.candidate_count]
            if pipeline == "rrf_ltr":
                hits, ranker_seconds = self.production_ltr(
                    query,
                    fused,
                    lexical_hits,
                    semantic_hits,
                )
                stages["ranker_seconds"] = ranker_seconds
            elif pipeline == "rrf_cross_encoder":
                cross_candidates = fused[: self.cross_encoder_candidates]
                ranker_started = time.perf_counter()
                cross_hits, applied = app.rerank_with_cross_encoder(query, cross_candidates)
                if not applied:
                    raise RuntimeError("Cross-encoder model could not be applied.")
                hits = cross_hits + fused[self.cross_encoder_candidates : self.candidate_count]
                stages["ranker_seconds"] = time.perf_counter() - ranker_started
            elif pipeline != "rrf":
                raise ValueError(f"Unsupported pipeline: {pipeline}")
        total_seconds = time.perf_counter() - started
        return PipelineResult(
            ranking=self.ranking(hits[: self.candidate_count]),
            total_seconds=total_seconds,
            stages=stages,
        )


def aggregate_quality(
    per_query: list[dict[str, Any]],
) -> dict[str, float | int | None]:
    metrics = (
        "ndcg@10",
        "mrr@25",
        "map@25",
        "recall@25",
        "hit_rate@25",
        "judged_coverage@10",
        "judged_coverage@25",
    )
    return {
        "queries": len(per_query),
        **{
            metric: mean(row["quality"].get(metric) for row in per_query)
            for metric in metrics
        },
    }


def aggregate_stage_latency(
    samples: list[dict[str, float]],
) -> dict[str, dict[str, float | int | None]]:
    stage_names = sorted({name for sample in samples for name in sample})
    return {
        name: latency_summary(
            sample[name] for sample in samples if name in sample
        )
        for name in stage_names
    }


def paired_comparison(
    pipeline_rows: dict[str, list[dict[str, Any]]],
    baseline: str,
) -> dict[str, dict[str, float | int | None]]:
    baseline_by_query = {
        row["query_id"]: row
        for row in pipeline_rows[baseline]
    }
    comparisons: dict[str, dict[str, float | int | None]] = {}
    for pipeline, rows in pipeline_rows.items():
        latency_deltas: list[float] = []
        latency_ratios: list[float] = []
        ndcg_deltas: list[float] = []
        latency_wins = 0
        for row in rows:
            reference = baseline_by_query[row["query_id"]]
            pipeline_latency = float(row["latency_seconds"]["p50"])
            baseline_latency = float(reference["latency_seconds"]["p50"])
            latency_deltas.append(pipeline_latency - baseline_latency)
            if baseline_latency > 0:
                latency_ratios.append(pipeline_latency / baseline_latency)
            if pipeline_latency < baseline_latency:
                latency_wins += 1
            pipeline_ndcg = row["quality"].get("ndcg@10")
            baseline_ndcg = reference["quality"].get("ndcg@10")
            if pipeline_ndcg is not None and baseline_ndcg is not None:
                ndcg_deltas.append(float(pipeline_ndcg) - float(baseline_ndcg))
        comparisons[pipeline] = {
            "baseline": baseline,
            "paired_queries": len(rows),
            "median_latency_delta_seconds": percentile(latency_deltas, 0.50),
            "median_latency_ratio": percentile(latency_ratios, 0.50),
            "latency_win_rate": latency_wins / len(rows) if rows else None,
            "mean_ndcg@10_delta": mean(ndcg_deltas),
        }
    return comparisons


def rotate(values: tuple[str, ...], amount: int) -> tuple[str, ...]:
    if not values:
        return values
    offset = amount % len(values)
    return values[offset:] + values[:offset]


def print_summary(report: dict[str, Any]) -> None:
    columns = (
        "pipeline",
        "ndcg@10",
        "mrr@25",
        "map@25",
        "recall@25",
        "p50_ms",
        "p95_ms",
        "mean_ms",
    )
    print("\t".join(columns))
    for pipeline in report["config"]["pipelines"]:
        summary = report["pipelines"][pipeline]
        quality = summary["quality"]
        latency = summary["latency_seconds"]
        values: list[str] = [pipeline]
        for metric in ("ndcg@10", "mrr@25", "map@25", "recall@25"):
            value = quality.get(metric)
            values.append(f"{value:.4f}" if value is not None else "n/a")
        for metric in ("p50", "p95", "mean"):
            value = latency.get(metric)
            values.append(f"{1000 * value:.1f}" if value is not None else "n/a")
        print("\t".join(values))


def markdown_summary(report: dict[str, Any]) -> str:
    config = report["config"]
    dataset = report["dataset"]
    lines = [
        "# Search pipeline benchmark",
        "",
        f"- Created: `{report['created_at']}`",
        f"- Catalog documents: {dataset['catalog_documents']}",
        f"- Held-out queries: {dataset['queries']}",
        f"- Relevance judgments: {dataset['judgments']}",
        f"- Repetitions: {config['repetitions']}",
        f"- Candidates per pipeline: {config['candidate_count']}",
        f"- Baseline: `{config['baseline']}`",
        f"- Unjudged policy: `{config['unjudged_policy']}`",
        "",
        "## Quality and latency",
        "",
        "| Pipeline | NDCG@10 | MRR@25 | MAP@25 | Recall@25 | Judged@10 | p50 | p95 | Mean |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pipeline in config["pipelines"]:
        summary = report["pipelines"][pipeline]
        quality = summary["quality"]
        latency = summary["latency_seconds"]

        def quality_value(name: str) -> str:
            value = quality.get(name)
            return f"{value:.4f}" if value is not None else "n/a"

        def latency_value(name: str) -> str:
            value = latency.get(name)
            return f"{1000 * value:.1f} ms" if value is not None else "n/a"

        lines.append(
            "| "
            + " | ".join(
                [
                    pipeline,
                    quality_value("ndcg@10"),
                    quality_value("mrr@25"),
                    quality_value("map@25"),
                    quality_value("recall@25"),
                    quality_value("judged_coverage@10"),
                    latency_value("p50"),
                    latency_value("p95"),
                    latency_value("mean"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Paired comparison",
            "",
            f"Each row is paired query-by-query against `{config['baseline']}`.",
            "",
            "| Pipeline | Median latency delta | Median latency ratio | Latency win rate | Mean NDCG@10 delta |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for pipeline in config["pipelines"]:
        comparison = report["paired_vs_baseline"][pipeline]
        delta = comparison["median_latency_delta_seconds"]
        ratio = comparison["median_latency_ratio"]
        win_rate = comparison["latency_win_rate"]
        ndcg_delta = comparison["mean_ndcg@10_delta"]
        lines.append(
            f"| {pipeline} | "
            f"{1000 * delta:+.1f} ms | "
            f"{ratio:.3f}x | "
            f"{100 * win_rate:.1f}% | "
            f"{ndcg_delta:+.4f} |"
        )
    lines.extend(
        [
            "",
            "Quality treats unjudged results as nonrelevant and reports judgment coverage. "
            "Latency is warm, single-client pipeline time; it is not a concurrency-capacity result.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--split-file", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument(
        "--all-queries",
        action="store_true",
        help="Use every annotated query. This may evaluate the production LTR model on training queries.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--summary-output",
        type=Path,
        help="Human-readable Markdown output. Defaults to the JSON output path with a .md suffix.",
    )
    parser.add_argument("--pipelines", type=parse_pipeline_names, default=DEFAULT_PIPELINES)
    parser.add_argument("--baseline", default="rrf_ltr")
    parser.add_argument("--candidate-count", type=int, default=250)
    parser.add_argument("--cross-encoder-candidates", type=int, default=250)
    parser.add_argument("--cross-encoder-backend", choices=("torch", "onnx"))
    parser.add_argument("--cross-encoder-max-length", type=int)
    parser.add_argument("--cross-encoder-batch-size", type=int)
    parser.add_argument("--cross-encoder-model-file")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--warmup-queries", type=int, default=2)
    parser.add_argument("--query-limit", type=int)
    parser.add_argument("--relevance-threshold", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--minimum-catalog-size", type=int, default=10000)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    cross_encoder_backend = getattr(args, "cross_encoder_backend", None)
    cross_encoder_max_length = getattr(args, "cross_encoder_max_length", None)
    cross_encoder_batch_size = getattr(args, "cross_encoder_batch_size", None)
    cross_encoder_model_file = getattr(args, "cross_encoder_model_file", None)
    if args.baseline not in args.pipelines:
        raise RuntimeError("--baseline must be one of the selected pipelines.")
    if args.candidate_count < BINARY_CUTOFF:
        raise RuntimeError(f"--candidate-count must be at least {BINARY_CUTOFF}.")
    if not 1 <= args.cross_encoder_candidates <= args.candidate_count:
        raise RuntimeError("--cross-encoder-candidates must be between 1 and candidate-count.")
    if cross_encoder_max_length is not None and cross_encoder_max_length < 1:
        raise RuntimeError("--cross-encoder-max-length must be positive.")
    if cross_encoder_batch_size is not None and cross_encoder_batch_size < 1:
        raise RuntimeError("--cross-encoder-batch-size must be positive.")
    if cross_encoder_model_file and cross_encoder_backend not in (None, "onnx"):
        raise RuntimeError("--cross-encoder-model-file requires the ONNX backend.")
    if args.repetitions < 1:
        raise RuntimeError("--repetitions must be positive.")
    if args.warmup_queries < 0:
        raise RuntimeError("--warmup-queries cannot be negative.")
    if args.query_limit is not None and args.query_limit < 1:
        raise RuntimeError("--query-limit must be positive.")


def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    cross_encoder_backend = getattr(args, "cross_encoder_backend", None)
    cross_encoder_max_length = getattr(args, "cross_encoder_max_length", None)
    cross_encoder_batch_size = getattr(args, "cross_encoder_batch_size", None)
    cross_encoder_model_file = getattr(args, "cross_encoder_model_file", None)
    judgments = load_judgments(args.annotations)
    query_ids = held_out_query_ids(
        judgments,
        args.split_file,
        all_queries=args.all_queries,
    )
    if args.query_limit is not None:
        query_ids = query_ids[: args.query_limit]
    if not query_ids:
        raise RuntimeError("No benchmark queries were selected.")

    postgres.open_pool()
    try:
        catalog_size = paradedb.document_count()
        if catalog_size < args.minimum_catalog_size:
            raise RuntimeError(
                f"Expected at least {args.minimum_catalog_size} catalog documents; found {catalog_size}."
            )
        if not paradedb.projection_is_current():
            raise RuntimeError(
                "Search projection is stale. Let application startup finish before benchmarking."
            )

        if any(
            value is not None
            for value in (
                cross_encoder_backend,
                cross_encoder_max_length,
                cross_encoder_batch_size,
                cross_encoder_model_file,
            )
        ):
            from sentence_transformers import CrossEncoder

            backend = cross_encoder_backend or (
                "onnx" if cross_encoder_model_file else "torch"
            )
            model_kwargs = (
                {"file_name": cross_encoder_model_file}
                if cross_encoder_model_file
                else None
            )
            app._cross_encoder_model = CrossEncoder(
                app.CROSS_ENCODER_MODEL_NAME,
                backend=backend,
                max_length=cross_encoder_max_length,
                model_kwargs=model_kwargs,
            )
            if cross_encoder_batch_size is not None:
                app.CROSS_ENCODER_PREDICT_BATCH_SIZE = cross_encoder_batch_size

        runner = PipelineRunner(
            candidate_count=args.candidate_count,
            cross_encoder_candidates=args.cross_encoder_candidates,
        )
        warmup_ids = query_ids[: min(args.warmup_queries, len(query_ids))]
        for query_id in warmup_ids:
            for pipeline in args.pipelines:
                runner.run(pipeline, judgments[query_id].query)

        rankings: dict[str, dict[str, list[str]]] = {
            pipeline: {} for pipeline in args.pipelines
        }
        latency_samples: dict[str, dict[str, list[float]]] = {
            pipeline: {query_id: [] for query_id in query_ids}
            for pipeline in args.pipelines
        }
        stage_samples: dict[str, list[dict[str, float]]] = {
            pipeline: [] for pipeline in args.pipelines
        }
        benchmark_started = time.perf_counter()
        completed = 0
        total_runs = len(query_ids) * args.repetitions * len(args.pipelines)
        for repetition in range(args.repetitions):
            for query_index, query_id in enumerate(query_ids):
                pipeline_order = rotate(args.pipelines, query_index + repetition)
                for pipeline in pipeline_order:
                    result = runner.run(pipeline, judgments[query_id].query)
                    if query_id not in rankings[pipeline]:
                        rankings[pipeline][query_id] = result.ranking
                    elif (
                        rankings[pipeline][query_id][:BINARY_CUTOFF]
                        != result.ranking[:BINARY_CUTOFF]
                    ):
                        raise RuntimeError(
                            f"Pipeline {pipeline} returned a nondeterministic top "
                            f"{BINARY_CUTOFF} ranking for {query_id}."
                        )
                    latency_samples[pipeline][query_id].append(result.total_seconds)
                    stage_samples[pipeline].append(result.stages)
                    completed += 1
                print(
                    f"[{completed}/{total_runs}] repetition={repetition + 1} "
                    f"query={query_id} elapsed={time.perf_counter() - benchmark_started:.1f}s",
                    flush=True,
                )

        pipeline_rows: dict[str, list[dict[str, Any]]] = {}
        pipeline_summary: dict[str, dict[str, Any]] = {}
        for pipeline in args.pipelines:
            rows: list[dict[str, Any]] = []
            all_latency: list[float] = []
            for query_id in query_ids:
                samples = latency_samples[pipeline][query_id]
                all_latency.extend(samples)
                quality = ranking_quality(
                    rankings[pipeline][query_id],
                    judgments[query_id].relevance_by_course,
                    relevance_threshold=args.relevance_threshold,
                )
                rows.append(
                    {
                        "query_id": query_id,
                        "query": judgments[query_id].query,
                        "quality": quality,
                        "latency_seconds": latency_summary(samples),
                        "top_25": rankings[pipeline][query_id][:25],
                    }
                )
            pipeline_rows[pipeline] = rows
            pipeline_summary[pipeline] = {
                "quality": aggregate_quality(rows),
                "latency_seconds": latency_summary(all_latency),
                "stage_latency_seconds": aggregate_stage_latency(stage_samples[pipeline]),
                "per_query": rows,
            }

        report = {
            "schema_version": "paired-full-catalog-search-benchmark-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "config": {
                "annotations": str(args.annotations),
                "annotations_sha256": file_sha256(args.annotations),
                "split_file": str(args.split_file) if not args.all_queries else None,
                "split_file_sha256": (
                    file_sha256(args.split_file) if not args.all_queries else None
                ),
                "all_queries": args.all_queries,
                "pipelines": list(args.pipelines),
                "baseline": args.baseline,
                "candidate_count": args.candidate_count,
                "cross_encoder_candidates": args.cross_encoder_candidates,
                "cross_encoder_backend": cross_encoder_backend or "production_default",
                "cross_encoder_max_length": cross_encoder_max_length,
                "cross_encoder_batch_size": (
                    cross_encoder_batch_size or app.CROSS_ENCODER_PREDICT_BATCH_SIZE
                ),
                "cross_encoder_model_file": cross_encoder_model_file,
                "repetitions": args.repetitions,
                "warmup_queries": args.warmup_queries,
                "relevance_threshold": args.relevance_threshold,
                "unjudged_policy": "nonrelevant",
                "production_ltr_model": str(app.LTR_MODEL_FILE),
                "production_ltr_model_sha256": file_sha256(app.LTR_MODEL_FILE),
                "production_ltr_metadata_sha256": file_sha256(app.LTR_MODEL_METADATA_FILE),
                "python_version": sys.version,
            },
            "dataset": {
                "catalog_documents": catalog_size,
                "queries": len(query_ids),
                "judgments": sum(
                    len(judgments[query_id].relevance_by_course)
                    for query_id in query_ids
                ),
                "query_ids": query_ids,
            },
            "benchmark_seconds": time.perf_counter() - benchmark_started,
            "pipelines": pipeline_summary,
            "paired_vs_baseline": paired_comparison(pipeline_rows, args.baseline),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        summary_output = args.summary_output or args.output.with_suffix(".md")
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary_output.write_text(markdown_summary(report), encoding="utf-8")
        print_summary(report)
        print(f"Saved paired benchmark: {args.output}")
        print(f"Saved benchmark summary: {summary_output}")
        return report
    finally:
        postgres.close_pool()


def main() -> int:
    try:
        run(parse_args())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
