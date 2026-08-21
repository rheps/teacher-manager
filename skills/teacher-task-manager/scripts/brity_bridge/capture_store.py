from __future__ import annotations

import json
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from brity_bridge import atomic_io

# 리치 기록(등록 항목 제목 포함)과 진행 상태는 설정폴더 로컬 파일에만 남는다. 외부 전송 없음.
PROGRESS_STEPS = ("capture", "analyze", "register", "done", "fail")
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ACTIVE_MAX_AGE = timedelta(minutes=3)
FINISHED_LINGER = timedelta(seconds=10)


def captures_path(state_dir: Path) -> Path:
    return Path(state_dir) / "captures.jsonl"


def progress_path(state_dir: Path) -> Path:
    return Path(state_dir) / "progress.json"


def _tail_missing_newline(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                return False
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def append_capture(state_dir: Path, entry: dict) -> Path:
    path = captures_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 크래시로 잘린 꼬리(개행 없음)에 새 기록이 한 줄로 병합되면 새 기록이
    # 파싱 불가로 조용히 사라진다 — 개행을 보정하고 이어 쓴다.
    lead = "\n" if _tail_missing_newline(path) else ""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(lead + json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def _capture_rows(state_dir: Path):
    try:
        handle = captures_path(state_dir).open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except ValueError:
                continue  # 깨진 줄은 그 줄만 건너뛴다
            if isinstance(parsed, dict):
                yield parsed


def read_captures(state_dir: Path, limit: int = 20) -> list[dict]:
    wanted = max(0, int(limit))
    if wanted == 0:
        return []
    rows = deque(maxlen=wanted)
    rows.extend(_capture_rows(state_dir))
    return list(reversed(rows))


def read_capture_page(state_dir: Path, page: int = 1, page_size: int = 10) -> dict:
    size = max(1, int(page_size))
    total = sum(1 for _ in _capture_rows(state_dir))
    total_pages = (total + size - 1) // size
    current = min(max(1, int(page)), total_pages or 1)

    newest_end = total - ((current - 1) * size)
    newest_start = max(0, newest_end - size)
    selected = [
        row for index, row in enumerate(_capture_rows(state_dir))
        if newest_start <= index < newest_end
    ]
    selected.reverse()
    return {
        "items": selected,
        "page": current,
        "page_size": size,
        "total": total,
        "total_pages": total_pages,
    }


class ProgressWriter:
    """캡처 1회의 진행 단계를 progress.json에 남긴다. 쓰기 실패가 캡처를 막으면 안 된다.

    run_id는 시작 시각과 무작위 꼬리를 함께 쓴다. 맡겨 둔 메시지가 아주 빠르게
    이어져 같은 초에 시작돼도 대시보드가 서로 다른 처리로 알아봐야 한다.
    """

    def __init__(self, state_dir: Path, now: datetime | None = None):
        self.state_dir = Path(state_dir)
        started = now or datetime.now()
        self.run_id = f"cap-{int(started.timestamp())}-{uuid.uuid4().hex[:8]}"

    def emit(self, step: str, message: str = "", now: datetime | None = None) -> None:
        if step not in PROGRESS_STEPS:
            raise ValueError(f"unknown step: {step}")
        body = {
            "run_id": self.run_id,
            "step": step,
            "when": (now or datetime.now()).strftime(TIME_FORMAT),
        }
        if message:
            body["message"] = message
        try:
            # 대시보드가 캡처 중 0.4초 간격으로 읽는다 — 제자리 쓰기면
            # truncate~write 사이 읽기가 빈 파일을 보고 진행 카드를 지운다.
            atomic_io.atomic_write_text(
                progress_path(self.state_dir), json.dumps(body, ensure_ascii=False) + "\n"
            )
        except OSError:
            pass


def read_progress(state_dir: Path, now: datetime | None = None) -> dict:
    inactive = {"active": False}
    try:
        raw = json.loads(progress_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return inactive
    if not isinstance(raw, dict):
        return inactive
    try:
        written = datetime.strptime(str(raw.get("when", "")), TIME_FORMAT)
    except ValueError:
        return inactive
    age = (now or datetime.now()) - written
    if age > ACTIVE_MAX_AGE:
        return inactive  # 헬퍼 강제 종료 등으로 오래 남은 상태
    if raw.get("step") in ("done", "fail") and age > FINISHED_LINGER:
        return inactive
    return {
        "active": True,
        "run_id": str(raw.get("run_id", "")),
        "step": str(raw.get("step", "")),
        "when": str(raw.get("when", "")),
        "message": str(raw.get("message", "")),
    }
