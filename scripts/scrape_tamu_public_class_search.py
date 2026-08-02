#!/usr/bin/env python3
"""Scrape TAMU Howdy public class-search sections into CSV or JSON."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BASE_URL = "https://howdyportal.tamu.edu"
TERMS_URL = urllib.parse.urljoin(BASE_URL, "/api/all-terms")
COURSE_SECTIONS_URL = urllib.parse.urljoin(BASE_URL, "/api/course-sections")
DEFAULT_OUTPUT = Path("data/tamu_public_class_sections.csv")
DEFAULT_OUTPUT_DIR = Path("data")
DEFAULT_FILENAME_TEMPLATE = "tamu_public_class_sections_{term_code}_with_attributes.csv"
SEMESTER_ORDER = {"Spring": "1", "Summer": "2", "Fall": "3"}


@dataclass(frozen=True)
class RestrictionEndpoint:
    route: str
    include_label: str
    value_fields: tuple[str, ...]
    indicator_fields: tuple[str, ...]


RESTRICTION_ENDPOINTS = [
    RestrictionEndpoint("section-program-restrictions", "Must be enrolled in one of the following Programs:", ("PROG_DESC",), ("SSRRPRG_PROGRAM_IND", "SSRRPRG_PROG_IND")),
    RestrictionEndpoint("section-college-restrictions", "Must be enrolled in one of the following Colleges:", ("COLLEGE_DESC", "STVCOLL_DESC", "COLL_DESC"), ("SSRRCOL_COLL_IND",)),
    RestrictionEndpoint("section-level-restrictions", "Must be enrolled in one of the following Levels:", ("LEVEL_DESC", "STVLEVL_DESC"), ("SSRRLEV_LEVL_IND",)),
    RestrictionEndpoint("section-degree-restrictions", "Must be enrolled in one of the following Degrees:", ("DEGREE_DESC", "STVDEGC_DESC"), ("SSRRDEG_DEGC_IND",)),
    RestrictionEndpoint("section-major-restrictions", "Must be enrolled in one of the following Majors:", ("STVMAJR_DESC",), ("SSRRMAJ_MAJOR_IND",)),
    RestrictionEndpoint("section-minor-restrictions", "Must be enrolled in one of the following Minors:", ("STVMAJR_DESC", "MINOR_DESC"), ("SSRRMAJ_MAJOR_IND", "SSRRMNR_MINOR_IND")),
    RestrictionEndpoint("section-concentrations-restrictions", "Must be enrolled in one of the following Concentrations:", ("CONCENTRATION_DESC", "STVMAJR_DESC", "SSRRMAJ_MAJR_CODE"), ("SSRRMAJ_MAJOR_IND",)),
    RestrictionEndpoint("section-department-restrictions", "Must be enrolled in one of the following Departments:", ("DEPT_DESC", "DEPARTMENT_DESC", "STVDEPT_DESC"), ("SSRRDEP_DEPT_IE_CDE",)),
    RestrictionEndpoint("section-cohort-restrictions", "Must be enrolled in one of the following Cohorts:", ("COHORT_DESC", "STVCHRT_DESC"), ("SSRRCHR_CHRT_IND",)),
    RestrictionEndpoint("section-student-attribute-restrictions", "Must be assigned one of the following Student Attributes:", ("STU_ATTR_CODE", "STVATTS_DESC"), ("SSRRATT_ATTS_IND",)),
    RestrictionEndpoint("section-classifications-restrictions", "Must be enrolled in one of the following Classifications:", ("STVCLAS_DESC",), ("SSRRCLS_CLAS_IND",)),
    RestrictionEndpoint("section-field-of-study-restrictions", "Must be enrolled in one of the following Fields of Study:", ("STVMAJR_DESC", "FIELD_OF_STUDY_DESC"), ("SSRRMAJ_MAJOR_IND",)),
    RestrictionEndpoint("section-campus-restrictions", "Must be enrolled in one of the following Campuses:", ("CAMPUS_DESC", "STVCAMP_DESC"), ("SSRRCAM_CAMP_IND",)),
]

FIELD_MAP = {
    "term_code": "SWV_CLASS_SEARCH_TERM",
    "crn": "SWV_CLASS_SEARCH_CRN",
    "subject": "SWV_CLASS_SEARCH_SUBJECT",
    "subject_description": "SWV_CLASS_SEARCH_SUBJECT_DESC",
    "course_number": "SWV_CLASS_SEARCH_COURSE",
    "section": "SWV_CLASS_SEARCH_SECTION",
    "title": "SWV_CLASS_SEARCH_TITLE",
    "attributes": "SWV_CLASS_SEARCH_ATTRIBUTES",
    "site": "SWV_CLASS_SEARCH_SITE",
    "part_of_term": "SWV_CLASS_SEARCH_PTRM",
    "schedule_type": "SWV_CLASS_SEARCH_SCHD",
    "instruction_type": "SWV_CLASS_SEARCH_INST_TYPE",
    "hours_low": "SWV_CLASS_SEARCH_HOURS_LOW",
    "hours_high": "SWV_CLASS_SEARCH_HOURS_HIGH",
    "hours_indicator": "SWV_CLASS_SEARCH_HOURS_IND",
    "section_hours": "SWV_CLASS_SEARCH_SSBSECT_HOURS",
    "has_syllabus": "SWV_CLASS_SEARCH_HAS_SYL_IND",
    "seat_status_open": "STUSEAT_OPEN",
    "max_enrollment": "SWV_CLASS_SEARCH_MAX_ENRL",
    "enrollment": "SWV_CLASS_SEARCH_ENRL",
    "seats_available": "SWV_CLASS_SEARCH_SEATS_AVAIL",
    "wait_capacity": "SWV_WAIT_CAPACITY",
    "wait_count": "SWV_WAIT_COUNT",
    "wait_available": "SWV_WAIT_AVAIL",
}

OUTPUT_FIELDS = [
    "term_code",
    "crn",
    "course",
    "subject",
    "course_number",
    "section",
    "title",
    "attributes",
    "subject_description",
    "site",
    "part_of_term",
    "schedule_type",
    "instruction_type",
    "credit_hours",
    "hours_low",
    "hours_high",
    "hours_indicator",
    "section_hours",
    "has_syllabus",
    "seat_status_open",
    "max_enrollment",
    "enrollment",
    "seats_available",
    "wait_capacity",
    "wait_count",
    "wait_available",
    "instructors",
    "instructor_details",
    "meeting_days",
    "meeting_times",
    "meeting_dates",
    "meeting_locations",
]


def request_json(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    retries: int = 3,
    timeout: int = 90,
) -> Any:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TAMUPublicClassSearchScraper/1.0)",
        "Accept": "application/json",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset, "replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)

    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def _first_restriction_text(row: dict[str, Any], fields: Iterable[str]) -> str:
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            return " ".join(str(value).split())
    return ""


def _restriction_indicator(
    endpoint: RestrictionEndpoint,
    row: dict[str, Any],
) -> str:
    indicator = _first_restriction_text(row, endpoint.indicator_fields)
    if indicator:
        return indicator
    for key, value in row.items():
        upper_key = key.upper()
        cleaned = str(value or "").strip().upper()
        if cleaned in {"I", "E"} and (
            upper_key.endswith("_IND") or upper_key.endswith("_IE_CDE")
        ):
            return cleaned
    return ""


def _restriction_label(endpoint: RestrictionEndpoint, indicator: str) -> str:
    if indicator.strip().upper().startswith("I"):
        return endpoint.include_label
    if endpoint.include_label.startswith("Must be assigned"):
        return endpoint.include_label.replace("Must be assigned", "May not be assigned", 1)
    return endpoint.include_label.replace("Must be enrolled", "May not be enrolled", 1)


def _join_restriction_values(values: Iterable[str]) -> str:
    seen: dict[str, str] = {}
    for value in values:
        cleaned = " ".join(str(value or "").split())
        if cleaned:
            seen.setdefault(cleaned.casefold(), cleaned)
    return ", ".join(seen.values())


def parse_restriction_response(
    endpoint: RestrictionEndpoint,
    rows: list[dict[str, Any]],
) -> str:
    """Render one Howdy restriction response using its authoritative I/E control row.

    Howdy sometimes puts a synthesized ``Cannot be`` label on a value row whose
    indicator is null. That row belongs to the preceding I/E control row, so its
    label must not replace the active header.
    """
    grouped: list[tuple[str, list[str]]] = []
    current_header = endpoint.include_label
    saw_control_row = False
    for row in rows:
        value = _first_restriction_text(row, endpoint.value_fields)
        indicator = _restriction_indicator(endpoint, row)
        displayed_header = " ".join(str(row.get("INDICATOR1") or "").split())

        if indicator:
            saw_control_row = True
            current_header = displayed_header or _restriction_label(endpoint, indicator)
            if not grouped or grouped[-1][0] != current_header:
                grouped.append((current_header, []))
        elif not saw_control_row and displayed_header:
            current_header = displayed_header
            if not grouped or grouped[-1][0] != current_header:
                grouped.append((current_header, []))
        elif not grouped:
            grouped.append((current_header, []))

        if value:
            if endpoint.route == "section-concentrations-restrictions" and value == "GV":
                value = "Galveston"
            grouped[-1][1].append(value)

    parts: list[str] = []
    for header, values in grouped:
        joined = _join_restriction_values(values)
        if joined:
            parts.append(f"{header.rstrip()} {joined}")
    return " | ".join(parts)


def fetch_section_restrictions(term_code: str, crn: str) -> str:
    payload = {"term": term_code, "crn": crn}
    parts: list[str] = []
    for endpoint in RESTRICTION_ENDPOINTS:
        url = urllib.parse.urljoin(BASE_URL, f"/api/{endpoint.route}")
        response = request_json(url, data=payload)
        if isinstance(response, list):
            text = parse_restriction_response(
                endpoint,
                [row for row in response if isinstance(row, dict)],
            )
            if text:
                parts.append(text)
    return " | ".join(parts)


def fetch_restrictions_for_sections(
    sections: Iterable[dict[str, Any]],
    *,
    workers: int = 12,
) -> list[dict[str, str]]:
    targets = [
        {
            "term_code": str(row.get("term_code") or "").strip(),
            "crn": str(row.get("crn") or "").strip(),
            "subject": str(row.get("subject") or "").strip().upper(),
            "course_number": str(row.get("course_number") or "").strip(),
        }
        for row in sections
        if str(row.get("term_code") or "").strip() and str(row.get("crn") or "").strip()
    ]

    def fetch(target: dict[str, str]) -> dict[str, str]:
        return {
            **target,
            "restrictions": fetch_section_restrictions(target["term_code"], target["crn"]),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        return list(executor.map(fetch, targets))


def fetch_terms() -> list[dict[str, Any]]:
    terms = request_json(TERMS_URL)
    if not isinstance(terms, list):
        raise ValueError("term endpoint returned an unexpected response")
    return terms


def fetch_sections(term_code: str) -> list[dict[str, Any]]:
    payload = {
        "startRow": 0,
        "endRow": 0,
        "termCode": term_code,
        "publicSearch": "Y",
    }
    sections = request_json(COURSE_SECTIONS_URL, data=payload)
    if not isinstance(sections, list):
        raise ValueError("course-sections endpoint returned an unexpected response")
    return sections


def regular_college_station_terms(terms: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = []
    for term in terms:
        description = str(term.get("STVTERM_DESC", ""))
        if "College Station" not in description:
            continue
        if not any(description.startswith(name) for name in SEMESTER_ORDER):
            continue
        wanted.append(term)
    return wanted


def latest_term_code(terms: Iterable[dict[str, Any]]) -> str:
    candidates = regular_college_station_terms(terms)
    if not candidates:
        raise ValueError("could not identify a College Station Spring/Summer/Fall term")
    return max(candidates, key=lambda term: str(term.get("STVTERM_CODE", "")))[
        "STVTERM_CODE"
    ]


def resolve_term_names(terms: Iterable[dict[str, Any]], labels: Iterable[str]) -> list[str]:
    candidates = regular_college_station_terms(terms)
    resolved = []
    for label in labels:
        cleaned_label = " ".join(label.strip().split()).casefold()
        match = next(
            (
                term
                for term in candidates
                if str(term.get("STVTERM_DESC", ""))
                .split(" - ", maxsplit=1)[0]
                .casefold()
                == cleaned_label
            ),
            None,
        )
        if match is None:
            available = ", ".join(
                str(term.get("STVTERM_DESC", "")).split(" - ", maxsplit=1)[0]
                for term in candidates[:12]
            )
            raise ValueError(f"could not resolve term name {label!r}; examples: {available}")
        resolved.append(str(match["STVTERM_CODE"]))
    return resolved


def term_description_by_code(terms: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {
        str(term.get("STVTERM_CODE", "")): str(term.get("STVTERM_DESC", ""))
        for term in terms
        if term.get("STVTERM_CODE")
    }


def output_path_for_term(
    output_dir: Path,
    filename_template: str,
    term_code: str,
    description: str,
) -> Path:
    label = description.split(" - ", maxsplit=1)[0]
    parts = label.split()
    semester = parts[0].lower() if parts else ""
    year = parts[1] if len(parts) > 1 else term_code[:4]
    slug = "_".join(part.lower() for part in parts) or term_code
    return output_dir / filename_template.format(
        term_code=term_code,
        semester=semester,
        year=year,
        slug=slug,
    )


def parse_json_field(row: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = row.get(field)
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str):
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [item for item in decoded if isinstance(item, dict)] if isinstance(decoded, list) else []


def compact_join(values: Iterable[Any]) -> str:
    cleaned = []
    for value in values:
        text = "" if value is None else str(value).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return "; ".join(cleaned)


def day_string(meeting: dict[str, Any]) -> str:
    days = [
        ("SSRMEET_SUN_DAY", "Sun"),
        ("SSRMEET_MON_DAY", "Mon"),
        ("SSRMEET_TUE_DAY", "Tue"),
        ("SSRMEET_WED_DAY", "Wed"),
        ("SSRMEET_THU_DAY", "Thu"),
        ("SSRMEET_FRI_DAY", "Fri"),
        ("SSRMEET_SAT_DAY", "Sat"),
    ]
    return "".join(label for key, label in days if meeting.get(key))


def time_string(meeting: dict[str, Any]) -> str:
    start = meeting.get("SSRMEET_BEGIN_TIME")
    end = meeting.get("SSRMEET_END_TIME")
    if start and end:
        return f"{start}-{end}"
    return str(start or end or "").strip()


def date_string(meeting: dict[str, Any]) -> str:
    start = meeting.get("SSRMEET_START_DATE")
    end = meeting.get("SSRMEET_END_DATE")
    if start and end:
        return f"{start}-{end}"
    return str(start or end or "").strip()


def location_string(meeting: dict[str, Any]) -> str:
    building = meeting.get("SSRMEET_BLDG_CODE")
    room = meeting.get("SSRMEET_ROOM_CODE")
    if building and building == room:
        return str(building).strip()
    if building and room:
        return f"{building} {room}"
    return str(building or room or "").strip()


def credit_hours(row: dict[str, Any]) -> str:
    section_hours = row.get("SWV_CLASS_SEARCH_SSBSECT_HOURS")
    low = row.get("SWV_CLASS_SEARCH_HOURS_LOW")
    high = row.get("SWV_CLASS_SEARCH_HOURS_HIGH")
    if section_hours not in (None, ""):
        return str(section_hours)
    if low not in (None, "") and high not in (None, "", low):
        return f"{low} to {high}"
    return "" if low in (None, "") else str(low)


def normalize_section(row: dict[str, Any]) -> dict[str, Any]:
    # CSV snapshots store scalar values as strings. Normalize Howdy's JSON
    # numbers and nulls the same way before returning rows so incremental
    # section comparisons do not report `4` versus `"4"` or null versus empty
    # string as a real availability change.
    normalized = {
        target: "" if row.get(source) is None else str(row.get(source, ""))
        for target, source in FIELD_MAP.items()
    }
    subject = str(normalized["subject"] or "").strip()
    number = str(normalized["course_number"] or "").strip()
    section = str(normalized["section"] or "").strip()
    normalized["course"] = f"{subject} {number}".strip()
    normalized["section"] = section
    normalized["credit_hours"] = credit_hours(row)

    instructors = parse_json_field(row, "SWV_CLASS_SEARCH_INSTRCTR_JSON")
    meetings = parse_json_field(row, "SWV_CLASS_SEARCH_JSON_CLOB")
    normalized["instructors"] = compact_join(
        instructor.get("NAME") for instructor in instructors
    )
    # Keep Howdy's per-person identifier with the snapshot.  The display name
    # is not a safe identity key (for example, there are multiple Yu Zhangs).
    normalized["instructor_details"] = json.dumps(
        [
            {
                "name": instructor.get("NAME"),
                "more": instructor.get("MORE"),
                "has_cv": instructor.get("HAS_CV"),
            }
            for instructor in instructors
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    normalized["meeting_days"] = compact_join(day_string(meeting) for meeting in meetings)
    normalized["meeting_times"] = compact_join(time_string(meeting) for meeting in meetings)
    normalized["meeting_dates"] = compact_join(date_string(meeting) for meeting in meetings)
    normalized["meeting_locations"] = compact_join(
        location_string(meeting) for meeting in meetings
    )
    return normalized


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OUTPUT_FIELDS})
            count += 1
    return count


def write_json(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(rows)


def print_terms(terms: Iterable[dict[str, Any]]) -> None:
    for term in regular_college_station_terms(terms):
        print(f"{term.get('STVTERM_CODE')}\t{term.get('STVTERM_DESC')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape TAMU Howdy public class-search sections."
    )
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        help="Term code to scrape, e.g. 202631. May be passed more than once.",
    )
    parser.add_argument(
        "--term-name",
        action="append",
        dest="term_names",
        help='College Station term name to scrape, e.g. "Fall 2026". May be passed more than once.',
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for --separate-files output.",
    )
    parser.add_argument(
        "--filename-template",
        default=DEFAULT_FILENAME_TEMPLATE,
        help=(
            "Filename template for --separate-files. Available fields: "
            "{term_code}, {semester}, {year}, {slug}."
        ),
    )
    parser.add_argument(
        "--separate-files",
        action="store_true",
        help="Write one CSV/JSON file per term using --output-dir and --filename-template.",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output normalized CSV or raw JSON.",
    )
    parser.add_argument(
        "--list-terms",
        action="store_true",
        help="Print available College Station Spring/Summer/Fall terms and exit.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between term requests when scraping multiple terms.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    terms_cache: list[dict[str, Any]] | None = None
    if args.list_terms or not args.terms or args.term_names:
        terms_cache = fetch_terms()

    if args.term_names:
        args.terms = (args.terms or []) + resolve_term_names(terms_cache or [], args.term_names)

    if args.list_terms or not args.terms:
        terms = terms_cache or fetch_terms()
        if args.list_terms:
            print_terms(terms)
            return 0
        args.terms = [latest_term_code(terms)]
        print(f"No --term supplied; using latest College Station term {args.terms[0]}.", file=sys.stderr)

    descriptions = term_description_by_code(terms_cache or fetch_terms()) if args.separate_files else {}
    all_rows: list[dict[str, Any]] = []
    for index, term_code in enumerate(args.terms, start=1):
        print(f"[{index}/{len(args.terms)}] Fetching {term_code}...", file=sys.stderr)
        rows = fetch_sections(term_code)
        if args.separate_files:
            output_path = output_path_for_term(
                args.output_dir,
                args.filename_template,
                term_code,
                descriptions.get(term_code, ""),
            )
            if args.format == "json":
                write_json(output_path, rows)
            else:
                write_csv(output_path, (normalize_section(row) for row in rows))
            print(f"  wrote: {output_path}", file=sys.stderr)
        else:
            all_rows.extend(rows)
        print(f"  rows: {len(rows)}", file=sys.stderr)
        if args.delay > 0 and index < len(args.terms):
            time.sleep(args.delay)

    if args.separate_files:
        return 0

    if args.format == "json":
        count = write_json(args.output, all_rows)
    else:
        count = write_csv(args.output, (normalize_section(row) for row in all_rows))

    print(f"Wrote {count} rows to {args.output}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
