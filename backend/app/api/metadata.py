"""Read-only API routes that expose runtime metadata."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter


def build_metadata_router(
    *,
    health_payload: Callable[[], dict[str, Any]],
    readiness_payload: Callable[[], dict[str, Any]],
    filter_options_payload: Callable[[], dict[str, Any]],
    section_refresh_status_payload: Callable[[], dict[str, Any]],
) -> APIRouter:
    """Create routes from application services without importing the app module.

    Dependency injection here prevents a router-to-service import cycle and
    lets the payload functions be moved out of the legacy module incrementally.
    """
    router = APIRouter(tags=["metadata"])

    @router.get("/health")
    def health() -> dict[str, Any]:
        return health_payload()

    @router.get("/healthz")
    def liveness() -> dict[str, str]:
        """Confirm that the API process can answer requests."""
        return {"api": "ok"}

    @router.get("/readyz")
    def readiness() -> dict[str, Any]:
        """Confirm that the API's required search backend is available."""
        return readiness_payload()

    @router.get("/filter-options")
    def filter_options() -> dict[str, Any]:
        return filter_options_payload()

    @router.get("/section-refresh-status")
    def section_refresh_status() -> dict[str, Any]:
        """Expose non-sensitive freshness and change counts for the current sections."""
        return section_refresh_status_payload()

    return router
