from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from gateway.config import get_settings
from gateway.logging_config import configure_logging
from gateway.queue_manager import RequestQueue
from gateway.rate_limiter import RateLimiter
from gateway.routers import chat

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    app.state.rate_limiter = RateLimiter(
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window,
    )
    app.state.queue = RequestQueue(
        max_concurrent=settings.max_concurrent_requests,
        max_queue_size=settings.max_queue_size,
    )

    log.info(
        "gateway_started",
        model_server=settings.model_server_url,
        model=settings.model_name,
        max_concurrent=settings.max_concurrent_requests,
        rate_limit=f"{settings.rate_limit_requests}/{settings.rate_limit_window}s",
    )
    yield
    log.info("gateway_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="LLM Inference Gateway",
        description="Rate-limited, queued proxy in front of a self-hosted LLM (Ollama / vLLM).",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Prometheus metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # API routers
    app.include_router(chat.router, prefix="/v1", tags=["chat"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log.error("unhandled_exception", path=str(request.url), error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
