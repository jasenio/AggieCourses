"""ParadeDB-backed course candidate retrieval.

The application intentionally keeps RRF and all post-retrieval rankers in
Python.  This module owns only the two independent candidate lists: BM25 from
``pg_search`` and cosine kNN from ``pgvector``.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterable

from backend.app import postgres


SEARCH_TABLE = "course_search_documents"
BM25_INDEX = "course_search_documents_bm25_idx"
PROJECTION_VERSION_KEY = "course_search_projection_version"
# Version 2 stores 384-dimensional vectors inline.  On the production catalog,
# exact cosine retrieval fell from roughly 113 ms to 17 ms by avoiding one
# TOAST lookup per document.
PROJECTION_VERSION = "2"


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8g}" for value in values) + "]"


def ensure_schema() -> None:
    """Create the ParadeDB/pgvector projection and its indexes.

    ``paradedb/paradedb`` ships both extensions.  Keeping the extension
    statements here makes a missing or incorrectly provisioned deployment
    fail early with a useful PostgreSQL error.
    """
    with postgres.connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS course_search_documents (
                course_id TEXT PRIMARY KEY,
                course_code TEXT NOT NULL,
                course_prefix TEXT NOT NULL,
                number TEXT NOT NULL,
                degree_level TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                prerequisites TEXT NOT NULL DEFAULT '',
                restrictions TEXT NOT NULL DEFAULT '',
                cross_listings TEXT NOT NULL DEFAULT '',
                attributes_text TEXT NOT NULL DEFAULT '',
                course_attributes_text TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL,
                document JSONB NOT NULL,
                embedding vector(384) STORAGE PLAIN,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute(f"ALTER TABLE {SEARCH_TABLE} ADD COLUMN IF NOT EXISTS degree_level TEXT NOT NULL DEFAULT ''")
        # CREATE TABLE IF NOT EXISTS does not update existing column storage.
        # Keep this idempotent guard so restored and upgraded volumes use the
        # same physical contract as newly bootstrapped databases.
        conn.execute(f"ALTER TABLE {SEARCH_TABLE} ALTER COLUMN embedding SET STORAGE PLAIN")
        # A single aggregate search field gives us stable, explicit field
        # weighting while retaining all original fields for exact boosts and
        # display.  Repeating higher-value fields in search_text is deliberate.
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS {BM25_INDEX}
            ON {SEARCH_TABLE} USING bm25 (
                course_id,
                course_code,
                course_prefix,
                number,
                title,
                search_text
            ) WITH (key_field = 'course_id')
        """)


def document_count() -> int:
    with postgres.connection() as conn:
        row = conn.execute(f"SELECT count(*) AS count FROM {SEARCH_TABLE}").fetchone()
    return int(row["count"])


def projection_is_current() -> bool:
    """Whether persisted documents were written with the active projection contract."""
    with postgres.connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_metadata WHERE key = %s",
            (PROJECTION_VERSION_KEY,),
        ).fetchone()
    return row is not None and str(row["value"]) == PROJECTION_VERSION


def documents_are_course_only() -> bool:
    """Whether the stored projection predates section embedding removal."""
    with postgres.connection() as conn:
        row = conn.execute(f"""
            SELECT NOT EXISTS (
                SELECT 1 FROM {SEARCH_TABLE}
                WHERE document ? 'current_sections'
                   OR document ? 'current_section_count'
                   OR document ? 'course_attributes'
            ) AS course_only
        """).fetchone()
    return bool(row["course_only"])


def documents_have_degree_levels() -> bool:
    with postgres.connection() as conn:
        row = conn.execute(f"""
            SELECT NOT EXISTS (
                SELECT 1 FROM {SEARCH_TABLE} WHERE degree_level = ''
            ) AS populated
        """).fetchone()
    return bool(row["populated"])


def documents_have_embeddings() -> bool:
    """Whether every projected course has a semantic vector."""
    with postgres.connection() as conn:
        row = conn.execute(f"""
            SELECT NOT EXISTS (
                SELECT 1 FROM {SEARCH_TABLE} WHERE embedding IS NULL
            ) AS populated
        """).fetchone()
    return bool(row["populated"])


def rebuild_documents(courses: Iterable[dict[str, Any]], embeddings: Iterable[list[float] | None]) -> int:
    """Replace the derived search projection from source-of-truth course rows."""
    values: list[tuple[Any, ...]] = []
    for course, embedding in zip(courses, embeddings):
        course_id = str(course["course_id"])
        course_code = str(course.get("course_code") or "")
        title = str(course.get("title") or "")
        description = str(course.get("description") or "")
        prerequisites = str(course.get("prerequisites") or "")
        restrictions = str(course.get("restrictions") or "")
        cross_listings = str(course.get("cross_listings") or "")
        attributes_text = str(course.get("attributes") or "")
        course_attributes_text = " ".join(str(value) for value in course.get("course_attributes") or [])
        # Field repetition is the portable way to express the existing
        # course-code/title lexical preference in a single ParadeDB field.
        search_text = " ".join([
            course_code, course_code, course_code, course_code, course_code,
            str(course.get("course_prefix") or ""), str(course.get("course_prefix") or ""),
            title, title, title,
            description, prerequisites, restrictions, cross_listings,
            attributes_text, course_attributes_text,
        ])
        values.append((
            course_id,
            course_code,
            str(course.get("course_prefix") or ""),
            str(course.get("number") or ""),
            str(course.get("degree_level") or ""),
            title,
            description,
            prerequisites,
            restrictions,
            cross_listings,
            attributes_text,
            course_attributes_text,
            search_text,
            json.dumps(course, ensure_ascii=False),
            _vector_literal(embedding) if embedding else None,
        ))

    with postgres.connection() as conn:
        conn.execute(f"TRUNCATE TABLE {SEARCH_TABLE}")
        with conn.cursor() as cursor:
            cursor.executemany(f"""
                INSERT INTO {SEARCH_TABLE} (
                    course_id, course_code, course_prefix, number, degree_level, title,
                    description, prerequisites, restrictions, cross_listings,
                    attributes_text, course_attributes_text, search_text,
                    document, embedding
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::vector
                )
            """, values)
        # Commit the version marker with the replacement itself. If insertion
        # fails, the transaction rolls back both the TRUNCATE and this marker,
        # and startup safely retries the migration.
        conn.execute("""
            INSERT INTO app_metadata (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = now()
        """, (PROJECTION_VERSION_KEY, PROJECTION_VERSION))
    return len(values)


def update_documents(courses: Iterable[dict[str, Any]]) -> int:
    """Update changed course documents without regenerating their embeddings."""
    values: list[tuple[Any, ...]] = []
    for course in courses:
        course_code = str(course.get("course_code") or "")
        title = str(course.get("title") or "")
        description = str(course.get("description") or "")
        prerequisites = str(course.get("prerequisites") or "")
        restrictions = str(course.get("restrictions") or "")
        cross_listings = str(course.get("cross_listings") or "")
        attributes_text = str(course.get("attributes") or "")
        course_attributes_text = " ".join(str(value) for value in course.get("course_attributes") or [])
        search_text = " ".join([
            course_code, course_code, course_code, course_code, course_code,
            str(course.get("course_prefix") or ""), str(course.get("course_prefix") or ""),
            title, title, title,
            description, prerequisites, restrictions, cross_listings,
            attributes_text, course_attributes_text,
        ])
        values.append((
            str(course["course_id"]), course_code, str(course.get("course_prefix") or ""),
            str(course.get("number") or ""), str(course.get("degree_level") or ""), title, description, prerequisites,
            restrictions, cross_listings, attributes_text, course_attributes_text,
            search_text, json.dumps(course, ensure_ascii=False),
        ))
    if not values:
        return 0
    with postgres.connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(f"""
                INSERT INTO {SEARCH_TABLE} (
                    course_id, course_code, course_prefix, number, degree_level, title,
                    description, prerequisites, restrictions, cross_listings,
                    attributes_text, course_attributes_text, search_text, document
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (course_id) DO UPDATE SET
                    course_code = EXCLUDED.course_code,
                    course_prefix = EXCLUDED.course_prefix,
                    number = EXCLUDED.number,
                    degree_level = EXCLUDED.degree_level,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    prerequisites = EXCLUDED.prerequisites,
                    restrictions = EXCLUDED.restrictions,
                    cross_listings = EXCLUDED.cross_listings,
                    attributes_text = EXCLUDED.attributes_text,
                    course_attributes_text = EXCLUDED.course_attributes_text,
                    search_text = EXCLUDED.search_text,
                    document = EXCLUDED.document,
                    updated_at = now()
            """, values)
    return len(values)


def _as_hits(rows: Iterable[dict[str, Any]], filter_match: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    hits = []
    for row in rows:
        document = row["document"]
        if isinstance(document, str):
            document = json.loads(document)
        if filter_match(document):
            hits.append({
                "_id": str(row["course_id"]),
                "_score": float(row.get("score") or 0.0),
                "_source": document,
            })
    return hits


def _course_predicate(
    prefix: str | None,
    degree_levels: list[str] | None,
    section_filters: postgres.ActiveSectionFilterSpec | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if section_filters is not None:
        section_predicate, section_params = postgres.active_section_exists_predicate(
            f"{SEARCH_TABLE}.course_id",
            section_filters,
        )
        clauses.append(section_predicate)
        params.extend(section_params)
    if prefix:
        clauses.append("course_prefix = %s")
        params.append(prefix.strip().upper())
    if degree_levels:
        clauses.append("degree_level = ANY(%s)")
        params.append(degree_levels)
    return " AND ".join(clauses) if clauses else "TRUE", params


def lexical_hits(
    query: str, fetch_limit: int, filter_match: Callable[[dict[str, Any]], bool],
    *, prefix: str | None = None, degree_levels: list[str] | None = None,
    section_filters: postgres.ActiveSectionFilterSpec | None = None,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    predicate, params = _course_predicate(prefix, degree_levels, section_filters)
    with postgres.connection() as conn:
        rows = list(conn.execute(f"""
            SELECT course_id, document, score
            FROM (
                SELECT course_id, document, pdb.score(course_id) AS score
                FROM {SEARCH_TABLE}
                WHERE search_text ||| %s AND {predicate}
                ORDER BY score DESC
                LIMIT %s
            ) AS candidates
            ORDER BY score DESC, course_id
        """, (query, *params, fetch_limit)))
    return _as_hits(rows, filter_match), time.perf_counter() - started


def semantic_hits(
    query_vector: list[float], fetch_limit: int, filter_match: Callable[[dict[str, Any]], bool],
    *, prefix: str | None = None, degree_levels: list[str] | None = None,
    section_filters: postgres.ActiveSectionFilterSpec | None = None,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    vector = _vector_literal(query_vector)
    predicate, params = _course_predicate(prefix, degree_levels, section_filters)
    with postgres.connection() as conn:
        rows = list(conn.execute(f"""
            SELECT course_id, document, 1 - (embedding <=> %s::vector) AS score
            FROM {SEARCH_TABLE}
            WHERE embedding IS NOT NULL AND {predicate}
            ORDER BY embedding <=> %s::vector, course_id
            LIMIT %s
        """, (vector, *params, vector, fetch_limit)))
    return _as_hits(rows, filter_match), time.perf_counter() - started


def catalog_hits(
    filter_match: Callable[[dict[str, Any]], bool], *, prefix: str | None = None,
    degree_levels: list[str] | None = None,
    section_filters: postgres.ActiveSectionFilterSpec | None = None,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    predicate, params = _course_predicate(prefix, degree_levels, section_filters)
    with postgres.connection() as conn:
        rows = list(conn.execute(f"""
            SELECT course_id, document, 0.0 AS score
            FROM {SEARCH_TABLE}
            WHERE {predicate}
            ORDER BY course_prefix, number, course_id
        """, params))
    return _as_hits(rows, filter_match), time.perf_counter() - started
