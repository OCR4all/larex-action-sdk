from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    backoff: float = 0.5
    max_backoff: float = 30.0
    read_attempts: int = 3
    heartbeat_attempts: int = 3
    heartbeat_timeout: float = 15.0
    heartbeat_failure_grace: float = 1800.0
    result_window: float = 900.0
    result_max_attempts: int | None = None

    def __post_init__(self) -> None:
        if self.backoff < 0 or self.max_backoff < 0:
            raise ValueError("backoff values must not be negative")
        if self.read_attempts < 1:
            raise ValueError("read_attempts must be at least 1")
        if self.heartbeat_attempts < 1:
            raise ValueError("heartbeat_attempts must be at least 1")
        if self.heartbeat_timeout <= 0:
            raise ValueError("heartbeat_timeout must be positive")
        if self.heartbeat_failure_grace < 0:
            raise ValueError("heartbeat_failure_grace must not be negative")
        if self.result_window < 0:
            raise ValueError("result_window must not be negative")
        if self.result_max_attempts is not None and self.result_max_attempts < 1:
            raise ValueError("result_max_attempts must be at least 1")
