from __future__ import annotations

import errno
import json
from datetime import datetime
from pathlib import Path

from brity_bridge import atomic_io

# 로그에는 이 단계 이름과 짧은 코드만 남긴다. 메시지 본문·개인정보는 절대 넣지 않는다.
STAGES = (
    "foreground",
    "menu",
    "clipboard",
    "capture",
    "parse",
    "attachment",
    "attachment-link",
    "duplicate",
    "profile",
    "gemini",
    "check",
    "execute",
    "done",
)

_HASH_PREFIX_LEN = 12
_DETAIL_MAX_LEN = 200
ATTACHMENT_LINK_PORT_IN_USE_DETAIL = (
    "port-in-use · 첨부파일 링크용 연결 자리를 다른 프로그램이 사용 중입니다. "
    "파일 링크만 사용할 수 없고 Brity 등록은 계속됩니다."
)
ATTACHMENT_LINK_START_ERROR_DETAIL = (
    "start-error · 첨부파일 링크를 열 준비를 하지 못했습니다. "
    "파일 링크만 사용할 수 없고 Brity 등록은 계속됩니다."
)
ATTACHMENT_LINK_PORT_IN_USE_MESSAGE = (
    "다른 프로그램이 첨부파일을 여는 자리를 사용하고 있습니다. "
    "다른 프로그램을 닫고 Teacher Manager를 다시 시작해 주세요."
)
ATTACHMENT_LINK_START_ERROR_MESSAGE = (
    "이 컴퓨터에서 첨부파일 열기 준비에 실패했습니다. "
    "Teacher Manager를 다시 시작해 주세요."
)


def attachment_link_start_failure_detail(error: BaseException) -> str:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            getattr(current, "errno", None) == errno.EADDRINUSE
            or getattr(current, "winerror", None) == 10048
        ):
            return ATTACHMENT_LINK_PORT_IN_USE_DETAIL
        current = current.__cause__ or current.__context__
    return ATTACHMENT_LINK_START_ERROR_DETAIL


def attachment_link_start_failure_message(error: BaseException) -> str:
    detail = attachment_link_start_failure_detail(error)
    return (
        ATTACHMENT_LINK_PORT_IN_USE_MESSAGE
        if detail == ATTACHMENT_LINK_PORT_IN_USE_DETAIL
        else ATTACHMENT_LINK_START_ERROR_MESSAGE
    )


def append_log(logs_dir: Path, ok: bool, stage: str, source_hash: str, detail: str = "") -> Path:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = logs_dir / f"bridge-{now:%Y-%m-%d}.log"
    clean_detail = " ".join(str(detail).split())[:_DETAIL_MAX_LEN]
    line = "\t".join(
        [
            f"{now:%Y-%m-%d %H:%M:%S}",
            "OK" if ok else "FAIL",
            stage,
            (source_hash or "")[:_HASH_PREFIX_LEN],
            clean_detail,
        ]
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def write_last_status(state_dir: Path, summary: dict) -> None:
    atomic_io.atomic_write_text(
        Path(state_dir) / "last-status.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )


def read_last_status(state_dir: Path) -> dict | None:
    try:
        raw = json.loads((Path(state_dir) / "last-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
