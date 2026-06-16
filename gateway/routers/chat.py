"""
/v1/chat/completions — OpenAI-compatible chat endpoint.

Flow:
  1. Extract client ID from X-Forwarded-For or peer IP.
  2. Check per-client rate limit (token bucket).
  3. Acquire a queue slot (semaphore, bounded wait).
  4. Proxy the request to the upstream model server.
  5. Record structured log + Prometheus metrics.
"""

import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from gateway.config import get_settings
from gateway.metrics import (
    INFERENCE_LATENCY,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
    TOKENS_TOTAL,
)
from gateway.queue_manager import QueueFull, QueueTimeout

log = structlog.get_logger(__name__)
router = APIRouter()


def _client_id(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prompt_chars(body: dict[str, Any]) -> int:
    total = 0
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += len(part.get("text", ""))
    return total


@router.post("/chat/completions")
async def chat_completions(request: Request):
    settings = get_settings()
    client_id = _client_id(request)
    wall_start = time.perf_counter()

    # ── rate limit ─────────────────────────────────────────────────────────
    rate_limiter = request.app.state.rate_limiter
    if not await rate_limiter.is_allowed(client_id):
        REQUESTS_TOTAL.labels(model=settings.model_name, status="rate_limited").inc()
        log.warning("rate_limited", client=client_id)
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # ── parse body ─────────────────────────────────────────────────────────
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = body.get("model", settings.model_name)
    stream = body.get("stream", False)
    prompt_chars = _prompt_chars(body)

    # Force the model name to match our deployment (can be overridden via env)
    body["model"] = settings.model_name

    upstream_url = f"{settings.model_server_url}/v1/chat/completions"

    # ── queue / semaphore ──────────────────────────────────────────────────
    queue: "RequestQueue" = request.app.state.queue  # noqa: F821
    try:
        async with queue.acquire(timeout=settings.queue_timeout):
            inference_start = time.perf_counter()
            status = "success"
            completion_tokens = 0

            try:
                async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
                    if stream:
                        return await _handle_streaming(
                            client, upstream_url, body, model,
                            client_id, prompt_chars, inference_start, wall_start,
                        )
                    else:
                        resp = await client.post(upstream_url, json=body)
                        resp.raise_for_status()
                        data = resp.json()

            except httpx.HTTPStatusError as exc:
                status = "error"
                log.error(
                    "upstream_error",
                    client=client_id,
                    status_code=exc.response.status_code,
                    body=exc.response.text[:200],
                )
                raise HTTPException(
                    status_code=exc.response.status_code,
                    detail=f"Upstream error: {exc.response.text[:200]}",
                )
            except httpx.RequestError as exc:
                status = "error"
                log.error("upstream_unreachable", client=client_id, error=str(exc))
                raise HTTPException(status_code=502, detail="Model server unreachable")
            finally:
                inference_elapsed = time.perf_counter() - inference_start
                INFERENCE_LATENCY.labels(model=model, status=status).observe(inference_elapsed)

            # ── token accounting (non-streaming) ──────────────────────────
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if prompt_tokens:
                TOKENS_TOTAL.labels(model=model, direction="prompt").inc(prompt_tokens)
            if completion_tokens:
                TOKENS_TOTAL.labels(model=model, direction="completion").inc(completion_tokens)

            wall_elapsed = time.perf_counter() - wall_start
            REQUEST_LATENCY.labels(model=model, status=status).observe(wall_elapsed)
            REQUESTS_TOTAL.labels(model=model, status=status).inc()

            log.info(
                "request_complete",
                client=client_id,
                model=model,
                prompt_chars=prompt_chars,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                inference_latency_s=round(inference_elapsed, 3),
                total_latency_s=round(wall_elapsed, 3),
                status=status,
            )

            return data

    except QueueFull:
        REQUESTS_TOTAL.labels(model=model, status="queue_full").inc()
        log.warning("queue_full", client=client_id)
        raise HTTPException(status_code=503, detail="Server busy — request queue full")
    except QueueTimeout:
        wall_elapsed = time.perf_counter() - wall_start
        REQUEST_LATENCY.labels(model=model, status="timeout").observe(wall_elapsed)
        REQUESTS_TOTAL.labels(model=model, status="timeout").inc()
        log.warning("queue_timeout", client=client_id, waited_s=round(wall_elapsed, 3))
        raise HTTPException(status_code=504, detail="Timed out waiting for a free model slot")


async def _handle_streaming(
    client: httpx.AsyncClient,
    upstream_url: str,
    body: dict,
    model: str,
    client_id: str,
    prompt_chars: int,
    inference_start: float,
    wall_start: float,
):
    settings = get_settings()
    status = "success"
    completion_chars = 0

    async def event_stream():
        nonlocal status, completion_chars
        try:
            async with client.stream("POST", upstream_url, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n\n"
                        if line.startswith("data:") and line != "data: [DONE]":
                            # rough token estimate: 4 chars ≈ 1 token
                            completion_chars += len(line)
        except Exception as exc:
            status = "error"
            log.error("stream_error", client=client_id, error=str(exc))
            yield f"data: {{'error': '{exc}'}}\n\n"
        finally:
            inference_elapsed = time.perf_counter() - inference_start
            wall_elapsed = time.perf_counter() - wall_start
            INFERENCE_LATENCY.labels(model=model, status=status).observe(inference_elapsed)
            REQUEST_LATENCY.labels(model=model, status=status).observe(wall_elapsed)
            REQUESTS_TOTAL.labels(model=model, status=status).inc()
            TOKENS_TOTAL.labels(model=model, direction="completion").inc(
                max(1, completion_chars // 4)
            )
            log.info(
                "stream_complete",
                client=client_id,
                model=model,
                prompt_chars=prompt_chars,
                inference_latency_s=round(inference_elapsed, 3),
                total_latency_s=round(wall_elapsed, 3),
                status=status,
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
