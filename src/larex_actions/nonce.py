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

    def check_and_store(self, nonce: str) -> None:
        if not nonce:
            raise DispatchVerificationError("Missing dispatch nonce")

        now = time.monotonic()
        with self._lock:
            self._evict(now)
            if nonce in self._seen:
                raise DispatchVerificationError("Dispatch nonce was already used")
            if len(self._seen) >= self.max_size:
                oldest = min(self._seen, key=self._seen.__getitem__)
                self._seen.pop(oldest, None)
            self._seen[nonce] = now

    def _evict(self, now: float) -> None:
        expired = [
            nonce for nonce, seen_at in self._seen.items() if now - seen_at > self.ttl_seconds
        ]
        for nonce in expired:
            self._seen.pop(nonce, None)
