from prometheus_client import Counter, Gauge, Histogram

# ── latency ──────────────────────────────────────────────────────────────────
REQUEST_LATENCY = Histogram(
    "llm_request_latency_seconds",
    "End-to-end request latency (including queue wait)",
    ["model", "status"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

INFERENCE_LATENCY = Histogram(
    "llm_inference_latency_seconds",
    "Latency of the upstream model call only",
    ["model", "status"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# ── counters ─────────────────────────────────────────────────────────────────
REQUESTS_TOTAL = Counter(
    "llm_requests_total",
    "Total requests received",
    ["model", "status"],  # status: success | error | rate_limited | queue_full | timeout
)

TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Tokens processed",
    ["model", "direction"],  # direction: prompt | completion
)

# ── gauges ────────────────────────────────────────────────────────────────────
QUEUE_WAITING = Gauge("llm_queue_waiting", "Requests currently waiting in queue")
ACTIVE_REQUESTS = Gauge("llm_active_requests", "Requests actively being served by the model")
