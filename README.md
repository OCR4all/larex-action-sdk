# LAREX Action SDK

> This SDK is work in progress. The public API can still change before LAREX
> Actions and the SDK are considered stable.

Framework-neutral Python SDK for building [LAREX](https://github.com/OCR4all/larex)
Action processors.

The core package verifies LAREX dispatch requests, parses typed run/input payloads,
sends heartbeats, downloads selected files, and uploads result manifests. FastAPI
support is available as an optional convenience extra.

## Installation

```bash
uv add "larex-action-sdk[fastapi]"
```

For framework-neutral usage only:

```bash
uv add larex-action-sdk
```

## FastAPI Processor

```python
import os

from larex_actions import ActionContext
from larex_actions.fastapi import create_larex_action_app


async def process(ctx: ActionContext) -> None:
    action_input = await ctx.pull_input()
    results = ctx.result_builder()

    for page in action_input.pages:
        await ctx.heartbeat(25, f"Processing {page.name}", raise_on_cancel=True)
        if page.xml:
            xml_bytes = await ctx.download_bytes(page.xml[0])
            results.add_xml_bytes(
                page_id=page.id,
                content=xml_bytes,
                file_name=f"{page.name}-processed.xml",
                variant="processed",
            )

    await ctx.complete(results, "Done")


app = create_larex_action_app(
    processor_id="my-processor",
    dispatch_secret=os.environ["LAREX_DISPATCH_HMAC_SECRET"],
    handler=process,
)
```

## Framework-Neutral Dispatch Verification

```python
from larex_actions import DispatchVerifier

payload = DispatchVerifier(
    processor_id="my-processor",
    dispatch_secret=secret,
).verify(
    method=request_method,
    path_and_query=request_path_and_query,
    headers=request_headers,
    body=request_body,
)
```

You can then pass `payload.model_dump(mode="json", by_alias=True)` to your own
queue/worker system and use `ActionClient.from_dispatch(payload)` in async workers.

## Security

- Dispatch requests are verified with the `X-LAREX-Action-*` HMAC headers.
- Timestamps and nonces are checked to reduce replay risk.
- Per-run bearer secrets and dispatch HMAC secrets are never included in model reprs.
- Processor YAML must still declare the inputs and outputs LAREX should expose or accept.

## Development

```bash
uv sync --all-extras
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
uv build
```

Releases are published with PyPI Trusted Publishing from GitHub Actions. Release
candidate tags containing `rc` publish to TestPyPI; published GitHub releases
publish to PyPI.
