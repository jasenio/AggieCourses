"""Unit tests for the paired quality and latency benchmark."""

from __future__ import annotations

import json
import tempfile
import unittest
from argparse import ArgumentTypeError, Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_search_pipelines import (
    PipelineResult,
    QueryJudgments,
    held_out_query_ids,
    latency_summary,
    markdown_summary,
    paired_comparison,
    parse_pipeline_names,
    percentile,
    ranking_quality,
    rotate,
    run,
)


class LatencyStatisticsTests(unittest.TestCase):
    def test_percentiles_are_interpolated_and_summary_keeps_sample_count(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]

        self.assertEqual(percentile(values, 0.50), 2.5)
        self.assertAlmostEqual(percentile(values, 0.95), 3.85)
        summary = latency_summary(values)
        self.assertEqual(summary["samples"], 4)
        self.assertEqual(summary["mean"], 2.5)
        self.assertEqual(summary["p50"], 2.5)
        self.assertAlmostEqual(float(summary["p95"]), 3.85)
        self.assertAlmostEqual(float(summary["p99"]), 3.97)
        self.assertEqual(summary["min"], 1.0)
        self.assertEqual(summary["max"], 4.0)

    def test_pipeline_rotation_balances_the_first_position(self) -> None:
        pipelines = ("bm25", "semantic", "rrf")

        self.assertEqual(rotate(pipelines, 0), pipelines)
        self.assertEqual(rotate(pipelines, 1), ("semantic", "rrf", "bm25"))
        self.assertEqual(rotate(pipelines, 2), ("rrf", "bm25", "semantic"))


class QualityMetricTests(unittest.TestCase):
    def test_unjudged_results_keep_their_rank_and_count_as_nonrelevant(self) -> None:
        metrics = ranking_quality(
            ["A", "UNJUDGED", "B"],
            {"A": 3, "B": 2, "C": 2, "D": 0},
            relevance_threshold=2,
        )

        self.assertLess(float(metrics["ndcg@10"]), 1.0)
        self.assertEqual(metrics["mrr@25"], 1.0)
        self.assertAlmostEqual(float(metrics["map@25"]), (1.0 + 2 / 3) / 3)
        self.assertAlmostEqual(float(metrics["recall@25"]), 2 / 3)
        self.assertAlmostEqual(float(metrics["judged_coverage@10"]), 2 / 3)


class BenchmarkContractTests(unittest.TestCase):
    def test_pipeline_parser_rejects_unknown_names(self) -> None:
        self.assertEqual(parse_pipeline_names("bm25,rrf,bm25"), ("bm25", "rrf"))
        with self.assertRaises(ArgumentTypeError):
            parse_pipeline_names("bm25,unknown")

    def test_held_out_split_is_required_unless_all_queries_are_explicit(self) -> None:
        judgments = {
            "Q1": QueryJudgments("Q1", "first", {"A": 3}),
            "Q2": QueryJudgments("Q2", "second", {"B": 2}),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split = root / "split.json"
            split.write_text(json.dumps({"test_query_ids": ["Q2"]}), encoding="utf-8")

            self.assertEqual(
                held_out_query_ids(judgments, split, all_queries=False),
                ["Q2"],
            )
            self.assertEqual(
                held_out_query_ids(judgments, root / "missing.json", all_queries=True),
                ["Q1", "Q2"],
            )

    def test_paired_comparison_uses_matching_query_medians(self) -> None:
        rows = {
            "baseline": [
                {
                    "query_id": "Q1",
                    "latency_seconds": {"p50": 0.10},
                    "quality": {"ndcg@10": 0.5},
                },
                {
                    "query_id": "Q2",
                    "latency_seconds": {"p50": 0.20},
                    "quality": {"ndcg@10": 0.7},
                },
            ],
            "candidate": [
                {
                    "query_id": "Q1",
                    "latency_seconds": {"p50": 0.05},
                    "quality": {"ndcg@10": 0.6},
                },
                {
                    "query_id": "Q2",
                    "latency_seconds": {"p50": 0.10},
                    "quality": {"ndcg@10": 0.8},
                },
            ],
        }

        comparison = paired_comparison(rows, "baseline")["candidate"]

        self.assertAlmostEqual(float(comparison["median_latency_ratio"]), 0.5)
        self.assertEqual(comparison["latency_win_rate"], 1.0)
        self.assertAlmostEqual(float(comparison["mean_ndcg@10_delta"]), 0.1)

    def test_end_to_end_report_pairs_every_query_and_writes_both_formats(self) -> None:
        class FakeRunner:
            def __init__(self, **_: object) -> None:
                pass

            def run(self, pipeline: str, query: str) -> PipelineResult:
                ranking = ["A", "B"] if pipeline == "bm25" else ["B", "A"]
                seconds = 0.01 if pipeline == "bm25" else 0.02
                return PipelineResult(ranking, seconds, {f"{pipeline}_seconds": seconds})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annotations = root / "annotations.json"
            split = root / "split.json"
            output = root / "benchmark.json"
            annotations.write_text(
                json.dumps(
                    [
                        {
                            "query_id": query_id,
                            "query": query,
                            "document": {"course_id": course_id},
                            "relevance": relevance,
                        }
                        for query_id, query in (("Q1", "first"), ("Q2", "second"))
                        for course_id, relevance in (("A", 3), ("B", 2))
                    ]
                ),
                encoding="utf-8",
            )
            split.write_text(
                json.dumps({"test_query_ids": ["Q1", "Q2"]}),
                encoding="utf-8",
            )
            args = Namespace(
                annotations=annotations,
                split_file=split,
                all_queries=False,
                output=output,
                summary_output=None,
                pipelines=("bm25", "semantic"),
                baseline="bm25",
                candidate_count=250,
                cross_encoder_candidates=25,
                repetitions=2,
                warmup_queries=1,
                query_limit=None,
                relevance_threshold=2,
                minimum_catalog_size=10000,
            )

            with (
                patch("scripts.benchmark_search_pipelines.PipelineRunner", FakeRunner),
                patch("scripts.benchmark_search_pipelines.postgres.open_pool"),
                patch("scripts.benchmark_search_pipelines.postgres.close_pool"),
                patch(
                    "scripts.benchmark_search_pipelines.paradedb.document_count",
                    return_value=11014,
                ),
                patch(
                    "scripts.benchmark_search_pipelines.paradedb.projection_is_current",
                    return_value=True,
                ),
            ):
                report = run(args)

            self.assertEqual(report["dataset"]["queries"], 2)
            self.assertEqual(report["pipelines"]["bm25"]["latency_seconds"]["samples"], 4)
            self.assertTrue(output.exists())
            self.assertTrue(output.with_suffix(".md").exists())
            self.assertIn("| bm25 |", markdown_summary(report))


if __name__ == "__main__":
    unittest.main()
