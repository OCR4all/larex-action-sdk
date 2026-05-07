from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from larex_actions.verifier import canonical_dispatch_request

SECRET = "test-dispatch-secret"
PROCESSOR_ID = "mock-image-copy"


def dispatch_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runId": "run-1",
        "processorId": PROCESSOR_ID,
        "workspaceId": "workspace-1",
        "projectId": "project-1",
        "pageIds": ["page-1"],
        "parameters": {"threshold": 0.5},
        "secret": "run-secret",
        "pullUrl": "https://larex.example/public/actions/runs/run-1/input",
        "heartbeatUrl": "https://larex.example/public/actions/runs/run-1/heartbeat",
        "resultUrl": "https://larex.example/public/actions/runs/run-1/results",
    }
    payload.update(overrides)
    return payload


def signed_dispatch(
    *,
    payload: dict[str, Any] | None = None,
    method: str = "POST",
    path_and_query: str = "/dispatch",
    secret: str = SECRET,
    timestamp: str | None = None,
    nonce: str = "nonce-1",
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload or dispatch_payload(), separators=(",", ":")).encode("utf-8")
    timestamp = timestamp or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    body_hash = _b64url(hashlib.sha256(body).digest())
    run_id = (payload or dispatch_payload())["runId"]
    processor_id = (payload or dispatch_payload())["processorId"]
    canonical = canonical_dispatch_request(
        method=method,
        path_and_query=path_and_query,
        run_id=run_id,
        processor_id=processor_id,
        timestamp=timestamp,
        nonce=nonce,
        body_hash=body_hash,
    )
    signature = "v1=" + _b64url(
        hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).digest()
    )
    return body, {
        "X-LAREX-Action-Auth": "hmac-sha256;v=1",
        "X-LAREX-Action-Processor": processor_id,
        "X-LAREX-Action-Run-Id": run_id,
        "X-LAREX-Action-Timestamp": timestamp,
        "X-LAREX-Action-Nonce": nonce,
        "X-LAREX-Action-Body-SHA256": body_hash,
        "X-LAREX-Action-Signature": signature,
    }


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
