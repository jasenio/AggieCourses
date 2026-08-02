"""Shared feature and annotation-pool construction for human-judged LTR data."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any


FEATURE_SCHEMA_VERSION = "human-ltr-v1"
FEATURE_NAMES = [
    "bm25_reciprocal_rank",
    "dense_reciprocal_rank",
    "exact_course_code_match",
    "course_code_prefix_match",
    "exact_course_number_match",
    "subject_name_token_coverage",
    "query_token_count",
    "exact_title_match",
    "title_token_coverage",
    "description_token_coverage",
    "overall_query_token_coverage",
    "prerequisite_token_coverage",
    "cross_listing_token_coverage",
    "log_current_section_count",
    "log_historical_enrollment",
]
RRF_RANK_CONSTANT = 60
ANNOTATION_POOL_SIZE = 25


def text_tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def normalized_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def token_coverage(query_tokens: set[str], document_text: Any) -> float:
    if not query_tokens:
        return 0.0
    return len(query_tokens.intersection(text_tokens(document_text))) / len(query_tokens)


def direct_course_code(query: str) -> tuple[str, str] | None:
    match = re.search(r"\b([A-Za-z]{2,8})\s*[- ]?\s*(\d{3}[A-Za-z]?)\b", query)
    return (match.group(1).upper(), match.group(2).upper()) if match else None


def direct_course_numbers(query: str) -> set[str]:
    return {number.upper() for number in re.findall(r"(?<![A-Za-z0-9])(\d{3}[A-Za-z]?)(?![A-Za-z0-9])", query)}


def subject_name(source: dict[str, Any]) -> str:
    prefix = str(source.get("course_prefix") or "").strip()
    context = str(source.get("subject_context") or "").strip()
    if context:
        context = re.sub(rf"^\s*{re.escape(prefix)}\s*(?:—|–|-|:)\s*", "", context, flags=re.IGNORECASE)
    return context or prefix


def reciprocal_rank(rank: int | None) -> float:
    return 1.0 / (RRF_RANK_CONSTANT + rank) if rank and rank > 0 else 0.0


def features_for_course(
    query: str,
    source: dict[str, Any],
    *,
    bm25_rank: int | None,
    semantic_rank: int | None,
    current_section_count: int,
    historical_enrollment: int,
) -> dict[str, float]:
    query_tokens = text_tokens(query)
    prefix = str(source.get("course_prefix") or "").strip().upper()
    number = str(source.get("number") or "").strip().upper()
    course_code = str(source.get("course_code") or f"{prefix} {number}").strip()
    title = str(source.get("title") or "")
    description = str(source.get("description") or "")
    prerequisites = str(source.get("prerequisites") or "")
    cross_listings = str(source.get("cross_listings") or "")
    subject = subject_name(source)
    code = direct_course_code(query)
    overall_text = " ".join(
        [
            course_code,
            subject,
            title,
            description,
            prerequisites,
            cross_listings,
            str(source.get("restrictions") or ""),
            str(source.get("attributes") or ""),
            " ".join(str(value) for value in source.get("course_attributes") or []),
        ]
    )
    features = {
        "bm25_reciprocal_rank": reciprocal_rank(bm25_rank),
        "dense_reciprocal_rank": reciprocal_rank(semantic_rank),
        "exact_course_code_match": float(bool(code and code == (prefix, number))),
        "course_code_prefix_match": float(prefix.casefold() in query_tokens),
        "exact_course_number_match": float(number in direct_course_numbers(query)),
        "subject_name_token_coverage": token_coverage(query_tokens, subject),
        "query_token_count": float(len(query_tokens)),
        "exact_title_match": float(bool(query_tokens) and normalized_text(query) == normalized_text(title)),
        "title_token_coverage": token_coverage(query_tokens, title),
        "description_token_coverage": token_coverage(query_tokens, description),
        "overall_query_token_coverage": token_coverage(query_tokens, overall_text),
        "prerequisite_token_coverage": token_coverage(query_tokens, prerequisites),
        "cross_listing_token_coverage": token_coverage(query_tokens, cross_listings),
        "log_current_section_count": math.log1p(max(0, int(current_section_count))),
        "log_historical_enrollment": math.log1p(max(0, int(historical_enrollment))),
    }
    return {name: float(features[name]) for name in FEATURE_NAMES}


def deterministic_random_hits(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(hit: dict[str, Any]) -> tuple[bytes, str]:
        document_id = str(hit.get("_id") or "")
        digest = hashlib.sha256(f"{query.casefold()}\0{document_id}".encode()).digest()
        return digest, document_id

    return sorted(hits, key=key)


def build_annotation_pool(
    cross_encoder_hits: list[dict[str, Any]],
    bm25_hits: list[dict[str, Any]],
    semantic_hits: list[dict[str, Any]],
    random_hits: list[dict[str, Any]],
    *,
    cross_encoder_applied: bool,
) -> list[dict[str, Any]]:
    """Select 10 CE/RRF + 5 BM25 + 5 semantic + 5 random, then CE/RRF-fill."""
    bm25_ranks = {str(hit.get("_id")): rank for rank, hit in enumerate(bm25_hits, 1)}
    semantic_ranks = {str(hit.get("_id")): rank for rank, hit in enumerate(semantic_hits, 1)}
    ce_ranks = {str(hit.get("_id")): rank for rank, hit in enumerate(cross_encoder_hits, 1)}
    random_ranks = {str(hit.get("_id")): rank for rank, hit in enumerate(random_hits, 1)}
    selected: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    def add(hits: list[dict[str, Any]], source_name: str, count: int) -> None:
        for hit in hits[:count]:
            document_id = str(hit.get("_id") or "")
            if not document_id:
                continue
            if document_id in by_id:
                sources = by_id[document_id]["_annotation_provenance"]["candidate_sources"]
                if source_name not in sources:
                    sources.append(source_name)
                continue
            saved = dict(hit)
            saved["_annotation_provenance"] = {
                "candidate_sources": [source_name],
                "cross_encoder_rank": ce_ranks.get(document_id),
                "bm25_rank": bm25_ranks.get(document_id),
                "semantic_rank": semantic_ranks.get(document_id),
                "random_rank": random_ranks.get(document_id),
                "cross_encoder_applied": cross_encoder_applied,
            }
            selected.append(saved)
            by_id[document_id] = saved

    add(cross_encoder_hits, "rrf_cross_encoder" if cross_encoder_applied else "rrf_fallback", 10)
    add(bm25_hits, "bm25", 5)
    add(semantic_hits, "semantic", 5)
    add(random_hits, "random", 5)
    add(
        cross_encoder_hits,
        "rrf_cross_encoder_fill" if cross_encoder_applied else "rrf_fill",
        len(cross_encoder_hits),
    )
    return selected[:ANNOTATION_POOL_SIZE]
