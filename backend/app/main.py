from __future__ import annotations

import csv
import gc
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

import numpy as np

# Serve from local Hugging Face caches by default. Set HF_HUB_OFFLINE=0 in
# the process environment before startup when a deliberate model download is
# needed.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("course-search")

from backend.app import paradedb, postgres
from backend.app.api.metadata import build_metadata_router
from backend.app.config import settings
from backend.app.ltr_features import (
    ANNOTATION_POOL_SIZE,
    FEATURE_NAMES as LTR_FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION as LTR_FEATURE_SCHEMA_VERSION,
    build_annotation_pool,
    deterministic_random_hits,
    features_for_course,
)

# Compatibility aliases keep existing scripts and the public API stable while
# configuration moves behind a single settings contract.
ROOT_DIR = settings.root_dir
DATA_FILE = settings.data.course_catalog
GRADE_DISTRIBUTION_FILE = settings.data.grade_distribution
TAMU_RESTRICTIONS_FILE = settings.data.restrictions
TAMU_MAJOR_TAXONOMY_FILE = settings.data.major_taxonomy
SUBJECT_CONTEXT_FILE = settings.data.subject_context
CURRENT_SECTIONS_PATTERN = settings.data.current_sections_pattern
CURRENT_SECTIONS_FALLBACK_PATTERN = settings.data.current_sections_fallback_pattern
FRONTEND_DIR = ROOT_DIR / "frontend"
# AP credit information is intentionally kept outside the catalog index: it is
# admissions/placement guidance rather than an attribute of a particular
# offering.  A course can appear under more than one AP exam or score.
AP_EQUIVALENCIES_BY_COURSE = {
    "AFST-289": [("African American Studies", "3", "3 credit hours")],
    "ARTS-149": [("Art History", "3", "ARTS 149 (3 credit hours)"), ("Art History", "4", "ARTS 149 and 150 (6 credit hours)")],
    "ARTS-150": [("Art History", "4", "ARTS 149 and 150 (6 credit hours)")],
    "BIOL-113": [("Biology", "3", "BIOL 113 (3 credit hours)")],
    "BIOL-111": [("Biology", "4", "BIOL 111 and 112 (8 credit hours)")], "BIOL-112": [("Biology", "4", "BIOL 111 and 112 (8 credit hours)")],
    "MATH-142": [("Calculus AB", "3", "MATH 142 (3 credit hours)")],
    "MATH-151": [("Calculus AB", "4*", "MATH 151 (4 credit hours)"), ("Calculus BC", "3*", "MATH 151 (4 credit hours)"), ("Calculus BC", "4*", "MATH 151 and 152 (8 credit hours)")],
    "MATH-152": [("Calculus BC", "4*", "MATH 151 and 152 (8 credit hours)")],
    "CHEM-119": [("Chemistry", "3", "CHEM 119 (4 credit hours)"), ("Chemistry", "4", "CHEM 119 and 120 (8 credit hours)")], "CHEM-120": [("Chemistry", "4", "CHEM 119 and 120 (8 credit hours)")],
    "CHIN-101": [("Chinese", "3", "CHIN 101 and 102 (8 credit hours)"), ("Chinese", "4", "CHIN 101, 102, 201, and 202 (14 credit hours)")], "CHIN-102": [("Chinese", "3", "CHIN 101 and 102 (8 credit hours)"), ("Chinese", "4", "CHIN 101, 102, 201, and 202 (14 credit hours)")], "CHIN-201": [("Chinese", "4", "CHIN 101, 102, 201, and 202 (14 credit hours)")], "CHIN-202": [("Chinese", "4", "CHIN 101, 102, 201, and 202 (14 credit hours)")],
    "POLS-229": [("Comparative Government", "3", "POLS 229 (3 credit hours)")], "CSCE-110": [("Computer Science A", "3", "CSCE 110 (4 credit hours)"), ("Computer Science Principles", "3", "CSCE 110 (4 credit hours)")],
    "ENGL-104": [("English Language and Composition", "3", "ENGL 104 (3 credit hours)"), ("English Language and Composition", "4", "ENGL 104 and 241 (6 credit hours)"), ("English Literature and Composition", "3", "ENGL 104 (3 credit hours)"), ("English Literature and Composition", "4", "ENGL 104 and 203 (6 credit hours)")], "ENGL-241": [("English Language and Composition", "4", "ENGL 104 and 241 (6 credit hours)")], "ENGL-203": [("English Literature and Composition", "4", "ENGL 104 and 203 (6 credit hours)")],
    "GEOS-105": [("Environmental Science", "3", "GEOS 105 (3 credit hours)")], "HIST-102": [("European History", "3", "HIST 102 (3 credit hours)")],
    "FREN-101": [("French", "3", "FREN 101 and 102 (8 credit hours)"), ("French", "4", "FREN 101, 102, 201, and 202 (14 credit hours)")], "FREN-102": [("French", "3", "FREN 101 and 102 (8 credit hours)"), ("French", "4", "FREN 101, 102, 201, and 202 (14 credit hours)")], "FREN-201": [("French", "4", "FREN 101, 102, 201, and 202 (14 credit hours)")], "FREN-202": [("French", "4", "FREN 101, 102, 201, and 202 (14 credit hours)")],
    "GERM-101": [("German", "3", "GERM 101 and 102 (8 credit hours)"), ("German", "4", "GERM 101, 102, 201, and 202 (14 credit hours)")], "GERM-102": [("German", "3", "GERM 101 and 102 (8 credit hours)"), ("German", "4", "GERM 101, 102, 201, and 202 (14 credit hours)")], "GERM-201": [("German", "4", "GERM 101, 102, 201, and 202 (14 credit hours)")], "GERM-202": [("German", "4", "GERM 101, 102, 201, and 202 (14 credit hours)")],
    "GEOG-201": [("Human Geography", "3", "GEOG 201 (3 credit hours)")],
    "ITAL-101": [("Italian", "3", "ITAL 101 and 102 (8 credit hours)"), ("Italian", "4", "ITAL 101, 102, 201, and 202 (14 credit hours)")], "ITAL-102": [("Italian", "3", "ITAL 101 and 102 (8 credit hours)"), ("Italian", "4", "ITAL 101, 102, 201, and 202 (14 credit hours)")], "ITAL-201": [("Italian", "4", "ITAL 101, 102, 201, and 202 (14 credit hours)")], "ITAL-202": [("Italian", "4", "ITAL 101, 102, 201, and 202 (14 credit hours)")],
    "JAPN-101": [("Japanese", "3", "JAPN 101 and 102 (8 credit hours)"), ("Japanese", "4", "JAPN 101, 102, 201, and 202 (16 credit hours)")], "JAPN-102": [("Japanese", "3", "JAPN 101 and 102 (8 credit hours)"), ("Japanese", "4", "JAPN 101, 102, 201, and 202 (16 credit hours)")], "JAPN-201": [("Japanese", "4", "JAPN 101, 102, 201, and 202 (16 credit hours)")], "JAPN-202": [("Japanese", "4", "JAPN 101, 102, 201, and 202 (16 credit hours)")],
    "CLAS-121": [("Latin Literature", "3", "CLAS 121 and 122 (8 credit hours)"), ("Latin Literature", "4", "CLAS 121, 122, 221, and 222 (14 credit hours)")], "CLAS-122": [("Latin Literature", "3", "CLAS 121 and 122 (8 credit hours)"), ("Latin Literature", "4", "CLAS 121, 122, 221, and 222 (14 credit hours)")], "CLAS-221": [("Latin Literature", "4", "CLAS 121, 122, 221, and 222 (14 credit hours)")], "CLAS-222": [("Latin Literature", "4", "CLAS 121, 122, 221, and 222 (14 credit hours)")],
    "ECON-203": [("Macroeconomics", "3", "ECON 203 (3 credit hours)")], "ECON-202": [("Microeconomics", "3", "ECON 202 (3 credit hours)")], "MUSC-204": [("Music Theory", "3", "MUSC 204 and 208 (3 credit hours)")], "MUSC-208": [("Music Theory", "3", "MUSC 204 and 208 (3 credit hours)")],
    "PHYS-205": [("Physics 1", "3", "PHYS 205 (4 credit hours)"), ("Physics 2", "3", "PHYS 205 (4 credit hours)")], "PHYS-201": [("Physics 1", "4", "PHYS 201 (4 credit hours)")], "PHYS-202": [("Physics 2", "4", "PHYS 202 (4 credit hours)")], "PHYS-207": [("Physics C: Electricity and Magnetism", "3", "PHYS 207 or PHYS 227 (3 or 4 credit hours**)")], "PHYS-227": [("Physics C: Electricity and Magnetism", "3", "PHYS 207 or PHYS 227 (3 or 4 credit hours**)")], "PHYS-206": [("Physics C: Mechanics", "3", "PHYS 206 or PHYS 226 (3 or 4 credit hours**)")], "PHYS-226": [("Physics C: Mechanics", "3", "PHYS 206 or PHYS 226 (3 or 4 credit hours**)")],
    "MATH-102": [("Precalculus", "3", "MATH 102 (3 credit hours)")], "PBSI-107": [("Psychology", "3", "PBSI 107 (3 credit hours)")],
    "SPAN-101": [("Spanish Language", "3", "SPAN 101 and 102 (8 credit hours)"), ("Spanish Language", "4", "SPAN 101, 102, and 201 (11 credit hours)"), ("Spanish Language", "5", "SPAN 101, 102, 201, and 202 (14 credit hours)")], "SPAN-102": [("Spanish Language", "3", "SPAN 101 and 102 (8 credit hours)"), ("Spanish Language", "4", "SPAN 101, 102, and 201 (11 credit hours)"), ("Spanish Language", "5", "SPAN 101, 102, 201, and 202 (14 credit hours)")], "SPAN-201": [("Spanish Language", "4", "SPAN 101, 102, and 201 (11 credit hours)"), ("Spanish Language", "5", "SPAN 101, 102, 201, and 202 (14 credit hours)")], "SPAN-202": [("Spanish Language", "5", "SPAN 101, 102, 201, and 202 (14 credit hours)"), ("Spanish Literature", "3", "SPAN 202 (3 credit hours)"), ("Spanish Literature", "5", "SPAN 202 and 320 (6 credit hours)")], "SPAN-320": [("Spanish Literature", "5", "SPAN 202 and 320 (6 credit hours)")],
    "STAT-201": [("Statistics", "3", "STAT 201 (3 credit hours)")],
    "ARTS-103": [("Studio Art: 2-D Design", "3", "ARTS 103 (3 credit hours)"), ("Studio Art: 2-D Design", "4", "ARTS 103 and 111 (6 credit hours)"), ("Studio Art: 3-D Design", "3", "ARTS 103 (3 credit hours)"), ("Studio Art: Drawing", "3", "ARTS 103 (3 credit hours)"), ("Studio Art: Drawing", "4", "ARTS 103 and 111 (6 credit hours)")], "ARTS-111": [("Studio Art: 2-D Design", "4", "ARTS 103 and 111 (6 credit hours)"), ("Studio Art: Drawing", "4", "ARTS 103 and 111 (6 credit hours)")],
    "POLS-206": [("U.S. Government and Politics", "3", "POLS 206 (3 credit hours)")], "HIST-105": [("U.S. History", "3", "HIST 105 and 106 (6 credit hours)")], "HIST-106": [("U.S. History", "3", "HIST 105 and 106 (6 credit hours)")], "HIST-104": [("World History", "3", "HIST 104 (3 credit hours)")],
}
POST_RANK_CANDIDATES = 50
ANNOTATION_RERANK_CANDIDATES = 100
HUMAN_TEST_QUERIES_FILE = ROOT_DIR / "data" / "human_test_queries.csv"
CROSS_ENCODER_MODEL_NAME = os.getenv(
    "CROSS_ENCODER_MODEL_NAME",
    "cross-encoder/ms-marco-MiniLM-L6-v2",
)
CROSS_ENCODER_PREDICT_BATCH_SIZE = 64
BAYESIAN_GPA_BASELINE_MIN = 2.8
BAYESIAN_GPA_BASELINE_MAX = 3.4
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
SEARCH_EMBEDDING_FIELD = "search_embedding"
RRF_RANK_CONSTANT = 60
LTR_CANDIDATE_COUNT = 50
LTR_MODEL_FILE = ROOT_DIR / "data" / "ltr" / "ltr-model.json"
LTR_MODEL_METADATA_FILE = ROOT_DIR / "data" / "ltr" / "ltr-model-metadata.json"
# The downstream rankers consume at most 50 candidates. A 250-document RRF
# window preserves ample retrieval headroom without paying to fetch 500
# lexical candidates on every text search.
RRF_MIN_RANK_WINDOW = 250
STUDENT_RESTRICTION_PATTERN = re.compile(
    r"(?P<mode>Must be enrolled in one of the following|May not be enrolled in one of the following|Cannot be enrolled in one of the following)\s+"
    r"(?P<category>Majors|Fields of Study|Departments|Colleges)\s*:?\s*(?P<values>[^|]+)",
    re.IGNORECASE,
)
SEMESTER_BY_TERM_CODE = {"1": "SPRING", "2": "SUMMER", "3": "FALL"}
SEMESTER_KEYS = ["fall", "spring", "summer"]
SEMESTER_LABEL_BY_KEY = {
    "fall": "Fall",
    "spring": "Spring",
    "summer": "Summer",
}
SEMESTER_CHRONOLOGICAL_ORDER = {
    "spring": 1,
    "summer": 2,
    "fall": 3,
}
SEMESTER_VALUE_BY_KEY = {
    "fall": "FALL",
    "spring": "SPRING",
    "summer": "SUMMER",
}
GRADE_COUNT_FIELDS = {
    "a_plus": "a_plus_count",
    "a": "a_count",
    "a_minus": "a_minus_count",
    "b_plus": "b_plus_count",
    "b": "b_count",
    "b_minus": "b_minus_count",
    "c_plus": "c_plus_count",
    "c": "c_count",
    "c_minus": "c_minus_count",
    "d_plus": "d_plus_count",
    "d": "d_count",
    "d_minus": "d_minus_count",
    "f": "f_count",
    "i": "i_count",
    "s": "s_count",
    "p": "p_count",
    "u": "u_count",
    "q": "q_count",
    "x": "x_count",
}
TERM_FILTERS = {
    "spring": "Spring",
    "summer": "Summer",
    "fall": "Fall",
}
DEGREE_LEVEL_FILTERS = {
    "100": "100",
    "200": "200",
    "300": "300",
    "400": "400",
    "600": "600",
    "700": "700",
    "800": "800",
    "900": "900",
}
LOCATION_FILTERS = {
    "college station": "College Station",
    "mcallen": "McAllen",
    "galveston": "Galveston",
    "bryan": "Bryan",
    "distance education": "Distance Education",
}
INSTRUCTION_TYPE_FILTERS = {
    "traditional face-to-face (f2f)": "Traditional Face-to-Face (F2F)",
    "traditional in person": "Traditional Face-to-Face (F2F)",
    "non-traditional": "Non-traditional",
    "web based": "Web Based",
}
CORE_FILTERS = {
    "core life/physical sci (klps)": "Core Life/Physical Sci (KLPS)",
    "core mathematics (kmth)": "Core Mathematics (KMTH)",
    "core lang, phil, culture(klpc)": "Core Lang, Phil, Culture(KLPC)",
    "core lang, phil, culture (klpc)": "Core Lang, Phil, Culture(KLPC)",
    "core communication (kcom)": "Core Communication (KCOM)",
    "core social & beh sci (ksoc)": "Core Social & Beh Sci (KSOC)",
    "core creative arts (kcra)": "Core Creative Arts (KCRA)",
    "core american history (khis)": "Core American History (KHIS)",
    "core local gov/pol sci (kpll)": "Core Local Gov/Pol Sci (KPLL)",
    "core fed gov/pol sci (kplf)": "Core Fed Gov/Pol Sci (KPLF)",
}
GRADUATION_REQUIREMENT_FILTERS = {
    "univ req-writing intensive": "Univ Req-Writing Intensive",
    "univ req-int'l&cult div (kicd)": "Univ Req-Int'l&Cult Div (KICD)",
    "univ req-cult discourse (kucd)": "Univ Req-Cult Discourse (KUCD)",
    "univ req-oral communication": "Univ Req-Oral Communication",
}

class SectionFilters(dict[str, list[str]]):
    pass


class SearchScope(dict[str, list[str]]):
    pass


_embedding_model: Any | None = None
_embedding_model_unavailable = False
_cross_encoder_model: Any | None = None
_ltr_model: Any | None = None
_ltr_best_iteration: int | None = None
_major_taxonomy: dict[str, dict[str, Any]] | None = None
_subject_option_set: set[str] | None = None
_subject_context_by_prefix: dict[str, str] | None = None


def hugging_face_token() -> str | None:
    for env_name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HG"):
        token = os.getenv(env_name)
        if token:
            return token
    return None


def hugging_face_model_is_cached(model_name: str, required_file: str) -> bool:
    # huggingface_hub reads this setting at import time. This service uses
    # locally cached models and should not block requests on unavailable Hub
    # metadata when a cache probe is performed.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return False

    cached_file = try_to_load_from_cache(model_name, required_file)
    return isinstance(cached_file, str) and Path(cached_file).exists()


def embedding_model_is_cached() -> bool:
    return hugging_face_model_is_cached(EMBEDDING_MODEL_NAME, "modules.json")


def embedding_model_may_load() -> bool:
    """Avoid retrying a missing model for every request in offline deployments."""
    if _embedding_model is not None:
        return True
    if _embedding_model_unavailable:
        return False
    offline = os.getenv("HF_HUB_OFFLINE", "1").strip().casefold() not in {"0", "false", "no"}
    return embedding_model_is_cached() or not offline


def course_id(course_prefix: str, number: str) -> str:
    return f"{course_prefix.strip().upper()}-{number.strip()}".strip("-")


def normalized_instructors(
    raw_value: str | None, instructor_details: str | None = None
) -> list[dict[str, str]]:
    """Return Howdy person keys plus legacy name keys for grade-report matching.

    Howdy's ``MORE`` value is the preferred identity key.  Older snapshots and
    grade reports do not include it, so their normalized full name remains a
    compatibility fallback.  ``legacy_key`` is used only to resolve abbreviated
    grade-report names on an exact course/term/section match.
    """
    instructors: list[dict[str, str]] = []
    seen: set[str] = set()
    details_by_name: dict[str, list[dict[str, Any]]] = {}
    try:
        decoded_details = json.loads(instructor_details or "[]")
    except json.JSONDecodeError:
        decoded_details = []
    if isinstance(decoded_details, list):
        for detail in decoded_details:
            if not isinstance(detail, dict):
                continue
            detail_name = str(detail.get("name") or "")
            detail_name = re.sub(r"\s*\([^)]*\)\s*$", "", detail_name).strip()
            if detail_name:
                details_by_name.setdefault(detail_name.casefold(), []).append(detail)

    for value in re.split(r"[;|]", raw_value or ""):
        display_name = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
        display_name = re.sub(r"\s+", " ", display_name)
        if not display_name:
            continue
        # Keep compound surnames intact so abbreviated grade-report names such
        # as "GUTIERREZ-OSUNA R" match section names like
        # "Ricardo Gutierrez-Osuna".
        tokens = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", display_name)
        if len(tokens) < 2:
            continue
        if "," in display_name:
            last_name, first_name = tokens[0], tokens[1]
        elif display_name.isupper() or (len(tokens[-1]) == 1 and len(tokens[0]) > 1):
            last_name, first_name = tokens[0], tokens[-1]
        else:
            last_name, first_name = tokens[-1], tokens[0]
        legacy_key = f"{last_name.casefold()}|{first_name[0].casefold()}"
        if "," in display_name:
            profile_tokens = [*tokens[1:], tokens[0]]
        elif display_name.isupper() or (len(tokens[-1]) == 1 and len(tokens[0]) > 1):
            profile_tokens = [*tokens[1:], tokens[0]]
        else:
            profile_tokens = tokens
        name_key = "-".join(token.casefold() for token in profile_tokens)
        matching_details = details_by_name.get(display_name.casefold(), [])
        howdy_id = next(
            (
                str(detail.get("more")).strip()
                for detail in matching_details
                if str(detail.get("more") or "").strip().isdigit()
            ),
            "",
        )
        key = f"howdy-{howdy_id}" if howdy_id else name_key
        if key not in seen:
            seen.add(key)
            instructors.append(
                {
                    "name": display_name,
                    "key": key,
                    "instructor_id": howdy_id,
                    "howdy_id": howdy_id,
                    "name_key": name_key,
                    "legacy_key": legacy_key,
                }
            )
    return instructors


def get_embedding_model() -> Any:
    global _embedding_model, _embedding_model_unavailable
    if _embedding_model is None:
        if not embedding_model_may_load():
            _embedding_model_unavailable = True
            return None
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model %s.", EMBEDDING_MODEL_NAME)
        model_kwargs: dict[str, Any] = {}
        token = hugging_face_token()
        if token:
            model_kwargs["token"] = token

        local_files_only = embedding_model_is_cached()
        if local_files_only:
            # Sentence Transformers 5 may probe optional Hub metadata even
            # with local_files_only=True. Avoid that network retry when this
            # model's required files are already cached.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            model_kwargs["local_files_only"] = True

        try:
            _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, **model_kwargs)
        except Exception:
            _embedding_model_unavailable = True
            if not local_files_only:
                raise
            # A partial cache cannot serve semantic retrieval. Let embed_query
            # use its existing lexical fallback instead of blocking every
            # request on a Hugging Face retry when the host is offline.
            logger.warning("Cached embedding model is incomplete; semantic retrieval is unavailable.")
            raise
    return _embedding_model


def get_cross_encoder_model() -> Any:
    global _cross_encoder_model
    if _cross_encoder_model is None:
        from sentence_transformers import CrossEncoder

        kwargs: dict[str, Any] = {}
        token = hugging_face_token()
        if token:
            kwargs["token"] = token
        if hugging_face_model_is_cached(CROSS_ENCODER_MODEL_NAME, "config.json"):
            kwargs["local_files_only"] = True
        logger.info("Loading cross-encoder model %s.", CROSS_ENCODER_MODEL_NAME)
        _cross_encoder_model = CrossEncoder(CROSS_ENCODER_MODEL_NAME, **kwargs)
    return _cross_encoder_model


def course_text_fields(course: dict[str, Any]) -> list[str]:
    fields = [
        course.get("course_code", ""),
        course.get("title", ""),
        course.get("description", ""),
        course.get("prerequisites", ""),
        course.get("restrictions", ""),
        course.get("cross_listings", ""),
        course.get("attributes", ""),
        " ".join(course.get("course_attributes", [])),
    ]
    return [str(value).strip() for value in fields if str(value).strip()]


def rerank_with_cross_encoder(query: str, hits: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    if not query.strip() or not hits:
        return hits, False
    try:
        scores = get_cross_encoder_model().predict(
            [(query, "\n".join(course_text_fields(hit.get("_source", {})))) for hit in hits],
            batch_size=CROSS_ENCODER_PREDICT_BATCH_SIZE,
            show_progress_bar=False,
        )
    except Exception:
        logger.exception("Cross-encoder reranking failed; using RRF order for this annotation pool.")
        return hits, False
    scored: list[dict[str, Any]] = []
    for original_rank, (hit, score) in enumerate(zip(hits, scores)):
        saved = dict(hit)
        saved["_score"] = float(score)
        saved["_cross_encoder_tiebreak"] = original_rank
        scored.append(saved)
    return sorted(scored, key=lambda hit: (-float(hit["_score"]), hit["_cross_encoder_tiebreak"])), True


def get_ltr_model() -> Any:
    global _ltr_best_iteration, _ltr_model
    if _ltr_model is not None:
        return _ltr_model
    if not LTR_MODEL_FILE.exists() or not LTR_MODEL_METADATA_FILE.exists():
        raise RuntimeError("The trained LTR model artifacts are unavailable.")
    try:
        metadata = json.loads(LTR_MODEL_METADATA_FILE.read_text(encoding="utf-8"))
        if metadata.get("feature_schema_version") != LTR_FEATURE_SCHEMA_VERSION:
            raise RuntimeError("The trained LTR model uses an incompatible feature schema version.")
        if metadata.get("feature_names") != LTR_FEATURE_NAMES:
            raise RuntimeError("The trained LTR model feature names do not match the active schema.")
        best_iteration = metadata.get("training", {}).get("best_iteration")
        if not isinstance(best_iteration, int) or best_iteration < 1:
            raise RuntimeError("The trained LTR model is missing a valid selected iteration count.")
        from xgboost import Booster

        model = Booster()
        model.load_model(str(LTR_MODEL_FILE))
    except Exception as exc:
        raise RuntimeError(f"Could not load the trained LTR model: {exc}") from exc
    _ltr_model = model
    _ltr_best_iteration = best_iteration
    return model


def rerank_with_ltr(
    query: str,
    hits: list[dict[str, Any]],
    bm25_ranks: dict[str, int],
    semantic_ranks: dict[str, int],
    selected_metrics_by_course: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Apply LTR to the first 50 RRF candidates; preserve RRF after that."""
    if not hits:
        return hits, False
    try:
        model = get_ltr_model()
        import xgboost

        ltr_hits = hits[:LTR_CANDIDATE_COUNT]
        vectors = []
        for hit in ltr_hits:
            source = dict(hit.get("_source") or {})
            course_id_value = str(source.get("course_id") or hit.get("_id") or "")
            metrics = selected_metrics_by_course.get(course_id_value, {})
            course_features = features_for_course(
                query,
                source,
                bm25_rank=bm25_ranks.get(str(hit.get("_id") or "")),
                semantic_rank=semantic_ranks.get(str(hit.get("_id") or "")),
                current_section_count=int(source.get("current_section_count") or 0),
                historical_enrollment=int(metrics.get("total_enrollment") or 0),
            )
            vectors.append(
                [course_features[name] for name in LTR_FEATURE_NAMES]
            )
        matrix = xgboost.DMatrix(np.asarray(vectors, dtype=np.float32), feature_names=LTR_FEATURE_NAMES)
        scores = model.predict(matrix, iteration_range=(0, _ltr_best_iteration))
        scored = []
        for tie_break, (hit, score) in enumerate(zip(ltr_hits, scores)):
            saved = dict(hit)
            saved["_ltr_score"] = float(score)
            saved["_ltr_tiebreak"] = tie_break
            scored.append(saved)
        reranked = sorted(scored, key=lambda hit: (-float(hit["_ltr_score"]), int(hit["_ltr_tiebreak"])))
        return reranked + hits[LTR_CANDIDATE_COUNT:], True
    except Exception:
        logger.exception("LTR reranking failed; keeping RRF order.")
        return hits, False


def embedding_text_for_course(course: dict[str, Any]) -> str:
    fields = course_text_fields(course)
    subject_context = str(course.get("subject_context", "")).strip()
    if subject_context:
        fields.append(f"Subject area: {subject_context}")
    return "\n".join(fields)


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_embedding_model()
    if model is None:
        raise RuntimeError("Semantic retrieval is unavailable because the embedding model is not installed.")
    embeddings = model.encode(
        texts,
        batch_size=64,
        convert_to_numpy=True,
        normalize_embeddings=False,
        show_progress_bar=False,
    )
    return [[float(value) for value in embedding] for embedding in embeddings]


def embed_query(text: str) -> list[float] | None:
    if not embedding_model_may_load():
        return None
    try:
        return embed_texts([text])[0]
    except Exception:
        logger.exception("Falling back to lexical search because query embedding failed.")
        return None


def split_attributes(raw_attributes: str) -> list[str]:
    seen: dict[str, str] = {}
    for item in raw_attributes.split("|"):
        attribute = item.strip()
        if attribute:
            seen.setdefault(attribute.lower(), attribute)
    return sorted(seen.values(), key=str.casefold)


def canonicalize(values: list[str] | None, options: dict[str, str] | None = None) -> list[str]:
    seen: dict[str, str] = {}
    for value in values or []:
        cleaned = value.strip()
        if not cleaned:
            continue
        canonical = options.get(cleaned.casefold(), cleaned) if options else cleaned
        seen.setdefault(canonical.casefold(), canonical)
    return sorted(seen.values(), key=str.casefold)


def matching_values(values: list[str], options: dict[str, str]) -> list[str]:
    allowed = set(options.values())
    return sorted((value for value in values if value in allowed), key=str.casefold)


def section_filter_locations(attributes: list[str], site: str) -> list[str]:
    """Normalize section locations without treating unknown sites as College Station."""
    locations = matching_values(attributes, LOCATION_FILTERS)
    site_key = site.casefold()
    canonical_site = LOCATION_FILTERS.get(site_key)

    if canonical_site and canonical_site not in locations:
        locations.append(canonical_site)

    college_station_site = site_key in {"", "college station", "distance education"}
    if college_station_site:
        if "College Station" not in locations:
            locations.append("College Station")
    else:
        locations = [location for location in locations if location != "College Station"]

    return sorted(locations, key=str.casefold)


def term_filter_from_code(term_code: str) -> str:
    term_name = SEMESTER_BY_TERM_CODE.get(term_code[4], "") if len(term_code) >= 5 else ""
    return term_name.title()


def year_from_term_code(term_code: str) -> int | None:
    try:
        return int(term_code[:4])
    except ValueError:
        return None


def semester_key_from_label(label: str) -> str:
    return label.strip().casefold()


def degree_level_from_number(number: str) -> str:
    for character in number.strip():
        if character.isdigit():
            return f"{character}00"
    return ""


def clean_affiliation_text(value: str) -> str:
    return " ".join(str(value or "").replace("\u200b", "").split())


def normalize_major_name(value: str) -> str:
    cleaned = clean_affiliation_text(value)
    cleaned = re.sub(r"\s*\((?:upper|lower)(?:\s+level)?\)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if " - " in cleaned:
        cleaned = cleaned.split(" - ", maxsplit=1)[0].strip()
    else:
        cleaned = re.sub(
            r"-(?:Executive Prog|Exec Prog|Alamo Coll|Austin Com|Blinn Bren|Collin Col|El Centro|HCC SprBrn|Midland Co|South TX|Tarrant Co|TX Southms|Tyler Jr C)$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
    return cleaned


def comparison_aliases(value: str, *, major: bool = False, college: bool = False) -> list[str]:
    cleaned = normalize_major_name(value) if major else clean_affiliation_text(value)
    if not cleaned:
        return []

    aliases = {cleaned}
    if college:
        aliases.add(re.sub(r"^College of\s+", "", cleaned, flags=re.IGNORECASE).strip())
        aliases.add(re.sub(r"^School of\s+", "", cleaned, flags=re.IGNORECASE).strip())
        aliases.add(re.sub(r"\s+School$", "", cleaned, flags=re.IGNORECASE).strip())
        if cleaned.casefold() == "mays business school":
            aliases.add("Business")

    for alias in list(aliases):
        aliases.add(alias.replace(" and ", " & "))
        aliases.add(alias.replace(" & ", " and "))
        aliases.add(alias.replace("Engineering", "Engr"))
        aliases.add(alias.replace("Engineering", "Eng"))
        aliases.add(re.sub(r"\bEngr\b", "Engineering", alias))
        aliases.add(re.sub(r"\bEng\b", "Engineering", alias))
        aliases.add(alias.replace("Management", "Mgmt"))
        aliases.add(re.sub(r"\bMgmt\b", "Management", alias))
        aliases.add(alias.replace("Agricultural", "Ag"))
        aliases.add(re.sub(r"\bAg\b", "Agricultural", alias))
        aliases.add(alias.replace("Communications", "Comm"))
        aliases.add(re.sub(r"\bComm\b", "Communications", alias))
        aliases.add(alias.replace("Sciences", "Sci"))
        aliases.add(alias.replace("Science", "Sci"))
        aliases.add(re.sub(r"\bSci\b", "Science", alias))
        aliases.add(alias.replace("Admin", "Administration"))

    return sorted({alias for alias in aliases if alias}, key=str.casefold)


def canonical_values(values: Iterable[str], *, major: bool = False) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        cleaned = normalize_major_name(value) if major else clean_affiliation_text(value)
        if cleaned:
            seen.setdefault(cleaned.casefold(), cleaned)
    return sorted(seen.values(), key=str.casefold)


def alias_values(values: Iterable[str], *, major: bool = False, college: bool = False) -> list[str]:
    aliases: dict[str, str] = {}
    for value in values:
        for alias in comparison_aliases(value, major=major, college=college):
            aliases.setdefault(alias.casefold(), alias)
    return sorted(aliases.values(), key=str.casefold)


def split_restriction_values(raw_values: str) -> list[str]:
    values = []
    for value in raw_values.split(","):
        cleaned = clean_affiliation_text(value)
        # The source occasionally formats the final item in a list as
        # ``..., & Law``. The ampersand is a list conjunction, not part of
        # the affiliation option itself.
        cleaned = re.sub(r"^&\s*", "", cleaned)
        if cleaned:
            values.append(cleaned)
    return values


def clean_registration_restrictions(restrictions: str) -> str:
    """Drop scraper artifacts such as an empty `Concentrations:` clause."""
    clauses = re.split(r"\s*\|\s*", restrictions or "")
    return " | ".join(
        clause.strip()
        for clause in clauses
        if clause.strip() and not re.search(r":\s*$", clause)
    )


def empty_restriction_groups() -> dict[str, dict[str, list[str]]]:
    return {
        "include": {"major": [], "department": [], "college": []},
        "exclude": {"major": [], "department": [], "college": []},
    }


def parse_student_restriction_groups(restrictions: str) -> dict[str, dict[str, list[str]]]:
    groups = empty_restriction_groups()
    for match in STUDENT_RESTRICTION_PATTERN.finditer(restrictions or ""):
        mode = match.group("mode").casefold()
        raw_category = match.group("category").casefold()
        if raw_category in {"majors", "fields of study"}:
            category = "major"
        elif raw_category == "departments":
            category = "department"
        elif raw_category == "colleges":
            category = "college"
        else:
            continue

        direction = "exclude" if mode.startswith(("may not", "cannot")) else "include"
        groups[direction][category].extend(split_restriction_values(match.group("values")))
    return groups


def read_major_taxonomy() -> dict[str, dict[str, Any]]:
    global _major_taxonomy
    if _major_taxonomy is not None:
        return _major_taxonomy

    taxonomy: dict[str, dict[str, Any]] = {}
    if postgres.configured():
        source_rows: Iterable[dict[str, Any]] = postgres.major_rows()
    elif TAMU_MAJOR_TAXONOMY_FILE.exists():
        with TAMU_MAJOR_TAXONOMY_FILE.open("r", encoding="utf-8", newline="") as csv_file:
            source_rows = list(csv.DictReader(csv_file))
    else:
        source_rows = []
    for row in source_rows:
        major = row.get("major", "")
        normalized_major = row.get("normalized_major", "") or normalize_major_name(major)
        departments = [row.get("department", ""), row.get("normalized_department", "")]
        colleges = [row.get("college", ""), row.get("normalized_college", "")]
        profile = {
            "major_aliases": alias_values([major, normalized_major], major=True),
            "department_aliases": alias_values(departments),
            "college_aliases": alias_values(colleges, college=True),
        }
        for alias in profile["major_aliases"]:
            taxonomy.setdefault(alias.casefold(), profile)

    _major_taxonomy = taxonomy
    return taxonomy


def student_restriction_profile(student_major: str | None) -> dict[str, list[str]]:
    major = clean_affiliation_text(student_major or "")
    major_aliases = alias_values([major], major=True)
    profile = {
        "major_aliases": major_aliases,
        "department_aliases": [],
        "college_aliases": [],
    }
    taxonomy = read_major_taxonomy()
    for alias in major_aliases:
        match = taxonomy.get(alias.casefold())
        if not match:
            continue
        profile["major_aliases"] = alias_values([*profile["major_aliases"], *match.get("major_aliases", [])], major=True)
        profile["department_aliases"] = alias_values([*profile["department_aliases"], *match.get("department_aliases", [])])
        profile["college_aliases"] = alias_values([*profile["college_aliases"], *match.get("college_aliases", [])], college=True)
    if any(alias.casefold() in {"business admin", "business administration"} for alias in profile["major_aliases"]):
        profile["college_aliases"] = alias_values([*profile["college_aliases"], "Business"], college=True)
    return profile


def parse_major_restrictions(restrictions: str) -> list[str]:
    groups = parse_student_restriction_groups(restrictions)
    return canonical_values(groups["include"]["major"], major=True)


def restriction_entry(raw_restrictions: str, major_restrictions: list[str] | None = None) -> dict[str, Any]:
    raw_restrictions = clean_registration_restrictions(raw_restrictions)
    groups = parse_student_restriction_groups(raw_restrictions)
    include_majors = major_restrictions or groups["include"]["major"]
    cleaned_major_restrictions = canonical_values(include_majors, major=True)
    excluded_major_restrictions = canonical_values(groups["exclude"]["major"], major=True)
    department_restrictions = canonical_values(groups["include"]["department"])
    excluded_department_restrictions = canonical_values(groups["exclude"]["department"])
    college_restrictions = canonical_values(groups["include"]["college"])
    excluded_college_restrictions = canonical_values(groups["exclude"]["college"])
    return {
        "registration_restrictions": raw_restrictions,
        "major_restrictions": cleaned_major_restrictions,
        "major_restriction_aliases": alias_values(cleaned_major_restrictions, major=True),
        "excluded_major_restrictions": excluded_major_restrictions,
        "excluded_major_restriction_aliases": alias_values(excluded_major_restrictions, major=True),
        "has_major_restriction": bool(cleaned_major_restrictions),
        "department_restrictions": department_restrictions,
        "department_restriction_aliases": alias_values(department_restrictions),
        "excluded_department_restrictions": excluded_department_restrictions,
        "excluded_department_restriction_aliases": alias_values(excluded_department_restrictions),
        "has_department_restriction": bool(department_restrictions),
        "college_restrictions": college_restrictions,
        "college_restriction_aliases": alias_values(college_restrictions, college=True),
        "excluded_college_restrictions": excluded_college_restrictions,
        "excluded_college_restriction_aliases": alias_values(excluded_college_restrictions, college=True),
        "has_college_restriction": bool(college_restrictions),
    }


def merge_restriction_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    raw_values: list[str] = []
    majors: list[str] = []
    excluded_majors: list[str] = []
    departments: list[str] = []
    excluded_departments: list[str] = []
    colleges: list[str] = []
    excluded_colleges: list[str] = []
    for entry in entries:
        raw_restrictions = entry.get("registration_restrictions", "").strip()
        if raw_restrictions and raw_restrictions not in raw_values:
            raw_values.append(raw_restrictions)
        majors.extend(str(value) for value in entry.get("major_restrictions", []))
        excluded_majors.extend(str(value) for value in entry.get("excluded_major_restrictions", []))
        departments.extend(str(value) for value in entry.get("department_restrictions", []))
        excluded_departments.extend(str(value) for value in entry.get("excluded_department_restrictions", []))
        colleges.extend(str(value) for value in entry.get("college_restrictions", []))
        excluded_colleges.extend(str(value) for value in entry.get("excluded_college_restrictions", []))

    merged = restriction_entry(" | ".join(raw_values), majors)
    merged["excluded_major_restrictions"] = canonical_values(excluded_majors, major=True)
    merged["excluded_major_restriction_aliases"] = alias_values(
        merged["excluded_major_restrictions"],
        major=True,
    )
    merged["department_restrictions"] = canonical_values(departments)
    merged["department_restriction_aliases"] = alias_values(merged["department_restrictions"])
    merged["excluded_department_restrictions"] = canonical_values(excluded_departments)
    merged["excluded_department_restriction_aliases"] = alias_values(
        merged["excluded_department_restrictions"]
    )
    merged["college_restrictions"] = canonical_values(colleges)
    merged["college_restriction_aliases"] = alias_values(merged["college_restrictions"], college=True)
    merged["excluded_college_restrictions"] = canonical_values(excluded_colleges)
    merged["excluded_college_restriction_aliases"] = alias_values(
        merged["excluded_college_restrictions"],
        college=True,
    )
    merged["has_department_restriction"] = bool(merged["department_restrictions"])
    merged["has_college_restriction"] = bool(merged["college_restrictions"])
    return merged


def read_restriction_terms() -> set[str]:
    if postgres.configured():
        return {row["term"] for row in postgres.restrictions_rows() if row.get("term")}
    if not TAMU_RESTRICTIONS_FILE.exists():
        return set()

    terms: set[str] = set()
    with TAMU_RESTRICTIONS_FILE.open("r", encoding="utf-8", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            term_code = row.get("term", "").strip()
            if term_code:
                terms.add(term_code)
    return terms


def term_code_value(term_code: str) -> int | None:
    try:
        return int(term_code)
    except ValueError:
        return None


def read_restrictions_by_course_term() -> dict[tuple[str, str], dict[str, Any]]:
    if postgres.configured():
        source_rows: Iterable[dict[str, str]] = postgres.restrictions_rows()
    elif TAMU_RESTRICTIONS_FILE.exists():
        with TAMU_RESTRICTIONS_FILE.open("r", encoding="utf-8", newline="") as csv_file:
            source_rows = list(csv.DictReader(csv_file))
    else:
        return {}

    grouped_entries: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in source_rows:
        term_code = row.get("term", "").strip()
        current_course_id = course_id(row.get("subject", ""), row.get("course_number", ""))
        if not term_code or not current_course_id:
            continue

        raw_restrictions = row.get("restrictions", "").strip()
        grouped_entries.setdefault((term_code, current_course_id), []).append(
            restriction_entry(raw_restrictions)
        )

    return {key: merge_restriction_entries(entries) for key, entries in grouped_entries.items()}


def read_restrictions_by_section_term() -> dict[tuple[str, str], dict[str, Any]]:
    if postgres.configured():
        source_rows: Iterable[dict[str, str]] = postgres.restrictions_rows()
    elif TAMU_RESTRICTIONS_FILE.exists():
        with TAMU_RESTRICTIONS_FILE.open("r", encoding="utf-8", newline="") as csv_file:
            source_rows = list(csv.DictReader(csv_file))
    else:
        return {}

    grouped_entries: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in source_rows:
        term_code = row.get("term", "").strip()
        crn = row.get("crn", "").strip()
        if not term_code or not crn:
            continue

        raw_restrictions = row.get("restrictions", "").strip()
        grouped_entries.setdefault((term_code, crn), []).append(
            restriction_entry(raw_restrictions)
        )

    return {key: merge_restriction_entries(entries) for key, entries in grouped_entries.items()}


def previous_course_restriction(
    restrictions_by_course_term: dict[tuple[str, str], dict[str, Any]],
    term_code: str,
    current_course_id: str,
) -> dict[str, Any] | None:
    current_term_value = term_code_value(term_code)
    previous_entries: list[dict[str, Any]] = []
    for (candidate_term, candidate_course_id), entry in restrictions_by_course_term.items():
        if candidate_course_id != current_course_id or candidate_term == term_code:
            continue

        candidate_term_value = term_code_value(candidate_term)
        if current_term_value is not None and candidate_term_value is not None:
            is_previous = candidate_term_value < current_term_value
        else:
            is_previous = candidate_term < term_code
        if is_previous:
            previous_entries.append(entry)

    if not previous_entries:
        return None
    return merge_restriction_entries(previous_entries)


def read_major_options() -> list[str]:
    if postgres.configured():
        source_rows: Iterable[dict[str, str]] = postgres.restrictions_rows()
    elif TAMU_RESTRICTIONS_FILE.exists():
        with TAMU_RESTRICTIONS_FILE.open("r", encoding="utf-8", newline="") as csv_file:
            source_rows = list(csv.DictReader(csv_file))
    else:
        return []

    majors: dict[str, str] = {}
    for row in source_rows:
        for major in parse_major_restrictions(row.get("restrictions", "")):
            majors.setdefault(major.casefold(), major)
    return sorted(majors.values(), key=str.casefold)


def compact_section(
    row: dict[str, str],
    restrictions: dict[str, Any] | None = None,
    restriction_terms: set[str] | None = None,
) -> dict[str, Any]:
    attributes = split_attributes(row.get("attributes", ""))
    raw_site = row.get("site", "").strip()
    site = LOCATION_FILTERS.get(raw_site.casefold(), raw_site)
    term_code = row.get("term_code", "").strip()
    filter_locations = section_filter_locations(attributes, raw_site)
    section_restrictions = restrictions or {}
    restriction_data_available = bool(section_restrictions) or term_code in (restriction_terms or set())

    instructors = row.get("instructors", "").strip()
    normalized_instructor_list = normalized_instructors(
        instructors, row.get("instructor_details", "")
    )
    return {
        "term_code": term_code,
        "filter_terms": [term_filter_from_code(term_code)] if term_filter_from_code(term_code) else [],
        "crn": row.get("crn", "").strip(),
        "section": row.get("section", "").strip(),
        "title": row.get("title", "").strip(),
        "attributes": attributes,
        "raw_attributes": row.get("attributes", "").strip(),
        "site": site,
        "schedule_type": row.get("schedule_type", "").strip(),
        "instruction_type": row.get("instruction_type", "").strip(),
        "filter_locations": filter_locations,
        "filter_core_attributes": matching_values(attributes, CORE_FILTERS),
        "filter_graduation_requirements": matching_values(attributes, GRADUATION_REQUIREMENT_FILTERS),
        "credit_hours": row.get("credit_hours", "").strip(),
        "seat_status_open": row.get("seat_status_open", "").strip(),
        "max_enrollment": row.get("max_enrollment", "").strip(),
        "enrollment": row.get("enrollment", "").strip(),
        "seats_available": row.get("seats_available", "").strip(),
        "wait_capacity": row.get("wait_capacity", "").strip(),
        "wait_count": row.get("wait_count", "").strip(),
        "wait_available": row.get("wait_available", "").strip(),
        "instructors": instructors,
        "normalized_instructors": normalized_instructor_list,
        "meeting_days": row.get("meeting_days", "").strip(),
        "meeting_times": row.get("meeting_times", "").strip(),
        "meeting_locations": row.get("meeting_locations", "").strip(),
        "restriction_data_available": restriction_data_available,
        "registration_restrictions": section_restrictions.get("registration_restrictions", ""),
        "major_restrictions": section_restrictions.get("major_restrictions", []),
        "major_restriction_aliases": section_restrictions.get("major_restriction_aliases", []),
        "excluded_major_restrictions": section_restrictions.get("excluded_major_restrictions", []),
        "excluded_major_restriction_aliases": section_restrictions.get("excluded_major_restriction_aliases", []),
        "has_major_restriction": bool(section_restrictions.get("has_major_restriction", False)),
        "department_restrictions": section_restrictions.get("department_restrictions", []),
        "department_restriction_aliases": section_restrictions.get("department_restriction_aliases", []),
        "excluded_department_restrictions": section_restrictions.get("excluded_department_restrictions", []),
        "excluded_department_restriction_aliases": section_restrictions.get("excluded_department_restriction_aliases", []),
        "has_department_restriction": bool(section_restrictions.get("has_department_restriction", False)),
        "college_restrictions": section_restrictions.get("college_restrictions", []),
        "college_restriction_aliases": section_restrictions.get("college_restriction_aliases", []),
        "excluded_college_restrictions": section_restrictions.get("excluded_college_restrictions", []),
        "excluded_college_restriction_aliases": section_restrictions.get("excluded_college_restriction_aliases", []),
        "has_college_restriction": bool(section_restrictions.get("has_college_restriction", False)),
    }


def current_sections_files() -> list[Path]:
    data_dir = ROOT_DIR / "data"
    with_attributes = sorted(data_dir.glob(CURRENT_SECTIONS_PATTERN))
    if with_attributes:
        return with_attributes

    fallback_files = [
        path
        for path in sorted(data_dir.glob(CURRENT_SECTIONS_FALLBACK_PATTERN))
        if not path.name.endswith("_with_attributes.csv")
    ]
    return fallback_files


def current_sections_file() -> Path | None:
    files = current_sections_files()
    return files[-1] if files else None


def term_code_for_sections_file(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        first_row = next(csv.DictReader(csv_file), None)
    return str((first_row or {}).get("term_code") or "").strip()


def active_current_sections_files() -> list[Path]:
    """Return the newest available snapshot for each offering season."""
    latest_by_season: dict[str, tuple[str, Path]] = {}
    for path in current_sections_files():
        term_code = term_code_for_sections_file(path)
        season = term_filter_from_code(term_code)
        if season and term_code > latest_by_season.get(season, ("", path))[0]:
            latest_by_season[season] = (term_code, path)
    return [entry[1] for entry in latest_by_season.values()]


def section_snapshot_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Read a section CSV into a stable `(term_code, CRN)` snapshot."""
    with path.open("r", encoding="utf-8", newline="") as csv_file:
        rows = csv.DictReader(csv_file)
        return {
            (row.get("term_code", "").strip(), row.get("crn", "").strip()): dict(row)
            for row in rows
            if row.get("term_code", "").strip() and row.get("crn", "").strip()
        }


def section_snapshot_course_ids(rows: Iterable[dict[str, str]]) -> set[str]:
    return {
        course_id(row.get("subject", "").strip().upper(), row.get("course_number", "").strip())
        for row in rows
        if course_id(row.get("subject", "").strip().upper(), row.get("course_number", "").strip())
    }


def section_change_detail(
    key: tuple[str, str],
    previous: dict[str, str] | None,
    current: dict[str, str] | None,
) -> dict[str, Any]:
    """Format one added, removed, or changed section for an opt-in sync report."""
    before = previous or {}
    after = current or {}
    if previous is None:
        change_type = "added"
    elif current is None:
        change_type = "removed"
    else:
        change_type = "updated"
    changed_fields = {
        field: {"before": before.get(field, ""), "after": after.get(field, "")}
        for field in sorted(set(before) | set(after))
        if before.get(field, "") != after.get(field, "")
    }
    representative = current or previous or {}
    return {
        "term_code": key[0],
        "crn": key[1],
        "course": representative.get("course", ""),
        "section": representative.get("section", ""),
        "title": representative.get("title", ""),
        "change_type": change_type,
        "changes": changed_fields,
    }


def refresh_tracked_public_sections_with_changes(
    dry_run: bool = False,
    show_changed: bool = False,
) -> dict[str, Any]:
    """Refresh active Howdy snapshots and report catalog courses whose sections changed.

    The CSV replacement is atomic.  The caller can use `changed_course_ids` to
    update only dynamic section fields in the relational/search projections.
    """
    files = current_sections_files()
    if not files:
        raise RuntimeError("No tracked public-sections CSV is available to refresh.")

    from scripts.scrape_tamu_public_class_search import OUTPUT_FIELDS, fetch_sections, normalize_section

    available_term_files: list[tuple[Path, str]] = []
    for path in files:
        term_code = term_code_for_sections_file(path)
        if not term_code:
            raise RuntimeError(f"Could not determine a term code from {path.name}.")
        available_term_files.append((path, term_code))

    # Hourly availability changes matter for the newest scheduled term. Keep
    # older term snapshots available to search, but do not repeatedly call
    # Howdy for their completed/obsolete sections.
    newest_term = max(term_code for _, term_code in available_term_files)
    term_files = [
        (path, term_code)
        for path, term_code in available_term_files
        if term_code == newest_term
    ]

    refreshed_terms: list[str] = []
    total_sections = 0
    changed_course_ids: set[str] = set()
    changed_section_count = 0
    changed_section_details: list[dict[str, Any]] = []
    added_section_details: list[dict[str, Any]] = []
    removed_section_details: list[dict[str, Any]] = []
    seat_status_changed_section_details: list[dict[str, Any]] = []
    for path, term_code in term_files:
        rows = [normalize_section(row) for row in fetch_sections(term_code)]
        previous_rows = section_snapshot_rows(path)
        next_rows = {
            (row.get("term_code", "").strip(), row.get("crn", "").strip()): row
            for row in rows
            if row.get("term_code", "").strip() and row.get("crn", "").strip()
        }
        changed_keys = {
            key
            for key in previous_rows.keys() | next_rows.keys()
            if previous_rows.get(key) != next_rows.get(key)
        }
        changed_section_count += len(changed_keys)
        for key in sorted(changed_keys):
            previous = previous_rows.get(key)
            current = next_rows.get(key)
            detail = section_change_detail(key, previous, current)
            if previous is None:
                added_section_details.append(detail)
            elif current is None:
                removed_section_details.append(detail)
            elif previous.get("seat_status_open", "") != current.get("seat_status_open", ""):
                seat_status_changed_section_details.append(detail)
            if show_changed:
                changed_section_details.append(detail)
        changed_course_ids.update(
            section_snapshot_course_ids(
                row
                for key in changed_keys
                for row in (previous_rows.get(key), next_rows.get(key))
                if row is not None
            )
        )
        if not dry_run:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.stem}-", suffix=".csv", dir=path.parent, text=True
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output_file:
                    writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
                    writer.writeheader()
                    writer.writerows(
                        {field: row.get(field, "") for field in OUTPUT_FIELDS}
                        for row in rows
                    )
                os.replace(temporary_name, path)
            except Exception:
                Path(temporary_name).unlink(missing_ok=True)
                raise
        refreshed_terms.append(term_code)
        total_sections += len(rows)
    report: dict[str, Any] = {
        "terms": refreshed_terms,
        # Kept alongside `fetched_sections` so reports plainly state the
        # number of Howdy section records compared against the snapshot.
        "scanned_sections": total_sections,
        "fetched_sections": total_sections,
        "changed_sections": changed_section_count,
        "added_sections": len(added_section_details),
        "removed_sections": len(removed_section_details),
        "seat_status_changed_sections": len(seat_status_changed_section_details),
        "changed_course_ids": sorted(changed_course_ids),
        "dry_run": dry_run,
    }
    if show_changed:
        report["changed_section_details"] = changed_section_details
        report["added_section_details"] = added_section_details
        report["removed_section_details"] = removed_section_details
        report["seat_status_changed_section_details"] = seat_status_changed_section_details
    return report


def current_term_code() -> str:
    if postgres.configured():
        return max(postgres.active_term_codes(), default="")
    files = current_sections_files()
    if not files:
        return ""

    term_codes: list[str] = []
    for path in files:
        with path.open("r", encoding="utf-8", newline="") as csv_file:
            for row in csv.DictReader(csv_file):
                term_code = row.get("term_code", "").strip()
                if term_code:
                    term_codes.append(term_code)
                break
    return max(term_codes) if term_codes else ""


def comparison_term_context() -> dict[str, Any]:
    term_code = current_term_code()
    if len(term_code) < 5:
        return {
            "current_term_code": term_code,
            "comparison_year": None,
            "comparison_semester": "",
            "comparison_semesters": SEMESTER_LABEL_BY_KEY,
        }

    semester = SEMESTER_BY_TERM_CODE.get(term_code[4], "")
    try:
        comparison_year = int(term_code[:4]) - 1
    except ValueError:
        comparison_year = None

    return {
        "current_term_code": term_code,
        "comparison_year": comparison_year,
        "comparison_semester": semester,
        "comparison_semesters": SEMESTER_LABEL_BY_KEY,
    }


def to_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def to_float(value: str | None) -> float | None:
    try:
        return float(value or "")
    except ValueError:
        return None


def empty_grade_metrics(context: dict[str, Any]) -> dict[str, Any]:
    return {
        **context,
        "avg_gpa": None,
        "total_enrollment": 0,
        "sections_observed": 0,
        "terms_observed": 0,
        "gpa_weight": 0,
        "has_data": False,
    }


def empty_semester_grade_metrics(context: dict[str, Any], semester_key: str) -> dict[str, Any]:
    return {
        "current_term_code": context["current_term_code"],
        "comparison_year": context["comparison_year"],
        "comparison_semester": SEMESTER_LABEL_BY_KEY[semester_key],
        "semester": semester_key,
        "avg_gpa": None,
        "total_enrollment": 0,
        "sections_observed": 0,
        "terms_observed": 0,
        "gpa_weight": 0,
        "has_data": False,
    }


def read_grade_metrics_by_course_and_semester(context: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    comparison_year = context["comparison_year"]
    if comparison_year is None:
        return {}
    if postgres.configured():
        source_rows = []
        for entry in postgres.grade_rows(academic_year=comparison_year):
            subject, _, number = str(entry["course_id"]).partition("-")
            source_rows.append({
                "year": str(entry["academic_year"]), "semester": str(entry["semester"]).upper(),
                "subject": subject, "course_number": number, "total_graded": str(entry["total_graded"]),
                "total_enrollment": str(entry["total_enrollment"]), "gpa": entry["gpa"],
                "term_code": str(entry["source_term_code"]),
            })
    elif GRADE_DISTRIBUTION_FILE.exists():
        with GRADE_DISTRIBUTION_FILE.open("r", encoding="utf-8", newline="") as csv_file:
            source_rows = list(csv.DictReader(csv_file))
    else:
        return {}

    aggregates: dict[str, dict[str, dict[str, Any]]] = {}
    for row in source_rows:
        if to_int(str(row.get("year"))) != comparison_year:
            continue
        row_semester = str(row.get("semester", "")).strip().upper()
        semester_key = next(
            (key for key, value in SEMESTER_VALUE_BY_KEY.items() if value == row_semester),
            "",
        )
        if not semester_key:
            continue

        current_course_id = course_id(str(row.get("subject", "")), str(row.get("course_number", "")))
        if not current_course_id:
            continue

        course_entry = aggregates.setdefault(current_course_id, {})
        entry = course_entry.setdefault(semester_key, empty_grade_aggregate())
        total_graded = to_int(str(row.get("total_graded")))
        total_enrollment = to_int(str(row.get("total_enrollment")))
        gpa = to_float(str(row.get("gpa") or ""))

        if gpa is not None and total_graded > 0:
            entry["gpa_weighted_total"] += gpa * total_graded
            entry["gpa_weight"] += total_graded
        entry["total_enrollment"] += total_enrollment
        entry["sections_observed"] += 1
        if row.get("term_code"):
            entry["terms"].add(str(row["term_code"]))

    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for current_course_id, semester_entries in aggregates.items():
        metrics[current_course_id] = {}
        for semester_key, entry in semester_entries.items():
            metrics[current_course_id][semester_key] = grade_metrics_from_aggregate(
                context,
                semester_key,
                entry,
            )
    return metrics


def empty_grade_aggregate() -> dict[str, Any]:
    return {
        "gpa_weighted_total": 0.0,
        "gpa_weight": 0,
        "total_enrollment": 0,
        "sections_observed": 0,
        "terms": set(),
    }


def grade_metrics_from_aggregate(
    context: dict[str, Any],
    semester_key: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    avg_gpa = None
    if entry["gpa_weight"] > 0:
        avg_gpa = round(entry["gpa_weighted_total"] / entry["gpa_weight"], 3)
    return {
        "current_term_code": context["current_term_code"],
        "comparison_year": context["comparison_year"],
        "comparison_semester": SEMESTER_LABEL_BY_KEY[semester_key],
        "semester": semester_key,
        "avg_gpa": avg_gpa,
        "total_enrollment": entry["total_enrollment"],
        "sections_observed": entry["sections_observed"],
        "terms_observed": len(entry["terms"]),
        "gpa_weight": entry["gpa_weight"],
        "has_data": True,
    }


def read_current_sections_by_course() -> dict[str, dict[str, Any]]:
    if postgres.configured():
        source_rows = postgres.current_sections_rows()
    else:
        files = current_sections_files()
        source_rows = []
        if files:
            active_files = active_current_sections_files()
            active_term_codes = {term_code_for_sections_file(path) for path in active_files}
            for path in files:
                with path.open("r", encoding="utf-8", newline="") as csv_file:
                    source_rows.extend(
                        row for row in csv.DictReader(csv_file)
                        if row.get("term_code", "").strip() in active_term_codes
                    )
    if not source_rows:
        logger.warning("No current sections CSV found; course attributes will be empty.")
        return {}

    restrictions_by_section_term = read_restrictions_by_section_term()
    restrictions_by_course_term = read_restrictions_by_course_term()
    restriction_terms = read_restriction_terms()
    sections_by_course: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        subject = str(row.get("subject", "")).strip().upper()
        number = str(row.get("course_number", "")).strip()
        current_course_id = course_id(subject, number)
        if not current_course_id:
            continue

        entry = sections_by_course.setdefault(current_course_id, {"attributes": {}, "sections": []})
        for attribute in split_attributes(str(row.get("attributes", ""))):
            entry["attributes"].setdefault(attribute.lower(), attribute)
        term_code = str(row.get("term_code", "")).strip()
        crn = str(row.get("crn", "")).strip()
        section_restrictions = restrictions_by_section_term.get((term_code, crn))
        if section_restrictions is None and term_code not in restriction_terms:
            section_restrictions = previous_course_restriction(
                restrictions_by_course_term,
                term_code,
                current_course_id,
            )
        entry["sections"].append(compact_section(row, section_restrictions, restriction_terms))

    return sections_by_course


def read_courses() -> list[dict[str, Any]]:
    current_sections = read_current_sections_by_course()
    grade_context = comparison_term_context()
    grade_metrics_by_course = read_grade_metrics_by_course_and_semester(grade_context)
    current_semester_key = semester_key_from_label(grade_context.get("comparison_semester", ""))
    if postgres.configured():
        source_rows: Iterable[dict[str, Any]] = postgres.catalog_rows()
    else:
        with DATA_FILE.open("r", encoding="utf-8", newline="") as csv_file:
            source_rows = list(csv.DictReader(csv_file))
    rows = []
    for row_number, row in enumerate(source_rows, start=1):
            course_prefix = str(row.get("course_prefix", "")).strip().upper()
            number = str(row.get("number", "")).strip()
            title = str(row.get("title", "")).strip()
            subject_context = str(row.get("subject_context") or subject_context_for_prefix(course_prefix))
            current_course_id = course_id(course_prefix, number)
            current_data = current_sections.get(current_course_id, {})
            course_attributes = sorted(
                current_data.get("attributes", {}).values(),
                key=str.casefold,
            )
            current_course_sections = current_data.get("sections", [])
            grade_metrics_by_semester = {
                semester_key: grade_metrics_by_course.get(current_course_id, {}).get(
                    semester_key,
                    empty_semester_grade_metrics(grade_context, semester_key),
                )
                for semester_key in SEMESTER_KEYS
            }
            grade_metrics = grade_metrics_by_semester.get(
                current_semester_key,
                empty_grade_metrics(grade_context),
            )
            rows.append(
                {
                    "id": f"{current_course_id}-{row_number:05d}",
                    "course_id": current_course_id,
                    "course_prefix": course_prefix,
                    "subject_context": subject_context,
                    "number": number,
                    "degree_level": degree_level_from_number(number),
                    "course_code": f"{course_prefix} {number}".strip(),
                    "title": title,
                    "credit_hours": str(row.get("credit_hours", "")).strip(),
                    "description": str(row.get("description", "")).strip(),
                    "prerequisites": str(row.get("prerequisites", "")).strip(),
                    "restrictions": str(row.get("restrictions", "")).strip(),
                    "cross_listings": str(row.get("cross_listings", "")).strip(),
                    "attributes": str(row.get("attributes", "")).strip(),
                    "course_attributes": course_attributes,
                    "current_sections": current_course_sections,
                    "current_section_count": len(current_course_sections),
                    "grade_metrics": grade_metrics,
                    "grade_metrics_by_semester": grade_metrics_by_semester,
                    "display_name": f"{course_prefix} {number}: {title}".strip(": "),
                    "sort_key": f"{course_prefix} {number}".strip(),
                }
            )
    return rows


def course_search_projection(course: dict[str, Any]) -> dict[str, Any]:
    """Return the stable, course-only payload used by ParadeDB retrieval.

    Active sections are deliberately excluded.  They change frequently and
    are evaluated in PostgreSQL after candidate retrieval, with every filter
    bound to one physical CRN.
    """
    omitted = {
        "current_sections", "current_section_count", "course_attributes",
        "grade_metrics", "grade_metrics_by_semester",
    }
    return {key: value for key, value in course.items() if key not in omitted}


def sync_section_filter_facts(courses: Iterable[dict[str, Any]]) -> int:
    """Persist active-section filter facts without merging facts across CRNs."""
    if not postgres.configured():
        return 0

    def normalized(values: Iterable[Any]) -> list[str]:
        return sorted({str(value).strip().casefold() for value in values if str(value).strip()})

    facts: list[dict[str, Any]] = []
    for course in courses:
        current_course_id = str(course.get("course_id") or "")
        for section in course.get("current_sections") or []:
            term_code = str(section.get("term_code") or "")
            crn = str(section.get("crn") or "")
            if not current_course_id or not term_code or not crn:
                continue
            facts.append({
                "term_code": term_code,
                "crn": crn,
                "course_id": current_course_id,
                "filter_terms": normalized(section.get("filter_terms") or []),
                "filter_locations": normalized(section.get("filter_locations") or []),
                "instruction_type": str(section.get("instruction_type") or "").casefold(),
                "filter_core_attributes": normalized(section.get("filter_core_attributes") or []),
                "filter_graduation_requirements": normalized(section.get("filter_graduation_requirements") or []),
                "attributes": normalized(section.get("attributes") or []),
                "restriction_data_available": bool(section.get("restriction_data_available")),
                "has_major_restriction": bool(section.get("has_major_restriction")),
                "major_restriction_aliases": normalized(section.get("major_restriction_aliases") or []),
                "excluded_major_restriction_aliases": normalized(section.get("excluded_major_restriction_aliases") or []),
                "has_department_restriction": bool(section.get("has_department_restriction")),
                "department_restriction_aliases": normalized(section.get("department_restriction_aliases") or []),
                "excluded_department_restriction_aliases": normalized(section.get("excluded_department_restriction_aliases") or []),
                "has_college_restriction": bool(section.get("has_college_restriction")),
                "college_restriction_aliases": normalized(section.get("college_restriction_aliases") or []),
                "excluded_college_restriction_aliases": normalized(section.get("excluded_college_restriction_aliases") or []),
                "section_data": section,
            })
    return postgres.replace_section_filter_facts(facts)


def post_filter_paradedb_hits(
    hits: list[dict[str, Any]],
    filters: SectionFilters,
    attributes: list[str] | None,
    student_major: str | None,
    require_current_sections: bool,
    include_section_data: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Use PostgreSQL section facts to retain courses with a matching CRN."""
    course_ids = list(dict.fromkeys(
        str(hit.get("_source", {}).get("course_id") or "") for hit in hits
        if str(hit.get("_source", {}).get("course_id") or "")
    ))
    profile = student_restriction_profile(student_major)
    sections_by_course = postgres.matching_active_sections(
        course_ids,
        filter_terms=list(filters.get("filter_terms", [])),
        locations=list(filters.get("filter_locations", [])),
        instruction_types=list(filters.get("instruction_type", [])),
        core_attributes=list(filters.get("filter_core_attributes", [])),
        graduation_requirements=list(filters.get("filter_graduation_requirements", [])),
        attributes=canonicalize(attributes),
        major_aliases=profile["major_aliases"],
        department_aliases=profile["department_aliases"],
        college_aliases=profile["college_aliases"],
        require_current_sections=require_current_sections,
        include_section_data=include_section_data,
    )
    has_section_filter = bool(
        filters or canonicalize(attributes) or (student_major or "").strip() or require_current_sections
    )
    if not has_section_filter:
        return hits, sections_by_course
    allowed = set(sections_by_course)
    return [hit for hit in hits if str(hit.get("_source", {}).get("course_id") or "") in allowed], sections_by_course


def active_section_filter_spec(
    filters: SectionFilters,
    attributes: list[str] | None,
    student_major: str | None,
) -> postgres.ActiveSectionFilterSpec:
    profile = student_restriction_profile(student_major)
    return postgres.ActiveSectionFilterSpec(
        filter_terms=list(filters.get("filter_terms", [])),
        locations=list(filters.get("filter_locations", [])),
        instruction_types=list(filters.get("instruction_type", [])),
        core_attributes=list(filters.get("filter_core_attributes", [])),
        graduation_requirements=list(filters.get("filter_graduation_requirements", [])),
        attributes=canonicalize(attributes),
        major_aliases=profile["major_aliases"],
        department_aliases=profile["department_aliases"],
        college_aliases=profile["college_aliases"],
    )


def read_subject_options() -> list[str]:
    if postgres.configured():
        return postgres.subject_options()
    if not DATA_FILE.exists():
        return []

    subjects: set[str] = set()
    with DATA_FILE.open("r", encoding="utf-8", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            subject = row.get("course_prefix", "").strip().upper()
            if subject:
                subjects.add(subject)
    return sorted(subjects)


def read_subject_context_by_prefix() -> dict[str, str]:
    global _subject_context_by_prefix
    if _subject_context_by_prefix is not None:
        return _subject_context_by_prefix

    contexts: dict[str, str] = {}
    if postgres.configured():
        contexts = postgres.subject_contexts()
    elif SUBJECT_CONTEXT_FILE.exists():
        with SUBJECT_CONTEXT_FILE.open("r", encoding="utf-8", newline="") as context_file:
            for row in csv.DictReader(context_file):
                prefix = row.get("course_prefix", "").strip().upper()
                context = row.get("subject_context", "").strip()
                if prefix and context:
                    contexts[prefix] = context
    else:
        logger.warning("Subject-context file %s is missing; embeddings will use prefix codes only.", SUBJECT_CONTEXT_FILE)

    _subject_context_by_prefix = contexts
    return contexts


def subject_context_for_prefix(course_prefix: str) -> str:
    prefix = course_prefix.strip().upper()
    return read_subject_context_by_prefix().get(prefix, prefix)


def subject_name_for_prefix(course_prefix: str) -> str:
    """Return the human-readable subject name for an autocomplete option."""
    prefix = course_prefix.strip().upper()
    context = subject_context_for_prefix(prefix)
    parts = re.split(r"\s+[—–-]\s+", context, maxsplit=2)
    if len(parts) >= 2 and parts[0].strip().upper() == prefix:
        return parts[1].strip() or prefix
    return context or prefix


def read_historical_section_lookup() -> dict[tuple[int, str, str, str], dict[str, Any]]:
    lookup: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    for path in current_sections_files():
        with path.open("r", encoding="utf-8", newline="") as csv_file:
            for row in csv.DictReader(csv_file):
                term_code = row.get("term_code", "").strip()
                year = year_from_term_code(term_code)
                semester_key = semester_key_from_label(term_filter_from_code(term_code))
                if year is None or semester_key not in SEMESTER_KEYS:
                    continue

                current_course_id = course_id(
                    row.get("subject", ""),
                    row.get("course_number", ""),
                )
                section = row.get("section", "").strip()
                if not current_course_id or not section:
                    continue

                compact = compact_section(row)
                lookup[(year, semester_key, current_course_id, section)] = {
                    "locations": compact["filter_locations"],
                    "instruction_type": compact["instruction_type"],
                    "attributes": compact["attributes"],
                    "normalized_instructors": compact["normalized_instructors"],
                }
    return lookup


def resolve_historical_instructors(
    grade_report_instructors: list[dict[str, str]],
    section_data: dict[str, Any],
) -> tuple[list[dict[str, str]], str]:
    """Resolve abbreviated grade-report names to full section-snapshot names.

    A grade report is assigned only when every abbreviated instructor has one
    and only one compatible full-name instructor on that exact section.
    """
    if not section_data:
        return [], "unmatched_section"
    section_instructors = section_data.get("normalized_instructors", [])
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for grade_report_instructor in grade_report_instructors:
        candidates = [
            instructor
            for instructor in section_instructors
            if instructor.get("legacy_key") == grade_report_instructor.get("legacy_key")
        ]
        if len(candidates) != 1:
            return [], "identity_mismatch" if not candidates else "ambiguous_section_instructors"
        candidate = candidates[0]
        if candidate["key"] not in seen:
            seen.add(candidate["key"])
            resolved.append(candidate)
    return (resolved, "section_match") if resolved else ([], "missing_grade_report_instructor")


def read_historical_outcomes(
    course_id_value: str | None = None,
    start_year: int | None = None,
    instructor_id: str | None = None,
) -> list[dict[str, Any]]:
    if postgres.configured():
        outcomes: list[dict[str, Any]] = []
        for row_number, row in enumerate(
            postgres.historical_outcome_rows(course_id_value, start_year, instructor_id),
            start=1,
        ):
            semester_key = str(row["semester"]).casefold()
            if semester_key not in SEMESTER_KEYS:
                continue
            section_data: dict[str, Any] = {}
            if row.get("matched_term_code"):
                compact = compact_section({
                    "term_code": str(row["matched_term_code"]), "crn": "", "section": str(row["section_number"]),
                    "title": "", "attributes": "| ".join(row.get("attributes") or []), "site": str(row.get("site") or ""),
                    "schedule_type": "", "instruction_type": str(row.get("instruction_type") or ""), "credit_hours": "",
                    "seat_status_open": "", "max_enrollment": "", "enrollment": "", "seats_available": "",
                    "wait_capacity": "", "wait_count": "", "wait_available": "", "instructors": str(row.get("instructors") or ""),
                    "instructor_details": json.dumps(row.get("instructor_details") or []), "meeting_days": "", "meeting_times": "", "meeting_locations": "",
                })
                section_data = {
                    "locations": compact["filter_locations"], "instruction_type": compact["instruction_type"],
                    "attributes": compact["attributes"], "normalized_instructors": compact["normalized_instructors"],
                }
            resolved_instructor_id = str(row.get("resolved_instructor_id") or "")
            if resolved_instructor_id:
                resolved_name = str(row.get("resolved_instructor_name") or row["instructor"] or "Instructor")
                instructors = [{
                    "name": resolved_name,
                    "key": resolved_instructor_id,
                    "instructor_id": resolved_instructor_id,
                    "howdy_id": resolved_instructor_id,
                    "name_key": "",
                    "legacy_key": "",
                }]
                identity_resolution = str(row.get("instructor_resolution_status") or "section_match")
            else:
                grade_report_instructors = normalized_instructors(str(row["instructor"]))
                instructors, identity_resolution = resolve_historical_instructors(grade_report_instructors, section_data)
            total_graded = int(row["total_graded"])
            gpa = row["gpa"]
            outcomes.append({
                "id": f"{row['source_hash']}-{row_number:06d}", "course_id": str(row["course_id"]),
                "year": int(row["academic_year"]), "semester": semester_key,
                "semester_label": SEMESTER_LABEL_BY_KEY[semester_key], "term_code": str(row["source_term_code"]),
                "section": str(row["section_number"]), "crn": str(row.get("matched_crn") or ""),
                "instructor": str(row["instructor"]),
                "normalized_instructors": instructors, "instructor_keys": [instructor["key"] for instructor in instructors],
                "instructor_ids": [str(instructor.get("instructor_id") or "") for instructor in instructors if instructor.get("instructor_id")],
                "identity_resolution": identity_resolution, "locations": section_data.get("locations", []),
                "instruction_type": section_data.get("instruction_type", ""), "total_enrollment": int(row["total_enrollment"]),
                "gpa_weight": total_graded,
                "grade_points_total": round((float(gpa) if gpa is not None else 0.0) * total_graded, 6),
                "grade_counts": {grade: int((row["grade_counts"] or {}).get(column, 0)) for grade, column in GRADE_COUNT_FIELDS.items()},
                "has_section_match": bool(section_data),
            })
        return outcomes
    if not GRADE_DISTRIBUTION_FILE.exists():
        return []

    section_lookup = read_historical_section_lookup()
    outcomes: list[dict[str, Any]] = []
    with GRADE_DISTRIBUTION_FILE.open("r", encoding="utf-8", newline="") as csv_file:
        for row_number, row in enumerate(csv.DictReader(csv_file), start=1):
            year = to_int(row.get("year"))
            if start_year is not None and year < start_year:
                continue
            row_semester = row.get("semester", "").strip().upper()
            semester_key = next(
                (key for key, value in SEMESTER_VALUE_BY_KEY.items() if value == row_semester),
                "",
            )
            if semester_key not in SEMESTER_KEYS:
                continue

            current_course_id = course_id(row.get("subject", ""), row.get("course_number", ""))
            if course_id_value and current_course_id != course_id_value:
                continue
            section = row.get("section", "").strip()
            if not current_course_id or not section:
                continue

            total_graded = to_int(row.get("total_graded"))
            total_enrollment = to_int(row.get("total_enrollment"))
            gpa = to_float(row.get("gpa"))
            grade_points_total = (gpa or 0.0) * total_graded if gpa is not None and total_graded > 0 else 0.0
            section_data = section_lookup.get((year, semester_key, current_course_id, section), {})
            grade_report_instructors = normalized_instructors(row.get("instructor", ""))
            instructors, identity_resolution = resolve_historical_instructors(
                grade_report_instructors,
                section_data,
            )
            if instructor_id and instructor_id not in {str(instructor.get("instructor_id") or "") for instructor in instructors}:
                continue
            outcomes.append(
                {
                    "id": f"{row.get('term_code', '')}-{current_course_id}-{section}-{row_number:06d}",
                    "course_id": current_course_id,
                    "year": year,
                    "semester": semester_key,
                    "semester_label": SEMESTER_LABEL_BY_KEY[semester_key],
                    "term_code": row.get("term_code", "").strip(),
                    "section": section, "crn": "",
                    "instructor": row.get("instructor", "").strip(),
                    "normalized_instructors": instructors,
                    "instructor_keys": [instructor["key"] for instructor in instructors],
                    "instructor_ids": [str(instructor.get("instructor_id") or "") for instructor in instructors if instructor.get("instructor_id")],
                    "identity_resolution": identity_resolution,
                    "locations": section_data.get("locations", []),
                    "instruction_type": section_data.get("instruction_type", ""),
                    "total_enrollment": total_enrollment,
                    "gpa_weight": total_graded,
                    "grade_points_total": round(grade_points_total, 6),
                    "grade_counts": {
                        grade: to_int(row.get(column))
                        for grade, column in GRADE_COUNT_FIELDS.items()
                    },
                    "has_section_match": bool(section_data),
                }
            )
    return outcomes


def rebuild_paradedb_projection() -> int:
    """Build the PostgreSQL-native search projection from source-of-truth rows."""
    courses = read_courses()
    sync_section_filter_facts(courses)
    projections = [course_search_projection(course) for course in courses]
    embeddings: list[list[float] | None]
    embeddings = [None] * len(projections)
    if embedding_model_may_load():
        try:
            embeddings = embed_texts([embedding_text_for_course(course) for course in projections])
        except Exception:
            logger.exception("Building ParadeDB lexical projection without semantic embeddings.")
    return paradedb.rebuild_documents(projections, embeddings)


def update_indexed_course_sections(changed_course_ids: Iterable[str]) -> dict[str, int]:
    """Refresh the relational section/filter projection for changed courses."""
    course_ids = sorted({value for value in changed_course_ids if value})
    if not course_ids:
        return {"updated_courses": 0, "missing_courses": 0}
    courses_by_id = {str(course["course_id"]): course for course in read_courses()}
    sync_section_filter_facts(courses_by_id.values())
    return {
        "updated_courses": len([course_id for course_id in course_ids if course_id in courses_by_id]),
        "missing_courses": len(set(course_ids) - set(courses_by_id)),
    }


def refresh_and_sync_current_sections(
    dry_run: bool = False,
    show_changed: bool = False,
    term_code: str | None = None,
    refresh_all_restrictions: bool = False,
    restriction_workers: int = 12,
) -> dict[str, Any]:
    """Fetch current Howdy sections and their restrictions into PostgreSQL."""
    if not postgres.configured():
        raise RuntimeError("DATABASE_URL is required for section refreshes.")
    active_term_code = term_code or max(postgres.active_term_codes(), default="")
    if not active_term_code:
        raise RuntimeError("PostgreSQL has no active term. Run scripts/bootstrap_postgres.py first.")
    from scripts.scrape_tamu_public_class_search import (
        fetch_restrictions_for_sections,
        fetch_sections,
        normalize_section,
    )

    run_id = None if dry_run else postgres.start_section_refresh()
    try:
        section_rows = [normalize_section(row) for row in fetch_sections(active_term_code)]
        report = postgres.sync_term_sections(
            active_term_code,
            section_rows,
            dry_run=dry_run,
            show_changed=show_changed,
            run_id=run_id,
        )
    except Exception as exc:
        if run_id is not None:
            postgres.fail_section_refresh(run_id, exc)
        raise

    try:
        existing_restriction_crns = postgres.restriction_snapshot_crns(active_term_code)
        changed_crns = set(report.get("changed_crns") or [])
        restriction_targets = [
            row
            for row in section_rows
            if (
                refresh_all_restrictions
                or str(row.get("crn") or "") in changed_crns
                or str(row.get("crn") or "") not in existing_restriction_crns
            )
        ]
        restriction_rows = fetch_restrictions_for_sections(
            restriction_targets,
            workers=restriction_workers,
        )
        restriction_report = postgres.sync_section_restrictions(
            active_term_code,
            restriction_rows,
            dry_run=dry_run,
        )
    except Exception as exc:
        if run_id is not None:
            postgres.fail_section_refresh(run_id, exc)
        raise
    report.update(restriction_report)
    report["full_restriction_refresh"] = refresh_all_restrictions
    report["terms"] = [active_term_code]
    if dry_run:
        report.update({"updated_courses": 0, "missing_courses": 0})
        return report
    affected_course_ids = set(report["changed_course_ids"])
    affected_course_ids.update(restriction_report["restriction_changed_course_ids"])
    report.update(update_indexed_course_sections(affected_course_ids))
    return report


def synchronize_section_term_snapshots() -> bool:
    """Repair active term rows when the persisted DB predates local snapshots.

    The frontend reads the available offering terms from the committed CSV
    snapshots, while search reads active terms from PostgreSQL.  A persistent
    database volume can therefore retain an older term set after a new image
    is deployed.  Re-import only when the active term sets differ so normal
    startups remain cheap.
    """
    expected_terms = {
        term_code_for_sections_file(path)
        for path in active_current_sections_files()
    }
    expected_terms.discard("")
    if not expected_terms:
        return False

    stored_terms = set(postgres.active_term_codes())
    if stored_terms == expected_terms:
        return False

    logger.info(
        "Reconciling PostgreSQL offering terms: stored=%s expected=%s",
        sorted(stored_terms),
        sorted(expected_terms),
    )
    postgres.import_terms_and_sections(current_sections_files())
    return True


def ensure_indexed() -> None:
    if not postgres.configured():
        raise RuntimeError("DATABASE_URL is required before starting the API.")
    postgres.ensure_schema()
    counts = postgres.database_counts()
    if counts["courses"] == 0:
        raise RuntimeError(
            "PostgreSQL is empty. Run scripts/bootstrap_postgres.py before starting the API."
        )
    synchronize_section_term_snapshots()
    if (
        postgres.section_filter_fact_count() != postgres.catalog_active_section_count()
        or postgres.section_filter_fact_locations_stale()
    ):
        sync_section_filter_facts(read_courses())
    if counts["grade_history"] and counts.get("course_historical_location_term_stats", 0) == 0:
        postgres.refresh_historical_stats()
    paradedb.ensure_schema()
    count = paradedb.document_count()
    if (
        count != postgres.database_counts()["courses"]
        or not paradedb.projection_is_current()
        or not paradedb.documents_are_course_only()
        or not paradedb.documents_have_degree_levels()
        or not paradedb.documents_have_embeddings()
    ):
        indexed = rebuild_paradedb_projection()
        logger.info("Indexed %s courses into ParadeDB.", indexed)
    else:
        logger.info("ParadeDB projection already contains %s courses.", count)


_runtime_gc_frozen = False


def warm_search_runtime() -> None:
    """Load long-lived ranking models, then exclude them from cyclic GC scans."""
    global _runtime_gc_frozen
    try:
        embedding_model = get_embedding_model()
        if embedding_model is not None:
            embedding_model.encode(
                ["course search warmup"],
                convert_to_numpy=True,
                normalize_embeddings=False,
                show_progress_bar=False,
            )
    except Exception:
        logger.exception("Could not preload the embedding model; lexical fallback remains available.")
    if LTR_MODEL_FILE.exists() and LTR_MODEL_METADATA_FILE.exists():
        try:
            get_ltr_model()
        except Exception:
            logger.exception("Could not preload the LTR model; RRF fallback remains available.")
    gc.collect()
    gc.freeze()
    _runtime_gc_frozen = True
    logger.info("Search models warmed and long-lived objects frozen for stable request latency.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _runtime_gc_frozen
    postgres.open_pool()
    try:
        ensure_indexed()
        warm_search_runtime()
        yield
    finally:
        if _runtime_gc_frozen:
            gc.unfreeze()
            _runtime_gc_frozen = False
        postgres.close_pool()


app = FastAPI(title="Course Search API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_utility_endpoint_indexing(request: Request, call_next):
    """Let renderers fetch JSON resources without indexing those resources."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/") or path == "/search":
        response.headers["X-Robots-Tag"] = "noindex"
    return response


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def restriction_group_filter(
    *,
    has_field: str,
    alias_field: str,
    profile_aliases: list[str],
) -> dict[str, Any]:
    should: list[dict[str, Any]] = [{"term": {f"current_sections.{has_field}": False}}]
    if profile_aliases:
        should.append({"terms": {f"current_sections.{alias_field}": [alias.lower() for alias in profile_aliases]}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


def exclusion_filter(alias_field: str, profile_aliases: list[str]) -> dict[str, Any] | None:
    if not profile_aliases:
        return None
    return {
        "bool": {
            "must_not": [
                {"terms": {f"current_sections.{alias_field}": [alias.lower() for alias in profile_aliases]}},
            ]
        }
    }


def build_course_filters(
    prefix: str | None,
    attributes: list[str] | None,
    degree_levels: list[str] | None,
    section_filters: SectionFilters | None = None,
    require_current_sections: bool = False,
    student_major: str | None = None,
) -> list[dict[str, Any]]:
    filters = []
    if prefix:
        filters.append({"term": {"course_prefix": prefix.strip().lower()}})
    cleaned_degree_levels = canonicalize(degree_levels, DEGREE_LEVEL_FILTERS)
    if cleaned_degree_levels:
        filters.append({"terms": {"degree_level": cleaned_degree_levels}})

    nested_filters = []
    for attribute in canonicalize(attributes):
        nested_filters.append({"term": {"current_sections.attributes": attribute.lower()}})

    for field, values in (section_filters or {}).items():
        cleaned_values = [value.lower() for value in values if value.strip()]
        if cleaned_values:
            nested_filters.append({"terms": {f"current_sections.{field}": cleaned_values}})

    student_profile = student_restriction_profile(student_major)
    if student_profile["major_aliases"]:
        nested_filters.append({"term": {"current_sections.restriction_data_available": True}})
        nested_filters.extend(
            [
                restriction_group_filter(
                    has_field="has_major_restriction",
                    alias_field="major_restriction_aliases",
                    profile_aliases=student_profile["major_aliases"],
                ),
                restriction_group_filter(
                    has_field="has_department_restriction",
                    alias_field="department_restriction_aliases",
                    profile_aliases=student_profile["department_aliases"],
                ),
                restriction_group_filter(
                    has_field="has_college_restriction",
                    alias_field="college_restriction_aliases",
                    profile_aliases=student_profile["college_aliases"],
                ),
            ]
        )
        for excluded_field, aliases in (
            ("excluded_major_restriction_aliases", student_profile["major_aliases"]),
            ("excluded_department_restriction_aliases", student_profile["department_aliases"]),
            ("excluded_college_restriction_aliases", student_profile["college_aliases"]),
        ):
            excluded_filter = exclusion_filter(excluded_field, aliases)
            if excluded_filter:
                nested_filters.append(excluded_filter)

    if nested_filters or require_current_sections:
        filters.append(
            {
                "nested": {
                    "path": "current_sections",
                    "query": {"bool": {"filter": nested_filters}} if nested_filters else {"match_all": {}},
                }
            }
        )

    return filters


def rank_window_size(offset: int, limit: int, post_rank_requested: bool, post_rank_candidates: int = POST_RANK_CANDIDATES) -> int:
    requested_size = post_rank_candidates if post_rank_requested else offset + limit
    return max(requested_size, RRF_MIN_RANK_WINDOW)


def rrf_fuse_hit_lists(hit_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    sequence = 0

    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            doc_id = hit.get("_id")
            if not doc_id:
                continue
            if doc_id not in candidates:
                candidates[doc_id] = dict(hit)
                first_seen[doc_id] = sequence
                sequence += 1
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (RRF_RANK_CONSTANT + rank))

    fused_hits = []
    for doc_id, hit in candidates.items():
        fused_hit = dict(hit)
        fused_hit["_score"] = scores[doc_id]
        fused_hits.append(fused_hit)

    return sorted(
        fused_hits,
        key=lambda hit: (-float(hit.get("_score") or 0), first_seen.get(str(hit.get("_id")), 0)),
    )


def section_matches(
    section: dict[str, Any],
    filters: SectionFilters,
    attributes: list[str] | None,
    student_major: str | None = None,
) -> bool:
    section_attributes = {value.casefold() for value in section.get("attributes", [])}
    for attribute in canonicalize(attributes):
        if attribute.casefold() not in section_attributes:
            return False

    student_profile = student_restriction_profile(student_major)
    if student_profile["major_aliases"] and not section.get("restriction_data_available"):
        return False

    def profile_intersects(section_field: str, profile_field: str) -> bool:
        section_values = {str(value).casefold() for value in section.get(section_field, [])}
        profile_values = {value.casefold() for value in student_profile.get(profile_field, [])}
        return bool(section_values.intersection(profile_values))

    if student_profile["major_aliases"]:
        if section.get("has_major_restriction") and not profile_intersects(
            "major_restriction_aliases",
            "major_aliases",
        ):
            return False
        if section.get("has_department_restriction") and not profile_intersects(
            "department_restriction_aliases",
            "department_aliases",
        ):
            return False
        if section.get("has_college_restriction") and not profile_intersects(
            "college_restriction_aliases",
            "college_aliases",
        ):
            return False
        if profile_intersects("excluded_major_restriction_aliases", "major_aliases"):
            return False
        if profile_intersects("excluded_department_restriction_aliases", "department_aliases"):
            return False
        if profile_intersects("excluded_college_restriction_aliases", "college_aliases"):
            return False

    for field, selected_values in filters.items():
        if not selected_values:
            continue
        if field == "instruction_type":
            section_values = {str(section.get("instruction_type", "")).casefold()}
        else:
            section_values = {value.casefold() for value in section.get(field, [])}
        if not section_values.intersection(value.casefold() for value in selected_values):
            return False
    return True


def matching_sections(
    source: dict[str, Any],
    filters: SectionFilters,
    attributes: list[str] | None,
    student_major: str | None = None,
) -> list[dict[str, Any]]:
    if not filters and not canonicalize(attributes) and not (student_major or "").strip():
        return source.get("current_sections", [])
    return [
        section
        for section in source.get("current_sections", [])
        if section_matches(section, filters, attributes, student_major)
    ]


def attributes_from_sections(sections: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, str] = {}
    for section in sections:
        for attribute in section.get("attributes", []):
            seen.setdefault(attribute.casefold(), attribute)
    return sorted(seen.values(), key=str.casefold)


def direct_course_code_query(q: str | None) -> str:
    match = re.search(r"\b([A-Za-z]{2,5})\s*-?\s*(\d{3}[A-Za-z]?)\b", q or "")
    if not match:
        return ""
    return f"{match.group(1).upper()} {match.group(2).upper()}"


def prioritize_exact_course_code_hits(hits: list[dict[str, Any]], q: str | None) -> list[dict[str, Any]]:
    direct_code = direct_course_code_query(q)
    if not direct_code:
        return hits

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, hit = item
        source = hit.get("_source", {})
        course_code = str(source.get("course_code", "")).upper()
        return (0 if course_code == direct_code else 1, index)

    return [hit for _, hit in sorted(enumerate(hits), key=sort_key)]


def selected_semester_keys(selected_terms: list[str] | None) -> list[str]:
    keys = [semester_key_from_label(term) for term in selected_terms or []]
    keys = [key for key in keys if key in SEMESTER_KEYS]
    return keys or SEMESTER_KEYS


def normalize_search_scope(
    location: list[str] | None,
    term: list[str] | None,
    instruction_type: list[str] | None,
    core: list[str] | None,
    graduation_requirement: list[str] | None,
    degree_level: list[str] | None,
) -> SearchScope:
    all_locations = location is not None and any(value.strip().casefold() == "all" for value in location)
    return SearchScope(
        {
            "terms": canonicalize(term, TERM_FILTERS),
            "locations": [] if location is None or all_locations else canonicalize(location, LOCATION_FILTERS),
            "instruction_types": canonicalize(instruction_type, INSTRUCTION_TYPE_FILTERS),
            "core_attributes": canonicalize(core, CORE_FILTERS),
            "graduation_requirements": canonicalize(graduation_requirement, GRADUATION_REQUIREMENT_FILTERS),
            "degree_levels": canonicalize(degree_level, DEGREE_LEVEL_FILTERS),
        }
    )


def section_filters_from_scope(scope: SearchScope) -> SectionFilters:
    return SectionFilters(
        {
            "filter_terms": scope["terms"],
            "filter_locations": scope["locations"],
            "instruction_type": scope["instruction_types"],
            "filter_core_attributes": scope["core_attributes"],
            "filter_graduation_requirements": scope["graduation_requirements"],
        }
    )


def metric_scope(
    scope: SearchScope,
    semesters: list[str],
    comparison_year: int | None,
    fallback_to_prior_years: bool = False,
) -> dict[str, Any]:
    applied_filters = []
    unsupported_filters = []
    if scope["terms"]:
        applied_filters.append("term")
    if scope["locations"]:
        (unsupported_filters if postgres.configured() else applied_filters).append("location")
    if scope["instruction_types"]:
        (unsupported_filters if postgres.configured() else applied_filters).append("instruction_type")
    if scope["core_attributes"]:
        unsupported_filters.append("core")
    if scope["graduation_requirements"]:
        unsupported_filters.append("graduation_requirement")

    return {
        "semesters": semesters,
        "semester_labels": [SEMESTER_LABEL_BY_KEY[semester] for semester in semesters],
        "comparison_year": comparison_year,
        "fallback_to_prior_years": fallback_to_prior_years,
        "locations": scope["locations"],
        "instruction_types": scope["instruction_types"],
        "applied_filters": applied_filters,
        "unsupported_filters": unsupported_filters,
    }


def empty_historical_aggregate() -> dict[str, Any]:
    return {
        "grade_points_total": 0.0,
        "gpa_weight": 0,
        "total_enrollment": 0,
        "sections_observed": 0,
        "terms": set(),
    }


def add_outcome_to_aggregate(entry: dict[str, Any], outcome: dict[str, Any]) -> None:
    entry["grade_points_total"] += float(outcome.get("grade_points_total") or 0)
    entry["gpa_weight"] += to_int(str(outcome.get("gpa_weight", 0)))
    entry["total_enrollment"] += to_int(str(outcome.get("total_enrollment", 0)))
    entry["sections_observed"] += 1
    term_code = outcome.get("term_code")
    if term_code:
        entry["terms"].add(str(term_code))


def historical_metrics_from_aggregate(
    entry: dict[str, Any],
    comparison_year: int | None,
    semester_key: str | None = None,
) -> dict[str, Any]:
    avg_gpa = None
    if entry["gpa_weight"] > 0:
        avg_gpa = round(entry["grade_points_total"] / entry["gpa_weight"], 3)
    metrics: dict[str, Any] = {
        "comparison_year": comparison_year,
        "avg_gpa": avg_gpa,
        "total_enrollment": entry["total_enrollment"],
        "sections_observed": entry["sections_observed"],
        "terms_observed": int(entry.get("terms_observed", len(entry["terms"]))),
        "gpa_weight": entry["gpa_weight"],
        "has_data": entry["sections_observed"] > 0,
    }
    if semester_key:
        metrics["semester"] = semester_key
        metrics["comparison_semester"] = SEMESTER_LABEL_BY_KEY[semester_key]
    return metrics


def empty_selected_grade_metrics(
    semesters: list[str],
    comparison_year: int | None,
    scope_details: dict[str, Any],
) -> dict[str, Any]:
    by_semester = {
        semester: historical_metrics_from_aggregate(
            empty_historical_aggregate(),
            comparison_year,
            semester,
        )
        for semester in semesters
    }
    return {
        **historical_metrics_from_aggregate(empty_historical_aggregate(), comparison_year),
        "requested_comparison_year": comparison_year,
        "used_historical_fallback": False,
        "semesters": semesters,
        "semester_labels": [SEMESTER_LABEL_BY_KEY[semester] for semester in semesters],
        "scope": scope_details,
        "by_semester": by_semester,
    }


def aggregate_historical_metrics(
    course_ids: list[str],
    scope: SearchScope,
    semesters: list[str],
    fallback_to_prior_years: bool = False,
    timings: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    # PostgreSQL maintains one row per course/year/season during grade imports.
    # Search only combines those tiny precomputed rows; it never aggregates raw
    # grade_history records on the request path.
    if postgres.configured():
        projection_started = time.perf_counter()
        grade_context = comparison_term_context()
        comparison_year = grade_context["comparison_year"]
        scope_details = metric_scope(scope, semesters, comparison_year, fallback_to_prior_years)
        empty_by_course = {
            current_course_id: empty_selected_grade_metrics(semesters, comparison_year, scope_details)
            for current_course_id in course_ids
        }
        if not course_ids or comparison_year is None:
            return empty_by_course
        rows = postgres.historical_term_stat_rows(
            course_ids, semesters, comparison_year, list(scope["locations"]),
        )
        by_course: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if not fallback_to_prior_years and int(row["academic_year"]) != comparison_year:
                continue
            by_course.setdefault(str(row["course_id"]), []).append(row)
        for current_course_id, course_rows in by_course.items():
            selected_year = max(int(row["academic_year"]) for row in course_rows)
            selected = [row for row in course_rows if int(row["academic_year"]) == selected_year]
            by_semester: dict[str, dict[str, Any]] = {}
            total = empty_historical_aggregate()
            total["terms_observed"] = 0
            for semester in semesters:
                row = next((item for item in selected if str(item["semester"]) == semester), None)
                entry = empty_historical_aggregate()
                entry["terms_observed"] = 0
                if row:
                    entry = {
                        "grade_points_total": float(row["average_gpa"] or 0) * int(row["gpa_weight"]),
                        "gpa_weight": int(row["gpa_weight"]),
                        "total_enrollment": int(row["total_enrollment"]),
                        "sections_observed": int(row["section_count"]),
                        "terms": set(),
                        "terms_observed": int(row["term_count"]),
                    }
                    for field in ("grade_points_total", "gpa_weight", "total_enrollment", "sections_observed"):
                        total[field] += entry[field]
                    total["terms_observed"] += entry["terms_observed"]
                by_semester[semester] = historical_metrics_from_aggregate(entry, selected_year, semester)
            metrics = historical_metrics_from_aggregate(total, selected_year)
            empty_by_course[current_course_id] = {
                **metrics,
                "requested_comparison_year": comparison_year,
                "used_historical_fallback": fallback_to_prior_years and selected_year != comparison_year,
                "semesters": semesters,
                "semester_labels": [SEMESTER_LABEL_BY_KEY[semester] for semester in semesters],
                "scope": scope_details,
                "by_semester": by_semester,
            }
        if timings is not None:
            timings["historical_projection_seconds"] = time.perf_counter() - projection_started
        return empty_by_course
    raise RuntimeError("DATABASE_URL is required for historical metrics.")


def metric_rank_sort_key(
    hit: dict[str, Any],
    metrics_by_course: dict[str, dict[str, Any]],
    rank: Literal["most_enrolled", "gpa"],
    bayesian_gpa_by_course: dict[str, float] | None = None,
) -> tuple[int, float, float, str]:
    source = hit["_source"]
    course_id_value = source.get("course_id", "")
    metrics = metrics_by_course.get(course_id_value, {})
    sort_key = source.get("sort_key", "")
    if rank == "most_enrolled":
        enrollment = float(metrics.get("total_enrollment") or 0)
        missing = 0 if enrollment > 0 else 1
        return (missing, -enrollment, 0, sort_key)

    avg_gpa = metrics.get("avg_gpa")
    enrollment = float(metrics.get("total_enrollment") or 0)
    missing = 0 if avg_gpa is not None else 1
    bayesian_gpa = (bayesian_gpa_by_course or {}).get(course_id_value, 0.0)
    return (missing, -bayesian_gpa, -float(avg_gpa or 0), -enrollment, sort_key)


def bayesian_gpa_scores(metrics_by_course: dict[str, dict[str, Any]]) -> dict[str, float]:
    metrics_with_gpa = [
        metrics
        for metrics in metrics_by_course.values()
        if metrics.get("avg_gpa") is not None and to_int(str(metrics.get("gpa_weight", 0))) > 0
    ]
    if not metrics_with_gpa:
        return {}

    total_weight = sum(to_int(str(metrics.get("gpa_weight", 0))) for metrics in metrics_with_gpa)
    if total_weight <= 0:
        return {}

    observed_global_gpa = sum(
        float(metrics.get("avg_gpa") or 0) * to_int(str(metrics.get("gpa_weight", 0)))
        for metrics in metrics_with_gpa
    ) / total_weight
    global_gpa = min(BAYESIAN_GPA_BASELINE_MAX, max(BAYESIAN_GPA_BASELINE_MIN, observed_global_gpa))
    weights = sorted(to_int(str(metrics.get("gpa_weight", 0))) for metrics in metrics_with_gpa)
    minimum_weight = weights[len(weights) // 2]
    if minimum_weight <= 0:
        minimum_weight = 1

    scores = {}
    for course_id_value, metrics in metrics_by_course.items():
        avg_gpa = metrics.get("avg_gpa")
        course_weight = to_int(str(metrics.get("gpa_weight", 0)))
        if avg_gpa is None or course_weight <= 0:
            continue
        scores[course_id_value] = round(
            ((course_weight / (course_weight + minimum_weight)) * float(avg_gpa))
            + ((minimum_weight / (course_weight + minimum_weight)) * global_gpa),
            6,
        )
    return scores


def format_hit(
    hit: dict[str, Any],
    filters: SectionFilters,
    attributes: list[str] | None,
    selected_metrics_by_course: dict[str, dict[str, Any]],
    student_major: str | None = None,
) -> dict[str, Any]:
    source = dict(hit["_source"])
    source.pop(SEARCH_EMBEDDING_FIELD, None)
    source.pop("subject_context", None)
    sections = matching_sections(source, filters, attributes, student_major)
    if filters or canonicalize(attributes) or (student_major or "").strip():
        source["current_sections"] = sections
        source["current_section_count"] = len(sections)
        source["course_attributes"] = attributes_from_sections(sections)
    source["matching_current_sections"] = sections
    source["matching_current_section_count"] = len(sections)
    source["selected_grade_metrics"] = selected_metrics_by_course.get(source.get("course_id", ""))
    source["score"] = hit.get("_score")
    return source


def normalize_course_identifier(value: str) -> str:
    match = re.fullmatch(r"\s*([A-Za-z]{2,8})[\s_-]*(\d{3}[A-Za-z]?)\s*", value or "")
    if not match:
        return ""
    return course_id(match.group(1), match.group(2))


def course_page_sections(course_id_value: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Fetch just the current and recent archived College Station offerings."""
    if not postgres.configured():
        current_data = read_current_sections_by_course().get(course_id_value, {})
        return current_data.get("sections", []), [], sorted(current_data.get("attributes", {}).values(), key=str.casefold)

    restriction_rows = postgres.course_restriction_rows(course_id_value)
    section_restrictions: dict[tuple[str, str], dict[str, Any]] = {}
    course_restriction_entries: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in restriction_rows:
        term_code = str(row.get("term_code") or "")
        crn = str(row.get("crn") or "")
        if not term_code or not crn:
            continue
        entry = restriction_entry(str(row.get("raw_restrictions") or ""))
        section_restrictions[(term_code, crn)] = entry
        course_restriction_entries.setdefault((term_code, course_id_value), []).append(entry)
    course_restrictions = {
        key: merge_restriction_entries(entries)
        for key, entries in course_restriction_entries.items()
    }
    restriction_terms = postgres.restriction_term_codes()

    def compact(rows: list[dict[str, Any]], archived: bool = False) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            term_code = str(row.get("term_code") or "")
            crn = str(row.get("crn") or "")
            restrictions = section_restrictions.get((term_code, crn))
            if restrictions is None and term_code not in restriction_terms:
                restrictions = previous_course_restriction(course_restrictions, term_code, course_id_value)
            section = compact_section(row, restrictions, restriction_terms)
            if archived:
                section["is_archived"] = True
            result.append(section)
        return result

    current_sections = compact(postgres.course_current_section_rows(course_id_value))
    archived_sections = compact(postgres.course_recent_archived_section_rows(course_id_value), archived=True)
    course_attributes = sorted(
        {attribute for section in current_sections for attribute in section.get("attributes", [])},
        key=str.casefold,
    )
    return current_sections, archived_sections, course_attributes


def course_page_catalog_record(
    row: dict[str, Any],
    current_sections: list[dict[str, Any]],
    course_attributes: list[str],
) -> dict[str, Any]:
    """Build the course-page catalog payload without building the full catalog."""
    course_prefix = str(row.get("course_prefix") or "").strip().upper()
    number = str(row.get("number") or "").strip()
    current_course_id = course_id(course_prefix, number)
    return {
        "id": current_course_id,
        "course_id": current_course_id,
        "course_prefix": course_prefix,
        "subject_context": str(row.get("subject_context") or subject_context_for_prefix(course_prefix)),
        "number": number,
        "degree_level": degree_level_from_number(number),
        "course_code": f"{course_prefix} {number}".strip(),
        "title": str(row.get("title") or "").strip(),
        "credit_hours": str(row.get("credit_hours") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "prerequisites": str(row.get("prerequisites") or "").strip(),
        "restrictions": str(row.get("restrictions") or "").strip(),
        "cross_listings": str(row.get("cross_listings") or "").strip(),
        "attributes": str(row.get("attributes") or "").strip(),
        "prerequisite_dependents": list(row.get("prerequisite_dependents") or []),
        "course_attributes": course_attributes,
        "current_sections": current_sections,
        "current_section_count": len(current_sections),
        "display_name": f"{course_prefix} {number}: {str(row.get('title') or '').strip()}".strip(": "),
        "sort_key": f"{course_prefix} {number}".strip(),
    }


def course_history_detail(course_id_value: str, start_year: int = 2023) -> dict[str, Any]:
    outcomes = read_historical_outcomes(course_id_value, start_year)
    by_term: dict[tuple[int, str], dict[str, Any]] = {}
    by_professor: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        key = (int(outcome.get("year") or 0), str(outcome.get("semester") or ""))
        entry = by_term.setdefault(
            key,
            {
                "year": key[0],
                "semester": key[1],
                "semester_label": SEMESTER_LABEL_BY_KEY.get(key[1], key[1].title()),
                **empty_historical_aggregate(),
            },
        )
        add_outcome_to_aggregate(entry, outcome)
        for instructor in outcome.get("normalized_instructors", []):
            instructor_id = str(instructor.get("instructor_id") or "")
            if not instructor_id:
                continue
            professor_entry = by_professor.setdefault(
                instructor_id,
                {
                    "professor_key": instructor_id,
                    "professor": instructor.get("name") or outcome.get("instructor") or "Unknown instructor",
                    **empty_historical_aggregate(),
                },
            )
            add_outcome_to_aggregate(professor_entry, outcome)

    term_history = [
        {
            "year": entry["year"],
            "semester": entry["semester"],
            "semester_label": entry["semester_label"],
            **historical_metrics_from_aggregate(entry, entry["year"], entry["semester"]),
        }
        for _, entry in sorted(
            by_term.items(),
            key=lambda item: (
                item[0][0],
                SEMESTER_CHRONOLOGICAL_ORDER.get(item[0][1], 0),
            ),
            reverse=True,
        )
    ]
    course_professor_summaries = [
        {
            "professor_key": entry["professor_key"],
            "professor": entry["professor"],
            **historical_metrics_from_aggregate(entry, None),
        }
        for _, entry in sorted(
            by_professor.items(),
            key=lambda item: str(item[1]["professor"]).casefold(),
        )
    ]
    return {
        "start_year": start_year,
        "term_history": term_history,
        "course_professor_summaries": course_professor_summaries,
        "outcomes": outcomes,
    }


def normalize_professor_key(value: str) -> str:
    """Accept a raw Howdy instructor ID, with the old prefixed form as an alias."""
    key = str(value or "").strip().casefold()
    if key.isdigit():
        return key
    legacy_match = re.fullmatch(r"howdy-(\d+)", key)
    return legacy_match.group(1) if legacy_match else ""


def professor_detail(professor_key: str, start_year: int = 2023) -> dict[str, Any]:
    """Build the data needed for a single instructor's public profile."""
    if not postgres.configured():
        raise RuntimeError("DATABASE_URL is required for professor details.")
    restriction_terms = postgres.restriction_term_codes()
    current_by_course: dict[str, dict[str, Any]] = {}
    for row in postgres.instructor_current_section_rows(professor_key):
        section = compact_section(
            row,
            restriction_entry(str(row.get("raw_restrictions") or "")),
            restriction_terms,
        )
        course_id_value = str(row["course_id"])
        current_by_course.setdefault(
            course_id_value,
            {
                "course_id": course_id_value,
                "course_code": str(row["course_code"]),
                "title": str(row["course_title"]),
                "current_sections": [],
            },
        )["current_sections"].append(section)
    current_courses = list(current_by_course.values())
    outcomes = read_historical_outcomes(start_year=start_year, instructor_id=professor_key)
    if not outcomes and not current_courses:
        raise HTTPException(status_code=404, detail="Professor not found.")

    display_names = [
        instructor.get("name")
        for course in current_courses
        for section in course.get("current_sections", [])
        for instructor in section.get("normalized_instructors", [])
        if instructor.get("instructor_id") == professor_key and instructor.get("name")
    ]
    if not display_names:
        display_names = [
            instructor.get("name")
            for outcome in outcomes
            for instructor in outcome.get("normalized_instructors", [])
            if instructor.get("instructor_id") == professor_key and instructor.get("name")
        ]
    professor_name = next(iter(display_names), (outcomes[0].get("instructor") if outcomes else "Instructor"))
    course_ids = sorted({str(outcome.get("course_id") or "") for outcome in outcomes if outcome.get("course_id")})
    historical_courses = []
    if course_ids:
        historical_courses = [
            {
                "course_id": str(course["course_id"]),
                "course_code": f"{course['course_prefix']} {course['number']}",
                "title": str(course["title"]),
                "current_sections": [],
            }
            for course in postgres.catalog_rows_by_ids(course_ids)
        ]
    courses = list({str(course.get("course_id")): course for course in [*historical_courses, *current_courses]}.values())
    course_by_id = {str(course.get("course_id")): course for course in courses}

    overall = empty_historical_aggregate()
    by_term: dict[tuple[int, str], dict[str, Any]] = {}
    by_course: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        add_outcome_to_aggregate(overall, outcome)
        term_key = (int(outcome.get("year") or 0), str(outcome.get("semester") or ""))
        term_entry = by_term.setdefault(
            term_key,
            {"year": term_key[0], "semester": term_key[1], "semester_label": SEMESTER_LABEL_BY_KEY.get(term_key[1], term_key[1].title()), **empty_historical_aggregate()},
        )
        add_outcome_to_aggregate(term_entry, outcome)
        course_id_value = str(outcome.get("course_id") or "")
        course_entry = by_course.setdefault(course_id_value, {"course_id": course_id_value, **empty_historical_aggregate()})
        add_outcome_to_aggregate(course_entry, outcome)

    course_summaries = []
    for course_id_value, entry in by_course.items():
        course = course_by_id.get(course_id_value, {})
        course_summaries.append(
            {
                "course_id": course_id_value,
                "course_code": course.get("course_code") or course_id_value.replace("-", " "),
                "title": course.get("title") or "Course title unavailable",
                **historical_metrics_from_aggregate(entry, None),
            }
        )
    course_summaries.sort(key=lambda entry: (-int(entry["total_enrollment"]), str(entry["course_code"])))
    term_history = [
        {**historical_metrics_from_aggregate(entry, entry["year"], entry["semester"]), "year": entry["year"], "semester": entry["semester"], "semester_label": entry["semester_label"]}
        for _, entry in sorted(
            by_term.items(),
            key=lambda item: (
                item[0][0],
                SEMESTER_CHRONOLOGICAL_ORDER.get(item[0][1], 0),
            ),
            reverse=True,
        )
    ]
    course_gpa_by_id = {
        str(summary["course_id"]): summary["avg_gpa"]
        for summary in course_summaries
    }

    current_sections = []
    for course in courses:
        for section in course.get("current_sections", []):
            instructors = section.get("normalized_instructors", [])
            if any(instructor.get("instructor_id") == professor_key for instructor in instructors):
                current_sections.append(
                    {
                        "course_id": course.get("course_id"),
                        "course_code": course.get("course_code") or str(course.get("course_id") or "").replace("-", " "),
                        "course_title": course.get("title") or "",
                        **section,
                        "professor_gpas": [
                            {
                                "name": professor_name,
                                "avg_gpa": course_gpa_by_id.get(str(course.get("course_id") or "")),
                            }
                        ],
                    }
                )
    current_sections.sort(key=lambda section: (str(section.get("term_code") or ""), str(section.get("course_code") or ""), str(section.get("section") or "")), reverse=True)
    return {
        "current_term_code": current_term_code(),
        "professor": {
            "key": professor_key,
            "name": professor_name,
            "start_year": start_year,
            **historical_metrics_from_aggregate(overall, None),
            "courses_taught": len(course_summaries),
        },
        "term_history": term_history,
        "course_summaries": course_summaries,
        "current_sections": current_sections,
        "outcomes": outcomes,
    }


@app.get("/")
def home(request: Request) -> Response:
    # The search UI used to live here, so shared links carry its query string.
    # Send those straight to the search page instead of the landing page.
    if request.url.query:
        return RedirectResponse(f"/courses?{request.url.query}", status_code=307)
    home_file = FRONTEND_DIR / "home.html"
    if not home_file.exists():
        raise HTTPException(status_code=404, detail="Frontend files are missing.")
    return FileResponse(home_file)


def public_origin(request: Request) -> str:
    """Use the externally visible scheme and host supplied by the reverse proxy."""
    return str(request.base_url).rstrip("/")


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request) -> Response:
    origin = public_origin(request)
    body = "\n".join(
        (
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin/",
            "Disallow: /annotation",
            "Disallow: /healthz",
            "Disallow: /readyz",
            f"Sitemap: {origin}/sitemap.xml",
            "",
        )
    )
    return Response(
        body,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request) -> Response:
    origin = public_origin(request)
    urls = [f"{origin}/", f"{origin}/courses"]
    urls.extend(
        f"{origin}/course/{quote(course_id, safe='')}"
        for course_id in postgres.sitemap_course_ids()
    )
    urls.extend(
        f"{origin}/professor/{quote(professor_id, safe='')}"
        for professor_id in postgres.sitemap_professor_ids()
    )
    entries = "".join(f"<url><loc>{xml_escape(url)}</loc></url>" for url in urls)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )
    return Response(
        body,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/courses")
def frontend() -> FileResponse:
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend files are missing.")
    return FileResponse(index_file)


@app.get("/annotation")
def annotation_frontend() -> FileResponse:
    annotation_file = FRONTEND_DIR / "annotation.html"
    if not annotation_file.exists():
        raise HTTPException(status_code=404, detail="Annotation workspace is missing.")
    return FileResponse(annotation_file)


@app.get("/annotation-queries")
def annotation_queries() -> dict[str, Any]:
    if not HUMAN_TEST_QUERIES_FILE.exists():
        raise HTTPException(status_code=404, detail=f"{HUMAN_TEST_QUERIES_FILE.name} is missing.")
    with HUMAN_TEST_QUERIES_FILE.open("r", encoding="utf-8-sig", newline="") as query_file:
        queries = [
            {
                "query_id": str(row.get("query_id") or "").strip(),
                "query": str(row.get("query") or "").strip(),
                "category": str(row.get("category") or "").strip(),
                "difficulty": str(row.get("difficulty") or "").strip(),
                "expected_intent": str(row.get("expected_intent") or "").strip(),
                "annotation_notes": str(row.get("annotation_notes") or "").strip(),
                "student_persona": str(row.get("student_persona") or "").strip(),
            }
            for row in csv.DictReader(query_file)
            if str(row.get("query") or "").strip()
        ]
    return {"source": HUMAN_TEST_QUERIES_FILE.name, "count": len(queries), "queries": queries}


@app.get("/annotation-search")
def annotation_search(
    q: str = Query(min_length=1, description="Human-test query to build a fixed annotation pool for"),
) -> dict[str, Any]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="A non-empty annotation query is required.")
    fetch_limit = min(paradedb.document_count(), ANNOTATION_RERANK_CANDIDATES)
    if fetch_limit < ANNOTATION_POOL_SIZE:
        raise HTTPException(status_code=503, detail="The search projection has fewer than 25 courses.")
    lexical_hits, _ = paradedb.lexical_hits(query, fetch_limit, lambda _: True)
    query_vector = embed_query(query)
    semantic_hits, _ = (
        paradedb.semantic_hits(query_vector, fetch_limit, lambda _: True)
        if query_vector is not None
        else ([], 0.0)
    )
    hybrid_hits = rrf_fuse_hit_lists([lexical_hits, semantic_hits]) if semantic_hits else lexical_hits
    reranked_hits, cross_encoder_applied = rerank_with_cross_encoder(
        query,
        hybrid_hits[:ANNOTATION_RERANK_CANDIDATES],
    )
    catalog_hits, _ = paradedb.catalog_hits(lambda _: True)
    random_hits = deterministic_random_hits(query, catalog_hits)
    pool = build_annotation_pool(
        reranked_hits,
        lexical_hits,
        semantic_hits,
        random_hits,
        cross_encoder_applied=cross_encoder_applied,
    )
    if len(pool) != ANNOTATION_POOL_SIZE:
        raise HTTPException(
            status_code=503,
            detail=f"Could only build {len(pool)} unique annotation candidates; 25 are required.",
        )

    course_ids = [str(hit.get("_id") or "") for hit in pool]
    metric_scope = SearchScope(
        {
            "terms": [],
            "locations": [],
            "instruction_types": [],
            "core_attributes": [],
            "graduation_requirements": [],
            "degree_levels": [],
        }
    )
    historical = aggregate_historical_metrics(
        course_ids,
        metric_scope,
        selected_semester_keys([]),
        fallback_to_prior_years=True,
    )
    live_section_counts = postgres.live_section_counts(course_ids) if postgres.configured() else {}
    results: list[dict[str, Any]] = []
    for pool_rank, hit in enumerate(pool, 1):
        source = dict(hit.get("_source") or {})
        document_id = str(hit.get("_id") or source.get("course_id") or "")
        provenance = dict(hit.get("_annotation_provenance") or {})
        metrics = historical.get(document_id, {})
        sections = list(source.get("current_sections") or [])
        current_section_count = live_section_counts.get(
            document_id,
            int(source.get("current_section_count") or len(sections)),
        )
        features = features_for_course(
            query,
            source,
            bm25_rank=provenance.get("bm25_rank"),
            semantic_rank=provenance.get("semantic_rank"),
            current_section_count=current_section_count,
            historical_enrollment=int(metrics.get("total_enrollment") or 0),
        )
        source.pop(SEARCH_EMBEDDING_FIELD, None)
        source["pool_rank"] = pool_rank
        source["selected_grade_metrics"] = metrics
        source["current_section_count"] = current_section_count
        source["annotation_provenance"] = provenance
        source["ltr_feature_schema_version"] = LTR_FEATURE_SCHEMA_VERSION
        source["ltr_features"] = features
        results.append(source)
    return {
        "query": query,
        "pool_size": len(results),
        "pool_recipe": {
            "rrf_cross_encoder": 10,
            "bm25": 5,
            "semantic": 5,
            "random": 5,
            "duplicate_fill": "remaining_rrf_cross_encoder",
        },
        "cross_encoder_ranking_applied": cross_encoder_applied,
        "feature_schema_version": LTR_FEATURE_SCHEMA_VERSION,
        "feature_names": LTR_FEATURE_NAMES,
        "results": results,
    }


@app.get("/course/{course_identifier}")
def course_frontend(course_identifier: str) -> FileResponse:
    if not normalize_course_identifier(course_identifier):
        raise HTTPException(status_code=404, detail="Course not found.")
    course_file = FRONTEND_DIR / "course.html"
    if not course_file.exists():
        raise HTTPException(status_code=404, detail="Course page is missing.")
    return FileResponse(course_file)


@app.get("/professor/{professor_key}")
def professor_frontend(professor_key: str) -> FileResponse:
    if not normalize_professor_key(professor_key):
        raise HTTPException(status_code=404, detail="Professor not found.")
    professor_file = FRONTEND_DIR / "professor.html"
    if not professor_file.exists():
        raise HTTPException(status_code=404, detail="Professor page is missing.")
    return FileResponse(professor_file)


@app.get("/api/courses/{course_identifier}")
def course_detail(course_identifier: str) -> dict[str, Any]:
    course_id_value = normalize_course_identifier(course_identifier)
    if not course_id_value:
        raise HTTPException(status_code=404, detail="Course not found.")
    if not postgres.configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL is required.")
    archived_sections: list[dict[str, Any]] = []
    catalog_row = postgres.catalog_row(course_id_value)
    if catalog_row is None:
        course = None
    else:
        current_sections, archived_sections, course_attributes = course_page_sections(course_id_value)
        course = course_page_catalog_record(catalog_row, current_sections, course_attributes)
    if course is None:
        raise HTTPException(status_code=404, detail=f"No course found for {course_id_value}.")
    course = dict(course)
    course.pop(SEARCH_EMBEDDING_FIELD, None)
    course.setdefault("subject_context", subject_context_for_prefix(str(course.get("course_prefix") or "")))
    course["archived_sections"] = archived_sections
    course["ap_equivalencies"] = [
        {"exam": exam, "minimum_score": score, "award": award}
        for exam, score, award in AP_EQUIVALENCIES_BY_COURSE.get(course_id_value, [])
    ]
    course["unlocks"] = list(course.pop("prerequisite_dependents", []) or [])
    history = course_history_detail(course_id_value)
    course_professor_summaries = history["course_professor_summaries"]
    history["professor_summaries"] = course_professor_summaries
    professor_gpa_by_key = {
        summary["professor_key"]: summary["avg_gpa"]
        for summary in course_professor_summaries
    }
    for section in course.get("current_sections", []):
        section["professor_gpas"] = [
            {
                "name": instructor.get("name") or "Instructor",
                "avg_gpa": professor_gpa_by_key.get(instructor.get("instructor_id")),
            }
            for instructor in section.get("normalized_instructors", [])
        ]
    current_instructor_keys = {
        instructor.get("instructor_id")
        for section in course.get("current_sections", [])
        for instructor in section.get("normalized_instructors", [])
        if instructor.get("instructor_id")
    }
    for outcome in history["outcomes"]:
        outcome["matches_current_instructor"] = bool(
            current_instructor_keys.intersection(outcome.get("instructor_ids", []))
        )
    return {"current_term_code": current_term_code(), "course": course, "history": history}


@app.get("/api/professors/{professor_key}")
def professor_api_detail(professor_key: str) -> dict[str, Any]:
    normalized_key = normalize_professor_key(professor_key)
    if not normalized_key:
        raise HTTPException(status_code=404, detail="Professor not found.")
    return professor_detail(normalized_key)


def health_payload() -> dict[str, Any]:
    try:
        indexed_count = paradedb.document_count()
        database = "ok"
    except Exception:
        logger.exception("ParadeDB health check failed.")
        indexed_count = None
        database = "unavailable"
    return {
        "api": "ok",
        "search_backend": "paradedb",
        "paradedb": database,
        "indexed_count": indexed_count,
        "data_file": str(DATA_FILE),
        "current_sections_file": str(current_sections_file()),
        "current_sections_files": [str(path) for path in current_sections_files()],
        "grade_distribution_file": str(GRADE_DISTRIBUTION_FILE),
        "restrictions_file": str(TAMU_RESTRICTIONS_FILE),
        "grade_metrics_context": comparison_term_context(),
    }


def readiness_payload() -> dict[str, Any]:
    """Return readiness data or a 503 when the configured search backend is down."""
    payload = health_payload()
    ready = payload.get("paradedb") == "ok" and payload.get("indexed_count") is not None
    if not ready:
        raise HTTPException(status_code=503, detail=payload)
    return payload


def filter_options_payload() -> dict[str, Any]:
    subjects = [
        {"code": subject, "name": subject_name_for_prefix(subject)}
        for subject in read_subject_options()
    ]
    return {
        "subjects": subjects,
        "majors": read_major_options(),
    }


@app.get("/search")
def search_courses(
    q: str | None = Query(default=None, description="Search text"),
    prefix: str | None = Query(default=None, description="Optional course prefix filter"),
    attribute: list[str] | None = Query(
        default=None,
        description="Optional current offering attribute filter. May be repeated.",
    ),
    location: list[str] | None = Query(
        default=None,
        description="Optional section location filter. No value or location=all means all locations.",
    ),
    term: list[str] | None = Query(
        default=None,
        description="Optional term filter: fall, spring, or summer. May be repeated.",
    ),
    instruction_type: list[str] | None = Query(
        default=None,
        description="Optional section instruction type filter. May be repeated.",
    ),
    core: list[str] | None = Query(
        default=None,
        description="Optional core curriculum attribute filter. May be repeated.",
    ),
    graduation_requirement: list[str] | None = Query(
        default=None,
        description="Optional graduation requirement filter. May be repeated.",
    ),
    degree_level: list[str] | None = Query(
        default=None,
        description="Optional degree level filter, such as 100, 200, 300, 400, 600, 700, 800, or 900. May be repeated.",
    ),
    student_major: str | None = Query(
        default=None,
        description="Optional student major used to remove sections with incompatible major restrictions.",
    ),
    rank: Literal["relevance", "most_enrolled", "gpa"] = Query(
        default="relevance",
        description="Ranking mode: relevance, most_enrolled, or gpa.",
    ),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    request_started = time.perf_counter()
    filtering_started = time.perf_counter()
    all_locations = location is not None and any(value.strip().casefold() == "all" for value in location)
    scope = normalize_search_scope(
        location,
        term,
        instruction_type,
        core,
        graduation_requirement,
        degree_level,
    )
    if len(scope["terms"]) > 1:
        raise HTTPException(status_code=400, detail="Select one offering term or Total Course Catalog.")
    section_filters = section_filters_from_scope(scope)
    catalog_scope = not scope["terms"]
    active_section_filters = SectionFilters() if catalog_scope else section_filters
    active_attributes = None if catalog_scope else attribute
    active_student_major = None if catalog_scope else student_major
    # Past-course metrics use the previous occurrence of the selected offering
    # season. Campus location remains the only current-section filter applied
    # to the historical grade records.
    metric_filter_scope = SearchScope(
        {
            "terms": [],
            "locations": scope["locations"],
            "instruction_types": [],
            "core_attributes": [],
            "graduation_requirements": [],
            "degree_levels": [],
        }
    )
    active_degree_levels = scope["degree_levels"]
    active_semester_keys = selected_semester_keys(scope["terms"])
    course_filters = build_course_filters(
        prefix,
        active_attributes,
        active_degree_levels,
        active_section_filters,
        require_current_sections=all_locations and not catalog_scope,
        student_major=active_student_major,
    )
    filtering_seconds = time.perf_counter() - filtering_started
    metric_rank_requested = rank in {"most_enrolled", "gpa"}
    trimmed_query = (q or "").strip()
    exhaustive_metric_rank_requested = metric_rank_requested and not trimmed_query
    post_rank_requested = metric_rank_requested
    if exhaustive_metric_rank_requested:
        post_rank_candidate_count = 0
    else:
        post_rank_candidate_count = POST_RANK_CANDIDATES
    embedding_started = time.perf_counter()
    hybrid_query_vector = embed_query(trimmed_query) if trimmed_query else None
    embedding_seconds = time.perf_counter() - embedding_started
    retrieval_started = time.perf_counter()
    response = None
    retrieval_timings: dict[str, float] = {}
    bm25_ranks: dict[str, int] = {}
    semantic_ranks: dict[str, int] = {}
    paradedb_sections_by_course: dict[str, list[dict[str, Any]]] = {}
    paradedb_sections_hydrated = False
    # Apply section predicates before candidate retrieval so both lexical and
    # semantic branches score only eligible course documents.
    filter_match = lambda _: True
    requires_section_filter = bool(
        active_section_filters or canonicalize(active_attributes)
        or (active_student_major or "").strip() or (all_locations and not catalog_scope)
    )
    retrieval_section_filters: postgres.ActiveSectionFilterSpec | None = None
    if requires_section_filter:
        retrieval_section_filters = active_section_filter_spec(
            active_section_filters,
            active_attributes,
            active_student_major,
        )
    if exhaustive_metric_rank_requested or not trimmed_query:
        catalog_hits, catalog_seconds = paradedb.catalog_hits(
            filter_match,
            prefix=prefix,
            degree_levels=active_degree_levels,
            section_filters=retrieval_section_filters,
        )
        retrieval_timings["catalog_projection_search_seconds"] = catalog_seconds
        response = {"hits": {"total": {"value": len(catalog_hits)}, "hits": catalog_hits}}
    else:
        window_size = rank_window_size(offset, limit, post_rank_requested, post_rank_candidate_count)
        fetch_limit = window_size
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="paradedb-search") as executor:
            lexical_future = executor.submit(
                paradedb.lexical_hits, trimmed_query, fetch_limit, filter_match,
                prefix=prefix, degree_levels=active_degree_levels,
                section_filters=retrieval_section_filters,
            )
            semantic_future = (
                executor.submit(
                    paradedb.semantic_hits, hybrid_query_vector, fetch_limit, filter_match,
                    prefix=prefix, degree_levels=active_degree_levels,
                    section_filters=retrieval_section_filters,
                )
                if hybrid_query_vector is not None
                else None
            )
            lexical_hits, lexical_seconds = lexical_future.result()
            semantic_hits, semantic_seconds = semantic_future.result() if semantic_future else ([], 0.0)
        bm25_ranks = {str(hit.get("_id") or ""): rank for rank, hit in enumerate(lexical_hits, 1)}
        semantic_ranks = {str(hit.get("_id") or ""): rank for rank, hit in enumerate(semantic_hits, 1)}
        retrieval_timings["lexical_search_seconds"] = lexical_seconds
        retrieval_timings["semantic_search_seconds"] = semantic_seconds
        rrf_started = time.perf_counter()
        fused_hits = rrf_fuse_hit_lists([lexical_hits, semantic_hits]) if semantic_hits else lexical_hits
        retrieval_timings["rrf_fusion_seconds"] = time.perf_counter() - rrf_started
        candidate_limit = (
            max(post_rank_candidate_count, LTR_CANDIDATE_COUNT)
            if post_rank_requested
            else max(offset + limit, LTR_CANDIDATE_COUNT)
        )
        fused_hits = fused_hits[:candidate_limit] if post_rank_requested else fused_hits[offset : offset + candidate_limit]
        response = {"hits": {"total": {"value": len(fused_hits)}, "hits": fused_hits}}

    retrieval_seconds = time.perf_counter() - retrieval_started

    hit_merging_started = time.perf_counter()
    total = response["hits"]["total"]
    total_value = total["value"] if isinstance(total, dict) else total
    matching_hits = prioritize_exact_course_code_hits(response["hits"]["hits"], q)
    # Hydrate display data from the relational projection. The section filter
    # was already applied before RRF for text searches.
    hydrate_hits = matching_hits
    if not trimmed_query and not exhaustive_metric_rank_requested:
        hydrate_hits = matching_hits[offset : offset + limit]
    deferred_section_hydration = bool(
        trimmed_query and rank == "relevance" and not metric_rank_requested
    )
    if deferred_section_hydration:
        summary_course_ids = list(dict.fromkeys(
            str(hit.get("_source", {}).get("course_id") or "")
            for hit in hydrate_hits
            if str(hit.get("_source", {}).get("course_id") or "")
        ))
        section_summary_started = time.perf_counter()
        section_summaries = postgres.matching_active_section_summaries(
            summary_course_ids,
            active_section_filter_spec(
                active_section_filters,
                active_attributes,
                active_student_major,
            ),
        )
        retrieval_timings["section_summary_seconds"] = time.perf_counter() - section_summary_started
        has_section_filter = bool(
            active_section_filters or canonicalize(active_attributes)
            or (active_student_major or "").strip() or (all_locations and not catalog_scope)
        )
        if has_section_filter:
            allowed_course_ids = set(section_summaries)
            matching_hits = [
                hit for hit in matching_hits
                if str(hit.get("_source", {}).get("course_id") or "") in allowed_course_ids
            ]
            hydrate_hits = matching_hits
        for hit in hydrate_hits:
            source = dict(hit.get("_source") or {})
            current_course_id = str(source.get("course_id") or "")
            summary = section_summaries.get(current_course_id, {})
            source["current_section_count"] = int(summary.get("current_section_count") or 0)
            source["course_attributes"] = list(summary.get("course_attributes") or [])
            hit["_source"] = source
    elif not paradedb_sections_hydrated:
        section_hydration_started = time.perf_counter()
        _, paradedb_sections_by_course = post_filter_paradedb_hits(
            hydrate_hits,
            active_section_filters,
            active_attributes,
            active_student_major,
            all_locations and not catalog_scope,
        )
        retrieval_timings["section_hydration_seconds"] = time.perf_counter() - section_hydration_started
    if not deferred_section_hydration:
        for hit in hydrate_hits:
            source = dict(hit.get("_source") or {})
            current_course_id = str(source.get("course_id") or "")
            sections = paradedb_sections_by_course.get(current_course_id, [])
            source["current_sections"] = sections
            source["current_section_count"] = len(sections)
            source["course_attributes"] = attributes_from_sections(sections)
            hit["_source"] = source
    if post_rank_requested:
        total_value = min(total_value, len(matching_hits))
    historical_hits = matching_hits
    if not trimmed_query and not exhaustive_metric_rank_requested:
        # Empty-query relevance pages are already in catalog order.  Only the
        # visible page needs historical metrics or section hydration.
        historical_hits = matching_hits[offset : offset + limit]
    course_ids = [
        hit["_source"].get("course_id", "")
        for hit in historical_hits
        if hit["_source"].get("course_id")
    ]
    retrieval_timings["hit_merging_seconds"] = retrieval_timings.get("hit_merging_seconds", 0.0) + (
        time.perf_counter() - hit_merging_started
    )
    historical_started = time.perf_counter()
    historical_timings: dict[str, float] = {}
    selected_metrics_by_course = aggregate_historical_metrics(
        course_ids,
        metric_filter_scope,
        active_semester_keys,
        fallback_to_prior_years=True,
        timings=historical_timings,
    )
    historical_seconds = time.perf_counter() - historical_started
    bayesian_gpa_by_course = bayesian_gpa_scores(selected_metrics_by_course)
    for course_id_value, bayesian_gpa in bayesian_gpa_by_course.items():
        selected_metrics_by_course[course_id_value]["bayesian_gpa"] = bayesian_gpa
    rerank_started = time.perf_counter()
    sorting_seconds = 0.0
    ltr_ranking_applied = False
    if trimmed_query and rank == "relevance" and not metric_rank_requested:
        matching_hits, ltr_ranking_applied = rerank_with_ltr(
            trimmed_query,
            matching_hits,
            bm25_ranks,
            semantic_ranks,
            selected_metrics_by_course,
        )
    if metric_rank_requested:
        matching_hits = sorted(
            matching_hits,
            key=lambda hit: metric_rank_sort_key(
                hit,
                selected_metrics_by_course,
                rank,
                bayesian_gpa_by_course,
            ),
        )
        matching_hits = prioritize_exact_course_code_hits(matching_hits, q)
    if metric_rank_requested:
        hits = matching_hits[offset : offset + limit]
    elif not trimmed_query:
        hits = matching_hits[offset : offset + limit]
    elif rank == "relevance":
        hits = matching_hits[offset : offset + limit]
    else:
        hits = matching_hits
    rerank_seconds = time.perf_counter() - rerank_started
    metric_ranking_applied = metric_rank_requested

    if deferred_section_hydration:
        final_hydration_started = time.perf_counter()
        _, final_sections_by_course = post_filter_paradedb_hits(
            hits,
            active_section_filters,
            active_attributes,
            active_student_major,
            all_locations and not catalog_scope,
        )
        retrieval_timings["section_hydration_seconds"] = time.perf_counter() - final_hydration_started
        for hit in hits:
            source = dict(hit.get("_source") or {})
            current_course_id = str(source.get("course_id") or "")
            sections = final_sections_by_course.get(current_course_id, [])
            source["current_sections"] = sections
            source["current_section_count"] = len(sections)
            source["course_attributes"] = attributes_from_sections(sections)
            hit["_source"] = source
        retrieval_timings["hit_merging_seconds"] += time.perf_counter() - final_hydration_started

    formatting_started = time.perf_counter()
    formatted_results = [
        format_hit(
            hit,
            active_section_filters,
            active_attributes,
            selected_metrics_by_course,
            active_student_major,
        )
        for hit in hits
    ]
    formatting_seconds = time.perf_counter() - formatting_started
    source_processing_seconds = formatting_seconds
    logger.info(
        "Search latency q=%r rank=%s total=%.3fs filtering=%.3fs embedding=%.3fs retrieval=%.3fs section_candidate_filter=%.3fs lexical=%.3fs semantic=%.3fs rrf_fusion=%.3fs hit_merging=%.3fs section_summary=%.3fs section_hydration=%.3fs historical=%.3fs historical_projection=%.3fs rerank=%.3fs sorting=%.3fs source_processing=%.3fs",
        trimmed_query,
        rank,
        time.perf_counter() - request_started,
        filtering_seconds,
        embedding_seconds,
        retrieval_seconds,
        retrieval_timings.get("section_candidate_filter_seconds", 0.0),
        retrieval_timings.get("lexical_search_seconds", 0.0),
        retrieval_timings.get("semantic_search_seconds", 0.0),
        retrieval_timings.get("rrf_fusion_seconds", 0.0),
        retrieval_timings.get("hit_merging_seconds", 0.0),
        retrieval_timings.get("section_summary_seconds", 0.0),
        retrieval_timings.get("section_hydration_seconds", 0.0),
        historical_seconds,
        historical_timings.get("historical_projection_seconds", 0.0),
        rerank_seconds,
        sorting_seconds,
        source_processing_seconds,
    )

    return {
        "query": q or "",
        "prefix": prefix or "",
        "attributes": active_attributes or [],
        "terms": active_section_filters.get("filter_terms", []),
        "locations": metric_filter_scope["locations"],
        "instruction_types": active_section_filters.get("instruction_type", []),
        "core": active_section_filters.get("filter_core_attributes", []),
        "graduation_requirements": active_section_filters.get("filter_graduation_requirements", []),
        "degree_levels": active_degree_levels,
        "student_major": (active_student_major or "").strip(),
        "rank": rank,
        "catalog_scope": catalog_scope,
        "metric_ranking_applied": metric_ranking_applied,
        "ltr_ranking_applied": ltr_ranking_applied,
        "post_rank_candidate_count": post_rank_candidate_count if post_rank_requested else 0,
        "timings": {
            "filtering_seconds": round(filtering_seconds, 6),
            "embedding_seconds": round(embedding_seconds, 6),
            "retrieval_seconds": round(retrieval_seconds, 6),
            **{name: round(value, 6) for name, value in retrieval_timings.items()},
            "historical_seconds": round(historical_seconds, 6),
            **{name: round(value, 6) for name, value in historical_timings.items()},
            "sorting_seconds": round(sorting_seconds, 6),
            "source_processing_seconds": round(source_processing_seconds, 6),
        },
        "metric_scope": metric_scope(
            metric_filter_scope,
            active_semester_keys,
            comparison_term_context()["comparison_year"],
            fallback_to_prior_years=True,
        ),
        "limit": limit,
        "offset": offset,
        "total": total_value,
        "results": formatted_results,
    }


@app.post("/admin/reindex")
def reindex_courses() -> dict[str, Any]:
    try:
        indexed = rebuild_paradedb_projection()
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"search_backend": "paradedb", "indexed_count": indexed}


@app.post("/admin/refresh-sections")
def refresh_sections(
    dry_run: bool = Query(default=False),
    show_changed: bool = Query(default=False),
) -> dict[str, Any]:
    """Refresh Howdy availability and update affected course projections."""
    try:
        return refresh_and_sync_current_sections(
            dry_run=dry_run,
            show_changed=show_changed,
        )
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


app.include_router(
    build_metadata_router(
        health_payload=health_payload,
        readiness_payload=readiness_payload,
        filter_options_payload=filter_options_payload,
        section_refresh_status_payload=postgres.section_refresh_status,
    )
)
