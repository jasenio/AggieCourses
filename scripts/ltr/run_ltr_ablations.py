#!/usr/bin/env python
"""Train grouped LTR feature ablations on one fixed query-level split."""

from __future__ import annotations

import argparse
import json
from argparse import Namespace
from pathlib import Path

try:
    from .train_ltr_model import FEATURE_NAMES, RANKING_OBJECTIVES, train
except ImportError:  # Direct execution.
    from train_ltr_model import FEATURE_NAMES, RANKING_OBJECTIVES, train


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ANNOTATIONS = ROOT_DIR / "artifacts" / "human-ltr-annotations.json"
DEFAULT_OUTPUT = ROOT_DIR / "artifacts" / "ltr-group-ablation-results.json"
DEFAULT_ARTIFACT_DIR = ROOT_DIR / "artifacts" / "ltr-group-ablations"
FEATURE_GROUPS = {
    "retrieval": ["bm25_reciprocal_rank", "dense_reciprocal_rank"],
    "identity": [
        "exact_course_code_match", "course_code_prefix_match", "exact_course_number_match",
        "subject_name_token_coverage", "query_token_count",
    ],
    "text_overlap": [
        "exact_title_match", "title_token_coverage", "description_token_coverage",
        "overall_query_token_coverage",
    ],
    "prerequisite_cross_listing": [
        "prerequisite_token_coverage",
        "cross_listing_token_coverage",
    ],
    "popularity": ["log_current_section_count", "log_historical_enrollment"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-child-weight", type=float, default=1.0)
    parser.add_argument("--reg-lambda", type=float, default=1.0)
    parser.add_argument(
        "--objectives",
        nargs="+",
        choices=RANKING_OBJECTIVES,
        default=["rank:ndcg", "rank:pairwise"],
        help="XGBoost objectives to compare on the same fixed split.",
    )
    parser.add_argument("--early-stopping-rounds", type=int, default=50)
    parser.add_argument("--relevance-threshold", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--n-jobs", type=int, default=-1)
    return parser.parse_args()


def training_args(
    args: argparse.Namespace, name: str, excluded: list[str], objective: str
) -> Namespace:
    objective_file_name = objective.replace(":", "_")
    return Namespace(
        annotations=args.annotations,
        model_file=args.artifact_dir / f"{objective_file_name}-{name}-model.json",
        metadata_file=args.artifact_dir / f"{objective_file_name}-{name}-metadata.json",
        test_scores=args.artifact_dir / f"{objective_file_name}-{name}-test-scores.csv",
        seed=args.seed,
        n_estimators=args.n_estimators,
        learning_rate=args.learning_rate,
        max_depth=args.max_depth,
        min_child_weight=args.min_child_weight,
        reg_lambda=args.reg_lambda,
        objective=objective,
        early_stopping_rounds=args.early_stopping_rounds,
        relevance_threshold=args.relevance_threshold,
        n_jobs=args.n_jobs,
        exclude_feature=excluded,
    )


def run(args: argparse.Namespace) -> list[dict[str, object]]:
    if not args.annotations.exists():
        raise RuntimeError(f"Annotation file does not exist: {args.annotations}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    variants = [("full", [])] + [(f"without_{name}", features) for name, features in FEATURE_GROUPS.items()]
    results: list[dict[str, object]] = []
    for objective in args.objectives:
        for name, excluded in variants:
            print(f"Training {objective}/{name}...", flush=True)
            variant_args = training_args(args, name, excluded, objective)
            metadata = train(variant_args)
            metrics = metadata["held_out_metrics"]["ltr_model"]
            results.append(
                {
                    "objective": objective,
                    "algorithm": metadata["training"]["algorithm"],
                    "variant": name,
                    "excluded_features": excluded,
                    "included_feature_count": len(FEATURE_NAMES) - len(excluded),
                    "best_iteration": metadata["training"]["best_iteration"],
                    "ndcg@10": metrics["ndcg@10"],
                    "mrr@25": metrics["mrr@25"],
                    "map@25": metrics["ap@25"],
                    "metadata_file": str(variant_args.metadata_file),
                }
            )

    baselines = {result["objective"]: result for result in results if result["variant"] == "full"}
    for result in results:
        baseline = baselines[result["objective"]]
        result["delta_ndcg@10"] = round(float(result["ndcg@10"]) - float(baseline["ndcg@10"]), 6)
        result["delta_mrr@25"] = round(float(result["mrr@25"]) - float(baseline["mrr@25"]), 6)
        result["delta_map@25"] = round(float(result["map@25"]) - float(baseline["map@25"]), 6)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "annotations": str(args.annotations),
                "seed": args.seed,
                "objectives": args.objectives,
                "feature_groups": FEATURE_GROUPS,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return results


if __name__ == "__main__":
    try:
        ablation_results = run(parse_args())
    except RuntimeError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)

    print("objective\tvariant\tfeatures\tndcg@10\tmrr@25\tmap@25\tdelta_ndcg@10")
    for result in ablation_results:
        print(
            f"{result['objective']}\t{result['variant']}\t{result['included_feature_count']}\t{result['ndcg@10']:.4f}\t"
            f"{result['mrr@25']:.4f}\t{result['map@25']:.4f}\t{result['delta_ndcg@10']:+.4f}"
        )
