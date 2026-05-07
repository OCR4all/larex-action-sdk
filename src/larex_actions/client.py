from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx

from .exceptions import ActionCancelled
from .models import ActionDispatchPayload, ActionFile, ActionInput, HeartbeatResponse
from .results import ResultBuilder


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
    ) -> None:
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
    ) -> ActionClient:
        return cls(
            pull_url=payload.pull_url,
            heartbeat_url=payload.heartbeat_url,
            result_url=payload.result_url,
            secret=payload.secret.get_secret_value(),
            client=client,
            timeout=timeout,
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
        status: str = "running",
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
        response = await self._client.get(file.download_url, headers=self._auth_headers())
        response.raise_for_status()
        return response.content

    async def download_to_path(self, file: ActionFile, path: str | Path) -> Path:
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
        status: str = "completed",
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
        status: str,
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

    async def fail(self, message: str, *, log: str | None = None) -> HeartbeatResponse:
        return await self.client.fail(message, log=log)
