from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import ValidationError

from .exceptions import DispatchVerificationError
from .models import ActionDispatchPayload
from .nonce import NonceStore

AUTH_HEADER = "x-larex-action-auth"
PROCESSOR_HEADER = "x-larex-action-processor"
RUN_HEADER = "x-larex-action-run-id"
TIMESTAMP_HEADER = "x-larex-action-timestamp"
NONCE_HEADER = "x-larex-action-nonce"
BODY_HASH_HEADER = "x-larex-action-body-sha256"
SIGNATURE_HEADER = "x-larex-action-signature"


class DispatchVerifier:
    def __init__(
        self,
        *,
        processor_id: str,
        dispatch_secret: str,
        nonce_store: NonceStore | None = None,
        max_clock_skew_seconds: int = 300,
    ) -> None:
        if not processor_id:
            raise ValueError("processor_id must not be blank")
        if not dispatch_secret:
            raise ValueError("dispatch_secret must not be blank")
        self.processor_id = processor_id
        self._dispatch_secret = dispatch_secret
        self.nonce_store = nonce_store or NonceStore(ttl_seconds=max_clock_skew_seconds)
        self.max_clock_skew_seconds = max_clock_skew_seconds

    def verify(
        self,
        *,
        method: str,
        path_and_query: str,
        headers: Mapping[str, str],
        body: bytes | str,
    ) -> ActionDispatchPayload:
        raw_body = body.encode("utf-8") if isinstance(body, str) else body
        if not raw_body:
            raise DispatchVerificationError("Dispatch body is empty", status_code=400)

        normalized_headers = {key.lower(): value for key, value in headers.items()}
        auth = _required_header(normalized_headers, AUTH_HEADER)
        header_processor = _required_header(normalized_headers, PROCESSOR_HEADER)
        header_run = _required_header(normalized_headers, RUN_HEADER)
        timestamp = _required_header(normalized_headers, TIMESTAMP_HEADER)
        nonce = _required_header(normalized_headers, NONCE_HEADER)
        body_hash = _required_header(normalized_headers, BODY_HASH_HEADER)
        signature = _required_header(normalized_headers, SIGNATURE_HEADER)

        if auth != "hmac-sha256;v=1":
            raise DispatchVerificationError("Unsupported dispatch auth header")
        if header_processor != self.processor_id:
            raise DispatchVerificationError("Processor id mismatch")

        expected_body_hash = _b64url(hashlib.sha256(raw_body).digest())
        if not hmac.compare_digest(expected_body_hash, body_hash):
            raise DispatchVerificationError("Body hash mismatch")

        canonical = canonical_dispatch_request(
            method=method,
            path_and_query=path_and_query,
            run_id=header_run,
            processor_id=header_processor,
            timestamp=timestamp,
            nonce=nonce,
            body_hash=body_hash,
        )
        expected_signature = "v1=" + _b64url(
            hmac.new(
                self._dispatch_secret.encode("utf-8"),
                canonical.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(expected_signature, signature):
            raise DispatchVerificationError("Dispatch signature mismatch")

        try:
            payload_data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise DispatchVerificationError(
                "Dispatch body is not valid JSON",
                status_code=400,
            ) from exc

        try:
            payload = ActionDispatchPayload.model_validate(payload_data)
        except ValidationError as exc:
            raise DispatchVerificationError(
                "Dispatch payload shape is invalid",
                status_code=400,
            ) from exc

        if header_processor != payload.processor_id or payload.processor_id != self.processor_id:
            raise DispatchVerificationError("Processor id mismatch")
        if header_run != payload.run_id:
            raise DispatchVerificationError("Run id mismatch")

        self._verify_timestamp(timestamp)
        self.nonce_store.check_and_store(nonce)

        return payload

    def _verify_timestamp(self, raw_timestamp: str) -> None:
        try:
            parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DispatchVerificationError("Invalid dispatch timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)

        skew = abs((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
        if skew > self.max_clock_skew_seconds:
            raise DispatchVerificationError("Dispatch timestamp is outside the allowed window")


def canonical_dispatch_request(
    *,
    method: str,
    path_and_query: str,
    run_id: str,
    processor_id: str,
    timestamp: str,
    nonce: str,
    body_hash: str,
) -> str:
    return "\n".join(
        [
            "larex-action-dispatch-v1",
            method.upper(),
            path_and_query or "/",
            run_id,
            processor_id,
            timestamp,
            nonce,
            body_hash,
        ]
    )


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if not value:
        raise DispatchVerificationError(f"Missing dispatch header: {name}")
    return value


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
