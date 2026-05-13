from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .models import FileType, ResultFile, ResultManifest, ResultPatch, ResultStatus

FileContent = bytes | Path
HttpxFile = tuple[str, tuple[str, bytes | BinaryIO | str, str]]


@dataclass(frozen=True)
class _PendingResultFile:
    field_name: str
    page_id: str
    type: FileType
    variant: str | None
    file_name: str
    mime_type: str
    content: FileContent


@dataclass(frozen=True)
class _PendingLayoutPatchFile:
    field_name: str
    page_id: str
    file_name: str
    mime_type: str
    content: FileContent


class ResultBuilder:
    def __init__(self) -> None:
        self._files: list[_PendingResultFile] = []
        self._text_patches: list[ResultPatch] = []
        self._layout_patches: list[_PendingLayoutPatchFile] = []

    @property
    def files(self) -> list[ResultFile]:
        return [
            ResultFile(
                fieldName=file.field_name,
                pageId=file.page_id,
                type=file.type,
                variant=file.variant,
                fileName=file.file_name,
            )
            for file in self._files
        ]

    @property
    def patches(self) -> list[ResultPatch]:
        patches = list(self._text_patches)
        patches.extend(
            ResultPatch(
                type="LAYOUT_XML",
                pageId=file.page_id,
                fieldName=file.field_name,
                fileName=file.file_name,
            )
            for file in self._layout_patches
        )
        return patches

    def add_image_bytes(
        self,
        page_id: str,
        content: bytes,
        file_name: str,
        *,
        variant: str,
        mime_type: str = "application/octet-stream",
    ) -> None:
        self._add_file(
            page_id=page_id,
            content=content,
            file_name=file_name,
            variant=variant,
            mime_type=mime_type,
            type_="image",
        )

    def add_xml_bytes(
        self,
        page_id: str,
        content: bytes,
        file_name: str,
    ) -> None:
        self._add_file(
            page_id=page_id,
            content=content,
            file_name=_ensure_xml_file_name(file_name),
            variant=None,
            mime_type="application/xml",
            type_="xml",
        )

    def add_image_path(
        self,
        page_id: str,
        path: str | Path,
        *,
        variant: str,
        file_name: str | None = None,
        mime_type: str = "application/octet-stream",
    ) -> None:
        path_value = Path(path)
        self._add_file(
            page_id=page_id,
            content=path_value,
            file_name=file_name or path_value.name,
            variant=variant,
            mime_type=mime_type,
            type_="image",
        )

    def add_xml_path(
        self,
        page_id: str,
        path: str | Path,
        *,
        file_name: str | None = None,
    ) -> None:
        path_value = Path(path)
        self._add_file(
            page_id=page_id,
            content=path_value,
            file_name=_ensure_xml_file_name(file_name or path_value.name),
            variant=None,
            mime_type="application/xml",
            type_="xml",
        )

    def add_text_line_text(
        self,
        page_id: str,
        text_line_id: str,
        text: str,
        *,
        confidence: float | None = None,
        index: int | None = None,
    ) -> None:
        if not page_id:
            raise ValueError("page_id must not be blank")
        if not text_line_id:
            raise ValueError("text_line_id must not be blank")
        self._text_patches.append(
            ResultPatch(
                type="TEXT_LINE_TEXT",
                pageId=page_id,
                textLineId=text_line_id,
                text=text,
                confidence=confidence,
                index=index,
            )
        )

    def add_layout_xml_bytes(
        self,
        page_id: str,
        content: bytes,
        file_name: str,
    ) -> None:
        self._add_layout_patch(
            page_id=page_id,
            content=content,
            file_name=_ensure_xml_file_name(file_name),
        )

    def add_layout_xml_path(
        self,
        page_id: str,
        path: str | Path,
        *,
        file_name: str | None = None,
    ) -> None:
        path_value = Path(path)
        self._add_layout_patch(
            page_id=page_id,
            content=path_value,
            file_name=_ensure_xml_file_name(file_name or path_value.name),
        )

    def manifest(
        self, *, status: ResultStatus = "completed", message: str | None = None
    ) -> ResultManifest:
        return ResultManifest(
            status=status, message=message, files=self.files, patches=self.patches
        )

    def httpx_files(
        self,
        *,
        status: ResultStatus = "completed",
        message: str | None = None,
        exit_stack: ExitStack,
    ) -> list[HttpxFile]:
        manifest = self.manifest(status=status, message=message)
        files: list[HttpxFile] = [
            (
                "manifest",
                (
                    "manifest.json",
                    manifest.model_dump_json(by_alias=True, exclude_none=True),
                    "application/json",
                ),
            )
        ]
        for file in self._files:
            content: bytes | BinaryIO
            if isinstance(file.content, Path):
                content = exit_stack.enter_context(file.content.open("rb"))
            else:
                content = file.content
            files.append((file.field_name, (file.file_name, content, file.mime_type)))
        for file in self._layout_patches:
            content: bytes | BinaryIO
            if isinstance(file.content, Path):
                content = exit_stack.enter_context(file.content.open("rb"))
            else:
                content = file.content
            files.append((file.field_name, (file.file_name, content, file.mime_type)))
        return files

    def _add_file(
        self,
        *,
        page_id: str,
        content: FileContent,
        file_name: str,
        variant: str | None,
        mime_type: str,
        type_: FileType,
    ) -> None:
        if not page_id:
            raise ValueError("page_id must not be blank")
        if not file_name:
            raise ValueError("file_name must not be blank")
        if type_ == "image" and not variant:
            raise ValueError("variant must not be blank")
        field_name = f"file_{len(self._files)}"
        self._files.append(
            _PendingResultFile(
                field_name=field_name,
                page_id=page_id,
                type=type_,
                variant=variant,
                file_name=file_name,
                mime_type=mime_type,
                content=content,
            )
        )

    def _add_layout_patch(
        self,
        *,
        page_id: str,
        content: FileContent,
        file_name: str,
    ) -> None:
        if not page_id:
            raise ValueError("page_id must not be blank")
        if not file_name:
            raise ValueError("file_name must not be blank")
        field_name = f"patch_file_{len(self._layout_patches)}"
        self._layout_patches.append(
            _PendingLayoutPatchFile(
                field_name=field_name,
                page_id=page_id,
                file_name=file_name,
                mime_type="application/xml",
                content=content,
            )
        )


def _ensure_xml_file_name(file_name: str) -> str:
    return file_name if file_name.lower().endswith(".xml") else f"{file_name}.xml"
