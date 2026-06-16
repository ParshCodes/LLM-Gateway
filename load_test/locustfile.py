"""
Locust load test for the LLM Inference Gateway.

Run:
    locust -f locustfile.py --host http://localhost:8000

Or headless:
    locust -f locustfile.py --host http://localhost:8000 \
           --headless -u 20 -r 2 --run-time 2m \
           --html report.html --csv results
"""

import random

from locust import HttpUser, between, task


PROMPTS = [
    "Explain the difference between TCP and UDP in two sentences.",
    "What is a transformer neural network?",
    "Write a Python function that reverses a string.",
    "What causes the northern lights?",
    "Summarize the French Revolution in three bullet points.",
    "Describe the CAP theorem.",
    "What is the halting problem?",
    "How does gradient descent work?",
    "Explain Docker containers vs virtual machines.",
    "What is REST?",
    "What is the difference between a process and a thread?",
    "Explain how HTTPS works.",
    "What is a B-tree index?",
    "What is backpropagation?",
    "Explain map-reduce in simple terms.",
]

SHORT_PROMPTS = [
    "Say 'OK' and nothing else.",
    "Reply with a single word.",
    "Echo: hello",
]


class GatewayUser(HttpUser):
    """Simulates a realistic mix of short and medium-length requests."""

    wait_time = between(0.5, 2.0)  # think time between requests

    @task(8)
    def chat_medium(self):
        payload = {
            "model": "llama3.2:1b",
            "messages": [{"role": "user", "content": random.choice(PROMPTS)}],
            "max_tokens": 150,
            "temperature": 0.7,
        }
        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            catch_response=True,
            name="/v1/chat/completions [medium]",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.failure(f"Rate limited: {resp.text[:100]}")
            elif resp.status_code == 503:
                resp.failure(f"Queue full: {resp.text[:100]}")
            else:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:100]}")

    @task(2)
    def chat_short(self):
        """Short requests to stress the queue without burning much model time."""
        payload = {
            "model": "llama3.2:1b",
            "messages": [{"role": "user", "content": random.choice(SHORT_PROMPTS)}],
            "max_tokens": 10,
            "temperature": 0.0,
        }
        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            catch_response=True,
            name="/v1/chat/completions [short]",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (429, 503, 504):
                # Expected under load — mark as success for the purposes of
                # measuring gateway behaviour (not a test failure)
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:100]}")

    @task(1)
    def health_check(self):
        self.client.get("/health", name="/health")
