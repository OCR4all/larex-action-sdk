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

## Quickstart

```python
import os

from larex_actions import ActionContext
from larex_actions.fastapi import create_larex_action_app


async def process(ctx: ActionContext) -> None:
    action_input = await ctx.pull_input()
    total = len(action_input.pages)
    for index, page in enumerate(action_input.pages, start=1):
        await ctx.heartbeat(
            status_message=f"Processing page {index}/{total}: {page.name}",
            raise_on_cancel=True,
        )
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

## Documentation

The full guide lives in the LAREX documentation under
[Processor SDK](https://docs.larex.kallimachos.de/actions/processor-sdk/overview):

- [Quickstart](https://docs.larex.kallimachos.de/actions/processor-sdk/quickstart): running a processor, adapter routes, concurrency, and capabilities
- [Inputs and Targets](https://docs.larex.kallimachos.de/actions/processor-sdk/inputs): pulled input, targets, input requirements, image types, and dataset items
- [Results](https://docs.larex.kallimachos.de/actions/processor-sdk/results): result builder, page submissions, image variants, custom files, and run outcomes
- [Progress and Cancellation](https://docs.larex.kallimachos.de/actions/processor-sdk/progress-and-cancellation): progress, heartbeats, and cooperative cancellation
- [Dynamic Parameter Values](https://docs.larex.kallimachos.de/actions/processor-sdk/parameter-values): processor-provided parameter allowlists
- [Retries and Logging](https://docs.larex.kallimachos.de/actions/processor-sdk/retries-and-logging): `RetryPolicy` and transport diagnostics
- [Framework-Neutral Processors](https://docs.larex.kallimachos.de/actions/processor-sdk/framework-neutral): `DispatchVerifier`, worker queues, and security

Training, evaluation, and integration processors are described in
[Dataset Model Training](https://docs.larex.kallimachos.de/actions/training),
[Dataset Model Evaluation](https://docs.larex.kallimachos.de/actions/evaluation#submit-with-the-sdk), and
[Integration Actions](https://docs.larex.kallimachos.de/actions/integration#processor-sdk).

## Development

```bash
uv sync --all-extras
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
uv build
```

Releases are automated by the `python-semantic-release` workflow. Conventional
commits on `main` determine the next version (`feat` → minor, `fix`/`perf` →
patch, and breaking changes → major). The workflow verifies the package, updates
`pyproject.toml` and `uv.lock`, creates the release commit/tag and GitHub release.
Run it manually from the Actions tab when you need to force a bump level or
create a prerelease.

Configure a repository secret named `RELEASE_TOKEN` with a GitHub token that has
Contents read/write access. A PAT is required so the generated
tag/release triggers the existing PyPI publication workflow. Release candidate
tags containing `rc` publish to TestPyPI; published stable releases publish to
PyPI.
