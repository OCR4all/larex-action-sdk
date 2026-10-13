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

## Custom File Results

LAREX servers that advertise `capabilities.customFileResults` accept arbitrary
durable files. Check the capability before doing expensive postprocessing and
add project-level bytes or paths to a result builder:

```python
if not ctx.capabilities.custom_file_results:
    raise RuntimeError("This LAREX server does not support custom file results")

results = ctx.result_builder()
results.add_file_bytes(
    content=ner_jsonl,
    file_name="named-entities.jsonl",
    mime_type="application/x-ndjson",
)
results.add_file_path(
    report_path,
    file_name="report.txt",
    mime_type="text/plain",
)
await ctx.complete(results, "Postprocessing complete")
```

Omit `page_id` for project-level files. Pass `page_id=page.id` when associating a
file with a page. Incremental page submissions require every file—including a
custom file—to carry the same page ID as the submission. The client raises
`CustomFileResultsUnsupported` before uploading if the server did not advertise
support.

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
If LAREX rejects a result, `ResultSubmissionError` includes a bounded, sanitized
response detail in its message so processor logs show the actual import or
validation failure. It remains an `httpx.HTTPStatusError` subtype for compatibility.

## SDK Transport Logging

The SDK uses Python's standard `logging` module for optional transport diagnostics.
Records are emitted at `DEBUG` on the `larex_actions.transport` logger, so normal
processor output remains unchanged until the application enables that logger. The
library does not configure the root logger or install handlers.

When the processor already configures a root handler, including many application
logging setups, enabling the named logger is enough:

```python
import logging

logging.getLogger("larex_actions.transport").setLevel(logging.DEBUG)
```

For a Uvicorn or Docker process where the root logger has no handler, attach a
standard stream handler in the processor's own startup code. `StreamHandler` writes
to stderr by default, which Docker and normal process supervisors collect alongside
stdout:

```python
import logging

sdk_transport_logger = logging.getLogger("larex_actions.transport")
sdk_transport_logger.setLevel(logging.DEBUG)
sdk_transport_logger.addHandler(logging.StreamHandler())
sdk_transport_logger.propagate = False
```

Transport records identify operations such as input pulls, heartbeats, downloads,
page-result submissions, completion uploads, retries, and failures. They include
safe metadata when available—for example run/page IDs, result status and file type
counts, HTTP status, duration, and retry attempt. They never include authorization
headers, request or response bodies, file contents, or callback/download URLs.

This is local SDK transport logging only. Enabling it does not send extra `log=`
heartbeats or otherwise add requests to LAREX. It can show when an SDK call
succeeds, fails, or is retried, but it cannot determine what an Action does
internally between calls. Use Action-specific application logging for semantic
progress, or call `ctx.heartbeat(...)`/`ctx.log(...)` when that progress should
also be reported to LAREX.

The FastAPI adapter always exposes `/dispatch`, `/preflight`, and `/health`.
`/preflight` accepts only a valid signed LAREX request and reports the processor
identity, protocol version, and configured capabilities. Set
`LAREX_ACTION_ROUTE_PREFIXES` to also expose prefixed routes when a reverse
proxy keeps an external path prefix:

```bash
LAREX_ACTION_ROUTE_PREFIXES=/kraken,/ocr
```

With that setting, the same processor also accepts `/kraken/dispatch`,
`/kraken/preflight`, `/kraken/health`, and the equivalent `/ocr/*` routes. LAREX
must sign and call the same path the processor receives; do not strip the prefix
in the reverse proxy before the request reaches the processor.

## Dynamic Parameter Values

Actions can constrain a parameter to values discovered by the processor. Register
zero-argument synchronous or asynchronous providers with the FastAPI adapter:

```python
from larex_actions import ParameterChoice


def discover_models():
    return [
        ParameterChoice(value="model-a", label="Model A"),
        ParameterChoice(value="model-b", label="Model B"),
    ]


app = create_larex_action_app(
    processor_id="my-processor",
    dispatch_secret=secret,
    handler=process,
    parameter_value_providers={"models": discover_models},
)
```

The adapter exposes the signed `/parameter-values` route (and prefixed variants),
advertises `parameterValueDiscovery` during preflight, and limits each provider to
1,000 unique primitive choices. LAREX calls providers again immediately before a
run so returned values are an authoritative allowlist rather than UI suggestions.

Capabilities default to both SDK-supported result features. Override them when a
processor intentionally implements a smaller surface:

```python
app = create_larex_action_app(
    processor_id="my-processor",
    dispatch_secret=secret,
    handler=process,
    processor_capabilities={"incrementalPageResults": True, "customFileResults": False},
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
Target metadata contains selected region/textline ids only. LAREX sends full page
images/XML and lets processors resolve geometry from PAGE XML, including whether
to crop, mask, pad, deskew, or process the full image.

Input definitions can declare whether each file type is unavailable, optional,
or required, including target-specific requirements:

```yaml
inputs:
  images:
    level: required
  xml:
    level: optional
    requiredForTargets:
      - REGION
```

LAREX resolves that contract for the selected target and exposes it on both
`ctx.payload.input_requirements` and `action_input.input_requirements`. Legacy
boolean definitions remain compatible (`true` is optional and `false` is none).
Pages missing a required input are excluded before dispatch.

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


## Dataset Training and Evaluation

Training and evaluation processors use the same signed dispatch, input download,
heartbeat, cancellation, and completion APIs as processing Actions. Define the
Action as `kind: TRAINING` or `kind: EVALUATION` in LAREX. Their payloads expose
`dataset_id` instead of `project_id`, and dispatches include `dataset_item_ids`.
Each pulled input page represents a dataset item: `page.id` is its item ID,
`page.source_page_id` identifies the source page, and `page.split` is `TRAIN`,
`VAL`, or `TEST`. Each item contains one frozen image by default and exactly one PAGE XML file.
Actions declaring `inputs.images.multiple: true` can receive multiple frozen images
per item. The SDK checks this capability in `inputRequirements.images.multiple`
for every Action kind.

Training processors download those pairs, perform training, validate and publish
the resulting model to processor-managed storage, then call
`await ctx.complete(message="Model published")`. Training results contain no
uploaded files; LAREX does not store or register the trained model.

Evaluation processors return one structured report:

```python
from larex_actions import ActionContext, EvaluationMetric, EvaluationReport
from larex_actions.fastapi import create_larex_action_app


async def evaluate(ctx: ActionContext) -> None:
    action_input = await ctx.pull_input()
    # Download image/XML pairs and compute metrics here.
    await ctx.check_cancelled()
    report = EvaluationReport(
        profile="larex.ocr-recognition",
        profileVersion=1,
        title="Recognition quality",
        summary=[EvaluationMetric(
            key="cer", label="CER", value=0.12,
            format="PERCENT", direction="LOWER_IS_BETTER",
        )],
    )
    await ctx.complete_evaluation(report, "Evaluation complete")


app = create_larex_action_app(
    processor_id="my-evaluation",
    dispatch_secret=secret,
    handler=evaluate,
    processor_capabilities={"evaluationReports": True},
)
```

Explicitly advertise `evaluationReports` in processor capabilities so LAREX's
preflight accepts the evaluation processor. The server must also advertise this
capability in its dispatch or pulled input; otherwise `complete_evaluation`
raises `EvaluationReportsUnsupported`. Existing processing capabilities retain
their defaults.

Reports require at least one finite numeric summary metric and a profile/version
matching the Action definition. Optional `EvaluationTable` and
`EvaluationSample` objects add diagnostics; use `EvaluationTableRow(values={...})`
for table cells and dataset item IDs for sample `inputId` references. Keys and
sample IDs must be unique, JSON object keys must be nonblank strings, and the
serialized report must fit within 10 MiB. Reports are revalidated before result
serialization. LAREX validates input references and profile compatibility.

Dataset Actions report progress through heartbeats and upload no result files.
Evaluation completes with `complete_evaluation`, while training completes with
an empty `complete` call. SDK support requires Pydantic 2.12 or later.

## Declaring annotation overwrite effects

Output import behavior is fixed: XML replaces the page or selected annotation scope, and images replace the matching result variant. There is no output `mode` field; processor code and the SDK protocol are unchanged.

In the Action YAML, XML-producing processors may declare target-specific potential replacement/deletion:

```yaml
outputs:
  xml:
    enabled: true
    overwrites:
      PAGE: [REGIONS, READING_ORDER]
      REGION: [TEXT_LINES]
```

Levels: `REGIONS`, `TEXT_LINES`, `BASELINES`, `TEXT`, `WORDS`, `GLYPHS`, `READING_ORDER`. Structural replacement includes descendant annotations. Text means nonempty text alternatives at any annotation level; reading order means explicit PAGE reading-order structures. Metadata is excluded. Declare the union of possible effects across parameters, matching LAREX's result-import target boundaries.

Missing declarations produce a generic warning for existing XML; `[]` explicitly declares no overwrite risk. Declarations are advisory and are used by LAREX's pre-run page review, where users can exclude affected pages. They do not alter SDK result submission or enforce preservation. Install these definitions only after upgrading LAREX to support the field; protocol version remains unchanged.

## Integration Actions

`kind: INTEGRATION` supports project/editor or dataset inputs for external exports,
API ingestion, and other work that returns status feedback. LAREX freezes the saved
files before dispatch or queueing; each input supplies images, XML, or both according
to the definition. Dataset splits are optional metadata. Exactly one of `projectId`
and `datasetId` identifies the resource.

```python
async def integrate(ctx: ActionContext) -> None:
    action_input = await ctx.pull_input()
    for page in action_input.pages:
        await ctx.check_cancelled()
        xml_files = [await ctx.download_bytes(file) for file in page.xml]
        image_files = [await ctx.download_bytes(file) for file in page.images]
        # Export these bytes to your external service using ctx.run_id for idempotency.
        await ctx.heartbeat(50, f"Exported {page.name}", raise_on_cancel=True)
    await ctx.complete(message="External export complete")
```

Use existing heartbeat, failure, and cancellation APIs. Completion carries only
final status, a message, and optional run-level issues. The SDK rejects file/evaluation-report/incremental result uploads
for integrations before sending them to LAREX. Configure the FastAPI adapter with
`processor_capabilities={"incrementalPageResults": False, "customFileResults": False}`.
Integration snapshot bytes expire on terminal runs; repeating work starts a new run.

### Downloadable evaluation files

Configure the Action YAML with `evaluation.outputMode: FILES` and advertise
`processor_capabilities={"customFileResults": True}` in your processor app.
Dispatch and pulled input expose `evaluationOutputMode` (`evaluation_output_mode`
in Python). Existing definitions default to structured reports.

```python
results = ResultBuilder()
results.add_file_path("metrics.csv", mime_type="text/csv")
results.add_file_path("report.pdf", mime_type="application/pdf")
await ctx.complete(results, message="Evaluation complete")
```

Return at least one run-level file without `page_id`. File-mode evaluations
cannot also return an `EvaluationReport`; report-mode evaluations continue to
use `ctx.complete_evaluation(report)`. Progress uses heartbeats, and `ctx.fail()`
may omit files. LAREX stores files with dataset-configured retention and offers
individual downloads from the evaluation detail page.

## Run outcomes and reports

LAREX advertises `capabilities.runReports`. The reporting APIs require it and
raise `RunReportsUnsupported` on older servers. Existing result calls remain
supported. Send `completed` normally; LAREX derives `COMPLETED_WITH_ISSUES` from
recorded failures and warnings, or `FAILED` when every page fails.

SDK 0.20 simplifies incremental submissions: supply the page ID once, then use
`page_status`, `reason`, and `warnings`. The SDK 0.19 `outcome` keyword has been
removed. Bulk reports continue to use `PageOutcome` entries containing page IDs.

```python
from larex_actions import PageResultRejected, RunIssue, RunReport

# Recoverable failure: acknowledge the page and continue with later pages.
await ctx.submit_page_results(
    page.id, page_status="FAILED", reason="Invalid input"
)

# Deliberate skips are counted without making the run have issues.
await ctx.submit_page_results(
    page.id, page_status="SKIPPED", reason="No eligible content"
)

# Successful output can still have nonfatal concerns.
await ctx.submit_page_results(page.id, results, warnings=["Low recognition confidence"])

# Catch only the typed rejection: LAREX already settled this failed page.
try:
    await ctx.submit_page_results(page.id, results)
except PageResultRejected:
    pass

await ctx.complete(run_report=RunReport(issues=[RunIssue(message="Some pages had low confidence")]))
```

Each snippet illustrates a separate page submission; a run/page is acknowledged
only once. Failed and skipped outcomes require a reason and cannot contain files.
Every page must be acknowledged before incremental completion. Duplicate page
submissions replay the stored outcome. Fatal errors and cancellation still stop
processing; do not catch transport, authentication, or arbitrary runtime errors
as recoverable page failures.

`message` is run progress text. `reason` describes this page's outcome and is
stored in its report; the SDK maps it to the protocol's page-outcome `message`.
Warnings are additional nonfatal findings, including on successful pages, and
make the run complete with issues. They do not make a successful page retryable.
Omitting `page_status` means success. Calls without any page diagnostics keep
their existing behavior and do not require `runReports` support.

Bulk completion uses `RunReport(pageOutcomes=[...])` with an outcome for every
selected page. Incremental completion reuses submitted page outcomes and accepts
only run-level issues in its report. All Action kinds can report run-level issues;
training, evaluation, and integration do not accept page outcomes. General run
reports remain separate from evaluation metrics and uploaded result files.

`complete`, `complete_evaluation`, and `upload_results` accept keyword-only
`run_report`. `heartbeat` and `fail` also accept reports containing run-level issues.
Report schema version is 1; supported issue severities are `WARNING` and `ERROR`.
Reports are revalidated before submission and limited to 10 MiB. LAREX computes
counts and provides the readable report and JSON download in run details.

Deploy LAREX reporting support before releasing this SDK, then upgrade official
processors using the simplified incremental interface to SDK 0.20 and regenerate
their lockfiles. Bulk reporting remains available from SDK 0.19.
