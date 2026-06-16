"""
Async load tester for the LLM Inference Gateway.

Usage:
    python load_test.py [--url URL] [--concurrency N] [--total N]
                        [--model MODEL] [--max-tokens N] [--ramp-up N]

Example:
    python load_test.py --concurrency 20 --total 200

Results are written to stdout as structured JSON lines and to
load_test_results.json for post-analysis.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import aiohttp

PROMPTS = [
    "Explain the difference between TCP and UDP in two sentences.",
    "What is a transformer neural network?",
    "Write a Python function that reverses a string.",
    "What causes northern lights?",
    "Summarize the French Revolution in three bullet points.",
    "Describe the CAP theorem.",
    "What is the halting problem?",
    "How does gradient descent work?",
    "Explain Docker containers vs virtual machines.",
    "What is a REST API?",
]


@dataclass
class Result:
    request_id: int
    status: int
    latency_s: float
    error: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_second: float = 0.0


@dataclass
class Summary:
    total_requests: int
    successful: int
    failed: int
    error_rate: float
    duration_s: float
    rps: float
    latencies: dict = field(default_factory=dict)
    token_throughput_per_s: float = 0.0


async def send_request(
    session: aiohttp.ClientSession,
    url: str,
    model: str,
    max_tokens: int,
    request_id: int,
    semaphore: asyncio.Semaphore,
) -> Result:
    prompt = PROMPTS[request_id % len(PROMPTS)]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }

    start = time.perf_counter()
    async with semaphore:
        try:
            async with session.post(url, json=payload) as resp:
                latency = time.perf_counter() - start
                body = await resp.json(content_type=None)

                if resp.status == 200:
                    usage = body.get("usage", {})
                    pt = usage.get("prompt_tokens", 0)
                    ct = usage.get("completion_tokens", 0)
                    tps = ct / latency if latency > 0 else 0
                    return Result(
                        request_id=request_id,
                        status=resp.status,
                        latency_s=round(latency, 4),
                        prompt_tokens=pt,
                        completion_tokens=ct,
                        tokens_per_second=round(tps, 2),
                    )
                else:
                    return Result(
                        request_id=request_id,
                        status=resp.status,
                        latency_s=round(latency, 4),
                        error=body.get("detail", str(resp.status)),
                    )
        except Exception as exc:
            latency = time.perf_counter() - start
            return Result(
                request_id=request_id,
                status=0,
                latency_s=round(latency, 4),
                error=str(exc),
            )


def print_progress(done: int, total: int, errors: int, start: float) -> None:
    elapsed = time.perf_counter() - start
    rps = done / elapsed if elapsed > 0 else 0
    bar_len = 30
    filled = int(bar_len * done / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(
        f"\r[{bar}] {done}/{total}  {rps:.1f} rps  errors={errors}  "
        f"elapsed={elapsed:.1f}s",
        end="",
        flush=True,
    )


async def run_load_test(
    url: str,
    model: str,
    concurrency: int,
    total: int,
    max_tokens: int,
    ramp_up: int,
) -> list[Result]:
    connector = aiohttp.TCPConnector(limit=concurrency * 2)
    timeout = aiohttp.ClientTimeout(total=120)
    semaphore = asyncio.Semaphore(concurrency)

    results: list[Result] = []
    errors = 0
    wall_start = time.perf_counter()

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks: list[asyncio.Task] = []

        for i in range(total):
            # Ramp-up: gradually increase load over the first `ramp_up` seconds
            if ramp_up > 0 and i < concurrency:
                await asyncio.sleep(ramp_up / concurrency)

            task = asyncio.create_task(
                send_request(session, url, model, max_tokens, i, semaphore)
            )
            tasks.append(task)

        for fut in asyncio.as_completed(tasks):
            result = await fut
            results.append(result)
            if result.status != 200:
                errors += 1
            print_progress(len(results), total, errors, wall_start)

    print()  # newline after progress bar
    return results


def compute_summary(results: list[Result], duration_s: float) -> Summary:
    successful = [r for r in results if r.status == 200]
    failed = [r for r in results if r.status != 200]
    latencies = sorted(r.latency_s for r in successful) if successful else [0]

    def pct(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        idx = int(len(data) * p / 100)
        return round(data[min(idx, len(data) - 1)], 3)

    total_completion_tokens = sum(r.completion_tokens for r in successful)

    return Summary(
        total_requests=len(results),
        successful=len(successful),
        failed=len(failed),
        error_rate=round(len(failed) / len(results), 4) if results else 0,
        duration_s=round(duration_s, 2),
        rps=round(len(results) / duration_s, 2) if duration_s > 0 else 0,
        latencies={
            "min": round(min(latencies), 3),
            "p50": pct(latencies, 50),
            "p75": pct(latencies, 75),
            "p90": pct(latencies, 90),
            "p95": pct(latencies, 95),
            "p99": pct(latencies, 99),
            "max": round(max(latencies), 3),
            "mean": round(statistics.mean(latencies), 3),
            "stdev": round(statistics.stdev(latencies), 3) if len(latencies) > 1 else 0,
        },
        token_throughput_per_s=round(total_completion_tokens / duration_s, 1) if duration_s > 0 else 0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Gateway load tester")
    parser.add_argument("--url", default="http://localhost:8000/v1/chat/completions")
    parser.add_argument("--model", default="llama3.2:1b")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--ramp-up", type=int, default=5, help="Ramp-up seconds")
    parser.add_argument("--output", default="load_test_results.json")
    args = parser.parse_args()

    print(f"Target:      {args.url}")
    print(f"Model:       {args.model}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Total:       {args.total} requests")
    print(f"Max tokens:  {args.max_tokens}")
    print(f"Ramp-up:     {args.ramp_up}s")
    print()

    wall_start = time.perf_counter()
    results = asyncio.run(
        run_load_test(
            url=args.url,
            model=args.model,
            concurrency=args.concurrency,
            total=args.total,
            max_tokens=args.max_tokens,
            ramp_up=args.ramp_up,
        )
    )
    duration = time.perf_counter() - wall_start
    summary = compute_summary(results, duration)

    print("\n" + "─" * 55)
    print("RESULTS")
    print("─" * 55)
    print(f"  Duration          : {summary.duration_s}s")
    print(f"  Requests/s        : {summary.rps}")
    print(f"  Total             : {summary.total_requests}")
    print(f"  Successful        : {summary.successful}")
    print(f"  Failed            : {summary.failed}  ({summary.error_rate * 100:.1f}%)")
    print(f"  Token throughput  : {summary.token_throughput_per_s} completion tok/s")
    print()
    print("  Latency (success only):")
    for k, v in summary.latencies.items():
        print(f"    {k:6s}: {v}s")
    print("─" * 55)

    # Dump all results + summary
    output = {
        "summary": asdict(summary),
        "results": [asdict(r) for r in results],
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nFull results saved to {args.output}")


if __name__ == "__main__":
    main()
