# Data

The committed files in this directory are the reproducible bootstrap bundle
for AggieCourses. They contain course catalog records, attributed section
snapshots, registration restrictions, the major taxonomy, subject context,
historical grade distributions, and the production learning-to-rank model.

Docker copies this bundle into the application image. On the first Compose
startup, the `bootstrap` service imports it into PostgreSQL and builds the
ParadeDB search projection. Keeping the bootstrap bundle in Git makes a fresh
clone self-contained; the tracked files total about 82 MB and no individual
file reaches GitHub's 100 MB file limit.

Current section availability is the live portion of the dataset. Refresh it
from Texas A&amp;M's public Howdy class-search API with:

```bash
docker compose exec app python scripts/sync_tamu_public_sections.py
```

The refresh updates PostgreSQL directly and does not rewrite the committed CSV
snapshots. Local dataset experiments or alternate snapshots belong in
`data/local/`, which is ignored by Git.
