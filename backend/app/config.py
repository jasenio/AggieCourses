"""Runtime configuration and filesystem contracts.

This module is intentionally dependency-light so the API, persistence layer,
and offline commands all resolve the same environment and artifact locations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def directory(self) -> Path:
        return self.root / "data"

    @property
    def course_catalog(self) -> Path:
        return self.directory / "course_info_all.csv"

    @property
    def grade_distribution(self) -> Path:
        return self.directory / "tamu_grade_distribution.csv"

    @property
    def restrictions(self) -> Path:
        return self.directory / "tamu_restrictions_full.csv"

    @property
    def major_taxonomy(self) -> Path:
        return self.directory / "tamu_major_taxonomy.csv"

    @property
    def subject_context(self) -> Path:
        return self.directory / "tamu_subject_context.csv"

    @property
    def current_sections_pattern(self) -> str:
        return "tamu_public_class_sections_*_with_attributes.csv"

    @property
    def current_sections_fallback_pattern(self) -> str:
        return "tamu_public_class_sections_*.csv"


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data: DataPaths
    database_url: str
    postgres_pool_min_size: int
    postgres_pool_max_size: int


def load_settings() -> Settings:
    # The runtime must not unexpectedly download models during a request or
    # container startup. An operator can explicitly opt in for a model build.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    postgres_pool_min_size = _positive_int("POSTGRES_POOL_MIN_SIZE", 1)
    return Settings(
        root_dir=ROOT_DIR,
        data=DataPaths(ROOT_DIR),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        postgres_pool_min_size=postgres_pool_min_size,
        postgres_pool_max_size=max(postgres_pool_min_size, _positive_int("POSTGRES_POOL_MAX_SIZE", 10)),
    )


settings = load_settings()
