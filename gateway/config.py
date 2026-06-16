from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Upstream model server
    model_server_url: str = "http://ollama:11434"
    model_name: str = "llama3.2:1b"
    request_timeout: float = 120.0

    # Rate limiting (token bucket per client IP)
    rate_limit_requests: int = 60   # tokens per window
    rate_limit_window: int = 60     # seconds

    # Concurrency / queue
    max_concurrent_requests: int = 4
    max_queue_size: int = 50
    queue_timeout: float = 30.0     # seconds a request may wait in queue

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
