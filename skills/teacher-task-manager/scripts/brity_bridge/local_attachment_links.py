from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

from brity_bridge.proposal_check import BODY_MAX, CheckError, CheckedAction

LOCAL_ATTACHMENT_HEADER = "📎 첨부파일 (컴퓨터에서만 열림)"
LOCAL_ATTACHMENT_HOST = "127.0.0.1"
LOCAL_ATTACHMENT_PORT = 49271
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


def build_local_attachment_url(name: str) -> str:
    query = urlencode({"name": name})
    return f"http://{LOCAL_ATTACHMENT_HOST}:{LOCAL_ATTACHMENT_PORT}/open?{query}"


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
    actions: list[CheckedAction], names: tuple[str, ...]
) -> list[CheckedAction]:
    if not names:
        return list(actions)
    if not any(action.kind == "calendar" for action in actions):
        return list(actions)
    attachment_lines = [LOCAL_ATTACHMENT_HEADER]
    for name in names:
        attachment_lines.extend((name, build_local_attachment_url(name)))
    attachment_block = "\n".join(attachment_lines)
    result: list[CheckedAction] = []
    for action in actions:
        if action.kind != "calendar":
            result.append(action)
            continue
        payload = dict(action.payload)
        old_description = str(payload.get("description", "")).rstrip()
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
