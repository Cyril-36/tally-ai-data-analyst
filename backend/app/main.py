import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.admin.router import read_system_settings
from app.admin.router import router as admin_router
from app.agent.provider import fake_provider_enabled
from app.agent.router import router as agent_router
from app.auth import passwords
from app.auth.router import router as auth_router
from app.config import get_settings
from app.contracts.outcomes import HTTP_STATUS, AppError, ErrorBody, Outcome
from app.datasets.router import router as datasets_router
from app.db import connect, db_path, init_db
from app.logging import configure_logging
from app.workspace import store
from app.workspace.router import router as workspace_router

# Routers. Other tracks append theirs here at merge (e.g. app.datasets.router).
ROUTERS = [auth_router, admin_router, datasets_router, workspace_router, agent_router]

log = logging.getLogger("app.request")
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _error(outcome: Outcome, message: str, status: int) -> JSONResponse:
    body = ErrorBody(outcome=outcome, message=message)
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(db_path())
    settings = get_settings()
    if fake_provider_enabled():
        log.warning("LLM_PROVIDER=fake: answers come from a scripted demo provider, "
                    "not a real model. Never use this in production.")
    with connect(db_path()) as conn:
        passwords.ensure_admin(conn, settings.admin_email, settings.admin_password)
        try:
            purged = store.purge_old_conversations(conn, read_system_settings(conn).retention_days)
            if purged:
                log.info("retention purge", extra={"purged_conversations": purged})
        except Exception:
            log.exception("retention purge failed")  # never block startup on housekeeping
    yield


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="AI Data Analyst", lifespan=lifespan)

    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        request.state.user_id = None
        start = time.perf_counter()
        path = request.url.path
        try:
            if (path.startswith("/api") and request.method in STATE_CHANGING
                    and "x-requested-with" not in request.headers):
                response = _error(Outcome.permission_denied,
                                  "Missing X-Requested-With header.", 403)
            else:
                response = await call_next(request)
        except Exception:
            # Traceback goes to the log only; the client gets a request id to quote.
            log.exception("unhandled error", extra={"request_id": request_id})
            response = _error(Outcome.execution_failed,
                              f"Something went wrong (request {request_id}).", 500)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        log.info("request", extra={
            "request_id": request_id, "method": request.method, "path": path,
            "status": response.status_code, "user_id": request.state.user_id,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        })
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        # StatusError (401/429) carries its own status; everything else uses the table.
        status = getattr(exc, "status", None) or HTTP_STATUS.get(exc.outcome, 400)
        return _error(exc.outcome, exc.message, status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        first = exc.errors()[0]
        message = first["msg"].removeprefix("Value error, ")
        return _error(Outcome.invalid_input, f"{first['loc'][-1]}: {message}", 422)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/ready")
    def ready():
        try:
            with connect(db_path()) as conn:
                conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        except Exception:
            return _error(Outcome.execution_failed, "Metadata store is unavailable.", 503)
        ai = bool(get_settings().llm_api_key) or fake_provider_enabled()
        return {"status": "ready", "ai_configured": ai}

    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
