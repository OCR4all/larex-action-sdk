from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .exceptions import DispatchVerificationError


@dataclass
class NonceStore:
    ttl_seconds: int = 300
    max_size: int = 10_000
    _seen: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if self.max_size <= 0:
            raise ValueError("max_size must be positive")

    def check_and_store(self, nonce: str) -> None:
        if not nonce:
            raise DispatchVerificationError("Missing dispatch nonce")

        now = time.monotonic()
        with self._lock:
            self._evict(now)
            if nonce in self._seen:
                raise DispatchVerificationError("Dispatch nonce was already used")
            if len(self._seen) >= self.max_size:
                raise DispatchVerificationError("Dispatch nonce store is full")
            self._seen[nonce] = now

    def _evict(self, now: float) -> None:
        expired = [
            nonce for nonce, seen_at in self._seen.items() if now - seen_at > self.ttl_seconds
        ]
        for nonce in expired:
            self._seen.pop(nonce, None)
