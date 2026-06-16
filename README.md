# LLM Inference Gateway

A production-grade FastAPI proxy that sits in front of a self-hosted LLM
(Ollama on CPU, or vLLM on GPU). Spin up the entire observability stack — 
gateway, model server, Prometheus, Grafana — with a single command.

---

## Architecture

```
Client
  │
  ▼  HTTP /v1/chat/completions
┌────────────────────────────────────────────────┐
│              FastAPI Gateway :8000             │
│                                                │
│  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Rate Limiter │  │   Request Queue       │   │
│  │ (token bucket│  │ (asyncio Semaphore    │   │
│  │  per client) │  │  + bounded wait)      │   │
│  └──────────────┘  └──────────────────────┘   │
│                                                │
│  ┌──────────────────────────────────────────┐  │
│  │  Prometheus metrics  /metrics            │  │
│  │  Structured JSON logs → stdout           │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
  │  HTTP /v1/chat/completions (forwarded)
  ▼
┌──────────────────────────┐
│   Ollama / vLLM :11434   │   (OpenAI-compatible API)
└──────────────────────────┘
         ▲
         │  scrape /metrics every 15s
┌────────────────────┐
│  Prometheus :9090  │
└────────────────────┘
         ▲
         │  query
┌────────────────────┐
│  Grafana :3000     │  pre-loaded dashboard + alert rules
└────────────────────┘
```

### Request lifecycle

1. **Rate limit check** — token bucket per client IP (60 req/min default).
   Returns `429` immediately if exhausted.
2. **Queue admission** — if all model slots are busy the request waits up to
   `QUEUE_TIMEOUT` seconds. Returns `503` if the queue is full, `504` if it
   times out.
3. **Upstream proxy** — forwarded to Ollama/vLLM's `/v1/chat/completions`.
   Supports both streaming (`stream: true`) and non-streaming responses.
4. **Metrics & logging** — every request records latency, token counts, and
   status to Prometheus histograms/counters and a JSON log line.

---

## Quick start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Compose v2)
- 4 GB RAM free (for the 1 B-parameter default model)
- No GPU required — Ollama runs on CPU out of the box

### 1 — Clone and configure

```bash
git clone <repo-url> llm-gateway
cd llm-gateway
cp .env.example .env   # edit if needed
```

### 2 — Start the stack

```bash
docker compose up --build
```

On first run Ollama will automatically pull `llama3.2:1b` (~750 MB).
Subsequent starts are instant.

**Service URLs once healthy:**

| Service    | URL                                    |
|------------|----------------------------------------|
| Gateway    | http://localhost:8000                  |
| Prometheus | http://localhost:9090                  |
| Grafana    | http://localhost:3000 (admin / admin)  |
| Ollama API | http://localhost:11434                 |

### 3 — Send a test request

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:1b",
    "messages": [{"role": "user", "content": "Hello! What is 2+2?"}],
    "max_tokens": 64
  }' | jq .
```

### 4 — Open the dashboard

Navigate to **http://localhost:3000**, log in with `admin / admin`, and open
**Dashboards → LLM Gateway → LLM Inference Gateway**.

---

## Configuration

All settings are read from environment variables (or `.env`):

| Variable | Default | Description |
|---|---|---|
| `MODEL_NAME` | `llama3.2:1b` | Ollama model to pull and serve |
| `RATE_LIMIT_REQUESTS` | `60` | Max requests per client per window |
| `RATE_LIMIT_WINDOW` | `60` | Window length in seconds |
| `MAX_CONCURRENT_REQUESTS` | `4` | Simultaneous upstream calls |
| `MAX_QUEUE_SIZE` | `50` | Requests allowed to queue before 503 |
| `QUEUE_TIMEOUT` | `30` | Seconds a queued request will wait |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `GRAFANA_PASSWORD` | `admin` | Change before exposing publicly |

### Swapping to a larger model

```bash
MODEL_NAME=llama3.2:3b docker compose up
```

### Swapping to vLLM (GPU)

Update `docker-compose.yml` to replace the `ollama` service with vLLM:

```yaml
vllm:
  image: vllm/vllm-openai:latest
  command: ["--model", "meta-llama/Llama-3.2-1B-Instruct", "--port", "11434"]
  deploy:
    resources:
      reservations:
        devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
  ports: ["11434:11434"]
```

No gateway code changes needed — both expose the same OpenAI-compatible API.

---

## Prometheus metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `llm_request_latency_seconds` | Histogram | `model`, `status` | End-to-end latency (incl. queue wait) |
| `llm_inference_latency_seconds` | Histogram | `model`, `status` | Upstream model call only |
| `llm_requests_total` | Counter | `model`, `status` | Total requests by outcome |
| `llm_tokens_total` | Counter | `model`, `direction` | Tokens processed (prompt/completion) |
| `llm_queue_waiting` | Gauge | — | Requests currently waiting in queue |
| `llm_active_requests` | Gauge | — | Requests being served by the model |

Status label values: `success`, `error`, `rate_limited`, `queue_full`, `timeout`.

---

## Alert rules

Defined in [prometheus/alert_rules.yml](prometheus/alert_rules.yml):

| Alert | Condition | For | Severity |
|---|---|---|---|
| `HighP95Latency` | P95 latency > 2 s | 5 min | warning |
| `HighErrorRate` | Error rate > 5 % | 2 min | critical |
| `QueueNearlyFull` | Queue depth > 40 | 1 min | warning |

View active alerts at **http://localhost:9090/alerts**.

---

## Load testing

### asyncio script (recommended for quick numbers)

```bash
cd load_test
pip install -r requirements.txt

# 20 concurrent users, 200 total requests
python load_test.py --concurrency 20 --total 200 --ramp-up 5
```

Output:

```
───────────────────────────────────────────────────────
RESULTS
───────────────────────────────────────────────────────
  Duration          : 62.4s
  Requests/s        : 3.21
  Total             : 200
  Successful        : 197
  Failed            : 3  (1.5%)
  Token throughput  : 84.7 completion tok/s

  Latency (success only):
    min   : 1.832s
    p50   : 4.201s
    p75   : 5.803s
    p90   : 7.912s
    p95   : 9.441s
    p99   : 14.23s
    max   : 18.91s
    mean  : 4.618s
    stdev : 2.391s
───────────────────────────────────────────────────────
Full results saved to load_test_results.json
```

### Locust (web UI)

```bash
cd load_test
locust -f locustfile.py --host http://localhost:8000
# Open http://localhost:8089 → set users=20, ramp=2 → Start
```

Headless run with HTML report:

```bash
locust -f locustfile.py --host http://localhost:8000 \
       --headless -u 20 -r 2 --run-time 2m \
       --html report.html --csv results
```

---

## Repo structure

```
llm-gateway/
├── gateway/                   # FastAPI application
│   ├── main.py                # App factory, lifespan, health endpoint
│   ├── config.py              # Pydantic settings (env-driven)
│   ├── rate_limiter.py        # Token-bucket rate limiter
│   ├── queue_manager.py       # Semaphore queue with bounded wait
│   ├── metrics.py             # Prometheus metric definitions
│   ├── logging_config.py      # structlog JSON configuration
│   ├── routers/
│   │   └── chat.py            # /v1/chat/completions — streaming + non-streaming
│   ├── requirements.txt
│   └── Dockerfile
├── prometheus/
│   ├── prometheus.yml         # Scrape config
│   └── alert_rules.yml        # HighP95Latency, HighErrorRate, QueueNearlyFull
├── grafana/
│   ├── provisioning/          # Auto-wired datasource + dashboard loader
│   └── dashboards/
│       └── llm-gateway.json   # 11-panel dashboard
├── load_test/
│   ├── load_test.py           # asyncio + aiohttp load tester
│   ├── locustfile.py          # Locust task set
│   └── requirements.txt
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Stopping the stack

```bash
docker compose down          # stop containers, keep volumes
docker compose down -v       # stop containers AND delete model/data volumes
```
