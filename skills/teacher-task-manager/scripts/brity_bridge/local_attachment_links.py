from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from brity_bridge.proposal_check import BODY_MAX, CheckError, CheckedAction

LOCAL_ATTACHMENT_HEADER = "📎 첨부파일"
LOCAL_ATTACHMENT_HOST = "127.0.0.1"
LOCAL_ATTACHMENT_PORT = 49271
SHORT_LINK_ID_LENGTH = 16
_SOURCE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SHORT_LINK_RE = re.compile(r"^https?://127\.0\.0\.1:49271/a/[0-9a-f]{16}$")
_LEGACY_LINK_PREFIX = f"http://{LOCAL_ATTACHMENT_HOST}:{LOCAL_ATTACHMENT_PORT}/open?name="
WINDOWS_PACKAGE_SUFFIXES = {
    ".appinstaller", ".appx", ".appxbundle", ".appxupload",
    ".msix", ".msixbundle", ".msixupload",
}
PYTHON_LAUNCH_SUFFIXES = {".py", ".pyc", ".pyo", ".pyw", ".pyz", ".pyzw"}
BLOCKED_SUFFIXES = WINDOWS_PACKAGE_SUFFIXES | PYTHON_LAUNCH_SUFFIXES | {
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".scr", ".msi", ".msp", ".mst",
    ".reg", ".lnk", ".url", ".hta", ".cpl", ".chm", ".jar",
    ".ade", ".adp", ".application", ".appref-ms", ".bas", ".gadget",
    ".inf", ".ins", ".isp", ".job", ".mde", ".msc", ".msh", ".msh1",
    ".msh2", ".mshxml", ".msh1xml", ".msh2xml", ".ocx", ".pcd", ".pif",
    ".scf", ".sct", ".shb", ".shs", ".vb", ".ws", ".wsc",
}


class InvalidAttachmentName(Exception):
    pass


class AttachmentNotFound(Exception):
    pass


class BlockedAttachmentType(Exception):
    pass


def build_local_attachment_url(source_hash: str) -> str:
    normalized = str(source_hash or "").casefold()
    if not _SOURCE_HASH_RE.fullmatch(normalized):
        raise ValueError("invalid source hash")
    short_id = normalized[:SHORT_LINK_ID_LENGTH]
    return f"http://{LOCAL_ATTACHMENT_HOST}:{LOCAL_ATTACHMENT_PORT}/a/{short_id}"


def _without_existing_attachment_lines(description: object, names: tuple[str, ...]) -> str:
    exact_names = {str(name) for name in names}
    kept: list[str] = []
    for line in str(description or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(LOCAL_ATTACHMENT_HEADER):
            continue
        if stripped in exact_names:
            continue
        if stripped.startswith("- ") and stripped[2:].strip() in exact_names:
            continue
        if stripped.startswith(_LEGACY_LINK_PREFIX) or _SHORT_LINK_RE.fullmatch(stripped):
            continue
        if not stripped and (not kept or not kept[-1].strip()):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).rstrip()


def resolve_local_attachment(download_dir: Path, name: str) -> Path:
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ":" in name
        or name.endswith((".", " "))
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or Path(name).name != name
    ):
        raise InvalidAttachmentName
    configured_root = Path(download_dir)
    if not configured_root.is_absolute():
        raise AttachmentNotFound
    try:
        root = configured_root.resolve(strict=True)
        if not root.is_dir():
            raise AttachmentNotFound
        entries = list(root.iterdir())
    except (OSError, RuntimeError) as error:
        raise AttachmentNotFound from error
    exact_entry = next((entry for entry in entries if entry.name == name), None)
    if exact_entry is None:
        if any(entry.name.casefold() == name.casefold() for entry in entries):
            raise InvalidAttachmentName
        raise AttachmentNotFound
    try:
        candidate = exact_entry.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise AttachmentNotFound from error
    if candidate.parent != root:
        raise InvalidAttachmentName
    if not candidate.is_file():
        raise AttachmentNotFound
    if candidate.suffix.casefold() in BLOCKED_SUFFIXES:
        raise BlockedAttachmentType
    return candidate


def add_local_attachment_links(
    actions: list[CheckedAction], names: tuple[str, ...], source_hash: str
) -> list[CheckedAction]:
    if not names:
        return list(actions)
    if not any(action.kind == "calendar" for action in actions):
        return list(actions)
    try:
        link = build_local_attachment_url(source_hash)
    except ValueError as error:
        raise CheckError(["첨부파일 연결 주소를 만들 수 없음"]) from error
    unique_names = tuple(dict.fromkeys(str(name) for name in names))
    attachment_lines = [LOCAL_ATTACHMENT_HEADER]
    attachment_lines.extend(f"- {name}" for name in unique_names)
    attachment_lines.append(link)
    attachment_block = "\n".join(attachment_lines)
    result: list[CheckedAction] = []
    for action in actions:
        if action.kind != "calendar":
            result.append(action)
            continue
        payload = dict(action.payload)
        old_description = _without_existing_attachment_lines(
            payload.get("description", ""), unique_names
        )
        payload["description"] = (
            f"{old_description}\n\n{attachment_block}"
            if old_description
            else attachment_block
        )
        if len(payload["description"]) > BODY_MAX:
            raise CheckError(["첨부파일 안내를 넣으면 일정 설명이 너무 길어짐"])
        payload.pop("attachments", None)
        result.append(replace(action, payload=payload))
    return result
