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

from larex_actions import ActionContext, crop_image_bytes
from larex_actions.fastapi import create_larex_action_app


async def process(ctx: ActionContext) -> None:
    action_input = await ctx.pull_input()
    results = ctx.result_builder()

    for page in action_input.pages:
        async with ctx.step(f"Processing {page.name}", progress_percent=25):
            if page.images and action_input.target_selection:
                image_bytes = await ctx.download_bytes(page.images[0])
                for target_page in action_input.target_selection.pages:
                    if target_page.page_id != page.id:
                        continue
                    for region in target_page.regions:
                        crop = crop_image_bytes(image_bytes, region.coords, padding=16)
                        # Run a model on the crop, then merge results into PAGE XML.

            if page.xml:
                xml_bytes = await ctx.download_bytes(page.xml[0])
                results.add_xml_bytes(
                    page_id=page.id,
                    content=xml_bytes,
                    file_name=f"{page.name}-processed.xml",
                )

    await ctx.complete(results, "Done")


app = create_larex_action_app(
    processor_id="my-processor",
    dispatch_secret=os.environ["LAREX_DISPATCH_HMAC_SECRET"],
    handler=process,
)
```

## Target-Aware Runs

LAREX can dispatch page, region, and textline targeted runs. The SDK exposes the
requested target on both dispatch and pulled input payloads:

```python
payload_target = ctx.payload.target
action_input = await ctx.pull_input()
input_target = action_input.target
```

Processors still receive full page files according to the Action YAML inputs.
Target metadata contains selected region/textline ids, geometry, and current text.
LAREX sends full page images/XML and lets processors decide how to use the target.
The SDK includes `crop_image_bytes(...)` and `bounding_box(...)` helpers for
building region/textline crops from target geometry.

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
