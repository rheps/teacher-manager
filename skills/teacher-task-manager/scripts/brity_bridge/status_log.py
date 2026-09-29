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
    "첨부파일을 바로 여는 기능을 켜지 못했어요. "
    "파일은 Brity 다운로드 폴더에서 직접 열 수 있습니다."
)
ATTACHMENT_LINK_START_ERROR_MESSAGE = (
    "첨부파일을 바로 여는 기능을 켜지 못했어요. "
    "파일은 Brity 다운로드 폴더에서 직접 열 수 있습니다."
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


# 도우미(TeacherManagerHelper.exe)가 켜지고 끝난 까닭 (PEND-43, R7-3).
# 끝난 까닭 줄이 없이 시작 줄만 남으면 밖에서 강제로 끝났거나 프로그램 자체가 멈춘 것이다.
HELPER_EVENTS = ("start", "already-running", "stop-requested", "session-end", "exception", "exit")


def helper_log_path(logs_dir: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    return Path(logs_dir) / f"helper-{now:%Y-%m-%d}.log"


def append_helper_event(logs_dir: Path, event: str, detail: str = "", pid: int | None = None) -> None:
    """도우미 시작·종료 까닭을 한 줄 남긴다. 기록 실패는 도우미를 막지 않는다."""
    import os

    if event not in HELPER_EVENTS:
        event = "exit"
    try:
        now = datetime.now()
        path = helper_log_path(logs_dir, now)
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = " ".join(str(detail).split())[:_DETAIL_MAX_LEN]
        line = "\t".join([f"{now:%Y-%m-%d %H:%M:%S}", str(pid or os.getpid()), event, clean])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001 - 기록 실패가 도우미 시작·종료를 막으면 안 된다
        pass


def exception_site(error: BaseException) -> str:
    """예외 종류와 마지막 코드 위치(파일 이름:줄)만 돌려준다. 예외 문장은 넣지 않는다."""
    import os
    import traceback

    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    site = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else "-"
    return f"{type(error).__name__} {site}"
