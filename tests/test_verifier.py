from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from larex_actions import DispatchVerificationError, DispatchVerifier, NonceStore

from .conftest import PROCESSOR_ID, SECRET, dispatch_payload, signed_dispatch


def test_verifies_valid_dispatch() -> None:
    body, headers = signed_dispatch()

    payload = DispatchVerifier(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
    ).verify(method="POST", path_and_query="/dispatch", headers=headers, body=body)

    assert payload.run_id == "run-1"
    assert payload.secret.get_secret_value() == "run-secret"


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-LAREX-Action-Signature", "v1=bad"),
        ("X-LAREX-Action-Body-SHA256", "bad"),
        ("X-LAREX-Action-Processor", "other"),
        ("X-LAREX-Action-Run-Id", "other"),
    ],
)
def test_rejects_bad_headers(header: str, value: str) -> None:
    body, headers = signed_dispatch()
    headers[header] = value

    with pytest.raises(DispatchVerificationError):
        DispatchVerifier(processor_id=PROCESSOR_ID, dispatch_secret=SECRET).verify(
            method="POST",
            path_and_query="/dispatch",
            headers=headers,
            body=body,
        )


def test_rejects_wrong_payload_processor() -> None:
    body, headers = signed_dispatch(payload=dispatch_payload(processorId="other"))

    with pytest.raises(DispatchVerificationError):
        DispatchVerifier(processor_id=PROCESSOR_ID, dispatch_secret=SECRET).verify(
            method="POST",
            path_and_query="/dispatch",
            headers=headers,
            body=body,
        )


def test_rejects_unsupported_protocol_version() -> None:
    body, headers = signed_dispatch(payload=dispatch_payload(protocolVersion=2))

    with pytest.raises(DispatchVerificationError):
        DispatchVerifier(processor_id=PROCESSOR_ID, dispatch_secret=SECRET).verify(
            method="POST",
            path_and_query="/dispatch",
            headers=headers,
            body=body,
        )


def test_rejects_stale_timestamp() -> None:
    timestamp = (datetime.now(UTC) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    body, headers = signed_dispatch(timestamp=timestamp)

    with pytest.raises(DispatchVerificationError):
        DispatchVerifier(processor_id=PROCESSOR_ID, dispatch_secret=SECRET).verify(
            method="POST",
            path_and_query="/dispatch",
            headers=headers,
            body=body,
        )


def test_rejects_replayed_nonce() -> None:
    nonce_store = NonceStore()
    verifier = DispatchVerifier(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        nonce_store=nonce_store,
    )
    body, headers = signed_dispatch(nonce="same-nonce")
    verifier.verify(method="POST", path_and_query="/dispatch", headers=headers, body=body)

    body, headers = signed_dispatch(nonce="same-nonce")
    with pytest.raises(DispatchVerificationError):
        verifier.verify(method="POST", path_and_query="/dispatch", headers=headers, body=body)


def test_rejected_dispatch_does_not_burn_nonce() -> None:
    nonce_store = NonceStore()
    verifier = DispatchVerifier(
        processor_id=PROCESSOR_ID,
        dispatch_secret=SECRET,
        nonce_store=nonce_store,
    )
    body, headers = signed_dispatch(nonce="reusable-after-rejection")
    headers["X-LAREX-Action-Signature"] = "v1=bad"

    with pytest.raises(DispatchVerificationError):
        verifier.verify(method="POST", path_and_query="/dispatch", headers=headers, body=body)

    body, headers = signed_dispatch(nonce="reusable-after-rejection")
    payload = verifier.verify(method="POST", path_and_query="/dispatch", headers=headers, body=body)

    assert payload.run_id == "run-1"
