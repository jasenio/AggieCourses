"""PostgreSQL persistence for the course recommender.

CSV files are deliberately handled only by the bootstrap importer in this
module.  Runtime reads and the hourly section sync use these tables instead.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, TypedDict

import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from backend.app.config import settings


ROOT_DIR = settings.root_dir
DATABASE_URL = settings.database_url
POSTGRES_POOL_MIN_SIZE = settings.postgres_pool_min_size
POSTGRES_POOL_MAX_SIZE = settings.postgres_pool_max_size
COLLEGE_STATION_SECTION_SITES = ("", "college station", "distance education")
_connection_pool: ConnectionPool | None = None
_connection_pool_lock = threading.Lock()


class ActiveSectionFilterSpec(TypedDict):
    filter_terms: list[str]
    locations: list[str]
    instruction_types: list[str]
    core_attributes: list[str]
    graduation_requirements: list[str]
    attributes: list[str]
    major_aliases: list[str]
    department_aliases: list[str]
    college_aliases: list[str]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS courses (
    course_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    course_number TEXT NOT NULL,
    title TEXT NOT NULL,
    credit_hours TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    prerequisites TEXT NOT NULL DEFAULT '',
    restrictions TEXT NOT NULL DEFAULT '',
    cross_listings TEXT NOT NULL DEFAULT '',
    catalog_attributes JSONB NOT NULL DEFAULT '[]'::jsonb,
    subject_context TEXT NOT NULL DEFAULT '',
    prerequisite_dependents JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(subject, course_number)
);

CREATE TABLE IF NOT EXISTS terms (
    term_code TEXT PRIMARY KEY,
    academic_year INTEGER NOT NULL,
    season TEXT NOT NULL CHECK (season IN ('spring', 'summer', 'fall')),
    is_search_active BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sections (
    term_code TEXT NOT NULL REFERENCES terms(term_code),
    crn TEXT NOT NULL,
    course_id TEXT NOT NULL,
    section_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    attributes JSONB NOT NULL DEFAULT '[]'::jsonb,
    site TEXT NOT NULL DEFAULT '',
    schedule_type TEXT NOT NULL DEFAULT '',
    instruction_type TEXT NOT NULL DEFAULT '',
    credit_hours TEXT NOT NULL DEFAULT '',
    seat_status_open TEXT NOT NULL DEFAULT '',
    is_open BOOLEAN,
    max_enrollment INTEGER,
    enrollment INTEGER,
    seats_available INTEGER,
    wait_capacity INTEGER,
    wait_count INTEGER,
    wait_available INTEGER,
    instructors TEXT NOT NULL DEFAULT '',
    instructor_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    meeting_days TEXT NOT NULL DEFAULT '',
    meeting_times TEXT NOT NULL DEFAULT '',
    meeting_dates TEXT NOT NULL DEFAULT '',
    meeting_locations TEXT NOT NULL DEFAULT '',
    is_present BOOLEAN NOT NULL DEFAULT TRUE,
    source_hash TEXT NOT NULL,
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(term_code, crn)
);
CREATE INDEX IF NOT EXISTS sections_course_id_idx ON sections(course_id);
CREATE INDEX IF NOT EXISTS sections_present_idx ON sections(term_code, is_present);

CREATE TABLE IF NOT EXISTS section_instructors (
    term_code TEXT NOT NULL,
    crn TEXT NOT NULL,
    instructor_id TEXT NOT NULL,
    instructor_name TEXT NOT NULL,
    PRIMARY KEY (term_code, crn, instructor_id),
    FOREIGN KEY (term_code, crn) REFERENCES sections(term_code, crn)
);
CREATE INDEX IF NOT EXISTS section_instructors_instructor_idx
    ON section_instructors(instructor_id, term_code, crn);

CREATE TABLE IF NOT EXISTS section_observations (
    term_code TEXT NOT NULL,
    crn TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_hash TEXT NOT NULL,
    seat_status_open TEXT NOT NULL DEFAULT '',
    enrollment INTEGER,
    seats_available INTEGER,
    wait_count INTEGER,
    wait_available INTEGER,
    PRIMARY KEY(term_code, crn, source_hash),
    FOREIGN KEY(term_code, crn) REFERENCES sections(term_code, crn)
);

CREATE TABLE IF NOT EXISTS section_restrictions (
    term_code TEXT NOT NULL,
    crn TEXT NOT NULL,
    course_id TEXT NOT NULL,
    raw_restrictions TEXT NOT NULL,
    parsed_restrictions JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(term_code, crn)
);
CREATE INDEX IF NOT EXISTS section_restrictions_course_idx ON section_restrictions(course_id, term_code);

CREATE TABLE IF NOT EXISTS majors (
    normalized_major TEXT PRIMARY KEY,
    major TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    normalized_department TEXT NOT NULL DEFAULT '',
    college TEXT NOT NULL DEFAULT '',
    normalized_college TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS grade_history (
    source_hash TEXT PRIMARY KEY,
    source_term_code TEXT NOT NULL,
    academic_year INTEGER NOT NULL,
    semester TEXT NOT NULL,
    course_id TEXT NOT NULL,
    section_number TEXT NOT NULL DEFAULT '',
    college_code TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    instructor TEXT NOT NULL DEFAULT '',
    total_enrollment INTEGER NOT NULL DEFAULT 0,
    total_graded INTEGER NOT NULL DEFAULT 0,
    gpa DOUBLE PRECISION,
    grade_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_payload JSONB NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS grade_history_course_idx ON grade_history(course_id, academic_year, semester);

CREATE TABLE IF NOT EXISTS historical_outcome_instructors (
    source_hash TEXT NOT NULL REFERENCES grade_history(source_hash),
    instructor_name TEXT NOT NULL,
    instructor_id TEXT,
    resolution_status TEXT NOT NULL,
    PRIMARY KEY (source_hash, instructor_name)
);
CREATE INDEX IF NOT EXISTS historical_outcome_instructors_lookup_idx
    ON historical_outcome_instructors(instructor_id, source_hash)
    WHERE instructor_id IS NOT NULL;

ALTER TABLE courses ADD COLUMN IF NOT EXISTS prerequisite_dependents JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS course_historical_stats (
    course_id TEXT PRIMARY KEY,
    total_historical_enrollment INTEGER NOT NULL DEFAULT 0,
    historical_average_gpa DOUBLE PRECISION,
    historical_gpa_weight INTEGER NOT NULL DEFAULT 0,
    historical_section_count INTEGER NOT NULL DEFAULT 0,
    historical_term_count INTEGER NOT NULL DEFAULT 0,
    last_grade_term TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS course_historical_term_stats (
    course_id TEXT NOT NULL,
    academic_year INTEGER NOT NULL,
    semester TEXT NOT NULL,
    total_enrollment INTEGER NOT NULL DEFAULT 0,
    average_gpa DOUBLE PRECISION,
    gpa_weight INTEGER NOT NULL DEFAULT 0,
    section_count INTEGER NOT NULL DEFAULT 0,
    term_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, academic_year, semester)
);

CREATE TABLE IF NOT EXISTS course_live_stats (
    course_id TEXT PRIMARY KEY,
    active_term_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    current_section_count INTEGER NOT NULL DEFAULT 0,
    open_section_count INTEGER NOT NULL DEFAULT 0,
    total_seats_available INTEGER NOT NULL DEFAULT 0,
    locations JSONB NOT NULL DEFAULT '[]'::jsonb,
    instruction_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    attributes JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_section_sync_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A deliberately section-scoped projection used by the search path.  Do not
-- collapse these values onto a course row: a location from one CRN and an
-- eligibility rule from another must never combine into a false match.
CREATE TABLE IF NOT EXISTS section_filter_facts (
    term_code TEXT NOT NULL,
    crn TEXT NOT NULL,
    course_id TEXT NOT NULL,
    filter_terms TEXT[] NOT NULL DEFAULT '{}',
    filter_locations TEXT[] NOT NULL DEFAULT '{}',
    instruction_type TEXT NOT NULL DEFAULT '',
    filter_core_attributes TEXT[] NOT NULL DEFAULT '{}',
    filter_graduation_requirements TEXT[] NOT NULL DEFAULT '{}',
    attributes TEXT[] NOT NULL DEFAULT '{}',
    restriction_data_available BOOLEAN NOT NULL DEFAULT FALSE,
    has_major_restriction BOOLEAN NOT NULL DEFAULT FALSE,
    major_restriction_aliases TEXT[] NOT NULL DEFAULT '{}',
    excluded_major_restriction_aliases TEXT[] NOT NULL DEFAULT '{}',
    has_department_restriction BOOLEAN NOT NULL DEFAULT FALSE,
    department_restriction_aliases TEXT[] NOT NULL DEFAULT '{}',
    excluded_department_restriction_aliases TEXT[] NOT NULL DEFAULT '{}',
    has_college_restriction BOOLEAN NOT NULL DEFAULT FALSE,
    college_restriction_aliases TEXT[] NOT NULL DEFAULT '{}',
    excluded_college_restriction_aliases TEXT[] NOT NULL DEFAULT '{}',
    section_data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (term_code, crn),
    FOREIGN KEY (term_code, crn) REFERENCES sections(term_code, crn)
);
CREATE INDEX IF NOT EXISTS section_filter_facts_course_idx ON section_filter_facts(course_id);
CREATE INDEX IF NOT EXISTS section_filter_facts_term_course_idx ON section_filter_facts(term_code, course_id);
CREATE INDEX IF NOT EXISTS section_filter_facts_course_term_location_cover_idx
    ON section_filter_facts(course_id, term_code) INCLUDE (filter_locations);
CREATE INDEX IF NOT EXISTS section_filter_facts_locations_idx ON section_filter_facts USING GIN(filter_locations);
CREATE INDEX IF NOT EXISTS section_filter_facts_attributes_idx ON section_filter_facts USING GIN(attributes);

CREATE TABLE IF NOT EXISTS course_historical_location_term_stats (
    course_id TEXT NOT NULL,
    academic_year INTEGER NOT NULL,
    semester TEXT NOT NULL,
    location TEXT NOT NULL,
    total_enrollment INTEGER NOT NULL DEFAULT 0,
    average_gpa DOUBLE PRECISION,
    gpa_weight INTEGER NOT NULL DEFAULT 0,
    section_count INTEGER NOT NULL DEFAULT 0,
    term_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, academic_year, semester, location)
);
CREATE INDEX IF NOT EXISTS course_historical_location_term_stats_lookup_idx
    ON course_historical_location_term_stats(course_id, semester, academic_year, location);

CREATE TABLE IF NOT EXISTS sync_runs (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    row_count INTEGER NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS search_outbox (
    id BIGSERIAL PRIMARY KEY,
    course_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS search_outbox_pending_idx
    ON search_outbox(course_id, reason) WHERE processed_at IS NULL;
"""


def configured() -> bool:
    return bool(DATABASE_URL)


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required. Start PostgreSQL and set DATABASE_URL before running the application.")
    return DATABASE_URL


def open_pool() -> None:
    """Open the API's shared PostgreSQL pool once per process."""
    global _connection_pool
    if not configured() or _connection_pool is not None:
        return
    with _connection_pool_lock:
        if _connection_pool is None:
            pool = ConnectionPool(
                conninfo=require_database_url(),
                min_size=POSTGRES_POOL_MIN_SIZE,
                max_size=POSTGRES_POOL_MAX_SIZE,
                kwargs={"row_factory": dict_row},
                open=False,
            )
            pool.open(wait=True)
            _connection_pool = pool


def close_pool() -> None:
    """Release pooled connections during API shutdown."""
    global _connection_pool
    with _connection_pool_lock:
        if _connection_pool is not None:
            _connection_pool.close()
            _connection_pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection[Any]]:
    if _connection_pool is not None:
        with _connection_pool.connection() as conn:
            yield conn
        return
    # Command-line import and maintenance scripts may use this module without
    # entering the FastAPI lifespan, so retain a direct-connection fallback.
    with psycopg.connect(require_database_url(), row_factory=dict_row) as conn:
        yield conn


def ensure_schema() -> None:
    with connection() as conn:
        conn.execute(SCHEMA_SQL)
        _refresh_prerequisite_dependents(conn)
        mapping_version = conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'instructor_mapping_version'"
        ).fetchone()
        if mapping_version is None or str(mapping_version["value"]) != "1":
            _refresh_instructor_mappings(conn)
            conn.execute("""
                INSERT INTO app_metadata (key, value) VALUES ('instructor_mapping_version', '1')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """)


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _int(value: str | None) -> int | None:
    try:
        return int((value or "").strip())
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    try:
        return float((value or "").strip())
    except ValueError:
        return None


def _course_id(subject: str, number: str) -> str:
    return f"{subject.strip().upper()}-{number.strip()}".strip("-")


def _term_parts(term_code: str) -> tuple[int, str]:
    cleaned = term_code.strip()
    if len(cleaned) < 5 or cleaned[4] not in {"1", "2", "3"}:
        raise ValueError(f"unsupported term code: {term_code!r}")
    return int(cleaned[:4]), {"1": "spring", "2": "summer", "3": "fall"}[cleaned[4]]


def _json_array(raw: str) -> list[str]:
    return [part.strip() for part in raw.split("|") if part.strip()]


def _json_object(raw: str) -> dict[str, Any] | list[Any]:
    try:
        decoded = json.loads(raw or "[]")
        return decoded if isinstance(decoded, (dict, list)) else []
    except json.JSONDecodeError:
        return []


def _course_ids_from_rows(rows: Iterable[dict[str, str]]) -> set[str]:
    return {_course_id(row.get("subject", ""), row.get("course_number", "")) for row in rows}


_COURSE_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<subject>[A-Za-z]{2,5})\s*-?\s*(?P<number>\d{3}[A-Za-z]?)(?![A-Za-z0-9])"
)


def _refresh_prerequisite_dependents(conn: psycopg.Connection[Any]) -> None:
    """Persist the reverse prerequisite graph alongside the catalog rows."""
    rows = list(conn.execute("""
        SELECT course_id, subject, course_number, title, prerequisites
        FROM courses
        ORDER BY subject, course_number
    """))
    if not rows:
        return
    known_course_ids = {str(row["course_id"]) for row in rows}
    dependents: dict[str, list[dict[str, str]]] = {course_id: [] for course_id in known_course_ids}
    for row in rows:
        dependent_id = str(row["course_id"])
        seen: set[str] = set()
        for match in _COURSE_REFERENCE_PATTERN.finditer(str(row["prerequisites"] or "")):
            referenced_id = _course_id(match.group("subject"), match.group("number"))
            if referenced_id == dependent_id or referenced_id not in known_course_ids or referenced_id in seen:
                continue
            seen.add(referenced_id)
            dependents[referenced_id].append({
                "course_id": dependent_id,
                "course_code": f"{row['subject']} {row['course_number']}",
                "title": str(row["title"]),
            })
    _executemany(
        conn,
        "UPDATE courses SET prerequisite_dependents = %s::jsonb WHERE course_id = %s",
        [
            (json.dumps(dependents[course_id]), course_id)
            for course_id in sorted(dependents)
        ],
    )


def _record_sync(conn: psycopg.Connection[Any], source_name: str) -> int:
    return int(conn.execute(
        "INSERT INTO sync_runs (source_name, status) VALUES (%s, 'running') RETURNING id",
        (source_name,),
    ).fetchone()["id"])


def _executemany(conn: psycopg.Connection[Any], query: str, values: Iterable[tuple[Any, ...]]) -> None:
    """Psycopg 3 exposes batch execution on cursors, not connections."""
    with conn.cursor() as cursor:
        cursor.executemany(query, values)


def _clean_instructor_name(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", value or "").strip()


def _legacy_instructor_key(value: str) -> str:
    cleaned = _clean_instructor_name(value)
    # A hyphen or apostrophe is part of the name token, not a separator.
    # Otherwise the two supported source formats retain different halves of
    # compound surnames (for example GUTIERREZ-OSUNA versus Gutierrez-Osuna).
    tokens = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", cleaned)
    if len(tokens) < 2:
        return ""
    if "," in cleaned or cleaned.isupper() or (len(tokens[-1]) == 1 and len(tokens[0]) > 1):
        last_name, first_name = tokens[0], tokens[-1] if "," not in cleaned else tokens[1]
    else:
        last_name, first_name = tokens[-1], tokens[0]
    return f"{last_name.casefold()}|{first_name[0].casefold()}"


def _refresh_section_instructors(conn: psycopg.Connection[Any], term_code: str | None = None) -> None:
    """Materialize numeric Howdy identities from the section JSON payload."""
    where_clause = "WHERE term_code = %s" if term_code else ""
    params: tuple[Any, ...] = (term_code,) if term_code else ()
    if term_code:
        conn.execute("DELETE FROM section_instructors WHERE term_code = %s", (term_code,))
    else:
        conn.execute("DELETE FROM section_instructors")
    rows = list(conn.execute(f"""
        SELECT term_code, crn, instructor_details
        FROM sections
        {where_clause}
    """, params))
    values: list[tuple[str, str, str, str]] = []
    for row in rows:
        details = row["instructor_details"] or []
        if not isinstance(details, list):
            continue
        seen: set[str] = set()
        for detail in details:
            if not isinstance(detail, dict):
                continue
            instructor_id = str(detail.get("more") or "").strip()
            instructor_name = _clean_instructor_name(str(detail.get("name") or ""))
            if not instructor_id.isdigit() or not instructor_name or instructor_id in seen:
                continue
            seen.add(instructor_id)
            values.append((str(row["term_code"]), str(row["crn"]), instructor_id, instructor_name))
    if values:
        _executemany(
            conn,
            "INSERT INTO section_instructors (term_code, crn, instructor_id, instructor_name) VALUES (%s, %s, %s, %s)",
            values,
        )


def _refresh_historical_outcome_instructors(conn: psycopg.Connection[Any]) -> None:
    """Persist conservative grade-report-name to Howdy-ID resolutions."""
    conn.execute("DELETE FROM historical_outcome_instructors")
    section_candidates: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in conn.execute("SELECT term_code, crn, instructor_id, instructor_name FROM section_instructors"):
        section_candidates.setdefault((str(row["term_code"]), str(row["crn"])), []).append(dict(row))
    outcomes = list(conn.execute("""
        SELECT gh.source_hash, gh.instructor, matched.term_code, matched.crn
        FROM grade_history gh
        LEFT JOIN LATERAL (
            SELECT s.term_code, s.crn
            FROM sections s
            WHERE substring(s.term_code FROM 1 FOR 5) = gh.source_term_code
              AND s.course_id = gh.course_id AND s.section_number = gh.section_number
            ORDER BY s.last_observed_at DESC
            LIMIT 1
        ) matched ON TRUE
    """))
    values: list[tuple[str, str, str | None, str]] = []
    for outcome in outcomes:
        source_hash = str(outcome["source_hash"])
        names = []
        seen_names: set[str] = set()
        for raw_name in re.split(r"[;|]", str(outcome["instructor"] or "")):
            name = _clean_instructor_name(raw_name)
            if name and name.casefold() not in seen_names:
                seen_names.add(name.casefold())
                names.append(name)
        if not names:
            values.append((source_hash, "", None, "missing_grade_report_instructor"))
            continue
        term_code = str(outcome.get("term_code") or "")
        crn = str(outcome.get("crn") or "")
        candidates = section_candidates.get((term_code, crn), [])
        if not candidates:
            values.extend((source_hash, name, None, "unmatched_section") for name in names)
            continue
        resolutions: list[tuple[str, str | None, str]] = []
        for name in names:
            legacy_key = _legacy_instructor_key(name)
            matches = [candidate for candidate in candidates if _legacy_instructor_key(candidate["instructor_name"]) == legacy_key]
            if len(matches) == 1:
                resolutions.append((name, matches[0]["instructor_id"], "section_match"))
            elif len(matches) > 1:
                resolutions.append((name, None, "ambiguous_section_instructors"))
            else:
                resolutions.append((name, None, "identity_mismatch"))
        if any(instructor_id is None for _, instructor_id, _ in resolutions):
            values.extend((source_hash, name, None, status) for name, _, status in resolutions)
        else:
            values.extend((source_hash, name, instructor_id, status) for name, instructor_id, status in resolutions)
    if values:
        _executemany(
            conn,
            """INSERT INTO historical_outcome_instructors (source_hash, instructor_name, instructor_id, resolution_status)
               VALUES (%s, %s, %s, %s)""",
            values,
        )


def _refresh_instructor_mappings(conn: psycopg.Connection[Any]) -> None:
    _refresh_section_instructors(conn)
    _refresh_historical_outcome_instructors(conn)


def _complete_sync(conn: psycopg.Connection[Any], run_id: int, row_count: int, details: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE sync_runs SET status = 'succeeded', completed_at = now(), row_count = %s, details = %s WHERE id = %s",
        (row_count, json.dumps(details), run_id),
    )


def _fail_sync(conn: psycopg.Connection[Any], run_id: int, error: Exception) -> None:
    conn.execute(
        """UPDATE sync_runs SET status = 'failed', completed_at = now(), error_message = %s
           WHERE id = %s AND status = 'running'""",
        (str(error), run_id),
    )


def start_section_refresh() -> int:
    """Start a durable run record before contacting the upstream section source."""
    with connection() as conn:
        return _record_sync(conn, "howdy_sections")


def fail_section_refresh(run_id: int, error: Exception) -> None:
    """Persist a refresh failure in its own transaction."""
    with connection() as conn:
        _fail_sync(conn, run_id, error)


def import_catalog(path: Path, subject_context: dict[str, str]) -> int:
    with path.open(encoding="utf-8", newline="") as handle, connection() as conn:
        rows = list(csv.DictReader(handle))
        run_id = _record_sync(conn, "catalog_csv")
        try:
            values = []
            for row in rows:
                subject = row.get("course_prefix", "").strip().upper()
                number = row.get("number", "").strip()
                values.append((
                    _course_id(subject, number), subject, number, row.get("title", "").strip(),
                    row.get("credit_hours", "").strip(), row.get("description", "").strip(),
                    row.get("prerequisites", "").strip(), row.get("restrictions", "").strip(),
                    row.get("cross_listings", "").strip(), json.dumps(_json_array(row.get("attributes", ""))),
                    subject_context.get(subject, subject), _hash(row),
                ))
            _executemany(conn, """
                INSERT INTO courses (course_id, subject, course_number, title, credit_hours, description,
                    prerequisites, restrictions, cross_listings, catalog_attributes, subject_context, source_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (course_id) DO UPDATE SET
                    title = EXCLUDED.title, credit_hours = EXCLUDED.credit_hours, description = EXCLUDED.description,
                    prerequisites = EXCLUDED.prerequisites, restrictions = EXCLUDED.restrictions,
                    cross_listings = EXCLUDED.cross_listings, catalog_attributes = EXCLUDED.catalog_attributes,
                    subject_context = EXCLUDED.subject_context, source_hash = EXCLUDED.source_hash, updated_at = now()
            """, values)
            _refresh_prerequisite_dependents(conn)
            _complete_sync(conn, run_id, len(rows), {"path": str(path)})
            return len(rows)
        except Exception as exc:
            _fail_sync(conn, run_id, exc)
            raise


def import_terms_and_sections(paths: Iterable[Path]) -> int:
    total = 0
    parsed_paths = list(paths)
    with connection() as conn:
        run_id = _record_sync(conn, "section_csv_bootstrap")
        try:
            term_rows: list[tuple[str, int, str, bool]] = []
            all_rows: list[dict[str, str]] = []
            latest_by_season: dict[str, str] = {}
            for path in parsed_paths:
                with path.open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                if not rows:
                    continue
                term_code = rows[0].get("term_code", "").strip()
                year, season = _term_parts(term_code)
                latest_by_season[season] = max(latest_by_season.get(season, ""), term_code)
                term_rows.append((term_code, year, season, False))
                all_rows.extend(rows)
            for index, (term_code, year, season, _) in enumerate(term_rows):
                term_rows[index] = (term_code, year, season, latest_by_season[season] == term_code)
            _executemany(conn, """
                INSERT INTO terms (term_code, academic_year, season, is_search_active)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (term_code) DO UPDATE SET academic_year = EXCLUDED.academic_year,
                    season = EXCLUDED.season, is_search_active = EXCLUDED.is_search_active, updated_at = now()
            """, term_rows)
            _upsert_sections(conn, all_rows, datetime.now(timezone.utc))
            _refresh_section_instructors(conn)
            total = len(all_rows)
            _complete_sync(conn, run_id, total, {"paths": [str(path) for path in parsed_paths]})
        except Exception as exc:
            _fail_sync(conn, run_id, exc)
            raise
    refresh_live_stats()
    return total


def _section_values(row: dict[str, str], observed_at: datetime) -> tuple[Any, ...]:
    attrs = _json_array(row.get("attributes", ""))
    status = row.get("seat_status_open", "").strip().upper()
    source_hash = _hash(row)
    return (
        row.get("term_code", "").strip(), row.get("crn", "").strip(),
        _course_id(row.get("subject", ""), row.get("course_number", "")), row.get("section", "").strip(),
        row.get("title", "").strip(), json.dumps(attrs), row.get("site", "").strip(),
        row.get("schedule_type", "").strip(), row.get("instruction_type", "").strip(),
        row.get("credit_hours", "").strip(), status, True if status == "Y" else False if status == "N" else None,
        _int(row.get("max_enrollment")), _int(row.get("enrollment")), _int(row.get("seats_available")),
        _int(row.get("wait_capacity")), _int(row.get("wait_count")), _int(row.get("wait_available")),
        row.get("instructors", "").strip(), json.dumps(_json_object(row.get("instructor_details", "[]"))),
        row.get("meeting_days", "").strip(), row.get("meeting_times", "").strip(),
        row.get("meeting_dates", "").strip(), row.get("meeting_locations", "").strip(), source_hash, observed_at,
    )


def _upsert_sections(conn: psycopg.Connection[Any], rows: Iterable[dict[str, str]], observed_at: datetime) -> set[str]:
    changed: set[str] = set()
    materialized_rows = list(rows)
    term_codes = sorted({row.get("term_code", "").strip() for row in materialized_rows if row.get("term_code", "").strip()})
    existing_rows = list(conn.execute("""
        SELECT term_code, crn, course_id, section_number, title, attributes, site, schedule_type,
            instruction_type, credit_hours, seat_status_open, max_enrollment, enrollment,
            seats_available, wait_capacity, wait_count, wait_available, instructors, instructor_details,
            meeting_days, meeting_times, meeting_dates, meeting_locations, is_present
        FROM sections WHERE term_code = ANY(%s)
    """, (term_codes,))) if term_codes else []
    existing_by_key = {(str(row["term_code"]), str(row["crn"])): dict(row) for row in existing_rows}
    for row in materialized_rows:
        existing = existing_by_key.get((row.get("term_code", "").strip(), row.get("crn", "").strip()))
        if existing and existing["is_present"] and _section_change_values(existing, stored=True) == _section_change_values(row):
            continue
        values = _section_values(row, observed_at)
        result = conn.execute("""
            INSERT INTO sections (term_code, crn, course_id, section_number, title, attributes, site,
                schedule_type, instruction_type, credit_hours, seat_status_open, is_open, max_enrollment,
                enrollment, seats_available, wait_capacity, wait_count, wait_available, instructors,
                instructor_details, meeting_days, meeting_times, meeting_dates, meeting_locations,
                source_hash, last_observed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s)
            ON CONFLICT (term_code, crn) DO UPDATE SET
                course_id = EXCLUDED.course_id, section_number = EXCLUDED.section_number, title = EXCLUDED.title,
                attributes = EXCLUDED.attributes, site = EXCLUDED.site, schedule_type = EXCLUDED.schedule_type,
                instruction_type = EXCLUDED.instruction_type, credit_hours = EXCLUDED.credit_hours,
                seat_status_open = EXCLUDED.seat_status_open, is_open = EXCLUDED.is_open,
                max_enrollment = EXCLUDED.max_enrollment, enrollment = EXCLUDED.enrollment,
                seats_available = EXCLUDED.seats_available, wait_capacity = EXCLUDED.wait_capacity,
                wait_count = EXCLUDED.wait_count, wait_available = EXCLUDED.wait_available,
                instructors = EXCLUDED.instructors, instructor_details = EXCLUDED.instructor_details,
                meeting_days = EXCLUDED.meeting_days, meeting_times = EXCLUDED.meeting_times,
                meeting_dates = EXCLUDED.meeting_dates, meeting_locations = EXCLUDED.meeting_locations,
                is_present = TRUE, source_hash = EXCLUDED.source_hash, last_observed_at = EXCLUDED.last_observed_at,
                updated_at = now()
            WHERE sections.source_hash IS DISTINCT FROM EXCLUDED.source_hash OR NOT sections.is_present
            RETURNING course_id
        """, values).fetchone()
        if result:
            changed.add(str(result["course_id"]))
            conn.execute("""
                INSERT INTO section_observations (term_code, crn, observed_at, source_hash, seat_status_open,
                    enrollment, seats_available, wait_count, wait_available)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (values[0], values[1], observed_at, values[-2], values[10], values[13], values[14], values[16], values[17]))
    return changed


_SECTION_CHANGE_FIELDS = (
    "course_id", "section", "title", "attributes", "site", "schedule_type", "instruction_type",
    "credit_hours", "seat_status_open", "max_enrollment", "enrollment", "seats_available",
    "wait_capacity", "wait_count", "wait_available", "instructors", "instructor_details",
    "meeting_days", "meeting_times", "meeting_dates", "meeting_locations",
)
_SECTION_NULLISH_FIELDS = {
    "max_enrollment", "enrollment", "seats_available", "wait_capacity", "wait_count", "wait_available",
}
_NULLISH_SECTION_VALUES = {"", "NA", "N/A", "NULL", "NONE"}


def _canonical_section_scalar(value: Any, *, nullish: bool = False) -> str:
    cleaned = str(value or "").strip()
    return "" if nullish and cleaned.upper() in _NULLISH_SECTION_VALUES else cleaned


def _canonical_section_text(value: Any) -> str:
    """Ignore presentation-only whitespace changes from the upstream source."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical_instructor_details(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = _json_object(value)
    if not isinstance(value, list):
        return []
    details = [item for item in value if isinstance(item, dict)]
    return sorted(
        details,
        key=lambda item: (str(item.get("more") or ""), str(item.get("name") or "").casefold()),
    )


def _canonical_instructors(value: Any, details: list[dict[str, Any]]) -> str:
    if details:
        return "; ".join(str(item.get("name") or "").strip() for item in details if item.get("name"))
    return "; ".join(sorted(
        (part.strip() for part in str(value or "").split(";") if part.strip()),
        key=str.casefold,
    ))


def _canonical_section_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize source-only representation differences before hashing or writing."""
    normalized = dict(row)
    for field in (
        "subject", "course_number", "section", "title", "site", "schedule_type", "instruction_type",
        "credit_hours", "seat_status_open", "meeting_days", "meeting_times", "meeting_dates", "meeting_locations",
    ):
        normalized[field] = _canonical_section_text(normalized.get(field))
    for field in _SECTION_NULLISH_FIELDS:
        normalized[field] = _canonical_section_scalar(normalized.get(field), nullish=True)
    normalized["attributes"] = "|".join(sorted(
        (_canonical_section_text(value) for value in _json_array(str(normalized.get("attributes") or ""))),
        key=str.casefold,
    ))
    details = _canonical_instructor_details(normalized.get("instructor_details"))
    normalized["instructor_details"] = json.dumps(details, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    normalized["instructors"] = _canonical_instructors(normalized.get("instructors"), details)
    return normalized


def _section_change_values(row: dict[str, Any], *, stored: bool = False) -> dict[str, str]:
    """Return the persisted section fields in the same stable shape for a diff."""
    values: dict[str, str] = {
        "course_id": str(row.get("course_id") or _course_id(str(row.get("subject") or ""), str(row.get("course_number") or ""))),
        "section": str(row.get("section_number") if stored else row.get("section") or ""),
        "title": _canonical_section_text(row.get("title")),
        "attributes": "|".join(sorted((_canonical_section_text(value) for value in (row.get("attributes") or [])), key=str.casefold)) if stored else "|".join(sorted((_canonical_section_text(value) for value in _json_array(str(row.get("attributes") or ""))), key=str.casefold)),
        "site": _canonical_section_text(row.get("site")),
        "schedule_type": _canonical_section_text(row.get("schedule_type")),
        "instruction_type": _canonical_section_text(row.get("instruction_type")),
        "credit_hours": _canonical_section_text(row.get("credit_hours")),
        "seat_status_open": _canonical_section_text(row.get("seat_status_open")),
        "meeting_days": _canonical_section_text(row.get("meeting_days")),
        "meeting_times": _canonical_section_text(row.get("meeting_times")),
        "meeting_dates": _canonical_section_text(row.get("meeting_dates")),
        "meeting_locations": _canonical_section_text(row.get("meeting_locations")),
    }
    for field in _SECTION_NULLISH_FIELDS:
        value = row.get(field)
        values[field] = _canonical_section_scalar(value, nullish=True)
    details = _canonical_instructor_details(row.get("instructor_details"))
    values["instructor_details"] = json.dumps(details or [], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    values["instructors"] = _canonical_instructors(row.get("instructors"), details)
    return values


def _section_change_detail(
    term_code: str,
    crn: str,
    previous: dict[str, str] | None,
    current: dict[str, str] | None,
) -> dict[str, Any]:
    representative = current or previous or {}
    changes = {
        field: {"before": (previous or {}).get(field, ""), "after": (current or {}).get(field, "")}
        for field in _SECTION_CHANGE_FIELDS
        if (previous or {}).get(field, "") != (current or {}).get(field, "")
    }
    return {
        "term_code": term_code,
        "crn": crn,
        "course_id": representative.get("course_id", ""),
        "section": representative.get("section", ""),
        "title": representative.get("title", ""),
        "change_type": "added" if previous is None else "removed" if current is None else "updated",
        "changes": changes,
    }


def _section_change_report(
    term_code: str,
    stored_rows: Iterable[dict[str, Any]],
    incoming_rows: Iterable[dict[str, Any]],
    *,
    show_changed: bool = False,
) -> dict[str, Any]:
    """Compare one complete term snapshot and return stable refresh metrics."""
    existing = {str(row["crn"]): dict(row) for row in stored_rows}
    incoming = {str(row.get("crn") or "").strip(): row for row in incoming_rows}
    changed_course_ids: set[str] = set()
    changed_crns: list[str] = []
    details: list[dict[str, Any]] = []
    added = removed = updated = seat_status_changed = 0
    for crn in sorted(set(existing) | set(incoming)):
        previous_row = existing.get(crn)
        current_row = incoming.get(crn)
        if previous_row is None:
            added += 1
            current_values = _section_change_values(current_row or {})
            changed_course_ids.add(current_values["course_id"])
            detail = _section_change_detail(term_code, crn, None, current_values)
        elif current_row is None:
            if not previous_row["is_present"]:
                continue
            removed += 1
            previous_values = _section_change_values(previous_row, stored=True)
            changed_course_ids.add(previous_values["course_id"])
            detail = _section_change_detail(term_code, crn, previous_values, None)
        else:
            previous_values = _section_change_values(previous_row, stored=True)
            current_values = _section_change_values(current_row)
            if previous_values == current_values and previous_row["is_present"]:
                continue
            updated += 1
            changed_course_ids.add(current_values["course_id"])
            detail = _section_change_detail(term_code, crn, previous_values, current_values)
            if previous_values["seat_status_open"] != current_values["seat_status_open"]:
                seat_status_changed += 1
        if show_changed:
            details.append(detail)
        changed_crns.append(crn)

    report: dict[str, Any] = {
        "term_code": term_code,
        "scanned_sections": len(incoming),
        "changed_course_ids": sorted(changed_course_ids),
        "changed_crns": changed_crns,
        "changed_courses": len(changed_course_ids),
        "changed_sections": added + removed + updated,
        "added_sections": added,
        "removed_sections": removed,
        "updated_sections": updated,
        "seat_status_changed_sections": seat_status_changed,
    }
    if show_changed:
        report["changed_section_details"] = details
    return report


def _sync_details(report: dict[str, Any]) -> dict[str, Any]:
    """Keep only compact, non-sensitive metrics in sync history."""
    fields = (
        "term_code",
        "scanned_sections",
        "changed_courses",
        "changed_sections",
        "added_sections",
        "removed_sections",
        "updated_sections",
        "seat_status_changed_sections",
    )
    return {field: report[field] for field in fields}


def sync_term_sections(
    term_code: str,
    rows: list[dict[str, str]],
    dry_run: bool = False,
    show_changed: bool = False,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Upsert one complete Howdy term and rebuild live stats only for changed courses."""
    rows = [_canonical_section_row(row) for row in rows]
    if not rows:
        raise ValueError("refusing to publish an empty section snapshot")
    if any(row.get("term_code", "").strip() != term_code for row in rows):
        raise ValueError("section rows must all belong to the requested term")
    crns = [row.get("crn", "").strip() for row in rows]
    if not all(crns) or len(crns) != len(set(crns)):
        raise ValueError("section snapshot contains missing or duplicate CRNs")
    with connection() as conn:
        stored_rows = list(conn.execute("""
            SELECT term_code, crn, course_id, section_number, title, attributes, site, schedule_type,
                instruction_type, credit_hours, seat_status_open, max_enrollment, enrollment,
                seats_available, wait_capacity, wait_count, wait_available, instructors,
                instructor_details, meeting_days, meeting_times, meeting_dates, meeting_locations,
                is_present
            FROM sections WHERE term_code = %s
        """, (term_code,)))
    report = _section_change_report(term_code, stored_rows, rows, show_changed=show_changed)
    report["dry_run"] = dry_run
    if dry_run:
        return report

    observed_at = datetime.now(timezone.utc)
    active_run_id = run_id if run_id is not None else start_section_refresh()
    try:
        with connection() as conn:
            year, season = _term_parts(term_code)
            # Search exposes one current offering per season. When a newly
            # discovered term becomes active, retire the older same-season
            # projection without deleting its historical section facts.
            conn.execute(
                "UPDATE terms SET is_search_active = FALSE WHERE season = %s AND term_code <> %s",
                (season, term_code),
            )
            conn.execute("""
                INSERT INTO terms (term_code, academic_year, season, is_search_active)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (term_code) DO UPDATE SET is_search_active = TRUE, updated_at = now()
            """, (term_code, year, season))
            changed = _upsert_sections(conn, rows, observed_at)
            incoming_crns = [row.get("crn", "").strip() for row in rows]
            removed = conn.execute("""
                UPDATE sections SET is_present = FALSE, updated_at = now()
                WHERE term_code = %s AND is_present AND NOT (crn = ANY(%s))
                RETURNING course_id
            """, (term_code, incoming_crns)).fetchall()
            changed.update(str(row["course_id"]) for row in removed)
            _refresh_section_instructors(conn, term_code)
        report["changed_course_ids"] = sorted(changed)
        report["changed_courses"] = len(changed)
        refresh_live_stats(changed)
        enqueue_search_updates(changed, "live_section_sync")
    except Exception as exc:
        fail_section_refresh(active_run_id, exc)
        raise
    with connection() as tracking_conn:
        _complete_sync(tracking_conn, active_run_id, len(rows), _sync_details(report))

    return report


def _refresh_status_payload(
    latest_attempt: dict[str, Any] | None,
    latest_success: dict[str, Any] | None,
    last_hour: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(hours=2),
) -> dict[str, Any]:
    """Shape section refresh history for the public status endpoint."""
    current_time = now or datetime.now(timezone.utc)
    latest_details = dict((latest_success or {}).get("details") or {})
    attempt_status = str((latest_attempt or {}).get("status") or "")
    success_at = (latest_success or {}).get("completed_at")
    if attempt_status == "running":
        state = "running"
    elif attempt_status == "failed" and (
        latest_success is None
        or (latest_attempt or {}).get("started_at") > (latest_success or {}).get("started_at")
    ):
        state = "failed"
    elif success_at is None:
        state = "unknown"
    elif current_time - success_at > stale_after:
        state = "stale"
    else:
        state = "healthy"

    hourly = last_hour or {}
    return {
        "state": state,
        "stale_after_hours": int(stale_after.total_seconds() // 3600),
        "last_attempt_at": (latest_attempt or {}).get("started_at"),
        "last_success_at": success_at,
        "last_error": (latest_attempt or {}).get("error_message") if attempt_status == "failed" else None,
        "latest": latest_details or None,
        "last_hour": {
            "runs": int(hourly.get("runs") or 0),
            "scanned_sections": int(hourly.get("scanned_sections") or 0),
            "changed_sections": int(hourly.get("changed_sections") or 0),
            "seat_status_changed_sections": int(hourly.get("seat_status_changed_sections") or 0),
        },
    }


def section_refresh_status() -> dict[str, Any]:
    """Return the latest Howdy refresh and aggregate successful work from the last hour."""
    with connection() as conn:
        latest_attempt = conn.execute("""
            SELECT started_at, completed_at, status, details, error_message
            FROM sync_runs
            WHERE source_name = 'howdy_sections'
            ORDER BY started_at DESC
            LIMIT 1
        """).fetchone()
        latest_success = conn.execute("""
            SELECT started_at, completed_at, status, details, error_message
            FROM sync_runs
            WHERE source_name = 'howdy_sections' AND status = 'succeeded'
            ORDER BY completed_at DESC
            LIMIT 1
        """).fetchone()
        last_hour = conn.execute("""
            SELECT
                count(*) AS runs,
                coalesce(sum((details->>'scanned_sections')::integer), 0) AS scanned_sections,
                coalesce(sum((details->>'changed_sections')::integer), 0) AS changed_sections,
                coalesce(sum((details->>'seat_status_changed_sections')::integer), 0)
                    AS seat_status_changed_sections
            FROM sync_runs
            WHERE source_name = 'howdy_sections'
              AND status = 'succeeded'
              AND completed_at >= now() - interval '1 hour'
        """).fetchone()
    return _refresh_status_payload(
        dict(latest_attempt) if latest_attempt else None,
        dict(latest_success) if latest_success else None,
        dict(last_hour) if last_hour else None,
    )


def import_restrictions(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle, connection() as conn:
        rows = list(csv.DictReader(handle))
        run_id = _record_sync(conn, "restrictions_csv")
        try:
            values = [(
                row.get("term", "").strip(), row.get("crn", "").strip(),
                _course_id(row.get("subject", ""), row.get("course_number", "")),
                row.get("restrictions", "").strip(), json.dumps({}), _hash(row),
            ) for row in rows]
            _executemany(conn, """
                INSERT INTO section_restrictions (term_code, crn, course_id, raw_restrictions, parsed_restrictions, source_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (term_code, crn) DO UPDATE SET course_id = EXCLUDED.course_id,
                    raw_restrictions = EXCLUDED.raw_restrictions, parsed_restrictions = EXCLUDED.parsed_restrictions,
                    source_hash = EXCLUDED.source_hash, updated_at = now()
            """, values)
            _complete_sync(conn, run_id, len(rows), {"path": str(path)})
            return len(rows)
        except Exception as exc:
            _fail_sync(conn, run_id, exc)
            raise


def restriction_snapshot_crns(term_code: str) -> set[str]:
    """Return CRNs already checked by the live restriction sync."""
    with connection() as conn:
        return {
            str(row["crn"])
            for row in conn.execute(
                "SELECT crn FROM section_restrictions WHERE term_code = %s",
                (term_code,),
            )
        }


def sync_section_restrictions(
    term_code: str,
    rows: list[dict[str, str]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Upsert freshly fetched restriction snapshots, including empty results."""
    if any(str(row.get("term_code") or "").strip() != term_code for row in rows):
        raise ValueError("restriction rows must all belong to the requested term")
    crns = [str(row.get("crn") or "").strip() for row in rows]
    if not all(crns) or len(crns) != len(set(crns)):
        raise ValueError("restriction snapshot contains missing or duplicate CRNs")
    if not rows:
        return {"scanned_restrictions": 0, "changed_restrictions": 0, "restriction_changed_course_ids": []}

    with connection() as conn:
        existing = {
            str(row["crn"]): dict(row)
            for row in conn.execute(
                """
                SELECT crn, course_id, raw_restrictions
                FROM section_restrictions
                WHERE term_code = %s AND crn = ANY(%s)
                """,
                (term_code, crns),
            )
        }

    values: list[tuple[str, str, str, str, str, str]] = []
    changed_course_ids: set[str] = set()
    changed_restrictions = 0
    for row in rows:
        crn = str(row.get("crn") or "").strip()
        current_course_id = _course_id(
            str(row.get("subject") or ""),
            str(row.get("course_number") or ""),
        )
        raw = str(row.get("restrictions") or "").strip()
        previous = existing.get(crn)
        if (
            previous is None
            or str(previous.get("course_id") or "") != current_course_id
            or str(previous.get("raw_restrictions") or "") != raw
        ):
            changed_restrictions += 1
            changed_course_ids.add(current_course_id)
            if previous and previous.get("course_id"):
                changed_course_ids.add(str(previous["course_id"]))
        source = {
            "term": term_code,
            "crn": crn,
            "course_id": current_course_id,
            "restrictions": raw,
        }
        values.append((term_code, crn, current_course_id, raw, json.dumps({}), _hash(source)))

    if not dry_run:
        with connection() as conn:
            _executemany(conn, """
                INSERT INTO section_restrictions (
                    term_code, crn, course_id, raw_restrictions,
                    parsed_restrictions, source_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (term_code, crn) DO UPDATE SET
                    course_id = EXCLUDED.course_id,
                    raw_restrictions = EXCLUDED.raw_restrictions,
                    parsed_restrictions = EXCLUDED.parsed_restrictions,
                    source_hash = EXCLUDED.source_hash,
                    updated_at = now()
            """, values)

    return {
        "scanned_restrictions": len(rows),
        "changed_restrictions": changed_restrictions,
        "restriction_changed_course_ids": sorted(changed_course_ids),
    }


def import_majors(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle, connection() as conn:
        rows = list(csv.DictReader(handle))
        run_id = _record_sync(conn, "major_taxonomy_csv")
        try:
            _executemany(conn, """
                INSERT INTO majors (normalized_major, major, department, normalized_department, college, normalized_college, source_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (normalized_major) DO UPDATE SET major = EXCLUDED.major, department = EXCLUDED.department,
                    normalized_department = EXCLUDED.normalized_department, college = EXCLUDED.college,
                    normalized_college = EXCLUDED.normalized_college, source_url = EXCLUDED.source_url, updated_at = now()
            """, [(
                row.get("normalized_major", "").strip(), row.get("major", "").strip(), row.get("department", "").strip(),
                row.get("normalized_department", "").strip(), row.get("college", "").strip(),
                row.get("normalized_college", "").strip(), row.get("source_url", "").strip(),
            ) for row in rows])
            _complete_sync(conn, run_id, len(rows), {"path": str(path)})
            return len(rows)
        except Exception as exc:
            _fail_sync(conn, run_id, exc)
            raise


GRADE_COLUMNS = [
    "a_plus_count", "a_count", "a_minus_count", "b_plus_count", "b_count", "b_minus_count",
    "c_plus_count", "c_count", "c_minus_count", "d_plus_count", "d_count", "d_minus_count",
    "f_count", "i_count", "s_count", "p_count", "u_count", "q_count", "x_count",
]


def replace_grade_history_snapshot(
    path: Path,
    term_codes: Iterable[str] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and atomically replace grade-report rows for complete term snapshots.

    Grade reports are occasionally corrected. Replacing a selected term is safer
    than retaining both the prior and corrected row under different source hashes.
    """
    selected_terms = sorted({str(term).strip() for term in term_codes or [] if str(term).strip()})
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if selected_terms:
        selected = set(selected_terms)
        rows = [row for row in rows if row.get("term_code", "").strip() in selected]
    snapshot_terms = sorted({row.get("term_code", "").strip() for row in rows if row.get("term_code", "").strip()})
    if not rows or not snapshot_terms:
        raise ValueError("grade snapshot contains no rows for the requested term(s)")
    if selected_terms and snapshot_terms != selected_terms:
        missing = sorted(set(selected_terms) - set(snapshot_terms))
        raise ValueError(f"grade snapshot is missing requested term(s): {', '.join(missing)}")
    hashes = [_hash(row) for row in rows]
    if len(hashes) != len(set(hashes)):
        raise ValueError("grade snapshot contains duplicate rows")

    values = [
        (
            source_hash, row.get("term_code", "").strip(), _int(row.get("year")) or 0,
            row.get("semester", "").strip().lower(), _course_id(row.get("subject", ""), row.get("course_number", "")),
            row.get("section", "").strip(), row.get("college_code", "").strip(), row.get("department", "").strip(),
            row.get("instructor", "").strip(), _int(row.get("total_enrollment")) or 0,
            _int(row.get("total_graded")) or 0, _float(row.get("gpa")),
            json.dumps({column: _int(row.get(column)) or 0 for column in GRADE_COLUMNS}), json.dumps(row),
        )
        for source_hash, row in zip(hashes, rows)
    ]
    source_hash = _hash(rows)
    with connection() as conn:
        existing = int(conn.execute(
            "SELECT count(*) AS count FROM grade_history WHERE source_term_code = ANY(%s)",
            (snapshot_terms,),
        ).fetchone()["count"])
        if dry_run:
            return {
                "path": str(path), "terms": snapshot_terms, "snapshot_hash": source_hash,
                "incoming_rows": len(rows), "replaced_rows": existing, "dry_run": True,
            }
        run_id = _record_sync(conn, "grade_history_snapshot")
        try:
            conn.execute("""
                DELETE FROM historical_outcome_instructors
                WHERE source_hash IN (
                    SELECT source_hash FROM grade_history WHERE source_term_code = ANY(%s)
                )
            """, (snapshot_terms,))
            conn.execute("DELETE FROM grade_history WHERE source_term_code = ANY(%s)", (snapshot_terms,))
            _executemany(conn, """
                INSERT INTO grade_history (source_hash, source_term_code, academic_year, semester, course_id, section_number,
                    college_code, department, instructor, total_enrollment, total_graded, gpa, grade_counts, source_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, values)
            _refresh_historical_outcome_instructors(conn)
            _complete_sync(conn, run_id, len(rows), {
                "path": str(path), "terms": snapshot_terms, "snapshot_hash": source_hash,
                "replaced_rows": existing,
            })
        except Exception as exc:
            _fail_sync(conn, run_id, exc)
            raise
    # Rebuild all projections so rows removed from a corrected snapshot cannot
    # leave stale course or location aggregates behind.
    refresh_historical_stats(reset=True)
    return {
        "path": str(path), "terms": snapshot_terms, "snapshot_hash": source_hash,
        "incoming_rows": len(rows), "replaced_rows": existing, "dry_run": False,
    }


def import_grade_history(path: Path) -> int:
    """Bootstrap the committed grade CSV as the authoritative full snapshot."""
    return int(replace_grade_history_snapshot(path)["incoming_rows"])


def refresh_historical_stats(course_ids: Iterable[str] | None = None, *, reset: bool = False) -> None:
    ids = sorted(set(course_ids or []))
    scope = "WHERE course_id = ANY(%s)" if ids else ""
    params: tuple[Any, ...] = (ids,) if ids else ()
    with connection() as conn:
        if reset:
            if ids:
                raise ValueError("resetting historical stats requires a full rebuild")
            conn.execute("DELETE FROM course_historical_location_term_stats")
            conn.execute("DELETE FROM course_historical_term_stats")
            conn.execute("DELETE FROM course_historical_stats")
        conn.execute(f"""
            INSERT INTO course_historical_stats (course_id, total_historical_enrollment, historical_average_gpa,
                historical_gpa_weight, historical_section_count, historical_term_count, last_grade_term, updated_at)
            SELECT course_id, SUM(total_enrollment)::INTEGER,
                CASE WHEN SUM(total_graded) FILTER (WHERE gpa IS NOT NULL) > 0
                    THEN SUM(gpa * total_graded) FILTER (WHERE gpa IS NOT NULL) /
                         SUM(total_graded) FILTER (WHERE gpa IS NOT NULL) END,
                SUM(total_graded)::INTEGER, COUNT(*)::INTEGER, COUNT(DISTINCT source_term_code)::INTEGER,
                MAX(source_term_code), now()
            FROM grade_history {scope}
            GROUP BY course_id
            ON CONFLICT (course_id) DO UPDATE SET
                total_historical_enrollment = EXCLUDED.total_historical_enrollment,
                historical_average_gpa = EXCLUDED.historical_average_gpa,
                historical_gpa_weight = EXCLUDED.historical_gpa_weight,
                historical_section_count = EXCLUDED.historical_section_count,
                historical_term_count = EXCLUDED.historical_term_count,
                last_grade_term = EXCLUDED.last_grade_term, updated_at = now()
        """, params)
        conn.execute(f"""
            INSERT INTO course_historical_term_stats (course_id, academic_year, semester, total_enrollment,
                average_gpa, gpa_weight, section_count, term_count, updated_at)
            SELECT course_id, academic_year, semester, SUM(total_enrollment)::INTEGER,
                CASE WHEN SUM(total_graded) FILTER (WHERE gpa IS NOT NULL) > 0
                    THEN SUM(gpa * total_graded) FILTER (WHERE gpa IS NOT NULL) /
                         SUM(total_graded) FILTER (WHERE gpa IS NOT NULL) END,
                SUM(total_graded)::INTEGER, COUNT(*)::INTEGER, COUNT(DISTINCT source_term_code)::INTEGER, now()
            FROM grade_history {scope}
            GROUP BY course_id, academic_year, semester
            ON CONFLICT (course_id, academic_year, semester) DO UPDATE SET
                total_enrollment = EXCLUDED.total_enrollment, average_gpa = EXCLUDED.average_gpa,
                gpa_weight = EXCLUDED.gpa_weight, section_count = EXCLUDED.section_count,
                term_count = EXCLUDED.term_count, updated_at = now()
        """, params)

        # Historical grade rows do not contain a site. Attach the best
        # matching historical section, then apply the same College Station
        # aliases used by current-section filtering: blank sites count as
        # College Station, while Distance Education counts under both its own
        # location and College Station.
        conn.execute(f"""
            INSERT INTO course_historical_location_term_stats (
                course_id, academic_year, semester, location, total_enrollment,
                average_gpa, gpa_weight, section_count, term_count, updated_at
            )
            SELECT gh.course_id, gh.academic_year, gh.semester, mapped.location,
                SUM(gh.total_enrollment)::INTEGER,
                CASE WHEN SUM(gh.total_graded) FILTER (WHERE gh.gpa IS NOT NULL) > 0
                    THEN SUM(gh.gpa * gh.total_graded) FILTER (WHERE gh.gpa IS NOT NULL) /
                         SUM(gh.total_graded) FILTER (WHERE gh.gpa IS NOT NULL) END,
                SUM(gh.total_graded)::INTEGER, COUNT(*)::INTEGER,
                COUNT(DISTINCT gh.source_term_code)::INTEGER, now()
            FROM grade_history gh
            JOIN LATERAL (
                SELECT site
                FROM sections s
                WHERE substring(s.term_code FROM 1 FOR 5) = gh.source_term_code
                  AND s.course_id = gh.course_id AND s.section_number = gh.section_number
                ORDER BY s.last_observed_at DESC
                LIMIT 1
            ) s ON TRUE
            JOIN LATERAL (
                SELECT DISTINCT location
                FROM (
                    VALUES
                        (NULLIF(btrim(s.site), '')),
                        (CASE
                            WHEN lower(btrim(COALESCE(s.site, ''))) = ANY(%s)
                            THEN 'College Station'
                        END)
                ) locations(location)
                WHERE location IS NOT NULL
            ) mapped ON TRUE
            {scope}
            GROUP BY gh.course_id, gh.academic_year, gh.semester, mapped.location
            ON CONFLICT (course_id, academic_year, semester, location) DO UPDATE SET
                total_enrollment = EXCLUDED.total_enrollment,
                average_gpa = EXCLUDED.average_gpa,
                gpa_weight = EXCLUDED.gpa_weight,
                section_count = EXCLUDED.section_count,
                term_count = EXCLUDED.term_count,
                updated_at = now()
        """, (list(COLLEGE_STATION_SECTION_SITES), *params))


def replace_section_filter_facts(rows: Iterable[dict[str, Any]]) -> int:
    """Replace the active-section filtering projection in one transaction."""
    values = [(
        row["term_code"], row["crn"], row["course_id"], row["filter_terms"],
        row["filter_locations"], row["instruction_type"], row["filter_core_attributes"],
        row["filter_graduation_requirements"], row["attributes"], row["restriction_data_available"],
        row["has_major_restriction"], row["major_restriction_aliases"],
        row["excluded_major_restriction_aliases"], row["has_department_restriction"],
        row["department_restriction_aliases"], row["excluded_department_restriction_aliases"],
        row["has_college_restriction"], row["college_restriction_aliases"],
        row["excluded_college_restriction_aliases"], json.dumps(row["section_data"]),
    ) for row in rows]
    with connection() as conn:
        conn.execute("DELETE FROM section_filter_facts")
        _executemany(conn, """
            INSERT INTO section_filter_facts (
                term_code, crn, course_id, filter_terms, filter_locations, instruction_type,
                filter_core_attributes, filter_graduation_requirements, attributes,
                restriction_data_available, has_major_restriction, major_restriction_aliases,
                excluded_major_restriction_aliases, has_department_restriction,
                department_restriction_aliases, excluded_department_restriction_aliases,
                has_college_restriction, college_restriction_aliases,
                excluded_college_restriction_aliases, section_data
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s::jsonb
            )
        """, values)
    return len(values)


def _active_section_filter_clauses(
    filters: ActiveSectionFilterSpec,
    *,
    section_alias: str,
    term_alias: str,
) -> tuple[list[str], list[Any]]:
    """Build the shared same-CRN predicates used by retrieval and hydration."""
    clauses: list[str] = []
    params: list[Any] = []

    def overlap(column: str, values: list[str]) -> None:
        if values:
            clauses.append(f"{column} && %s::text[]")
            params.append([value.casefold() for value in values])

    if filters["filter_terms"]:
        clauses.append(f"{term_alias}.season = ANY(%s)")
        params.append([value.casefold() for value in filters["filter_terms"]])
    overlap(f"{section_alias}.filter_locations", filters["locations"])
    overlap(f"ARRAY[{section_alias}.instruction_type]", filters["instruction_types"])
    overlap(f"{section_alias}.filter_core_attributes", filters["core_attributes"])
    overlap(
        f"{section_alias}.filter_graduation_requirements",
        filters["graduation_requirements"],
    )
    if filters["attributes"]:
        clauses.append(f"{section_alias}.attributes @> %s::text[]")
        params.append([value.casefold() for value in filters["attributes"]])

    major_aliases = [value.casefold() for value in filters["major_aliases"]]
    if major_aliases:
        department_aliases = [value.casefold() for value in filters["department_aliases"]]
        college_aliases = [value.casefold() for value in filters["college_aliases"]]
        clauses.extend([
            f"{section_alias}.restriction_data_available",
            f"(NOT {section_alias}.has_major_restriction "
            f"OR {section_alias}.major_restriction_aliases && %s::text[])",
            f"(NOT {section_alias}.has_department_restriction "
            f"OR {section_alias}.department_restriction_aliases && %s::text[])",
            f"(NOT {section_alias}.has_college_restriction "
            f"OR {section_alias}.college_restriction_aliases && %s::text[])",
            f"NOT ({section_alias}.excluded_major_restriction_aliases && %s::text[])",
            f"NOT ({section_alias}.excluded_department_restriction_aliases && %s::text[])",
            f"NOT ({section_alias}.excluded_college_restriction_aliases && %s::text[])",
        ])
        params.extend([
            major_aliases,
            department_aliases,
            college_aliases,
            major_aliases,
            department_aliases,
            college_aliases,
        ])
    return clauses, params


def active_section_exists_predicate(
    outer_course_id_sql: str,
    filters: ActiveSectionFilterSpec,
) -> tuple[str, list[Any]]:
    """Return a correlated same-CRN predicate for a course retrieval query."""
    clauses, params = _active_section_filter_clauses(
        filters,
        section_alias="candidate_section",
        term_alias="candidate_term",
    )
    clauses.insert(0, f"candidate_section.course_id = {outer_course_id_sql}")
    return f"""
        EXISTS (
            SELECT 1
            FROM section_filter_facts candidate_section
            JOIN terms candidate_term
              ON candidate_term.term_code = candidate_section.term_code
             AND candidate_term.is_search_active
            WHERE {' AND '.join(clauses)}
        )
    """, params


def matching_active_sections(
    course_ids: list[str] | None, *, filter_terms: list[str], locations: list[str],
    instruction_types: list[str], core_attributes: list[str],
    graduation_requirements: list[str], attributes: list[str],
    major_aliases: list[str], department_aliases: list[str], college_aliases: list[str],
    require_current_sections: bool,
    course_prefix: str | None = None,
    degree_levels: list[str] | None = None,
    include_section_data: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Return active sections that satisfy every filter on the same CRN.

    Optional stable course predicates are evaluated here too, before this
    function emits candidate course IDs for ParadeDB retrieval.
    """
    if course_ids is not None and not course_ids:
        return {}

    clauses: list[str] = []
    params: list[Any] = []
    if course_ids is not None:
        clauses.append("f.course_id = ANY(%s)")
        params.append(course_ids)
    normalized_prefix = (course_prefix or "").strip().upper()
    normalized_degree_levels = [value.strip() for value in degree_levels or [] if value.strip()]
    course_join = ""
    if normalized_prefix or normalized_degree_levels:
        course_join = "JOIN courses c ON c.course_id = f.course_id"
    if normalized_prefix:
        clauses.append("c.subject = %s")
        params.append(normalized_prefix)
    if normalized_degree_levels:
        clauses.append("(LEFT(c.course_number, 1) || '00') = ANY(%s)")
        params.append(normalized_degree_levels)

    filter_clauses, filter_params = _active_section_filter_clauses(
        ActiveSectionFilterSpec(
            filter_terms=filter_terms,
            locations=locations,
            instruction_types=instruction_types,
            core_attributes=core_attributes,
            graduation_requirements=graduation_requirements,
            attributes=attributes,
            major_aliases=major_aliases,
            department_aliases=department_aliases,
            college_aliases=college_aliases,
        ),
        section_alias="f",
        term_alias="t",
    )
    clauses.extend(filter_clauses)
    params.extend(filter_params)
    where_clause = " AND ".join(clauses) if clauses else "TRUE"
    select_columns = "f.course_id, f.section_data" if include_section_data else "DISTINCT f.course_id"
    order_clause = "ORDER BY f.course_id, f.term_code, f.crn" if include_section_data else "ORDER BY f.course_id"
    with connection() as conn:
        rows = list(conn.execute(f"""
            SELECT {select_columns}
            FROM section_filter_facts f
            {course_join}
            JOIN terms t ON t.term_code = f.term_code AND t.is_search_active
            WHERE {where_clause}
            {order_clause}
        """, params))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not include_section_data:
            grouped.setdefault(str(row["course_id"]), [])
            continue
        section = row["section_data"]
        if isinstance(section, str):
            section = json.loads(section)
        grouped.setdefault(str(row["course_id"]), []).append(section)
    return grouped


def matching_active_section_summaries(
    course_ids: list[str],
    filters: ActiveSectionFilterSpec,
) -> dict[str, dict[str, Any]]:
    """Return compact filtered section counts and attributes by course."""
    if not course_ids:
        return {}

    clauses = ["f.course_id = ANY(%s)"]
    params: list[Any] = [course_ids]
    filter_clauses, filter_params = _active_section_filter_clauses(
        filters,
        section_alias="f",
        term_alias="t",
    )
    clauses.extend(filter_clauses)
    params.extend(filter_params)
    with connection() as conn:
        rows = list(conn.execute(f"""
            SELECT f.course_id, f.attributes
            FROM section_filter_facts f
            JOIN terms t ON t.term_code = f.term_code AND t.is_search_active
            WHERE {' AND '.join(clauses)}
        """, params))

    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        current_course_id = str(row["course_id"])
        summary = summaries.setdefault(
            current_course_id,
            {"current_section_count": 0, "course_attributes": set()},
        )
        summary["current_section_count"] += 1
        summary["course_attributes"].update(str(value) for value in row.get("attributes") or [])
    return {
        course_id: {
            "current_section_count": summary["current_section_count"],
            "course_attributes": sorted(summary["course_attributes"], key=str.casefold),
        }
        for course_id, summary in summaries.items()
    }


def refresh_live_stats(course_ids: Iterable[str] | None = None) -> None:
    ids = sorted(set(course_ids or []))
    course_filter = "AND s.course_id = ANY(%s)" if ids else ""
    params: tuple[Any, ...] = (ids,) if ids else ()
    with connection() as conn:
        if ids:
            # A course whose final active section disappeared must not retain
            # stale live facets/counts from the prior sync.
            conn.execute("DELETE FROM course_live_stats WHERE course_id = ANY(%s)", (ids,))
        conn.execute(f"""
            INSERT INTO course_live_stats (course_id, active_term_codes, current_section_count, open_section_count,
                total_seats_available, locations, instruction_types, attributes, last_section_sync_at, updated_at)
            WITH active_sections AS (
                SELECT s.*
                FROM sections s
                JOIN terms t ON t.term_code = s.term_code AND t.is_search_active
                WHERE s.is_present {course_filter}
            ), counts AS (
                SELECT course_id, jsonb_agg(DISTINCT term_code) AS active_term_codes,
                    COUNT(*)::INTEGER AS current_section_count,
                    COUNT(*) FILTER (WHERE is_open)::INTEGER AS open_section_count,
                    COALESCE(SUM(seats_available), 0)::INTEGER AS total_seats_available,
                    MAX(last_observed_at) AS last_section_sync_at
                FROM active_sections
                GROUP BY course_id
            ), filters AS (
                SELECT s.course_id,
                    COALESCE(jsonb_agg(DISTINCT s.site) FILTER (WHERE s.site <> ''), '[]'::jsonb) AS locations,
                    COALESCE(jsonb_agg(DISTINCT s.instruction_type) FILTER (WHERE s.instruction_type <> ''), '[]'::jsonb) AS instruction_types,
                    COALESCE(jsonb_agg(DISTINCT attribute.value) FILTER (WHERE attribute.value <> ''), '[]'::jsonb) AS attributes
                FROM active_sections s
                LEFT JOIN LATERAL jsonb_array_elements_text(s.attributes) AS attribute(value) ON TRUE
                GROUP BY s.course_id
            )
            SELECT counts.course_id, counts.active_term_codes, counts.current_section_count, counts.open_section_count,
                counts.total_seats_available, filters.locations, filters.instruction_types, filters.attributes,
                counts.last_section_sync_at, now()
            FROM counts JOIN filters USING (course_id)
            ON CONFLICT (course_id) DO UPDATE SET
                active_term_codes = EXCLUDED.active_term_codes, current_section_count = EXCLUDED.current_section_count,
                open_section_count = EXCLUDED.open_section_count, total_seats_available = EXCLUDED.total_seats_available,
                locations = COALESCE(EXCLUDED.locations, '[]'::jsonb),
                instruction_types = COALESCE(EXCLUDED.instruction_types, '[]'::jsonb),
                attributes = EXCLUDED.attributes, last_section_sync_at = EXCLUDED.last_section_sync_at, updated_at = now()
        """, params)


def enqueue_search_updates(course_ids: Iterable[str], reason: str) -> None:
    ids = sorted(set(course_ids))
    if not ids:
        return
    with connection() as conn:
        _executemany(conn,
            "INSERT INTO search_outbox (course_id, reason) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(course_id, reason) for course_id in ids],
        )


def bootstrap(data_dir: Path) -> dict[str, int]:
    subject_context: dict[str, str] = {}
    context_path = data_dir / "tamu_subject_context.csv"
    if context_path.exists():
        with context_path.open(encoding="utf-8", newline="") as handle:
            subject_context = {row["course_prefix"].strip().upper(): row["subject_context"].strip() for row in csv.DictReader(handle)}
    ensure_schema()
    result = {
        "courses": import_catalog(data_dir / "course_info_all.csv", subject_context),
        "sections": import_terms_and_sections(sorted(data_dir.glob("tamu_public_class_sections_*_with_attributes.csv"))),
        "restrictions": import_restrictions(data_dir / "tamu_restrictions_full.csv"),
        "majors": import_majors(data_dir / "tamu_major_taxonomy.csv"),
        "grade_history": import_grade_history(data_dir / "tamu_grade_distribution.csv"),
    }
    return result


def catalog_rows() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = list(conn.execute("""
            SELECT subject AS course_prefix, course_number AS number, title, credit_hours, description,
                prerequisites, restrictions, cross_listings, catalog_attributes AS attributes, subject_context
            FROM courses ORDER BY subject, course_number
        """))
    for row in rows:
        row["attributes"] = "| ".join(row["attributes"] or [])
    return rows


def sitemap_course_ids() -> list[str]:
    """Return every catalog course that has a public detail page."""
    with connection() as conn:
        return [
            str(row["course_id"])
            for row in conn.execute("SELECT course_id FROM courses ORDER BY course_id")
        ]


def sitemap_professor_ids() -> list[str]:
    """Return resolved instructor IDs that have a public professor page."""
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT instructor_id
            FROM (
                SELECT instructor_id
                FROM section_instructors
                WHERE instructor_id ~ '^[0-9]+$'
                UNION
                SELECT instructor_id
                FROM historical_outcome_instructors
                WHERE instructor_id ~ '^[0-9]+$'
            ) public_instructors
            ORDER BY instructor_id
            """
        )
        return [str(row["instructor_id"]) for row in rows]


def live_section_counts(course_ids: Iterable[str]) -> dict[str, int]:
    """Return active-section counts from the refreshed live projection."""
    ids = sorted({str(course_id).strip() for course_id in course_ids if str(course_id).strip()})
    if not ids:
        return {}
    with connection() as conn:
        rows = conn.execute(
            "SELECT course_id, current_section_count FROM course_live_stats WHERE course_id = ANY(%s)",
            (ids,),
        )
        return {str(row["course_id"]): int(row["current_section_count"] or 0) for row in rows}


def catalog_rank_statistics(course_ids: Iterable[str]) -> dict[str, dict[str, int]]:
    """Return the live-section and historical-enrollment signals for ranking."""
    ids = sorted({str(course_id).strip() for course_id in course_ids if str(course_id).strip()})
    if not ids:
        return {}
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT c.course_id,
                   COALESCE(l.current_section_count, 0) AS current_section_count,
                   COALESCE(h.total_historical_enrollment, 0) AS historical_enrollment
            FROM courses c
            LEFT JOIN course_live_stats l ON l.course_id = c.course_id
            LEFT JOIN course_historical_stats h ON h.course_id = c.course_id
            WHERE c.course_id = ANY(%s)
            """,
            (ids,),
        )
        return {
            str(row["course_id"]): {
                "current_section_count": int(row["current_section_count"] or 0),
                "historical_enrollment": int(row["historical_enrollment"] or 0),
            }
            for row in rows
        }


def catalog_row(course_id: str) -> dict[str, Any] | None:
    """Return the small, stable catalog record needed by a course page."""
    with connection() as conn:
        row = conn.execute("""
            SELECT subject AS course_prefix, course_number AS number, title, credit_hours, description,
                prerequisites, restrictions, cross_listings, catalog_attributes AS attributes, subject_context,
                prerequisite_dependents
            FROM courses
            WHERE course_id = %s
        """, (course_id,)).fetchone()
    if row is None:
        return None
    row["attributes"] = "| ".join(row["attributes"] or [])
    return dict(row)


def subject_contexts() -> dict[str, str]:
    with connection() as conn:
        return {str(row["subject"]): str(row["subject_context"]) for row in conn.execute(
            "SELECT subject, subject_context FROM courses"
        )}


def subject_options() -> list[str]:
    with connection() as conn:
        return [str(row["subject"]) for row in conn.execute("SELECT DISTINCT subject FROM courses ORDER BY subject")]


def active_term_codes() -> list[str]:
    with connection() as conn:
        return [str(row["term_code"]) for row in conn.execute(
            "SELECT term_code FROM terms WHERE is_search_active ORDER BY term_code"
        )]


def catalog_active_section_count() -> int:
    """Count active sections whose course can appear in the catalog search."""
    with connection() as conn:
        row = conn.execute("""
            SELECT count(*) AS count
            FROM sections s JOIN terms t ON t.term_code = s.term_code AND t.is_search_active
            JOIN courses c ON c.course_id = s.course_id
            WHERE s.is_present
        """).fetchone()
    return int(row["count"])


def section_filter_fact_count() -> int:
    with connection() as conn:
        row = conn.execute("SELECT count(*) AS count FROM section_filter_facts").fetchone()
    return int(row["count"])


def section_filter_fact_locations_stale() -> bool:
    """Detect section facts created before raw-site location normalization."""
    with connection() as conn:
        row = conn.execute("""
            SELECT EXISTS (
                SELECT 1
                FROM section_filter_facts f
                JOIN sections s ON s.term_code = f.term_code AND s.crn = f.crn
                JOIN terms t ON t.term_code = s.term_code AND t.is_search_active
                WHERE s.is_present AND (
                    CASE lower(btrim(COALESCE(s.site, '')))
                        WHEN '' THEN
                            NOT (f.filter_locations @> ARRAY['college station'])
                            OR btrim(COALESCE(f.section_data->>'site', '')) <> ''
                        WHEN 'college station' THEN
                            NOT (f.filter_locations @> ARRAY['college station'])
                        WHEN 'distance education' THEN
                            NOT (f.filter_locations @> ARRAY['college station', 'distance education'])
                        WHEN 'mcallen' THEN
                            f.filter_locations @> ARRAY['college station']
                            OR NOT (f.filter_locations @> ARRAY['mcallen'])
                        WHEN 'galveston' THEN
                            f.filter_locations @> ARRAY['college station']
                            OR NOT (f.filter_locations @> ARRAY['galveston'])
                        WHEN 'bryan' THEN
                            f.filter_locations @> ARRAY['college station']
                            OR NOT (f.filter_locations @> ARRAY['bryan'])
                        ELSE
                            f.filter_locations @> ARRAY['college station']
                            OR f.filter_locations
                                @> ARRAY[lower(btrim(COALESCE(s.site, '')))]
                    END
                )
            ) AS stale
        """).fetchone()
    return bool(row["stale"])


def current_sections_rows() -> list[dict[str, Any]]:
    """Return active-term section rows using the legacy CSV field names."""
    with connection() as conn:
        rows = list(conn.execute("""
            SELECT s.term_code, s.crn, split_part(s.course_id, '-', 1) AS subject,
                substring(s.course_id FROM position('-' IN s.course_id) + 1) AS course_number,
                s.section_number AS section, s.title, s.attributes, s.site, s.schedule_type,
                s.instruction_type, s.credit_hours, s.seat_status_open, s.max_enrollment, s.enrollment,
                s.seats_available, s.wait_capacity, s.wait_count, s.wait_available, s.instructors,
                s.instructor_details, s.meeting_days, s.meeting_times, s.meeting_dates, s.meeting_locations
            FROM sections s
            JOIN terms t ON t.term_code = s.term_code AND t.is_search_active
            WHERE s.is_present
            ORDER BY s.term_code, s.crn
        """))
    for row in rows:
        row["attributes"] = "| ".join(row["attributes"] or [])
        row["instructor_details"] = json.dumps(row["instructor_details"] or [])
        for field in ("max_enrollment", "enrollment", "seats_available", "wait_capacity", "wait_count", "wait_available"):
            row[field] = "" if row[field] is None else str(row[field])
    return rows


def _course_section_rows(course_id: str, term_predicate: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Return one course's College Station sections in the legacy row shape."""
    with connection() as conn:
        rows = list(conn.execute(f"""
            SELECT s.term_code, s.crn, split_part(s.course_id, '-', 1) AS subject,
                substring(s.course_id FROM position('-' IN s.course_id) + 1) AS course_number,
                s.section_number AS section, s.title, s.attributes, s.site, s.schedule_type,
                s.instruction_type, s.credit_hours, s.seat_status_open, s.max_enrollment, s.enrollment,
                s.seats_available, s.wait_capacity, s.wait_count, s.wait_available, s.instructors,
                s.instructor_details, s.meeting_days, s.meeting_times, s.meeting_dates, s.meeting_locations
            FROM sections s
            JOIN terms t ON t.term_code = s.term_code
            WHERE s.course_id = %s AND s.is_present
              AND lower(btrim(COALESCE(s.site, ''))) = ANY(%s)
              AND {term_predicate}
            ORDER BY s.term_code DESC, s.section_number, s.crn
        """, (course_id, list(COLLEGE_STATION_SECTION_SITES), *params)))
    for row in rows:
        row["attributes"] = "| ".join(row["attributes"] or [])
        row["instructor_details"] = json.dumps(row["instructor_details"] or [])
        for field in ("max_enrollment", "enrollment", "seats_available", "wait_capacity", "wait_count", "wait_available"):
            row[field] = "" if row[field] is None else str(row[field])
    return [dict(row) for row in rows]


def course_current_section_rows(course_id: str) -> list[dict[str, Any]]:
    return _course_section_rows(course_id, "t.is_search_active", ())


def course_recent_archived_section_rows(course_id: str, term_limit: int = 2) -> list[dict[str, Any]]:
    """Return the most recent non-active offerings for the course page archive."""
    with connection() as conn:
        rows = list(conn.execute("""
            WITH archived_terms AS (
                SELECT DISTINCT s.term_code
                FROM sections s
                JOIN terms t ON t.term_code = s.term_code
                WHERE s.course_id = %s AND s.is_present
                  AND lower(btrim(COALESCE(s.site, ''))) = ANY(%s)
                  AND NOT t.is_search_active
                ORDER BY s.term_code DESC
                LIMIT %s
            )
            SELECT s.term_code, s.crn, split_part(s.course_id, '-', 1) AS subject,
                substring(s.course_id FROM position('-' IN s.course_id) + 1) AS course_number,
                s.section_number AS section, s.title, s.attributes, s.site, s.schedule_type,
                s.instruction_type, s.credit_hours, s.seat_status_open, s.max_enrollment, s.enrollment,
                s.seats_available, s.wait_capacity, s.wait_count, s.wait_available, s.instructors,
                s.instructor_details, s.meeting_days, s.meeting_times, s.meeting_dates, s.meeting_locations
            FROM sections s
            JOIN archived_terms a ON a.term_code = s.term_code
            WHERE s.course_id = %s AND s.is_present
              AND lower(btrim(COALESCE(s.site, ''))) = ANY(%s)
            ORDER BY s.term_code DESC, s.section_number, s.crn
        """, (
            course_id,
            list(COLLEGE_STATION_SECTION_SITES),
            term_limit,
            course_id,
            list(COLLEGE_STATION_SECTION_SITES),
        )))
    for row in rows:
        row["attributes"] = "| ".join(row["attributes"] or [])
        row["instructor_details"] = json.dumps(row["instructor_details"] or [])
        for field in ("max_enrollment", "enrollment", "seats_available", "wait_capacity", "wait_count", "wait_available"):
            row[field] = "" if row[field] is None else str(row[field])
    return [dict(row) for row in rows]


def course_restriction_rows(course_id: str) -> list[dict[str, str]]:
    with connection() as conn:
        rows = list(conn.execute("""
            SELECT term_code, crn, course_id, raw_restrictions
            FROM section_restrictions
            WHERE course_id = %s
        """, (course_id,)))
    return [dict(row) for row in rows]


def instructor_current_section_rows(instructor_id: str) -> list[dict[str, Any]]:
    """Current section rows for one persisted Howdy instructor ID."""
    with connection() as conn:
        rows = list(conn.execute("""
            SELECT s.term_code, s.crn, split_part(s.course_id, '-', 1) AS subject,
                substring(s.course_id FROM position('-' IN s.course_id) + 1) AS course_number,
                s.course_id, c.subject || ' ' || c.course_number AS course_code, c.title AS course_title,
                si.instructor_name AS matched_instructor_name,
                s.section_number AS section, s.title, s.attributes, s.site, s.schedule_type,
                s.instruction_type, s.credit_hours, s.seat_status_open, s.max_enrollment, s.enrollment,
                s.seats_available, s.wait_capacity, s.wait_count, s.wait_available, s.instructors,
                s.instructor_details, s.meeting_days, s.meeting_times, s.meeting_dates, s.meeting_locations,
                COALESCE(r.raw_restrictions, '') AS raw_restrictions
            FROM section_instructors si
            JOIN sections s ON s.term_code = si.term_code AND s.crn = si.crn
            JOIN terms t ON t.term_code = s.term_code AND t.is_search_active
            JOIN courses c ON c.course_id = s.course_id
            LEFT JOIN section_restrictions r ON r.term_code = s.term_code AND r.crn = s.crn
            WHERE si.instructor_id = %s AND s.is_present
            ORDER BY s.term_code DESC, c.subject, c.course_number, s.section_number, s.crn
        """, (instructor_id,)))
    for row in rows:
        row["attributes"] = "| ".join(row["attributes"] or [])
        row["instructor_details"] = json.dumps(row["instructor_details"] or [])
        for field in ("max_enrollment", "enrollment", "seats_available", "wait_capacity", "wait_count", "wait_available"):
            row[field] = "" if row[field] is None else str(row[field])
    return [dict(row) for row in rows]


def catalog_rows_by_ids(course_ids: list[str]) -> list[dict[str, Any]]:
    if not course_ids:
        return []
    with connection() as conn:
        rows = list(conn.execute("""
            SELECT course_id, subject AS course_prefix, course_number AS number, title
            FROM courses
            WHERE course_id = ANY(%s)
        """, (course_ids,)))
    return [dict(row) for row in rows]


def restriction_term_codes() -> set[str]:
    with connection() as conn:
        return {str(row["term_code"]) for row in conn.execute("SELECT DISTINCT term_code FROM section_restrictions")}


def restrictions_rows() -> list[dict[str, str]]:
    with connection() as conn:
        rows = list(conn.execute("SELECT term_code, crn, course_id, raw_restrictions FROM section_restrictions"))
    values = []
    for row in rows:
        subject, _, number = str(row["course_id"]).partition("-")
        values.append({
            "term": str(row["term_code"]), "crn": str(row["crn"]), "subject": subject,
            "course_number": number, "restrictions": str(row["raw_restrictions"]),
        })
    return values


def major_rows() -> list[dict[str, str]]:
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM majors")]


def grade_rows(*, academic_year: int | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM grade_history"
    params: tuple[Any, ...] = ()
    if academic_year is not None:
        query += " WHERE academic_year = %s"
        params = (academic_year,)
    with connection() as conn:
        return list(conn.execute(query, params))


def historical_term_stat_rows(
    course_ids: list[str], semesters: list[str], comparison_year: int,
    locations: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not course_ids or not semesters:
        return []
    if locations:
        with connection() as conn:
            return list(conn.execute("""
                SELECT course_id, academic_year, semester,
                    SUM(total_enrollment)::INTEGER AS total_enrollment,
                    CASE WHEN SUM(gpa_weight) > 0
                        THEN SUM(average_gpa * gpa_weight) FILTER (WHERE average_gpa IS NOT NULL) /
                             SUM(gpa_weight) FILTER (WHERE average_gpa IS NOT NULL) END AS average_gpa,
                    SUM(gpa_weight)::INTEGER AS gpa_weight,
                    SUM(section_count)::INTEGER AS section_count,
                    SUM(term_count)::INTEGER AS term_count
                FROM course_historical_location_term_stats
                WHERE course_id = ANY(%s) AND semester = ANY(%s) AND academic_year <= %s
                  AND lower(location) = ANY(%s)
                GROUP BY course_id, academic_year, semester
                ORDER BY course_id, academic_year DESC, semester
            """, (course_ids, semesters, comparison_year, [location.casefold() for location in locations])))
    with connection() as conn:
        return list(conn.execute("""
            SELECT * FROM course_historical_term_stats
            WHERE course_id = ANY(%s) AND semester = ANY(%s) AND academic_year <= %s
            ORDER BY course_id, academic_year DESC, semester
        """, (course_ids, semesters, comparison_year)))


def historical_outcome_rows(
    course_id: str | None = None,
    start_year: int | None = None,
    instructor_id: str | None = None,
) -> list[dict[str, Any]]:
    """Grade rows plus the best available historical section metadata.

    Grade reports omit CRNs, so the link is intentionally based on the source
    term, course, and section number and remains nullable.
    """
    filters: list[str] = []
    params: list[Any] = []
    if course_id:
        filters.append("gh.course_id = %s")
        params.append(course_id)
    if start_year is not None:
        filters.append("gh.academic_year >= %s")
        params.append(start_year)
    instructor_join = ""
    instructor_columns = "NULL::text AS resolved_instructor_id, NULL::text AS resolved_instructor_name, NULL::text AS instructor_resolution_status"
    if instructor_id:
        instructor_join = """
            JOIN historical_outcome_instructors hoi
              ON hoi.source_hash = gh.source_hash AND hoi.instructor_id = %s
        """
        params.insert(0, instructor_id)
        instructor_columns = "hoi.instructor_id AS resolved_instructor_id, hoi.instructor_name AS resolved_instructor_name, hoi.resolution_status AS instructor_resolution_status"
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    with connection() as conn:
        return list(conn.execute(f"""
            SELECT gh.*, s.site, s.instruction_type, s.attributes, s.instructors, s.instructor_details,
                s.term_code AS matched_term_code, s.crn AS matched_crn, {instructor_columns}
            FROM grade_history gh
            {instructor_join}
            LEFT JOIN LATERAL (
                SELECT * FROM sections s
                WHERE substring(s.term_code FROM 1 FOR 5) = gh.source_term_code
                  AND s.course_id = gh.course_id AND s.section_number = gh.section_number
                ORDER BY s.last_observed_at DESC
                LIMIT 1
            ) s ON TRUE
            {where_clause}
            ORDER BY gh.academic_year, gh.semester, gh.course_id, gh.section_number
        """, params))


def database_counts() -> dict[str, int]:
    tables = ["courses", "terms", "sections", "section_restrictions", "grade_history", "course_live_stats", "course_historical_stats", "course_historical_term_stats", "course_historical_location_term_stats"]
    with connection() as conn:
        return {table: int(conn.execute(f"SELECT count(*) AS count FROM {table}").fetchone()["count"]) for table in tables}
