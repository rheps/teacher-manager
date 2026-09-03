"""실패 화면이 뜰 때 개발자에게 자동으로 보내는 오류 보고 대기열과 전송기.

보고에는 설계서 2절의 필드만 들어간다. 대기열은 앱 시작 때와 다음 전송 성공 뒤에
다시 비우므로 인터넷이 없어서 난 실패도 나중에 보고된다.
"""
from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from . import paths, recovery

ENDPOINT_PATH = "/v1/error-reports"
QUEUE_NAME = "error-reports.jsonl"
MAX_PENDING = 200
MAX_QUEUE_BYTES = 512 * 1024
MAX_REPORT_BYTES = 8 * 1024
SCHEMA = 1
_TEXT_LIMIT = 1000
# 화면 스레드의 enqueue와 배경 스레드의 flush가 같은 파일을 원자 교체한다.
# Windows에서는 열린 파일을 바꿔치기하지 못하므로 한 프로세스 안에서 순서를 정한다.
_QUEUE_LOCK = threading.Lock()


def _text(value: Any, limit: int = _TEXT_LIMIT) -> str:
    return str(value or "").strip()[:limit]


def build_report(
    issue: recovery.UserIssue,
    *,
    app_version: str,
    windows_version: str,
    account: str,
    teacher: Mapping[str, Any],
    kind: str,
    code: str,
    error_chain: str,
    detail: str,
    now: datetime | None = None,
) -> dict:
    moment = now or datetime.now(recovery.KOREA_TIME)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=recovery.KOREA_TIME)
    return {
        "schema": SCHEMA,
        "reportId": _text(issue.diagnostic_id, 64),
        "at": moment.astimezone(recovery.KOREA_TIME).isoformat(timespec="seconds"),
        "appVersion": _text(app_version, 32),
        "windowsVersion": _text(windows_version, 64),
        "account": _text(account, 200),
        "teacher": {
            "name": _text(teacher.get("name"), 100),
            "school": _text(teacher.get("school"), 100),
            "grade": _text(teacher.get("grade"), 10),
            "class": _text(teacher.get("class"), 10),
        },
        "operation": _text(issue.operation, 64),
        "state": _text(issue.state, 16),
        "attemptCount": int(issue.attempt_count or 0),
        "kind": _text(kind, 32),
        "code": _text(code, 64),
        "reason": _text(issue.reason),
        "errorChain": _text(error_chain, 300),
        "detail": _text(detail),
    }


def report_json_bytes(report: Mapping[str, Any]) -> bytes:
    """8KB에 맞춘다. detail부터, 그다음 reason을 줄인다."""

    trimmed = dict(report)
    for key in ("detail", "reason"):
        while len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8")) > MAX_REPORT_BYTES:
            value = str(trimmed.get(key) or "")
            if not value:
                break
            trimmed[key] = value[: max(0, len(value) // 2)]
    return json.dumps(trimmed, ensure_ascii=False).encode("utf-8")


def queue_path(config_dir: Path) -> Path:
    return paths.bridge_state_dir(config_dir) / QUEUE_NAME


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("report"), dict):
            rows.append(row)
    return rows


def _write_rows(path: Path, rows: list[dict]) -> None:
    from . import atomic_io

    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    atomic_io.atomic_write_text(path, text)


def _trim(rows: list[dict]) -> list[dict]:
    pending_rows = [row for row in rows if row.get("status") == "pending"]
    if len(pending_rows) > MAX_PENDING:
        drop = set(id(row) for row in pending_rows[: len(pending_rows) - MAX_PENDING])
        rows = [row for row in rows if id(row) not in drop]
    while rows and len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) > MAX_QUEUE_BYTES:
        sent_index = next((i for i, row in enumerate(rows) if row.get("status") == "sent"), None)
        if sent_index is None:
            rows = rows[1:]
        else:
            rows = rows[:sent_index] + rows[sent_index + 1:]
    return rows


def enqueue(config_dir: Path, report: Mapping[str, Any]) -> bool:
    """같은 식별번호는 한 번만 넣는다. 넣었으면 True."""

    report_id = str(report.get("reportId") or "")
    if not report_id:
        return False
    path = queue_path(Path(config_dir))
    with _QUEUE_LOCK:
        rows = _read_rows(path)
        if any(str(row["report"].get("reportId") or "") == report_id for row in rows):
            return False
        rows.append({"status": "pending", "report": json.loads(report_json_bytes(report))})
        _write_rows(path, _trim(rows))
    return True


def pending(config_dir: Path) -> list[dict]:
    with _QUEUE_LOCK:
        rows = _read_rows(queue_path(Path(config_dir)))
    return [row["report"] for row in rows if row.get("status") == "pending"]


def flush(config_dir: Path, endpoint: str, poster: Callable[[str, bytes], int]) -> int:
    """대기 중인 보고를 오래된 것부터 보낸다. 첫 실패에서 멈추고 보낸 수를 돌려준다."""

    path = queue_path(Path(config_dir))
    with _QUEUE_LOCK:
        rows = _read_rows(path)
    sent_ids = []
    for row in rows:
        if row.get("status") != "pending":
            continue
        try:
            status = int(poster(endpoint, report_json_bytes(row["report"])))
        except Exception:  # noqa: BLE001 - 통신 실패는 다음 기회에 다시 보낸다.
            break
        if status != 200:
            break
        sent_ids.append(str(row["report"].get("reportId") or ""))
    if not sent_ids:
        return 0
    # 전송하는 동안 화면 스레드가 새 보고를 더했을 수 있으니 다시 읽어 표시만 바꾼다.
    with _QUEUE_LOCK:
        current = _read_rows(path)
        for row in current:
            if str(row["report"].get("reportId") or "") in sent_ids:
                row["status"] = "sent"
        _write_rows(path, _trim(current))
    return len(sent_ids)


def endpoint_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("오류 보고 주소는 https여야 해요")
    return base + ENDPOINT_PATH


def sender_base_url(environ: Mapping[str, str], release_path: Path | None) -> str:
    """환경값이 먼저다. 릴리스 정보 경로가 None이면(소스 실행) 주소를 주지 않는다."""

    override = str(environ.get("CENTRAL_CHAT_SENDER_URL", "") or "").strip()
    if override:
        return override
    if release_path is None:
        return ""
    try:
        data = json.loads(Path(release_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("centralChatSenderUrl", "") or "").strip()


def default_poster(url: str, body: bytes, timeout: float = 5.0) -> int:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "TeacherManager",
        },
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
