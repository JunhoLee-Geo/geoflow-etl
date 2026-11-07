"""Local read-only monitoring API. Start with uvicorn geoflow.api.app:app."""

import os
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from geoflow import __version__
from geoflow.api.models import (
    DatasetSummary,
    DatasetView,
    Envelope,
    Health,
    IssueView,
    Overview,
    Page,
    Preview,
    QualityView,
    RunView,
)
from geoflow.api.store import DatabaseUnavailable, MissingResource, MonitoringStore
from geoflow.database.repository import normalize_url

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10000)]
RunID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
DatasetID = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,47}$")]


def get_store(request: Request) -> MonitoringStore:
    return request.app.state.store


Store = Annotated[MonitoringStore, Depends(get_store)]


def error(code, message, status):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def configured_origins() -> list[str]:
    origins = []
    for origin in os.environ.get("GEOFLOW_CORS_ORIGINS", "").split(","):
        origin = origin.strip()
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or "*" in origin
        ):
            raise ValueError("CORS origins must be explicit HTTP(S) origins without credentials")
        origins.append(origin)
    return origins


def create_app(store: MonitoringStore | None = None, *, preview_datasets: set[str] | None = None):
    @asynccontextmanager
    async def lifespan(application):
        engine = None
        if store is None:
            url = os.environ.get("GEOFLOW_API_DATABASE_URL")
            if url:
                engine = create_engine(
                    normalize_url(url),
                    pool_size=3,
                    max_overflow=0,
                    pool_timeout=3,
                    pool_pre_ping=True,
                    connect_args={"connect_timeout": 3},
                )
            application.state.store = MonitoringStore(engine)
        else:
            application.state.store = store
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    application = FastAPI(title="GeoFlow Monitoring", version=__version__, lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured_origins(),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept"],
    )
    allowed = (
        preview_datasets
        if preview_datasets is not None
        else {
            value.strip()
            for value in os.environ.get("GEOFLOW_PREVIEW_DATASETS", "").split(",")
            if value.strip()
        }
    )

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Never reflect supplied values or validation contexts into public errors.
        return error("invalid_request", "Request parameters are invalid", 422)

    @application.exception_handler(MissingResource)
    async def missing(request, exc):
        return error("not_found", "Resource was not found", 404)

    @application.exception_handler(DatabaseUnavailable)
    @application.exception_handler(SQLAlchemyError)
    async def unavailable(request, exc):
        return error("database_unavailable", "Monitoring data is temporarily unavailable", 503)

    @application.exception_handler(ResponseValidationError)
    async def invalid_response(request, exc):
        return error("invalid_stored_data", "Stored monitoring data could not be represented", 503)

    @application.exception_handler(Exception)
    async def unexpected(request, exc):
        return error("internal_error", "Monitoring request could not be completed", 500)

    @application.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(
            "not_found" if exc.status_code == 404 else "method_not_allowed",
            "Resource or method is not available",
            exc.status_code,
        )

    @application.get("/health", response_model=Envelope[Health])
    def health(db: Store):
        return {"data": db.health()}

    @application.get("/overview", response_model=Envelope[Overview])
    def overview(db: Store):
        return {"data": db.overview()}

    @application.get("/datasets", response_model=Page[DatasetView])
    def datasets(db: Store, limit: Limit = 50, offset: Offset = 0):
        data, total = db.datasets(limit, offset)
        return {"data": data, "pagination": {"limit": limit, "offset": offset, "total": total}}

    @application.get("/runs", response_model=Page[RunView])
    def runs(
        db: Store,
        limit: Limit = 50,
        offset: Offset = 0,
        dataset_id: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,47}$")] = None,
    ):
        data, total = db.runs(limit, offset, dataset_id)
        return {"data": data, "pagination": {"limit": limit, "offset": offset, "total": total}}

    @application.get("/runs/{run_id}", response_model=Envelope[RunView])
    def run(run_id: RunID, db: Store):
        return {"data": db.run(run_id)}

    @application.get("/runs/{run_id}/quality", response_model=Envelope[QualityView])
    def quality(run_id: RunID, db: Store):
        return {"data": db.quality(run_id)}

    @application.get("/runs/{run_id}/issues", response_model=Page[IssueView])
    def issues(run_id: RunID, db: Store, limit: Limit = 50, offset: Offset = 0):
        data, total = db.issues(run_id, limit, offset)
        return {"data": data, "pagination": {"limit": limit, "offset": offset, "total": total}}

    @application.get("/datasets/{dataset_id}/summary", response_model=Envelope[DatasetSummary])
    def summary(dataset_id: DatasetID, db: Store):
        return {"data": db.summary(dataset_id)}

    @application.get("/datasets/{dataset_id}/preview", response_model=Envelope[Preview])
    def preview(dataset_id: DatasetID, db: Store, limit: Limit = 50):
        if dataset_id not in allowed:
            return error("preview_disabled", "Spatial preview is not enabled for this dataset", 403)
        return {"data": db.preview(dataset_id, limit)}

    return application


app = create_app()
