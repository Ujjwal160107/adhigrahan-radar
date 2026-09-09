import logging
import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from backend import fallback
from backend.auth import current_role, log_request
from backend.db import db_path
from backend.routers import auth as auth_router
from backend.routers import cases, dashboard, models, parcels, projects, watchlist

logger = logging.getLogger("vivaad.startup")


def _check_db() -> None:
    path = db_path()
    logger.info("Vivaad Radar DB path: %s", path)
    if not path.is_file():
        logger.warning(
            "DB file missing at %s; responses will rely on the fallback cache", path
        )
        return
    try:
        conn = sqlite3.connect(path)
        try:
            has_parcel = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Parcel'"
            ).fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("Could not inspect DB at %s", path, exc_info=True)
        return
    if not has_parcel:
        logger.warning("DB at %s has no 'Parcel' table; is it the right database?", path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_db()
    yield


app = FastAPI(title="Adhigrahan Radar API", lifespan=lifespan)


@app.exception_handler(FastAPIHTTPException)
async def structured_http_exception(request: Request, exc: FastAPIHTTPException):
    body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
    return JSONResponse(body, status_code=exc.status_code)


app.include_router(parcels.router)
app.include_router(cases.router)
app.include_router(dashboard.router)
app.include_router(watchlist.router)
app.include_router(projects.router)
app.include_router(models.router)
app.include_router(auth_router.router)

fallback.install(app)
# Added after the fallback middleware so CORS runs outermost and
# fallback-served responses also carry CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """Append-only request log: every request, whatever role claimed it,
    whatever it returned. Added last (outermost) so it always sees the
    final status code, including tier-2 fallback substitutions."""
    try:
        role = current_role(request.headers.get("x-role"))
    except Exception:
        role = "unknown"
    response = await call_next(request)
    log_request(role, request.method, request.url.path, response.status_code)
    return response
