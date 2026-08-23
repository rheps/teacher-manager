"""메시지 원문과 판단 근거를 메시지별 파일 하나로 남긴다.

captures.jsonl은 대시보드가 열 때마다 통째로 읽으므로 원문을 넣으면 화면이 느려진다.
원문은 이 기록에서 되살릴 수 없는 유일한 값이라, 분석 도중에 프로그램이 꺼져도 남도록
메시지를 읽어낸 직후에 먼저 쓰고 결과는 끝날 때 붙인다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from brity_bridge import atomic_io

RECORD_VERSION = 1
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def messages_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "messages"


def message_path(state_dir: Path, source_hash: str) -> Path:
    return messages_dir(state_dir) / f"{source_hash}.json"


def _text(value) -> str:
    return str(value or "")


def _names(value) -> list:
    return [_text(name) for name in (value or ())]


def _write(path: Path, document: dict) -> None:
    try:
        atomic_io.atomic_write_text(
            path, json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        )
    except (OSError, ValueError, TypeError):
        pass  # 기록 실패가 등록 자체를 실패시키면 안 된다


def _read(path: Path) -> dict | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def begin(state_dir: Path, record, attachment_dir: str = "") -> None:
    """메시지를 읽어낸 직후 원문을 남긴다. 파일이 이미 있으면 덮지 않는다."""
    source_hash = _text(getattr(record, "source_hash", ""))
    if not source_hash:
        return
    path = message_path(Path(state_dir), source_hash)
    try:
        if path.exists():
            return  # 같은 메시지를 다시 눌렀을 때 원문을 덮으면 안 된다
    except OSError:
        return
    _write(
        path,
        {
            "record_version": RECORD_VERSION,
            "source_hash": source_hash,
            "message": {
                "first_seen": datetime.now().strftime(TIME_FORMAT),
                "sender": _text(getattr(record, "sender", "")),
                "sent_at": _text(getattr(record, "sent_at", "")),
                "plain_text": _text(getattr(record, "plain_text", "")),
                "html": _text(getattr(record, "html", "")),
                "attachment_names": _names(getattr(record, "attachment_names", ())),
                "local_attachment_names": _names(getattr(record, "local_attachment_names", ())),
                "media_part_names": [
                    _text(getattr(part, "name", ""))
                    for part in (getattr(record, "media_parts", ()) or ())
                ],
                "attachment_dir": _text(attachment_dir),
            },
            "runs": [],
        },
    )


def add_run(state_dir: Path, source_hash: str, run: dict) -> None:
    """시도 기록 하나를 붙인다. 파일을 읽지 못하면 기존 파일을 건드리지 않는다."""
    source_hash = _text(source_hash)
    if not source_hash or not isinstance(run, dict):
        return
    path = message_path(Path(state_dir), source_hash)
    document = _read(path)
    if document is None:
        return  # 억지로 새로 쓰다 원문을 잃는 쪽이 더 나쁘다
    runs = document.get("runs")
    document["runs"] = ([*runs] if isinstance(runs, list) else []) + [run]
    _write(path, document)
