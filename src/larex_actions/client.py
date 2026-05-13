from __future__ import annotations

import ipaddress
from collections.abc import AsyncGenerator, Collection, Mapping
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import SplitResult, urlsplit

import httpx
from pydantic import BaseModel

from .exceptions import ActionCancelled, ActionUrlSecurityError
from .models import (
    ActionDispatchPayload,
    ActionFile,
    ActionInput,
    HeartbeatResponse,
    ResultStatus,
    RunStatus,
)
from .results import ResultBuilder

ParameterModelT = TypeVar("ParameterModelT", bound=BaseModel)


class ActionClient:
    def __init__(
        self,
        *,
        pull_url: str,
        heartbeat_url: str,
        result_url: str,
        secret: str,
        client: httpx.AsyncClient | None = None,
        timeout: float | httpx.Timeout = 120.0,
        enforce_url_security: bool = True,
        allowed_callback_origins: Collection[str] | None = None,
        allow_insecure_local_urls: bool = True,
    ) -> None:
        self._trusted_origin = _validate_callback_urls(
            pull_url=pull_url,
            heartbeat_url=heartbeat_url,
            result_url=result_url,
            enforce=enforce_url_security,
            allowed_callback_origins=allowed_callback_origins,
            allow_insecure_local_urls=allow_insecure_local_urls,
        )
        self._enforce_url_security = enforce_url_security
        self._allow_insecure_local_urls = allow_insecure_local_urls
        self.pull_url = pull_url
        self.heartbeat_url = heartbeat_url
        self.result_url = result_url
        self._secret = secret
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @classmethod
    def from_dispatch(
        cls,
        payload: ActionDispatchPayload,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | httpx.Timeout = 120.0,
        enforce_url_security: bool = True,
        allowed_callback_origins: Collection[str] | None = None,
        allow_insecure_local_urls: bool = True,
    ) -> ActionClient:
        return cls(
            pull_url=payload.pull_url,
            heartbeat_url=payload.heartbeat_url,
            result_url=payload.result_url,
            secret=payload.secret.get_secret_value(),
            client=client,
            timeout=timeout,
            enforce_url_security=enforce_url_security,
            allowed_callback_origins=allowed_callback_origins,
            allow_insecure_local_urls=allow_insecure_local_urls,
        )

    async def __aenter__(self) -> ActionClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def pull_input(self) -> ActionInput:
        response = await self._client.get(self.pull_url, headers=self._auth_headers())
        response.raise_for_status()
        return ActionInput.model_validate(response.json())

    async def heartbeat(
        self,
        progress_percent: int | None = None,
        status_message: str | None = None,
        *,
        log: str | None = None,
        status: RunStatus = "running",
        error_message: str | None = None,
        raise_on_cancel: bool = False,
    ) -> HeartbeatResponse:
        payload: dict[str, Any] = {
            "status": status,
            "progressPercent": progress_percent,
            "statusMessage": status_message,
            "log": log,
            "errorMessage": error_message,
        }
        response = await self._client.post(
            self.heartbeat_url,
            headers=self._auth_headers(),
            json={key: value for key, value in payload.items() if value is not None},
        )
        response.raise_for_status()
        heartbeat = HeartbeatResponse.model_validate(response.json())
        if raise_on_cancel and heartbeat.cancel_requested:
            raise ActionCancelled("LAREX requested cancellation")
        return heartbeat

    async def download_bytes(self, file: ActionFile) -> bytes:
        self._validate_download_url(file.download_url)
        response = await self._client.get(file.download_url, headers=self._auth_headers())
        response.raise_for_status()
        return response.content

    async def download_to_path(self, file: ActionFile, path: str | Path) -> Path:
        self._validate_download_url(file.download_url)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        async with self._client.stream(
            "GET",
            file.download_url,
            headers=self._auth_headers(),
        ) as response:
            response.raise_for_status()
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    output.write(chunk)
        return target

    async def complete(
        self,
        results: ResultBuilder,
        message: str | None = None,
    ) -> Mapping[str, Any]:
        return await self._post_results(results, status="completed", message=message)

    async def upload_results(
        self,
        results: ResultBuilder,
        *,
        status: ResultStatus = "completed",
        message: str | None = None,
    ) -> Mapping[str, Any]:
        return await self._post_results(results, status=status, message=message)

    async def fail(
        self,
        message: str,
        *,
        log: str | None = None,
        progress_percent: int | None = None,
    ) -> HeartbeatResponse:
        return await self.heartbeat(
            progress_percent=progress_percent,
            status_message=message,
            log=log,
            status="failed",
            error_message=message,
        )

    async def _post_results(
        self,
        results: ResultBuilder,
        *,
        status: ResultStatus,
        message: str | None,
    ) -> Mapping[str, Any]:
        with ExitStack() as exit_stack:
            response = await self._client.post(
                self.result_url,
                headers=self._auth_headers(),
                files=results.httpx_files(status=status, message=message, exit_stack=exit_stack),
            )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, Mapping) else {"response": data}

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}"}

    def _validate_download_url(self, url: str) -> None:
        if not self._enforce_url_security:
            return
        parsed = _parse_url(url, "downloadUrl")
        _require_https_or_local(parsed, self._allow_insecure_local_urls, "downloadUrl")
        origin = _origin(parsed)
        if origin != self._trusted_origin:
            raise ActionUrlSecurityError(
                f"downloadUrl origin {origin} does not match trusted LAREX origin "
                f"{self._trusted_origin}"
            )


class ActionContext:
    def __init__(self, *, payload: ActionDispatchPayload, client: ActionClient) -> None:
        self.payload = payload
        self.client = client

    @property
    def run_id(self) -> str:
        return self.payload.run_id

    @property
    def processor_id(self) -> str:
        return self.payload.processor_id

    @property
    def parameters(self) -> dict[str, Any]:
        return self.payload.parameters

    def parameters_as(self, model: type[ParameterModelT]) -> ParameterModelT:
        return model.model_validate(self.payload.parameters)

    async def pull_input(self) -> ActionInput:
        return await self.client.pull_input()

    async def heartbeat(
        self,
        progress_percent: int | None = None,
        status_message: str | None = None,
        *,
        log: str | None = None,
        raise_on_cancel: bool = False,
    ) -> HeartbeatResponse:
        return await self.client.heartbeat(
            progress_percent=progress_percent,
            status_message=status_message,
            log=log,
            raise_on_cancel=raise_on_cancel,
        )

    async def log(
        self,
        message: str,
        *,
        progress_percent: int | None = None,
        status_message: str | None = None,
        raise_on_cancel: bool = False,
    ) -> HeartbeatResponse:
        return await self.heartbeat(
            progress_percent=progress_percent,
            status_message=status_message,
            log=message,
            raise_on_cancel=raise_on_cancel,
        )

    @asynccontextmanager
    async def step(
        self,
        name: str,
        *,
        progress_percent: int | None = None,
    ) -> AsyncGenerator[None]:
        await self.heartbeat(
            progress_percent=progress_percent,
            status_message=name,
            log=f"step:start {name}",
            raise_on_cancel=True,
        )
        try:
            yield
        except Exception:
            await self.heartbeat(
                progress_percent=progress_percent,
                status_message=f"{name} failed",
                log=f"step:failed {name}",
            )
            raise
        await self.heartbeat(
            progress_percent=progress_percent,
            status_message=f"{name} complete",
            log=f"step:complete {name}",
            raise_on_cancel=True,
        )

    async def raise_if_cancelled(self) -> None:
        await self.heartbeat(raise_on_cancel=True)

    async def download_bytes(self, file: ActionFile) -> bytes:
        return await self.client.download_bytes(file)

    async def download_to_path(self, file: ActionFile, path: str | Path) -> Path:
        return await self.client.download_to_path(file, path)

    def result_builder(self) -> ResultBuilder:
        return ResultBuilder()

    async def complete(
        self,
        results: ResultBuilder,
        message: str | None = None,
    ) -> Mapping[str, Any]:
        return await self.client.complete(results, message)

    async def upload_results(
        self,
        results: ResultBuilder,
        *,
        status: ResultStatus = "completed",
        message: str | None = None,
    ) -> Mapping[str, Any]:
        return await self.client.upload_results(results, status=status, message=message)

    async def fail(self, message: str, *, log: str | None = None) -> HeartbeatResponse:
        return await self.client.fail(message, log=log)


def _validate_callback_urls(
    *,
    pull_url: str,
    heartbeat_url: str,
    result_url: str,
    enforce: bool,
    allowed_callback_origins: Collection[str] | None,
    allow_insecure_local_urls: bool,
) -> str:
    if not enforce:
        return ""

    parsed_urls = [
        _parse_url(pull_url, "pullUrl"),
        _parse_url(heartbeat_url, "heartbeatUrl"),
        _parse_url(result_url, "resultUrl"),
    ]
    origins = {_origin(parsed) for parsed in parsed_urls}
    if len(origins) != 1:
        raise ActionUrlSecurityError("LAREX callback URLs must all use the same origin")
    for parsed, field_name in zip(
        parsed_urls,
        ("pullUrl", "heartbeatUrl", "resultUrl"),
        strict=True,
    ):
        _require_https_or_local(parsed, allow_insecure_local_urls, field_name)

    trusted_origin = origins.pop()
    if allowed_callback_origins is not None:
        normalized_allowed = {_normalize_origin(origin) for origin in allowed_callback_origins}
        if trusted_origin not in normalized_allowed:
            raise ActionUrlSecurityError(
                f"LAREX callback origin {trusted_origin} is not in allowed_callback_origins"
            )
    return trusted_origin


def _parse_url(url: str, field_name: str) -> SplitResult:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ActionUrlSecurityError(f"{field_name} must use http or https")
    if not parsed.hostname:
        raise ActionUrlSecurityError(f"{field_name} must include a host")
    if parsed.username or parsed.password:
        raise ActionUrlSecurityError(f"{field_name} must not include credentials")
    return parsed


def _require_https_or_local(
    parsed: SplitResult,
    allow_insecure_local_urls: bool,
    field_name: str,
) -> None:
    if parsed.scheme == "https":
        return
    if allow_insecure_local_urls and _is_local_or_private_host(parsed.hostname or ""):
        return
    raise ActionUrlSecurityError(f"{field_name} must use https")


def _normalize_origin(origin: str) -> str:
    parsed = _parse_url(origin, "allowed_callback_origins")
    return _origin(parsed)


def _origin(parsed: SplitResult) -> str:
    return f"{parsed.scheme}://{(parsed.hostname or '').lower()}:{_effective_port(parsed)}"


def _effective_port(parsed: SplitResult) -> int:
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _is_local_or_private_host(host: str) -> bool:
    normalized = host.lower()
    if (
        normalized == "localhost"
        or normalized.endswith(".localhost")
        or normalized.endswith(".local")
        or "." not in normalized
    ):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local
