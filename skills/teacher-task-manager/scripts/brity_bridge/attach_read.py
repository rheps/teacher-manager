"""첨부파일 내용 읽기 — 화면에서 감지한 파일명을 다운로드 폴더와 맞춰 텍스트를 뽑는다.

읽는 형식: .hwpx(ZIP+XML), .hwp(hwp_text), .xlsx(ZIP+XML). 지원하지 않는 형식은 제외 목록에 남긴다.
무료 등급 토큰·메모리 보호를 위해 파일당 50MB 상한을 둔다 — zip 형식은
압축 해제 크기 기준이라 zip bomb도 부풀리기 전에 거절된다.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
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
_COPY_SUFFIX_RE = re.compile(r"\s+\(\d+\)$")
_ELLIPSIS_RE = re.compile(r"(?:…|\.\.+)+$")


def _zip_inflated_size(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(info.file_size for info in archive.infolist())


def _too_large(path: Path, suffix: str) -> bool:
    if path.stat().st_size > MAX_ATTACHMENT_BYTES:
        return True
    return suffix in _ZIP_SUFFIXES and _zip_inflated_size(path) > MAX_ATTACHMENT_BYTES


@dataclass(frozen=True)
class _AttachmentNameParts:
    literal: str
    compact: str
    copy_literal: str
    copy_compact: str
    suffix: str
    truncated: bool


@dataclass(frozen=True)
class AttachmentBundle:
    block: str
    count: int
    names: tuple[str, ...]
    fingerprints: tuple[str, ...]
    media_parts: tuple[MediaPart, ...] = ()
    skipped_names: tuple[str, ...] = ()


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


def _name_parts(value: str) -> _AttachmentNameParts:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    truncated = bool(_ELLIPSIS_RE.search(normalized))
    normalized = _ELLIPSIS_RE.sub("", normalized).rstrip()
    suffix = Path(normalized).suffix
    if suffix not in SUPPORTED_SUFFIXES:
        suffix = ""
    base = normalized[:-len(suffix)] if suffix else normalized
    truncated = truncated or bool(_ELLIPSIS_RE.search(base))
    base = _ELLIPSIS_RE.sub("", base).strip()
    literal = " ".join(base.split())
    copy_literal = _COPY_SUFFIX_RE.sub("", literal).strip()
    return _AttachmentNameParts(
        literal=literal,
        compact=re.sub(r"\s+", "", literal),
        copy_literal=copy_literal,
        copy_compact=re.sub(r"\s+", "", copy_literal),
        suffix=suffix,
        truncated=truncated,
    )


def find_attachment_file(download_dir: Path, screen_name: str) -> Path | None:
    screen = _name_parts(screen_name)
    if not screen.literal:
        return None
    try:
        children = [c for c in Path(download_dir).iterdir() if c.is_file()]
    except OSError:
        return None
    matches: list[tuple[int, float, Path, _AttachmentNameParts]] = []
    for child in children:
        candidate = _name_parts(child.name)
        if screen.suffix and candidate.suffix != screen.suffix:
            continue

        if candidate.literal == screen.literal:
            match_rank = 0
        elif candidate.copy_literal == screen.literal:
            match_rank = 1
        elif candidate.compact == screen.compact:
            match_rank = 2
        elif candidate.copy_compact == screen.compact:
            match_rank = 3
        elif (not screen.suffix or screen.truncated) and (
            candidate.literal.startswith(screen.literal)
            or candidate.copy_literal.startswith(screen.literal)
        ):
            match_rank = 4
        elif (not screen.suffix or screen.truncated) and (
            candidate.compact.startswith(screen.compact)
            or candidate.copy_compact.startswith(screen.compact)
        ):
            match_rank = 5
        else:
            continue

        mtime = child.stat().st_mtime
        matches.append((match_rank, mtime, child, candidate))

    if not matches:
        return None
    best_match_rank = min(item[0] for item in matches)
    best_matches = [item for item in matches if item[0] == best_match_rank]
    if len(best_matches) == 1:
        return best_matches[0][2]

    canonical_names = {
        (candidate.copy_literal, candidate.suffix)
        for _rank, _mtime, _path, candidate in best_matches
    }
    if len(canonical_names) != 1:
        return None
    return max(best_matches, key=lambda item: item[1])[2]


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


def resolve_attachment_files(download_dir: Path, names: list[str]) -> tuple[Path, ...]:
    """화면을 바꾸기 전에 이번 메시지에 해당하는 실제 파일만 빠르게 고정한다."""
    matches = [(name, find_attachment_file(download_dir, name)) for name in names]
    missing = tuple(name for name, path in matches if path is None)
    if missing:
        raise AttachmentBlocked("missing", missing, "첨부파일을 먼저 내려받아 주세요.")
    return tuple(Path(path) for _name, path in matches)


def prepare_resolved_attachment_bundle(paths: tuple[Path, ...]) -> AttachmentBundle:
    """이미 고정한 파일의 내용 읽기와 지문 계산을 차례대로 끝낸다."""

    sections: list[str] = []
    actual_names: list[str] = []
    fingerprints: list[str] = []
    media_parts: list[MediaPart] = []
    skipped_names: list[str] = []
    for path in paths:
        path = Path(path)
        result = read_attachment(path)
        if not result.ok:
            if result.reason == "unsupported":
                skipped_names.append(path.name)
                continue
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
        skipped_names=tuple(skipped_names),
    )


def prepare_attachment_bundle(download_dir: Path, names: list[str]) -> AttachmentBundle:
    resolved = resolve_attachment_files(download_dir, names)
    return prepare_resolved_attachment_bundle(resolved)


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
