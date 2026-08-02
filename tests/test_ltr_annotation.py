from __future__ import annotations

import math
import sys
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import main
from backend.app.ltr_features import (
    FEATURE_NAMES,
    build_annotation_pool,
    deterministic_random_hits,
    features_for_course,
)


def hit(number: int) -> dict:
    return {
        "_id": f"TEST-{number:03d}",
        "_source": {
            "course_id": f"TEST-{number:03d}",
            "course_prefix": "TEST",
            "number": f"{number:03d}",
            "course_code": f"TEST {number:03d}",
            "title": f"Course {number}",
        },
    }


def test_feature_schema_and_exact_matches() -> None:
    features = features_for_course(
        "computer science CSCE 121 programming",
        {
            "course_prefix": "CSCE",
            "number": "121",
            "course_code": "CSCE 121",
            "subject_context": "CSCE — Computer Science and Engineering",
            "title": "Introduction to Program Design and Concepts",
            "description": "Programming fundamentals for computer science.",
            "prerequisites": "MATH 151",
            "cross_listings": "",
        },
        bm25_rank=1,
        semantic_rank=3,
        current_section_count=4,
        historical_enrollment=1000,
    )
    assert list(features) == FEATURE_NAMES
    assert features["exact_course_code_match"] == 1.0
    assert features["course_code_prefix_match"] == 1.0
    assert features["exact_course_number_match"] == 1.0
    assert features["subject_name_token_coverage"] == 2 / 5
    assert features["description_token_coverage"] == 3 / 5
    assert features["log_current_section_count"] == math.log1p(4)
    assert features["log_historical_enrollment"] == math.log1p(1000)


def test_pool_uses_top_slices_then_fills_duplicates_from_reranked_rrf() -> None:
    reranked = [hit(index) for index in range(1, 101)]
    bm25 = [hit(index) for index in range(1, 6)]
    semantic = [hit(index) for index in range(6, 11)]
    random = [hit(index) for index in range(8, 13)]
    pool = build_annotation_pool(
        reranked,
        bm25,
        semantic,
        random,
        cross_encoder_applied=True,
    )
    assert len(pool) == 25
    assert len({row["_id"] for row in pool}) == 25
    assert [row["_id"] for row in pool[:10]] == [f"TEST-{index:03d}" for index in range(1, 11)]
    assert pool[-1]["_id"] == "TEST-025"
    assert "semantic" in pool[5]["_annotation_provenance"]["candidate_sources"]


def test_random_pool_order_is_query_stable() -> None:
    courses = [hit(index) for index in range(1, 50)]
    assert deterministic_random_hits("data science", courses) == deterministic_random_hits("data science", courses)
    assert deterministic_random_hits("data science", courses) != deterministic_random_hits("organic chemistry", courses)


def test_ltr_reranking_generates_features_once_per_candidate() -> None:
    candidates = [hit(index) for index in range(1, 4)]
    feature_values = {name: 0.0 for name in FEATURE_NAMES}

    class Model:
        def predict(self, matrix, *, iteration_range):
            return [float(index) for index in range(len(matrix))]

    fake_xgboost = SimpleNamespace(DMatrix=lambda values, feature_names: values)
    with (
        patch.dict(sys.modules, {"xgboost": fake_xgboost}),
        patch.object(main, "get_ltr_model", return_value=Model()),
        patch.object(main, "features_for_course", return_value=feature_values) as feature_builder,
    ):
        _, applied = main.rerank_with_ltr(
            "chess",
            candidates,
            {candidate["_id"]: rank for rank, candidate in enumerate(candidates, 1)},
            {candidate["_id"]: rank for rank, candidate in enumerate(candidates, 1)},
            {},
        )

    assert applied
    assert feature_builder.call_count == len(candidates)
