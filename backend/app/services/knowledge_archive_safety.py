from __future__ import annotations

import stat
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree

from fastapi import HTTPException


@dataclass(frozen=True)
class KnowledgeArchiveLimits:
    max_members: int
    max_member_bytes: int
    max_total_expanded_bytes: int
    max_compression_ratio: float = 100.0
    max_xml_bytes: int = 8 * 1024 * 1024
    max_xml_depth: int = 64
    max_xml_nodes: int = 200_000
    max_extracted_text_chars: int = 2_000_000


class SafeKnowledgeArchive:
    def __init__(self, content: bytes, *, limits: KnowledgeArchiveLimits) -> None:
        self._buffer = BytesIO(content)
        try:
            self._archive = zipfile.ZipFile(self._buffer)
        except zipfile.BadZipFile as exc:
            self._buffer.close()
            raise HTTPException(status_code=400, detail="Knowledge OOXML archive is invalid") from exc
        self.limits = limits
        try:
            self._infos = self._validate()
        except Exception:
            self.close()
            raise

    def __enter__(self) -> "SafeKnowledgeArchive":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.close()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._infos))

    def close(self) -> None:
        try:
            self._archive.close()
        finally:
            self._buffer.close()

    def read(self, name: str, *, xml: bool = False) -> bytes:
        info = self._infos.get(name)
        if info is None:
            raise HTTPException(status_code=400, detail="Knowledge OOXML archive member is missing")
        maximum = min(
            self.limits.max_member_bytes,
            self.limits.max_xml_bytes if xml else self.limits.max_member_bytes,
        )
        if info.file_size > maximum:
            raise HTTPException(status_code=400, detail="Knowledge OOXML archive member exceeds the extraction budget")
        try:
            with self._archive.open(info, "r") as handle:
                payload = handle.read(maximum + 1)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail="Knowledge OOXML archive member cannot be read safely") from exc
        if len(payload) > maximum or len(payload) != info.file_size:
            raise HTTPException(status_code=400, detail="Knowledge OOXML archive member exceeds the extraction budget")
        return payload

    def _validate(self) -> dict[str, zipfile.ZipInfo]:
        infos = self._archive.infolist()
        if not infos or len(infos) > self.limits.max_members:
            raise HTTPException(status_code=400, detail="Knowledge OOXML archive has an unsafe member count")
        total_expanded = 0
        validated: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = _validate_member_name(info.filename)
            if name in validated:
                raise HTTPException(status_code=400, detail="Knowledge OOXML archive contains duplicate members")
            if info.flag_bits & 0x1:
                raise HTTPException(status_code=400, detail="Encrypted Knowledge OOXML members are not supported")
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                raise HTTPException(status_code=400, detail="Knowledge OOXML archive links are not supported")
            if info.is_dir():
                validated[name] = info
                continue
            if info.file_size < 0 or info.compress_size < 0 or info.file_size > self.limits.max_member_bytes:
                raise HTTPException(status_code=400, detail="Knowledge OOXML archive member exceeds the extraction budget")
            total_expanded += info.file_size
            if total_expanded > self.limits.max_total_expanded_bytes:
                raise HTTPException(status_code=400, detail="Knowledge OOXML archive exceeds the total extraction budget")
            if info.file_size >= 1024:
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > self.limits.max_compression_ratio:
                    raise HTTPException(status_code=400, detail="Knowledge OOXML archive compression ratio is unsafe")
            validated[name] = info
        return validated


def archive_limits_for_upload(max_upload_bytes: int) -> KnowledgeArchiveLimits:
    upload_limit = max(1, int(max_upload_bytes))
    return KnowledgeArchiveLimits(
        max_members=512,
        max_member_bytes=upload_limit,
        max_total_expanded_bytes=min(max(upload_limit * 4, upload_limit), 64 * 1024 * 1024),
        max_xml_bytes=min(upload_limit, 8 * 1024 * 1024),
        max_extracted_text_chars=min(max(upload_limit, 200_000), 2_000_000),
    )


def parse_bounded_xml(xml_bytes: bytes, *, limits: KnowledgeArchiveLimits):  # noqa: ANN201
    if len(xml_bytes) > limits.max_xml_bytes:
        raise HTTPException(status_code=400, detail="Knowledge XML member exceeds the parsing budget")
    upper_prefix = xml_bytes[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise HTTPException(status_code=400, detail="Knowledge XML declarations are not supported")
    try:
        root = ElementTree.fromstring(xml_bytes)
    except (ElementTree.ParseError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Knowledge XML member is invalid") from exc
    node_count = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > limits.max_xml_nodes:
            raise HTTPException(status_code=400, detail="Knowledge XML member exceeds the node budget")
        if depth > limits.max_xml_depth:
            raise HTTPException(status_code=400, detail="Knowledge XML member exceeds the depth budget")
        children = list(node)
        stack.extend((child, depth + 1) for child in children)
    return root


def enforce_extracted_text_budget(value: str, *, limits: KnowledgeArchiveLimits) -> str:
    if len(value) > limits.max_extracted_text_chars:
        raise HTTPException(status_code=400, detail="Knowledge document text exceeds the extraction budget")
    return value


def _validate_member_name(value: str) -> str:
    name = str(value or "")
    if not name or "\\" in name or "\x00" in name:
        raise HTTPException(status_code=400, detail="Knowledge OOXML archive member path is invalid")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=400, detail="Knowledge OOXML archive member path is invalid")
    normalized = str(path)
    if len(normalized) > 512:
        raise HTTPException(status_code=400, detail="Knowledge OOXML archive member path is too long")
    return normalized
