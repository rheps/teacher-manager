"""첨부파일 내용 읽기 — 화면에서 감지한 파일명을 다운로드 폴더와 맞춰 텍스트를 뽑는다.

읽는 형식: .hwpx(ZIP+XML), .hwp(hwp_text), .xlsx(ZIP+XML). 나머지는 파일명만.
무료 등급 토큰·메모리 보호를 위해 파일당 50MB 상한을 둔다 — zip 형식은
압축 해제 크기 기준이라 zip bomb도 부풀리기 전에 거절된다.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from brity_bridge import hwp_text, office_read
from brity_bridge.message_parse import MediaPart

UNSUPPORTED_MESSAGE = "아직 읽을 수 없는 첨부파일 형식이에요."
BROKEN_MESSAGE = "첨부파일을 읽을 수 없어요. 파일의 암호나 상태를 확인해 주세요."
TOO_LARGE_MESSAGE = "첨부파일이 너무 커서 읽을 수 없어요."
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
SUPPORTED_SUFFIXES = {
    ".hwp", ".hwpx", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".txt", ".csv", ".jpg", ".jpeg", ".png",
}
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024  # PDF와 같은 상한 — zip은 압축 해제 크기 기준
_ZIP_SUFFIXES = {".hwpx", ".docx", ".xlsx", ".pptx"}


def _zip_inflated_size(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(info.file_size for info in archive.infolist())


def _too_large(path: Path, suffix: str) -> bool:
    if path.stat().st_size > MAX_ATTACHMENT_BYTES:
        return True
    return suffix in _ZIP_SUFFIXES and _zip_inflated_size(path) > MAX_ATTACHMENT_BYTES


@dataclass(frozen=True)
class AttachmentBundle:
    block: str
    count: int
    names: tuple[str, ...]
    fingerprints: tuple[str, ...]
    media_parts: tuple[MediaPart, ...] = ()


@dataclass(frozen=True)
class AttachmentReadResult:
    ok: bool
    text: str = ""
    media: MediaPart | None = None
    reason: str = ""
    message: str = ""


class AttachmentBlocked(Exception):
    def __init__(self, reason: str, names: tuple[str, ...], message: str):
        super().__init__(message)
        self.reason = reason
        self.names = names
        self.message = message


def find_attachment_file(download_dir: Path, screen_name: str) -> Path | None:
    prefix = screen_name.strip().rstrip("…").strip()
    if not prefix:
        return None
    try:
        children = [c for c in Path(download_dir).iterdir() if c.is_file()]
    except OSError:
        return None
    best: Path | None = None
    best_mtime = -1.0
    for child in children:
        # 화면 이름은 잘릴 수 있고(전방일치), 짧은 이름은 확장자까지 보일 수 있다(역방향).
        if not (child.stem.startswith(prefix) or prefix.startswith(child.stem)):
            continue
        mtime = child.stat().st_mtime
        if mtime > best_mtime:
            best, best_mtime = child, mtime
    return best


def extract_hwpx_text(path: Path) -> str:
    texts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            n for n in archive.namelist()
            if "section" in n.lower() and n.lower().endswith(".xml")
        )
        for name in names:
            root = ET.fromstring(archive.read(name))
            for element in root.iter():
                if element.text and element.text.strip():
                    texts.append(element.text.strip())
    return " ".join(texts)


def extract_xlsx_text(path: Path) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall("{*}si"):
                shared.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
        sheet_names = sorted(
            n for n in archive.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        )
        for name in sheet_names:
            root = ET.fromstring(archive.read(name))
            for row in root.findall(".//{*}row"):
                cells: list[str] = []
                for cell in row.findall("{*}c"):
                    value = cell.find("{*}v")
                    if value is not None and value.text is not None:
                        if cell.get("t") == "s" and value.text.isdigit():
                            index = int(value.text)
                            cells.append(shared[index] if 0 <= index < len(shared) else "")
                        else:
                            cells.append(value.text)
                        continue
                    inline = "".join(t.text or "" for t in cell.iter() if t.tag.endswith("}t"))
                    if inline:
                        cells.append(inline)
                if any(cell.strip() for cell in cells):
                    lines.append("\t".join(cells))
    return "\n".join(lines)


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, 1, "지원하지 않는 글자 인코딩")


def _read_pdf(path: Path) -> AttachmentReadResult:
    from pypdf import PdfReader

    if path.stat().st_size > 50 * 1024 * 1024:
        return AttachmentReadResult(False, reason="too-large", message=TOO_LARGE_MESSAGE)
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        return AttachmentReadResult(False, reason="unreadable", message=BROKEN_MESSAGE)
    if len(reader.pages) > 1000:
        return AttachmentReadResult(False, reason="too-large", message=TOO_LARGE_MESSAGE)
    text = "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    if len(text) >= 20:
        return AttachmentReadResult(True, text=text)
    return AttachmentReadResult(
        True,
        media=MediaPart(path.name, "application/pdf", path.read_bytes()),
    )


def read_attachment(path: Path) -> AttachmentReadResult:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return AttachmentReadResult(False, reason="unsupported", message=UNSUPPORTED_MESSAGE)
    try:
        if suffix != ".pdf" and _too_large(path, suffix):
            return AttachmentReadResult(False, reason="too-large", message=TOO_LARGE_MESSAGE)
        if suffix == ".hwpx":
            return AttachmentReadResult(True, text=extract_hwpx_text(path))
        if suffix == ".xlsx":
            return AttachmentReadResult(True, text=extract_xlsx_text(path))
        if suffix == ".hwp":
            ok, text = hwp_text.extract_hwp_text(path.read_bytes())
            return AttachmentReadResult(ok, text=text if ok else "", reason="unreadable", message=BROKEN_MESSAGE)
        if suffix == ".docx":
            return AttachmentReadResult(True, text=office_read.extract_docx_text(path))
        if suffix == ".pptx":
            return AttachmentReadResult(True, text=office_read.extract_pptx_text(path))
        if suffix in (".txt", ".csv"):
            return AttachmentReadResult(True, text=_read_text_file(path))
        if suffix == ".pdf":
            return _read_pdf(path)
        if suffix in IMAGE_MIME:
            return AttachmentReadResult(
                True, media=MediaPart(path.name, IMAGE_MIME[suffix], path.read_bytes())
            )
        if suffix in (".doc", ".xls", ".ppt"):
            legacy = office_read.extract_legacy_office_text(path)
            return AttachmentReadResult(
                legacy.ok,
                text=legacy.text,
                reason="office-required" if legacy.message == office_read.OFFICE_REQUIRED_MESSAGE else "unreadable",
                message=legacy.message,
            )
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, ImportError, RuntimeError):
        # RuntimeError: zipfile이 암호 걸린 항목을 읽을 때 — .hwp의 암호 안내와 같은 문구로.
        return AttachmentReadResult(False, reason="unreadable", message=BROKEN_MESSAGE)
    return AttachmentReadResult(False, reason="unsupported", message=UNSUPPORTED_MESSAGE)


def read_attachment_text(path: Path) -> tuple[bool, str]:
    result = read_attachment(path)
    if not result.ok:
        return False, result.message
    if result.text:
        return True, result.text
    return True, "(파일 화면을 함께 읽음)"


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_attachment_bundle(download_dir: Path, names: list[str]) -> AttachmentBundle:
    matches = [(name, find_attachment_file(download_dir, name)) for name in names]
    missing = tuple(name for name, path in matches if path is None)
    if missing:
        raise AttachmentBlocked("missing", missing, "첨부파일을 먼저 내려받아 주세요.")

    sections: list[str] = []
    actual_names: list[str] = []
    fingerprints: list[str] = []
    media_parts: list[MediaPart] = []
    for _screen_name, found in matches:
        path = Path(found)
        result = read_attachment(path)
        if not result.ok:
            raise AttachmentBlocked(result.reason, (path.name,), result.message)
        content = result.text.strip() if result.text else "(파일 화면을 함께 읽음)"
        sections.append(f"[첨부파일: {path.name}]\n{content}")
        if result.media is not None:
            media_parts.append(result.media)
        actual_names.append(path.name)
        fingerprints.append(_fingerprint(path))
    return AttachmentBundle(
        block="\n\n".join(sections),
        count=len(actual_names),
        names=tuple(actual_names),
        fingerprints=tuple(fingerprints),
        media_parts=tuple(media_parts),
    )


def build_attachment_block(download_dir: Path, names: list[str]) -> tuple[str, int]:
    """본문 뒤에 붙일 [첨부파일: …] 블록과 미다운로드 수를 돌려준다."""
    if not names:
        return "", 0
    sections: list[str] = []
    missing = 0
    for name in names:
        path = find_attachment_file(download_dir, name)
        if path is None:
            missing += 1
            sections.append(f"[첨부파일: {name}]\n(아직 다운로드하지 않아 내용은 못 읽음 — 받아두면 내용까지 읽어요)")
            continue
        ok, text = read_attachment_text(path)
        if not ok:
            sections.append(f"[첨부파일: {path.name}]\n({text})")
            continue
        sections.append(f"[첨부파일: {path.name}]\n{text.strip()}")
    return "\n\n".join(sections), missing
