#!/usr/bin/env python
"""Score every rated course in an annotation file with an XGBoost LTR model."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

try:
    from .train_ltr_model import FEATURE_NAMES, read_training_annotations
except ImportError:  # Direct execution.
    from train_ltr_model import FEATURE_NAMES, read_training_annotations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    features = metadata.get("feature_names")
    if not isinstance(features, list) or any(feature not in FEATURE_NAMES for feature in features):
        raise RuntimeError("Model metadata contains an incompatible feature schema.")
    from xgboost import Booster, DMatrix

    model = Booster()
    model.load_model(str(args.model))
    courses_by_query = read_training_annotations(args.annotations)
    rows: list[dict[str, object]] = []
    for query_id, courses in courses_by_query.items():
        matrix = np.asarray([[course.features.get(feature, 0.0) for feature in features] for course in courses])
        scores = model.predict(DMatrix(matrix, feature_names=features))
        rows.extend(
            {"query_id": query_id, "course_id": course.course_id, "score": f"{float(score):.10f}"}
            for course, score in zip(courses, scores)
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=["query_id", "course_id", "score"])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    run(parse_args())
