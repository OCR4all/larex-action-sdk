from __future__ import annotations

import asyncio
import ipaddress
import os
import random
from collections.abc import AsyncGenerator, Collection, Mapping, Sequence
from contextlib import ExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import SplitResult, urlsplit

import httpx
from pydantic import BaseModel

from .exceptions import (
    ActionCancelled,
    ActionUrlSecurityError,
    CustomFileResultsUnsupported,
    IncrementalResultsUnsupported,
)
from .models import (
    ActionCapabilities,
    ActionDispatchPayload,
    ActionFile,
    ActionInput,
    HeartbeatResponse,
    ResultStatus,
    RunStatus,
)
from .results import ResultBuilder

ParameterModelT = TypeVar("ParameterModelT", bound=BaseModel)


@dataclass(frozen=True)
class ActionSubprocessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes | None
    stderr: bytes | None


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
        supports_incremental_page_results: bool = False,
        supports_custom_file_results: bool = False,
        result_max_attempts: int = 4,
        result_retry_backoff: float = 0.5,
        result_retry_max_backoff: float = 8.0,
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
        self._cancel_requested = False
        self._cancelled_reported = False
        self._supports_incremental_page_results = supports_incremental_page_results
        self._supports_custom_file_results = supports_custom_file_results
        self._incremental_submission_started = False
        if result_max_attempts < 1:
            raise ValueError("result_max_attempts must be at least 1")
        if result_retry_backoff < 0 or result_retry_max_backoff < 0:
            raise ValueError("result retry backoff values must not be negative")
        self._result_max_attempts = result_max_attempts
        self._result_retry_backoff = result_retry_backoff
        self._result_retry_max_backoff = result_retry_max_backoff

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
        result_max_attempts: int = 4,
        result_retry_backoff: float = 0.5,
        result_retry_max_backoff: float = 8.0,
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
            supports_incremental_page_results=payload.capabilities.incremental_page_results,
            supports_custom_file_results=payload.capabilities.custom_file_results,
            result_max_attempts=result_max_attempts,
            result_retry_backoff=result_retry_backoff,
            result_retry_max_backoff=result_retry_max_backoff,
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
        action_input = ActionInput.model_validate(response.json())
        self._supports_incremental_page_results = (
            self._supports_incremental_page_results
            or action_input.capabilities.incremental_page_results
        )
        self._supports_custom_file_results = (
            self._supports_custom_file_results or action_input.capabilities.custom_file_results
        )
        self._cancel_requested = self._cancel_requested or action_input.cancel_requested
        return action_input

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
        if status == "running" and raise_on_cancel:
            await self._raise_if_cancel_requested(status_message=status_message, log=log)

        heartbeat = await self._post_heartbeat(
            progress_percent=progress_percent,
            status_message=status_message,
            log=log,
            status=status,
            error_message=error_message,
        )
        if status == "cancelled":
            self._cancel_requested = True
            self._cancelled_reported = True
            return heartbeat

        self._cancel_requested = self._cancel_requested or heartbeat.cancel_requested
        if raise_on_cancel:
            await self._raise_if_cancel_requested(status_message=status_message, log=log)
        return heartbeat

    async def cancelled(
        self,
        *,
        status_message: str | None = None,
        log: str | None = None,
    ) -> HeartbeatResponse:
        if self._cancelled_reported:
            return HeartbeatResponse.model_validate({"cancelRequested": True})

        heartbeat = await self._post_heartbeat(
            status="cancelled",
            status_message=status_message,
            log=log,
        )
        self._cancel_requested = True
        self._cancelled_reported = True
        return heartbeat

    async def _post_heartbeat(
        self,
        *,
        progress_percent: int | None = None,
        status_message: str | None = None,
        log: str | None = None,
        status: RunStatus = "running",
        error_message: str | None = None,
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
        return HeartbeatResponse.model_validate(response.json())

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
        results: ResultBuilder | None = None,
        message: str | None = None,
    ) -> Mapping[str, Any]:
        await self._ensure_results_allowed()
        if self._incremental_submission_started and results is not None and results.files:
            raise ValueError("Incremental runs must be completed without bulk result files")
        return await self._post_results(
            results or ResultBuilder(), status="completed", message=message, page_id=None
        )

    async def submit_page_results(
        self,
        page_id: str,
        results: ResultBuilder,
        message: str | None = None,
    ) -> Mapping[str, Any]:
        await self._ensure_results_allowed()
        if not self._supports_incremental_page_results:
            raise IncrementalResultsUnsupported(
                "This LAREX server did not advertise incrementalPageResults support"
            )
        if not page_id.strip():
            raise ValueError("page_id must not be blank")
        mismatched_page_ids = {file.page_id for file in results.files if file.page_id != page_id}
        if mismatched_page_ids:
            raise ValueError("Every incremental result file must match page_id")
        response = await self._post_results(
            results,
            status="running",
            message=message,
            page_id=page_id,
        )
        self._incremental_submission_started = True
        return response

    async def upload_results(
        self,
        results: ResultBuilder,
        *,
        status: ResultStatus = "completed",
        message: str | None = None,
    ) -> Mapping[str, Any]:
        await self._ensure_results_allowed()
        if status == "running":
            raise ValueError("Use submit_page_results for running incremental results")
        if self._incremental_submission_started and results.files:
            raise ValueError("Incremental runs cannot upload bulk result files")
        return await self._post_results(results, status=status, message=message, page_id=None)

    async def fail(
        self,
        message: str,
        *,
        log: str | None = None,
        progress_percent: int | None = None,
    ) -> HeartbeatResponse:
        if self._cancel_requested:
            return await self.cancelled(status_message=message, log=log)
        return await self.heartbeat(
            progress_percent=progress_percent,
            status_message=message,
            log=log,
            status="failed",
            error_message=message,
        )

    async def _ensure_results_allowed(self) -> None:
        await self._raise_if_cancel_requested()

    async def _raise_if_cancel_requested(
        self,
        *,
        status_message: str | None = None,
        log: str | None = None,
    ) -> None:
        if not self._cancel_requested:
            return
        await self.cancelled(status_message=status_message, log=log)
        raise ActionCancelled("LAREX requested cancellation")

    async def _post_results(
        self,
        results: ResultBuilder,
        *,
        status: ResultStatus,
        message: str | None,
        page_id: str | None,
    ) -> Mapping[str, Any]:
        if any(file.type == "file" for file in results.files) and not (
            self._supports_custom_file_results
        ):
            raise CustomFileResultsUnsupported(
                "This LAREX server did not advertise customFileResults support"
            )
        for attempt in range(1, self._result_max_attempts + 1):
            response: httpx.Response | None = None
            try:
                with ExitStack() as exit_stack:
                    response = await self._client.post(
                        self.result_url,
                        headers=self._auth_headers(),
                        files=results.httpx_files(
                            status=status,
                            message=message,
                            page_id=page_id,
                            exit_stack=exit_stack,
                        ),
                    )
                if (
                    _is_retryable_result_status(response.status_code)
                    and attempt < self._result_max_attempts
                ):
                    await asyncio.sleep(self._result_retry_delay(attempt, response))
                    continue
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, Mapping) else {"response": data}
            except httpx.TransportError:
                if attempt >= self._result_max_attempts:
                    raise
                await asyncio.sleep(self._result_retry_delay(attempt, response))
        raise RuntimeError("Result submission retry loop exited unexpectedly")

    def _result_retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return max(0.0, min(float(retry_after), self._result_retry_max_backoff))
                except ValueError:
                    pass
        exponential = min(
            self._result_retry_max_backoff,
            self._result_retry_backoff * (2 ** (attempt - 1)),
        )
        return random.uniform(0, exponential)

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

    @property
    def capabilities(self) -> ActionCapabilities:
        return self.payload.capabilities

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
        except ActionCancelled:
            raise
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
        await self.check_cancelled()

    async def check_cancelled(self) -> None:
        await self.heartbeat(raise_on_cancel=True)

    async def cancelled(
        self,
        message: str | None = None,
        *,
        log: str | None = None,
    ) -> HeartbeatResponse:
        return await self.client.cancelled(status_message=message, log=log)

    async def download_bytes(self, file: ActionFile) -> bytes:
        return await self.client.download_bytes(file)

    async def download_to_path(self, file: ActionFile, path: str | Path) -> Path:
        return await self.client.download_to_path(file, path)

    async def run_subprocess(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        terminate_grace_seconds: float = 5.0,
        capture_output: bool = False,
        input: bytes | None = None,
        cancel_poll_seconds: float = 5.0,
    ) -> ActionSubprocessResult:
        if not command:
            raise ValueError("command must not be empty")
        if terminate_grace_seconds < 0:
            raise ValueError("terminate_grace_seconds must not be negative")
        if cancel_poll_seconds <= 0:
            raise ValueError("cancel_poll_seconds must be positive")

        normalized_command = tuple(os.fspath(part) for part in command)
        await self.check_cancelled()
        process = await asyncio.create_subprocess_exec(
            *normalized_command,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdin=asyncio.subprocess.PIPE if input is not None else None,
            stdout=asyncio.subprocess.PIPE if capture_output else None,
            stderr=asyncio.subprocess.PIPE if capture_output else None,
        )
        communicate_task = asyncio.create_task(process.communicate(input))
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout

        try:
            while True:
                try:
                    stdout, stderr = await _wait_for_subprocess_output(
                        communicate_task,
                        timeout=_subprocess_wait_timeout(
                            loop=loop,
                            deadline=deadline,
                            cancel_poll_seconds=cancel_poll_seconds,
                            timeout=timeout,
                            command_name=normalized_command[0],
                        ),
                    )
                    return ActionSubprocessResult(
                        args=normalized_command,
                        returncode=process.returncode or 0,
                        stdout=stdout,
                        stderr=stderr,
                    )
                except TimeoutError:
                    await self.check_cancelled()
        except BaseException:
            await _terminate_subprocess(process, terminate_grace_seconds)
            raise
        finally:
            if not communicate_task.done():
                communicate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await communicate_task

    def result_builder(self) -> ResultBuilder:
        return ResultBuilder()

    async def complete(
        self,
        results: ResultBuilder | None = None,
        message: str | None = None,
    ) -> Mapping[str, Any]:
        return await self.client.complete(results, message)

    async def submit_page_results(
        self,
        page_id: str,
        results: ResultBuilder,
        message: str | None = None,
    ) -> Mapping[str, Any]:
        return await self.client.submit_page_results(page_id, results, message)

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


def _is_retryable_result_status(status_code: int) -> bool:
    return status_code in {408, 429, 502, 503, 504}


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


async def _terminate_subprocess(
    process: asyncio.subprocess.Process,
    terminate_grace_seconds: float,
) -> None:
    if process.returncode is not None:
        return

    try:
        process.terminate()
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(process.wait(), timeout=terminate_grace_seconds)
        return
    except TimeoutError:
        pass
    except ProcessLookupError:
        return

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            await process.wait()
        except ProcessLookupError:
            return


def _subprocess_wait_timeout(
    *,
    loop: asyncio.AbstractEventLoop,
    deadline: float | None,
    cancel_poll_seconds: float,
    timeout: float | None,
    command_name: str,
) -> float:
    if deadline is None:
        return cancel_poll_seconds

    remaining = deadline - loop.time()
    if remaining <= 0:
        raise TimeoutError(f"Subprocess timed out after {timeout} seconds: {command_name}")
    return min(cancel_poll_seconds, remaining)


async def _wait_for_subprocess_output(
    communicate_task: asyncio.Task[tuple[bytes | None, bytes | None]],
    *,
    timeout: float,
) -> tuple[bytes | None, bytes | None]:
    return await asyncio.wait_for(asyncio.shield(communicate_task), timeout=timeout)
