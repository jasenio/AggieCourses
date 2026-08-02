# Runtime boundary

The serving path remains limited to:

- FastAPI and the static frontend;
- PostgreSQL/ParadeDB persistence and search projection;
- one database bootstrap command;
- one active-section sync command; and
- health checks plus a lightweight end-to-end search benchmark.

The supported commands are `scripts/bootstrap_postgres.py`,
`scripts/sync_tamu_public_sections.py`,
`scripts/scrape_tamu_public_class_search.py`, and
`scripts/benchmark_paradedb_retrieval.py`.

The repository also contains a self-contained human LTR annotation workspace
at `/annotation` and offline training utilities under `scripts/ltr/`. They use
`data/human_test_queries.csv` and browser-exported human judgments; they do not
read legacy automated course-relevance annotations. NumPy and XGBoost are part
of the main dependency set because the serving path loads the production LTR
model as well as supporting offline training.

AEFIS collection, grade scraping, legacy automated annotation, broad
experiment suites, and legacy search migration tooling remain removed. The
normal search runtime continues to use ParadeDB hybrid relevance search and
the existing catalog/section/grade bootstrap data.
