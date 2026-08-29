from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

KST = timezone(timedelta(hours=9))

# Brity 일반 복사 머리말: "[보낸 사람] YYYY-MM-DD HH:mm" (초는 있어도 되고 없어도 된다)
_HEADER_RE = re.compile(
    r"^\[(?P<sender>[^\[\]]{1,80})\]\s+(?P<stamp>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}(?::\d{2})?)\s*$"
)
_PLACEHOLDER_TEXT = "copied text"


@dataclass(frozen=True)
class MediaPart:
    name: str
    mime_type: str
    data: bytes


@dataclass
class MessageRecord:
    source: str
    sender: str
    sent_at: str
    plain_text: str
    html: str
    source_hash: str
    attachment_count: int = 0
    attachment_names: tuple[str, ...] = ()
    media_parts: tuple[MediaPart, ...] = ()
    local_attachment_names: tuple[str, ...] = ()
    screen_attempt_count: int = 0
    attachment_attempt_count: int = 0


def compute_source_hash(sender: str, sent_at: str, plain_text: str) -> str:
    joined = "\n".join([sender, sent_at, plain_text])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _display_text(value: str, limit: int) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = "".join(
        " " if character.isspace() else character
        for character in normalized
        if character.isspace() or not unicodedata.category(character).startswith("C")
    )
    compact = " ".join(cleaned.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def message_identity(record: MessageRecord) -> dict[str, str]:
    """Return only the short, local display fields needed to identify a message."""

    sent_at = _display_text(record.sent_at, 64)
    try:
        datetime.fromisoformat(sent_at)
    except (TypeError, ValueError):
        sent_at = ""
    return {
        "sender": _display_text(record.sender, 40),
        "sent_at": sent_at,
        "preview": _display_text(record.plain_text, 60),
    }


def build_cf_html_bytes(fragment_html: str) -> bytes:
    """CF_HTML(클립보드 HTML Format) 바이트를 만든다. 오프셋은 바이트 기준."""
    prefix = "<html><body>\r\n<!--StartFragment-->"
    suffix = "<!--EndFragment-->\r\n</body></html>"
    header_template = (
        "Version:0.9\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\n"
        "StartFragment:{:010d}\r\nEndFragment:{:010d}\r\n"
    )
    header_len = len(header_template.format(0, 0, 0, 0).encode("utf-8"))
    prefix_b = prefix.encode("utf-8")
    fragment_b = fragment_html.encode("utf-8")
    suffix_b = suffix.encode("utf-8")
    start_html = header_len
    start_fragment = header_len + len(prefix_b)
    end_fragment = start_fragment + len(fragment_b)
    end_html = end_fragment + len(suffix_b)
    header = header_template.format(start_html, end_html, start_fragment, end_fragment)
    return header.encode("utf-8") + prefix_b + fragment_b + suffix_b


def extract_cf_html_fragment(raw: bytes) -> str:
    """CF_HTML 헤더의 StartFragment/EndFragment 바이트 오프셋으로 조각을 꺼낸다."""
    if not raw:
        return ""
    head = raw[:2048].decode("ascii", errors="replace")
    start_match = re.search(r"StartFragment:(\d+)", head)
    end_match = re.search(r"EndFragment:(\d+)", head)
    if start_match and end_match:
        start, end = int(start_match.group(1)), int(end_match.group(1))
        if 0 <= start < end <= len(raw):
            return raw[start:end].decode("utf-8", errors="replace")
    decoded = raw.decode("utf-8", errors="replace")
    marker_start = decoded.find("<!--StartFragment-->")
    marker_end = decoded.find("<!--EndFragment-->")
    if marker_start != -1 and marker_end != -1:
        return decoded[marker_start + len("<!--StartFragment-->") : marker_end]
    return decoded


class _HtmlTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._current: list[str] = []
        self._row_cells: list[str] | None = None

    def _flush_current(self):
        text = "".join(self._current).strip()
        self._current = []
        if text:
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            if self._row_cells is not None:
                self._current.append(" ")
            else:
                self._flush_current()
        elif tag == "tr":
            self._flush_current()
            self._row_cells = []
        elif tag in ("td", "th") and self._row_cells is not None:
            self._row_cells.append("".join(self._current).strip())
            self._current = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row_cells is not None:
            self._row_cells.append("".join(self._current).strip())
            self._current = []
        elif tag == "tr" and self._row_cells is not None:
            cells = [cell for cell in self._row_cells + ["".join(self._current).strip()] if cell]
            self._current = []
            self._row_cells = None
            if cells:
                self.lines.append(" | ".join(cells))
        elif tag in ("p", "div", "li", "table", "h1", "h2", "h3", "h4"):
            self._flush_current()

    def handle_data(self, data):
        self._current.append(data)

    def text(self) -> str:
        self._flush_current()
        return "\n".join(self.lines).strip()


def html_fragment_to_text(html: str) -> str:
    extractor = _HtmlTextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text()


def _split_header(text: str) -> tuple[str, str, str]:
    lines = text.split("\n")
    match = _HEADER_RE.match(lines[0].strip()) if lines else None
    if not match:
        return "", "", text.strip("\n")
    stamp = match.group("stamp")
    fmt = "%Y-%m-%d %H:%M:%S" if stamp.count(":") == 2 else "%Y-%m-%d %H:%M"
    sent = datetime.strptime(stamp, fmt).replace(tzinfo=KST)
    body = "\n".join(lines[1:]).strip("\n")
    return match.group("sender").strip(), sent.isoformat(), body


def parse_clipboard_message(text: str | None, html_raw: bytes | None, now: datetime) -> MessageRecord:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    fragment = extract_cf_html_fragment(html_raw) if html_raw else ""
    use_html = bool(fragment) and (not text.strip() or text.strip().lower() == _PLACEHOLDER_TEXT)
    base_text = html_fragment_to_text(fragment) if use_html else text
    sender, sent_at, body = _split_header(base_text)
    if not body.strip():
        raise ValueError("빈 메시지: 클립보드에 등록할 내용이 없습니다")
    kept_html = fragment if use_html and "<table" in fragment.lower() else ""
    return MessageRecord(
        source="brity-copy",
        sender=sender,
        sent_at=sent_at,
        plain_text=body,
        html=kept_html,
        source_hash=compute_source_hash(sender, sent_at, body),
    )
