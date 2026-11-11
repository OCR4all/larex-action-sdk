from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from larex_actions import ResultBuilder


def test_custom_file_bytes_can_be_project_level() -> None:
    results = ResultBuilder()
    results.add_file_bytes(
        b'{"entity":"Ada"}\n',
        "entities.jsonl",
        mime_type="application/x-ndjson",
    )

    manifest = results.manifest()
    assert manifest.files[0].type == "file"
    assert manifest.files[0].page_id is None
    assert manifest.model_dump(by_alias=True, exclude_none=True)["files"][0] == {
        "fieldName": "file_0",
        "type": "file",
        "fileName": "entities.jsonl",
    }

    with ExitStack() as exit_stack:
        multipart = results.httpx_files(exit_stack=exit_stack)
        assert multipart[1] == (
            "file_0",
            ("entities.jsonl", b'{"entity":"Ada"}\n', "application/x-ndjson"),
        )


def test_custom_file_path_preserves_optional_page_association(tmp_path: Path) -> None:
    result_path = tmp_path / "page.txt"
    result_path.write_text("recognized text", encoding="utf-8")
    results = ResultBuilder()
    results.add_file_path(result_path, page_id="page-1", mime_type="text/plain")

    assert results.files[0].page_id == "page-1"
    with ExitStack() as exit_stack:
        multipart = results.httpx_files(exit_stack=exit_stack)
        uploaded = multipart[1][1]
        assert uploaded[0] == "page.txt"
        assert uploaded[2] == "text/plain"
        content = uploaded[1]
        assert not isinstance(content, (bytes, str))
        assert content.read() == b"recognized text"


def test_custom_file_rejects_blank_optional_page_id() -> None:
    results = ResultBuilder()
    with pytest.raises(ValueError, match="page_id"):
        results.add_file_bytes(b"data", "result.bin", page_id="  ")


def test_one_page_can_return_several_image_variants() -> None:
    results = ResultBuilder()
    results.add_image_bytes(
        "page-1", b"binary", "page-1.bin.png", variant="binary", mime_type="image/png"
    )
    results.add_image_bytes(
        "page-1", b"gray", "page-1.nrm.png", variant="grayscale", mime_type="image/png"
    )
    results.add_image_bytes(
        "page-2", b"binary", "page-2.bin.png", variant="binary", mime_type="image/png"
    )

    manifest = results.manifest()
    assert [(file.page_id, file.variant) for file in manifest.files] == [
        ("page-1", "binary"),
        ("page-1", "grayscale"),
        ("page-2", "binary"),
    ]
    with ExitStack() as exit_stack:
        multipart = results.httpx_files(exit_stack=exit_stack)
    assert [part[0] for part in multipart] == ["manifest", "file_0", "file_1", "file_2"]


def test_duplicate_image_variant_for_one_page_is_rejected() -> None:
    results = ResultBuilder()
    results.add_image_bytes("page-1", b"a", "a.png", variant="binary")

    with pytest.raises(ValueError, match="already has an image with variant binary"):
        results.add_image_bytes("page-1", b"b", "b.png", variant="binary")
    assert len(results.files) == 1


def test_manifest_rejects_duplicate_image_variants() -> None:
    from pydantic import ValidationError

    from larex_actions import ResultFile, ResultManifest

    files = [
        ResultFile(
            fieldName=f"file_{index}",
            pageId="page-1",
            type="image",
            variant="binary",
            fileName="a.png",
        )
        for index in range(2)
    ]
    with pytest.raises(ValidationError, match="distinct variant per page"):
        ResultManifest(files=files)
