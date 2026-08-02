"""Regression tests for the physical and query-shape search contracts."""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from backend.app import main, paradedb, postgres


def section_filters(**overrides: list[str]) -> postgres.ActiveSectionFilterSpec:
    values: postgres.ActiveSectionFilterSpec = {
        "filter_terms": [],
        "locations": [],
        "instruction_types": [],
        "core_attributes": [],
        "graduation_requirements": [],
        "attributes": [],
        "major_aliases": [],
        "department_aliases": [],
        "college_aliases": [],
    }
    values.update(overrides)  # type: ignore[typeddict-item]
    return values


class FakeResult(list):
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        super().__init__(rows or [])

    def fetchone(self) -> dict[str, object] | None:
        return self[0] if self else None


class FakeCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def executemany(self, query: str, values: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, values))


class FakeConnection:
    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.results = list(results or [])
        self.execute_calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.cursor_instance = FakeCursor()

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> FakeResult:
        self.execute_calls.append((query, params))
        return self.results.pop(0) if self.results else FakeResult()

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


class ParadeDBSchemaContractTests(unittest.TestCase):
    def test_schema_enforces_plain_embedding_storage_for_new_and_existing_tables(self) -> None:
        connection = FakeConnection()

        with patch.object(paradedb.postgres, "connection", return_value=connection):
            paradedb.ensure_schema()

        sql = "\n".join(query for query, _ in connection.execute_calls)
        self.assertIn("embedding vector(384) STORAGE PLAIN", sql)
        self.assertIn(
            "ALTER TABLE course_search_documents ALTER COLUMN embedding SET STORAGE PLAIN",
            sql,
        )

    def test_section_filter_schema_covers_common_course_term_location_lookup(self) -> None:
        self.assertIn(
            "ON section_filter_facts(course_id, term_code) INCLUDE (filter_locations)",
            " ".join(postgres.SCHEMA_SQL.split()),
        )

    def test_projection_version_must_match_active_contract(self) -> None:
        current = FakeConnection([FakeResult([{"value": paradedb.PROJECTION_VERSION}])])
        stale = FakeConnection([FakeResult([{"value": "1"}])])

        with patch.object(paradedb.postgres, "connection", return_value=current):
            self.assertTrue(paradedb.projection_is_current())
        with patch.object(paradedb.postgres, "connection", return_value=stale):
            self.assertFalse(paradedb.projection_is_current())

    def test_rebuild_records_projection_version_in_replacement_transaction(self) -> None:
        connection = FakeConnection()
        course = {
            "course_id": "CSCE-121",
            "course_code": "CSCE 121",
            "course_prefix": "CSCE",
            "number": "121",
            "degree_level": "100",
            "title": "Introduction to Program Design and Concepts",
        }

        with patch.object(paradedb.postgres, "connection", return_value=connection):
            count = paradedb.rebuild_documents([course], [[0.0] * 384])

        self.assertEqual(count, 1)
        self.assertEqual(len(connection.cursor_instance.executemany_calls), 1)
        metadata_calls = [
            params
            for query, params in connection.execute_calls
            if "INSERT INTO app_metadata" in query
        ]
        self.assertEqual(
            metadata_calls,
            [(paradedb.PROJECTION_VERSION_KEY, paradedb.PROJECTION_VERSION)],
        )


class ParadeDBRetrievalContractTests(unittest.TestCase):
    def test_lexical_retrieval_limits_by_score_before_deterministic_tie_break(self) -> None:
        connection = FakeConnection([
            FakeResult([{"course_id": "CSCE-121", "document": {}, "score": 1.0}])
        ])

        with patch.object(paradedb.postgres, "connection", return_value=connection):
            hits, _ = paradedb.lexical_hits("computer science", 250, lambda _: True)

        self.assertEqual([hit["_id"] for hit in hits], ["CSCE-121"])
        query, params = connection.execute_calls[0]
        normalized = " ".join(query.split())
        self.assertIn(
            "ORDER BY score DESC LIMIT %s ) AS candidates "
            "ORDER BY score DESC, course_id",
            normalized,
        )
        self.assertEqual(params, ("computer science", 250))

    def test_term_filter_stays_inside_the_retrieval_query(self) -> None:
        connection = FakeConnection([
            FakeResult([{"course_id": "CSCE-121", "document": {}, "score": 1.0}])
        ])

        with patch.object(paradedb.postgres, "connection", return_value=connection):
            paradedb.lexical_hits(
                "computer science",
                50,
                lambda _: True,
                section_filters=section_filters(filter_terms=["fall"]),
            )

        query, params = connection.execute_calls[0]
        normalized = " ".join(query.split())
        self.assertIn("EXISTS ( SELECT 1 FROM section_filter_facts candidate_section", normalized)
        self.assertIn("candidate_term.season = ANY(%s)", normalized)
        self.assertNotIn("course_id = ANY(%s)", normalized)
        self.assertEqual(params, ("computer science", ["fall"], 50))

    def test_location_filter_stays_in_the_same_section_predicate(self) -> None:
        connection = FakeConnection()

        with patch.object(paradedb.postgres, "connection", return_value=connection):
            paradedb.lexical_hits(
                "computer science",
                50,
                lambda _: True,
                section_filters=section_filters(
                    filter_terms=["fall"],
                    locations=["College Station"],
                ),
            )

        query, params = connection.execute_calls[0]
        normalized = " ".join(query.split())
        self.assertIn("candidate_section.filter_locations && %s::text[]", normalized)
        self.assertEqual(params, ("computer science", ["fall"], ["college station"], 50))

    def test_every_section_filter_uses_one_correlated_predicate(self) -> None:
        connection = FakeConnection()
        filters = section_filters(
            filter_terms=["fall"],
            locations=["College Station"],
            instruction_types=["Lecture"],
            core_attributes=["Core Mathematics (KMTH)"],
            graduation_requirements=["Univ Req-Writing Intensive"],
            attributes=["Honors"],
            major_aliases=["Computer Science"],
            department_aliases=["Computer Science and Engineering"],
            college_aliases=["Engineering"],
        )

        with patch.object(paradedb.postgres, "connection", return_value=connection):
            paradedb.lexical_hits(
                "computer science",
                50,
                lambda _: True,
                section_filters=filters,
            )

        query, params = connection.execute_calls[0]
        normalized = " ".join(query.split())
        for fragment in (
            "candidate_term.season = ANY(%s)",
            "candidate_section.filter_locations && %s::text[]",
            "ARRAY[candidate_section.instruction_type] && %s::text[]",
            "candidate_section.filter_core_attributes && %s::text[]",
            "candidate_section.filter_graduation_requirements && %s::text[]",
            "candidate_section.attributes @> %s::text[]",
            "candidate_section.restriction_data_available",
            "candidate_section.major_restriction_aliases && %s::text[]",
            "candidate_section.department_restriction_aliases && %s::text[]",
            "candidate_section.college_restriction_aliases && %s::text[]",
            "candidate_section.excluded_major_restriction_aliases && %s::text[]",
            "candidate_section.excluded_department_restriction_aliases && %s::text[]",
            "candidate_section.excluded_college_restriction_aliases && %s::text[]",
        ):
            self.assertIn(fragment, normalized)
        self.assertNotIn("course_id = ANY(%s)", normalized)
        self.assertEqual(params[0], "computer science")
        self.assertEqual(params[-1], 50)

    def test_semantic_and_catalog_retrieval_share_the_section_predicate(self) -> None:
        filters = section_filters(
            filter_terms=["fall"],
            instruction_types=["Lecture"],
        )
        connection = FakeConnection()

        with patch.object(paradedb.postgres, "connection", return_value=connection):
            paradedb.semantic_hits(
                [0.0] * 384,
                50,
                lambda _: True,
                section_filters=filters,
            )
            paradedb.catalog_hits(lambda _: True, section_filters=filters)

        self.assertEqual(len(connection.execute_calls), 2)
        for query, _ in connection.execute_calls:
            normalized = " ".join(query.split())
            self.assertIn("EXISTS ( SELECT 1 FROM section_filter_facts candidate_section", normalized)
            self.assertIn("ARRAY[candidate_section.instruction_type] && %s::text[]", normalized)


class SectionSummaryContractTests(unittest.TestCase):
    def test_matching_section_summaries_avoid_full_section_json(self) -> None:
        connection = FakeConnection([
            FakeResult([
                {"course_id": "ECEN-403", "attributes": ["honors"]},
                {"course_id": "ECEN-403", "attributes": ["writing intensive"]},
                {"course_id": "CSCE-121", "attributes": []},
            ])
        ])
        filters = section_filters(
            filter_terms=["fall"],
            locations=["College Station"],
        )

        with patch.object(postgres, "connection", return_value=connection):
            summaries = postgres.matching_active_section_summaries(
                ["ECEN-403", "CSCE-121"],
                filters,
            )

        self.assertEqual(summaries, {
            "ECEN-403": {
                "current_section_count": 2,
                "course_attributes": ["honors", "writing intensive"],
            },
            "CSCE-121": {
                "current_section_count": 1,
                "course_attributes": [],
            },
        })
        query, params = connection.execute_calls[0]
        normalized = " ".join(query.split())
        self.assertIn("SELECT f.course_id, f.attributes", normalized)
        self.assertNotIn("section_data", normalized)
        self.assertIn("t.season = ANY(%s)", normalized)
        self.assertIn("f.filter_locations && %s::text[]", normalized)
        self.assertEqual(
            params,
            [["ECEN-403", "CSCE-121"], ["fall"], ["college station"]],
        )


class ParadeDBStartupMigrationTests(unittest.TestCase):
    counts = {
        "courses": 1,
        "grade_history": 0,
        "course_historical_location_term_stats": 0,
    }

    def startup_patches(self, *, projection_is_current: bool) -> tuple[object, ...]:
        return (
            patch.object(main.postgres, "configured", return_value=True),
            patch.object(main.postgres, "ensure_schema"),
            patch.object(main.postgres, "database_counts", return_value=self.counts),
            patch.object(main, "synchronize_section_term_snapshots"),
            patch.object(main.postgres, "section_filter_fact_count", return_value=1),
            patch.object(main.postgres, "catalog_active_section_count", return_value=1),
            patch.object(main.postgres, "section_filter_fact_locations_stale", return_value=False),
            patch.object(main.paradedb, "ensure_schema"),
            patch.object(main.paradedb, "document_count", return_value=1),
            patch.object(
                main.paradedb,
                "projection_is_current",
                return_value=projection_is_current,
            ),
            patch.object(main.paradedb, "documents_are_course_only", return_value=True),
            patch.object(main.paradedb, "documents_have_degree_levels", return_value=True),
            patch.object(main.paradedb, "documents_have_embeddings", return_value=True),
        )

    def test_stale_projection_version_triggers_rebuild(self) -> None:
        with ExitStack() as stack:
            for patcher in self.startup_patches(projection_is_current=False):
                stack.enter_context(patcher)
            rebuild = stack.enter_context(
                patch.object(main, "rebuild_paradedb_projection", return_value=1)
            )
            main.ensure_indexed()

        rebuild.assert_called_once_with()

    def test_current_projection_does_not_rebuild_on_normal_startup(self) -> None:
        with ExitStack() as stack:
            for patcher in self.startup_patches(projection_is_current=True):
                stack.enter_context(patcher)
            rebuild = stack.enter_context(
                patch.object(main, "rebuild_paradedb_projection")
            )
            main.ensure_indexed()

        rebuild.assert_not_called()

    def test_search_runtime_warms_models_before_freezing_long_lived_objects(self) -> None:
        with (
            patch.object(main, "get_embedding_model") as embedding_model,
            patch.object(main, "get_ltr_model") as ltr_model,
            patch.object(main, "LTR_MODEL_FILE") as ltr_file,
            patch.object(main, "LTR_MODEL_METADATA_FILE") as ltr_metadata_file,
            patch.object(main.gc, "collect") as collect,
            patch.object(main.gc, "freeze") as freeze,
        ):
            ltr_file.exists.return_value = True
            ltr_metadata_file.exists.return_value = True
            main.warm_search_runtime()

        embedding_model.assert_called_once_with()
        embedding_model.return_value.encode.assert_called_once_with(
            ["course search warmup"],
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        )
        ltr_model.assert_called_once_with()
        collect.assert_called_once_with()
        freeze.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
