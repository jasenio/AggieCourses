"""Fast, dependency-light checks for boundaries introduced by the refactor."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.metadata import build_metadata_router
from backend.app import main
from backend.app.postgres import (
    _canonical_section_row,
    _legacy_instructor_key,
    _refresh_status_payload,
    _section_change_report,
    _term_parts,
)
from scripts.scrape_tamu_public_class_search import (
    RESTRICTION_ENDPOINTS,
    parse_restriction_response,
)


class FrontendReturnNavigationTests(unittest.TestCase):
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"

    def test_detail_navigation_uses_session_storage_instead_of_from_parameters(self) -> None:
        app_source = (self.frontend_dir / "app.js").read_text(encoding="utf-8")
        course_source = (self.frontend_dir / "course.js").read_text(encoding="utf-8")

        self.assertIn('sessionStorage.setItem(', app_source)
        self.assertIn('SEARCH_RETURN_STORAGE_KEY', app_source)
        self.assertNotIn('?from=${encodeURIComponent(from)}', app_source)
        self.assertIn('COURSE_RETURN_STORAGE_KEY', course_source)
        self.assertNotIn('?from=${encodeURIComponent(window.location', course_source)

    def test_detail_pages_still_migrate_legacy_from_parameters(self) -> None:
        course_source = (self.frontend_dir / "course.js").read_text(encoding="utf-8")
        professor_source = (self.frontend_dir / "professor.js").read_text(encoding="utf-8")

        self.assertIn('removeLegacyFromParameter()', course_source)
        self.assertIn('removeLegacyFromParameter()', professor_source)
        self.assertIn('coursePathFromLegacyValue', professor_source)

    def test_primary_button_hover_does_not_apply_to_every_button(self) -> None:
        styles = (self.frontend_dir / "styles.css").read_text(encoding="utf-8")

        self.assertNotRegex(styles, r"(?m)^button:hover")
        self.assertIn('#search-form button[type="submit"]:hover', styles)
        self.assertIn(".show-more:hover", styles)

    def test_page_limit_is_request_state_not_shareable_url_state(self) -> None:
        app_source = (self.frontend_dir / "app.js").read_text(encoding="utf-8")
        build_params = app_source.split("function buildSearchParams()", 1)[1].split(
            "function restoreSearchFromUrl()", 1
        )[0]
        fetch_results = app_source.split("async function fetchResults", 1)[1].split(
            "async function runSearch()", 1
        )[0]

        self.assertNotIn('params.set("limit"', build_params)
        self.assertIn('params.set("limit", String(PAGE_SIZE))', fetch_results)


class HomeSearchSuggestionTests(unittest.TestCase):
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"

    def suggestion_params(self, label: str) -> dict[str, list[str]]:
        source = (self.frontend_dir / "home.html").read_text(encoding="utf-8")
        marker = f">{label}</a>"
        href_prefix = 'href="'
        before_label = source.split(marker, 1)[0]
        href = before_label.rsplit(href_prefix, 1)[1].split('"', 1)[0].replace("&amp;", "&")
        return parse_qs(urlparse(href).query)

    def test_filter_only_suggestions_do_not_submit_search_text(self) -> None:
        creative_arts = self.suggestion_params("easy online creative arts credit")
        csce = self.suggestion_params("popular CSCE electives")

        self.assertNotIn("q", creative_arts)
        self.assertEqual(creative_arts["instruction_type"], ["Web Based"])
        self.assertEqual(creative_arts["core"], ["Core Creative Arts (KCRA)"])
        self.assertEqual(creative_arts["rank"], ["gpa"])

        self.assertNotIn("q", csce)
        self.assertEqual(csce["prefix"], ["CSCE"])
        self.assertEqual(csce["degree_level"], ["400", "600"])
        self.assertEqual(csce["rank"], ["most_enrolled"])

    def test_entrepreneurship_is_a_level_limited_text_search(self) -> None:
        entrepreneurship = self.suggestion_params("entrepreneurship")

        self.assertEqual(entrepreneurship["q"], ["entrepreneurship"])
        self.assertEqual(entrepreneurship["degree_level"], ["100", "200"])


class SeoSurfaceTests(unittest.TestCase):
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"

    def test_every_public_page_declares_the_favicon(self) -> None:
        for filename in ("home.html", "index.html", "course.html", "professor.html"):
            source = (self.frontend_dir / filename).read_text(encoding="utf-8")
            self.assertIn('rel="icon" href="/static/favicon.svg"', source)
            self.assertIn('property="og:title"', source)
        self.assertTrue((self.frontend_dir / "favicon.svg").is_file())

    def test_static_pages_have_descriptions_and_canonicals(self) -> None:
        home = (self.frontend_dir / "home.html").read_text(encoding="utf-8")
        courses = (self.frontend_dir / "index.html").read_text(encoding="utf-8")

        self.assertIn('name="description"', home)
        self.assertIn('rel="canonical" href="/"', home)
        self.assertIn('name="description"', courses)
        self.assertIn('rel="canonical" href="/courses"', courses)
        self.assertIn("Texas A&amp;M Course Search and Grade Data", courses)

    def test_detail_scripts_set_unique_metadata_and_canonicals(self) -> None:
        for filename in ("course.js", "professor.js"):
            source = (self.frontend_dir / filename).read_text(encoding="utf-8")
            self.assertIn("updatePageMetadata(", source)
            self.assertIn('canonical.rel = "canonical"', source)
            self.assertIn("window.location.pathname", source)
            self.assertIn('openGraphUrl.setAttribute("property", "og:url")', source)

    def test_container_trusts_the_internal_caddy_proxy_headers(self) -> None:
        dockerfile = (self.frontend_dir.parent / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"--proxy-headers", "--forwarded-allow-ips=*"', dockerfile)

    def test_robots_points_to_sitemap_and_blocks_non_public_routes(self) -> None:
        client = TestClient(main.app, base_url="https://courses.example.edu")
        response = client.get("/robots.txt")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sitemap: https://courses.example.edu/sitemap.xml", response.text)
        self.assertIn("Disallow: /admin/", response.text)
        self.assertNotIn("Disallow: /api/", response.text)
        self.assertNotIn("Disallow: /search", response.text)
        self.assertNotIn("Disallow: /static/", response.text)

    def test_json_resources_are_fetchable_but_not_indexable(self) -> None:
        client = TestClient(main.app, base_url="https://courses.example.edu")
        response = client.get("/api/not-a-real-resource")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex")

    def test_sitemap_includes_static_course_and_professor_pages(self) -> None:
        client = TestClient(main.app, base_url="https://courses.example.edu")
        with (
            patch.object(main.postgres, "sitemap_course_ids", return_value=["CSCE-120", "MATH-151"]),
            patch.object(main.postgres, "sitemap_professor_ids", return_value=["12345", "67890"]),
        ):
            response = client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        root = ET.fromstring(response.text)
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = {node.text for node in root.findall("s:url/s:loc", namespace)}
        self.assertEqual(
            locations,
            {
                "https://courses.example.edu/",
                "https://courses.example.edu/courses",
                "https://courses.example.edu/course/CSCE-120",
                "https://courses.example.edu/course/MATH-151",
                "https://courses.example.edu/professor/12345",
                "https://courses.example.edu/professor/67890",
            },
        )


class TermCodeParsingTests(unittest.TestCase):
    def test_uses_the_semester_digit_in_six_digit_tamu_codes(self) -> None:
        self.assertEqual(_term_parts("202611"), (2026, "spring"))
        self.assertEqual(_term_parts("202621"), (2026, "summer"))
        self.assertEqual(_term_parts("202631"), (2026, "fall"))


class InstructorIdentityNormalizationTests(unittest.TestCase):
    def test_hyphenated_surname_matches_grade_report_and_section_formats(self) -> None:
        grade_report = main.normalized_instructors("GUTIERREZ-OSUNA R")
        section = main.normalized_instructors(
            "Ricardo Gutierrez-Osuna (P)",
            '[{"name":"Ricardo Gutierrez-Osuna (P)","more":672598}]',
        )

        self.assertEqual(grade_report[0]["legacy_key"], "gutierrez-osuna|r")
        self.assertEqual(section[0]["legacy_key"], "gutierrez-osuna|r")
        self.assertEqual(section[0]["instructor_id"], "672598")

    def test_persisted_mapping_normalizer_preserves_hyphenated_surname(self) -> None:
        self.assertEqual(
            _legacy_instructor_key("GUTIERREZ-OSUNA R"),
            _legacy_instructor_key("Ricardo Gutierrez-Osuna"),
        )


class SectionLocationNormalizationTests(unittest.TestCase):
    def test_blank_site_keeps_blank_display_but_filters_as_college_station(self) -> None:
        section = main.compact_section({"site": "", "attributes": ""})

        self.assertEqual(section["site"], "")
        self.assertEqual(section["filter_locations"], ["College Station"])

    def test_distance_education_is_also_available_under_college_station(self) -> None:
        section = main.compact_section({"site": "Distance Education", "attributes": "Distance Education"})

        self.assertEqual(section["site"], "Distance Education")
        self.assertEqual(section["filter_locations"], ["College Station", "Distance Education"])

    def test_unrecognized_site_is_preserved_without_college_station_fallback(self) -> None:
        section = main.compact_section({"site": "Fort Worth", "attributes": "College Station| Fort Worth"})

        self.assertEqual(section["site"], "Fort Worth")
        self.assertEqual(section["filter_locations"], [])


class RestrictionValueParsingTests(unittest.TestCase):
    def test_drops_leading_conjunction_from_final_list_item(self) -> None:
        self.assertEqual(
            main.split_restriction_values("Philosophy, Society, Ethics, & Law"),
            ["Philosophy", "Society", "Ethics", "Law"],
        )

    def test_control_indicator_wins_over_misleading_value_row_label(self) -> None:
        college_endpoint = next(
            endpoint
            for endpoint in RESTRICTION_ENDPOINTS
            if endpoint.route == "section-college-restrictions"
        )
        response = [
            {
                "INDICATOR1": "Must be enrolled in one of the following Colleges:",
                "SSRRCOL_COLL_CODE": None,
                "SSRRCOL_COLL_IND": "I",
                "STVCOLL_DESC": None,
            },
            {
                "INDICATOR1": "Cannot be enrolled in one of the following Colleges:",
                "SSRRCOL_COLL_CODE": "NU",
                "SSRRCOL_COLL_IND": None,
                "STVCOLL_DESC": "Nursing",
            },
        ]

        self.assertEqual(
            parse_restriction_response(college_endpoint, response),
            "Must be enrolled in one of the following Colleges: Nursing",
        )


class OfferingTermReconciliationTests(unittest.TestCase):
    def test_reimports_snapshots_when_active_terms_are_missing(self) -> None:
        snapshots = [Path("spring.csv"), Path("summer.csv"), Path("fall.csv")]
        term_codes = {"spring.csv": "202611", "summer.csv": "202621", "fall.csv": "202631"}

        with (
            patch.object(main, "active_current_sections_files", return_value=snapshots),
            patch.object(main, "current_sections_files", return_value=snapshots),
            patch.object(main, "term_code_for_sections_file", side_effect=lambda path: term_codes[path.name]),
            patch.object(main.postgres, "active_term_codes", return_value=["202631"]),
            patch.object(main.postgres, "import_terms_and_sections") as import_snapshots,
        ):
            self.assertTrue(main.synchronize_section_term_snapshots())

        import_snapshots.assert_called_once_with(snapshots)


class SectionRefreshMetricsTests(unittest.TestCase):
    @staticmethod
    def stored_section(crn: str, *, status: str = "N") -> dict[str, object]:
        return {
            "term_code": "202631",
            "crn": crn,
            "course_id": "CSCE-120",
            "section_number": "500",
            "title": "Program Design",
            "attributes": [],
            "seat_status_open": status,
            "instructor_details": [],
            "is_present": True,
        }

    @staticmethod
    def incoming_section(crn: str, *, status: str = "N") -> dict[str, object]:
        return _canonical_section_row({
            "term_code": "202631",
            "crn": crn,
            "subject": "CSCE",
            "course_number": "120",
            "section": "500",
            "title": "Program Design",
            "seat_status_open": status,
            "instructor_details": "[]",
        })

    def test_counts_added_removed_updated_and_availability_changes(self) -> None:
        report = _section_change_report(
            "202631",
            [
                self.stored_section("10001"),
                self.stored_section("10002"),
            ],
            [
                self.incoming_section("10001", status="Y"),
                self.incoming_section("10003"),
            ],
            show_changed=True,
        )

        self.assertEqual(report["scanned_sections"], 2)
        self.assertEqual(report["added_sections"], 1)
        self.assertEqual(report["removed_sections"], 1)
        self.assertEqual(report["updated_sections"], 1)
        self.assertEqual(report["changed_sections"], 3)
        self.assertEqual(report["seat_status_changed_sections"], 1)
        self.assertEqual(report["changed_courses"], 1)
        self.assertEqual(len(report["changed_section_details"]), 3)

    def test_identical_snapshots_report_no_changes(self) -> None:
        report = _section_change_report(
            "202631",
            [self.stored_section("10001")],
            [self.incoming_section("10001")],
        )

        self.assertEqual(report["changed_sections"], 0)
        self.assertEqual(report["seat_status_changed_sections"], 0)


class SectionRefreshStatusTests(unittest.TestCase):
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    def test_no_history_is_unknown(self) -> None:
        payload = _refresh_status_payload(None, None, None, now=self.now)

        self.assertEqual(payload["state"], "unknown")
        self.assertEqual(payload["last_hour"]["runs"], 0)

    def test_recent_success_is_healthy_and_preserves_counts(self) -> None:
        success = {
            "started_at": self.now - timedelta(minutes=6),
            "completed_at": self.now - timedelta(minutes=5),
            "status": "succeeded",
            "details": {"scanned_sections": 15527, "changed_sections": 18},
            "error_message": None,
        }
        payload = _refresh_status_payload(
            success,
            success,
            {"runs": 1, "scanned_sections": 15527, "changed_sections": 18},
            now=self.now,
        )

        self.assertEqual(payload["state"], "healthy")
        self.assertEqual(payload["latest"]["changed_sections"], 18)
        self.assertEqual(payload["last_hour"]["scanned_sections"], 15527)

    def test_newer_failure_is_visible(self) -> None:
        success = {
            "started_at": self.now - timedelta(hours=1),
            "completed_at": self.now - timedelta(hours=1),
            "status": "succeeded",
            "details": {},
        }
        failure = {
            "started_at": self.now - timedelta(minutes=5),
            "completed_at": self.now - timedelta(minutes=4),
            "status": "failed",
            "details": {},
            "error_message": "upstream timeout",
        }

        payload = _refresh_status_payload(failure, success, None, now=self.now)

        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["last_error"], "upstream timeout")

    def test_old_success_is_stale(self) -> None:
        success = {
            "started_at": self.now - timedelta(hours=3),
            "completed_at": self.now - timedelta(hours=3),
            "status": "succeeded",
            "details": {},
        }

        payload = _refresh_status_payload(success, success, None, now=self.now)

        self.assertEqual(payload["state"], "stale")


class SectionRefreshFailureTrackingTests(unittest.TestCase):
    def test_upstream_fetch_failure_is_persisted(self) -> None:
        error = OSError("upstream timeout")
        with (
            patch.object(main.postgres, "configured", return_value=True),
            patch.object(main.postgres, "active_term_codes", return_value=["202631"]),
            patch.object(main.postgres, "start_section_refresh", return_value=42),
            patch.object(main.postgres, "fail_section_refresh") as record_failure,
            patch(
                "scripts.scrape_tamu_public_class_search.fetch_sections",
                side_effect=error,
            ),
        ):
            with self.assertRaisesRegex(OSError, "upstream timeout"):
                main.refresh_and_sync_current_sections()

        record_failure.assert_called_once_with(42, error)

    def test_refreshes_restrictions_for_changed_and_unsnapshotted_sections(self) -> None:
        section_rows = [
            {"term_code": "202631", "crn": "10001", "subject": "NURS", "course_number": "464"},
            {"term_code": "202631", "crn": "10002", "subject": "NURS", "course_number": "465"},
            {"term_code": "202631", "crn": "10003", "subject": "NURS", "course_number": "466"},
        ]
        section_report = {
            "changed_crns": ["10001"],
            "changed_course_ids": ["NURS-464"],
        }
        restriction_report = {
            "scanned_restrictions": 2,
            "changed_restrictions": 1,
            "restriction_changed_course_ids": ["NURS-465"],
        }
        with (
            patch.object(main.postgres, "configured", return_value=True),
            patch.object(main.postgres, "active_term_codes", return_value=["202631"]),
            patch.object(main.postgres, "start_section_refresh", return_value=42),
            patch.object(main.postgres, "sync_term_sections", return_value=section_report),
            patch.object(
                main.postgres,
                "restriction_snapshot_crns",
                return_value={"10001", "10003"},
            ),
            patch.object(
                main.postgres,
                "sync_section_restrictions",
                return_value=restriction_report,
            ),
            patch.object(main, "update_indexed_course_sections", return_value={"updated_courses": 2, "missing_courses": 0}),
            patch("scripts.scrape_tamu_public_class_search.fetch_sections", return_value=section_rows),
            patch("scripts.scrape_tamu_public_class_search.normalize_section", side_effect=lambda row: row),
            patch("scripts.scrape_tamu_public_class_search.fetch_restrictions_for_sections", return_value=[]) as fetch_restrictions,
        ):
            report = main.refresh_and_sync_current_sections()

        targets = fetch_restrictions.call_args.args[0]
        self.assertEqual([row["crn"] for row in targets], ["10001", "10002"])
        self.assertEqual(report["changed_restrictions"], 1)


class MetadataRouterTests(unittest.TestCase):
    def test_routes_delegate_to_the_injected_services(self) -> None:
        app = FastAPI()
        app.include_router(
            build_metadata_router(
                health_payload=lambda: {"api": "ok"},
                readiness_payload=lambda: {"api": "ok", "database": "ok"},
                filter_options_payload=lambda: {"subjects": ["CSCE"], "majors": []},
                section_refresh_status_payload=lambda: {
                    "state": "healthy",
                    "last_hour": {"scanned_sections": 15527},
                },
            )
        )
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"api": "ok"})
            self.assertEqual(client.get("/healthz").json(), {"api": "ok"})
            self.assertEqual(client.get("/readyz").json()["database"], "ok")
            self.assertEqual(client.get("/filter-options").json()["subjects"], ["CSCE"])
            self.assertEqual(
                client.get("/section-refresh-status").json()["last_hour"]["scanned_sections"],
                15527,
            )


if __name__ == "__main__":
    unittest.main()
