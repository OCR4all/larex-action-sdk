# LAREX Action SDK

> This SDK is work in progress. The public API can still change before LAREX
> Actions and the SDK are considered stable.

Framework-neutral Python SDK for building [LAREX](https://github.com/OCR4all/larex)
Action processors with signed dispatch verification, typed payloads, and cooperative
run cancellation.

The core package verifies LAREX dispatch requests, parses typed run/input payloads,
sends heartbeats, downloads selected files, uploads result manifests, and helps
processors acknowledge cancellation cleanly. FastAPI support is available as an
optional convenience extra.

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
    for page in action_input.pages:
        async with ctx.step(f"Processing {page.name}", progress_percent=25):
            await ctx.check_cancelled()
            results = ctx.result_builder()
            if page.xml:
                xml_bytes = await ctx.download_bytes(page.xml[0])
                results.add_xml_bytes(
                    page_id=page.id,
                    content=xml_bytes,
                    file_name=f"{page.name}-processed.xml",
                )
            await ctx.submit_page_results(page.id, results, f"Finished {page.name}")

    await ctx.complete(message="Done")


app = create_larex_action_app(
    processor_id="my-processor",
    dispatch_secret=os.environ["LAREX_DISPATCH_HMAC_SECRET"],
    handler=process,
    max_concurrent_runs=1,
)
```

Incremental page submissions require LAREX to advertise
`capabilities.incrementalPageResults`. The SDK refuses the submission when an
older server does not advertise it. Existing processors can continue to call
`await ctx.complete(results, "Done")` once with a bulk result.

`max_concurrent_runs` bounds simultaneous in-process handlers for CPU/GPU-heavy
processors. Additional signed dispatches remain accepted and wait for a slot.
`/ready` returns `503` while every slot is occupied; `/health` remains a liveness
endpoint. For crash-durable queuing, run the handler in an external worker system
instead of relying on FastAPI background tasks.

Result callbacks retry connection failures and transient HTTP responses (`408`,
`429`, `502`, `503`, and `504`) automatically. Path-based files are reopened for
every attempt. The defaults are four attempts with exponential backoff and jitter;
processors can tune `result_max_attempts`, `result_retry_backoff`, and
`result_retry_max_backoff` on `ActionClient` or `ActionClient.from_dispatch(...)`.

The FastAPI adapter always exposes `/dispatch` and `/health`. Set
`LAREX_ACTION_ROUTE_PREFIXES` to also expose prefixed routes when a reverse
proxy keeps an external path prefix:

```bash
LAREX_ACTION_ROUTE_PREFIXES=/kraken,/ocr
```

With that setting, the same processor also accepts `/kraken/dispatch`,
`/kraken/health`, `/ocr/dispatch`, and `/ocr/health`. LAREX must sign and call
the same path the processor receives; do not strip the prefix in the reverse
proxy before the request reaches the processor.

## Target-Aware Runs

LAREX can dispatch page, region, and textline targeted runs. The SDK exposes the
requested target on both dispatch and pulled input payloads:

```python
payload_target = ctx.payload.target
action_input = await ctx.pull_input()
input_target = action_input.target
```

Processors still receive full page files according to the Action YAML inputs.
Target metadata contains selected region/textline ids only. LAREX sends full page
images/XML and lets processors resolve geometry from PAGE XML, including whether
to crop, mask, pad, deskew, or process the full image.

Processors return normal PAGE XML via `ResultBuilder.add_xml_bytes(...)` or
`add_xml_path(...)`. For region or textline targeted runs, LAREX imports only the
selected target scope from the returned PAGE XML.

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

## Cooperative Cancellation

LAREX cancellation is cooperative. The processor keeps polling the heartbeat
endpoint and LAREX responds with `cancelRequested: true` when the run should stop.

- Use `await ctx.check_cancelled()` at safe interruption points.
- `ctx.check_cancelled()` performs a heartbeat request, so avoid calling it in a
  hot inner loop without pacing.
- `await ctx.heartbeat(..., raise_on_cancel=True)` also raises `ActionCancelled`
  when a cancellation is pending.
- `await ctx.run_subprocess(...)` polls for cancellation while a child process is
  running, sends a final `status="cancelled"` heartbeat, and terminates the child
  process gracefully before escalating to `kill`.
- Once cancellation has been requested, the SDK refuses result uploads and
  acknowledges cancellation instead.

## Security

- Dispatch requests are verified with the `X-LAREX-Action-*` HMAC headers.
- Timestamps and nonces are checked to reduce replay risk.
- The FastAPI adapter rejects dispatch bodies larger than `max_dispatch_body_bytes`.
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
