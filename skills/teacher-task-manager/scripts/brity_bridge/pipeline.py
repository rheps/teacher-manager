from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from attendance_install_record import (
    attendance_script_is_attested,
    load_attendance_install_record,
)
from attendance_script_update import (
    inspect_attendance_script_update,
    target_bundle_sha256,
)
from brity_bridge import bundle_paths, capture_store, paths, status_log
from brity_bridge.gemini_analyze import AnalysisError, run_gemini_analysis
from brity_bridge.gws_exec import (
    RESULT_RECORD_FAILURE_DETAIL,
    ExecutionReport,
    execute_actions,
)
from brity_bridge.history import HistoryStore
from brity_bridge.local_attachment_links import (
    LOCAL_ATTACHMENT_HEADER,
    add_local_attachment_links,
)
from brity_bridge.message_parse import (
    MessageRecord,
    compute_source_hash,
    message_identity,
    parse_clipboard_message,
)
from brity_bridge.proposal_check import (
    BODY_MAX,
    CheckError,
    add_work_task_copies,
    check_proposal,
)

GEMINI_FAILURE_MESSAGES = {
    "key-missing": "Gemini API 키가 없습니다. 설정 대시보드에서 키를 넣어 주세요.",
    "key-invalid": "API 키가 맞지 않습니다. 설정 대시보드에서 키를 다시 확인해 주세요.",
    "rate-limited": "요청 한도에 걸렸습니다. 1~2분 뒤 다시, 계속되면 내일 다시 시도해 주세요.",
    "network": "인터넷 연결(학교 방화벽)을 확인해 주세요.",
    "shape": "분석 결과가 올바르지 않아 등록하지 않았습니다. 다시 시도해 주세요.",
}

# 기록 목록에 보여줄 대상 이름 — profile에 표시 이름이 없을 때의 대체 표기.
TARGET_FALLBACK_LABELS = {
    "work_calendar": "업무 캘린더",
    "school_calendar": "학사일정 캘린더",
    "work_tasks": "업무 할 일",
    "homeroom_tasks": "담임 안내 할 일",
}
KOREA_TIME = timezone(timedelta(hours=9), name="KST")
_CALENDAR_TIME_LINE_RE = re.compile(
    r"^[ \t]*(?:🕒[ \t]*등록한 시각|📁[ \t]*처리 시각)[ \t]*:[^\r\n]*$"
)
RETRY_MESSAGE = capture_store.CHECK_RETRY
CURRENT_UNREAD_PREVIEW = "메시지 내용을 읽지 못해 어떤 메시지인지 확인할 수 없음"
ATTACHMENT_HELPER_FAILURE_MESSAGE = (
    "일정에 파일 링크는 넣었지만 이 컴퓨터에서 파일 열기 준비에 실패했습니다. "
    "Teacher Manager를 다시 시작해 주세요."
)
ATTACHMENT_HELPER_PORT_MESSAGE = (
    "일정에 파일 링크는 넣었지만 다른 프로그램이 파일 열기 자리를 사용하고 있습니다. "
    "다른 프로그램을 닫고 Teacher Manager를 다시 시작해 주세요."
)

@dataclass
class CaptureContext:
    clipboard_text: str | None
    clipboard_html: bytes | None


@dataclass
class FlowResult:
    ok: bool
    stage: str
    message: str
    report: ExecutionReport | None = None


def _without_calendar_time_lines(description: object) -> str:
    return "\n".join(
        line
        for line in str(description or "").splitlines()
        if not _CALENDAR_TIME_LINE_RE.fullmatch(line)
    ).rstrip()


def _remove_calendar_time_lines(actions: list) -> list:
    result = []
    for action in actions:
        if action.kind != "calendar":
            result.append(action)
            continue
        payload = dict(action.payload)
        payload["description"] = _without_calendar_time_lines(
            payload.get("description", "")
        )
        result.append(replace(action, payload=payload))
    return result


def _add_calendar_registration_time(actions: list, now: datetime) -> list:
    registered_at = (
        now.replace(tzinfo=KOREA_TIME)
        if now.tzinfo is None
        else now.astimezone(KOREA_TIME)
    )
    time_line = f"🕒 등록한 시각: {registered_at:%Y-%m-%d %H:%M} (한국 시간)"
    result = []
    for action in actions:
        if action.kind != "calendar":
            result.append(action)
            continue
        payload = dict(action.payload)
        description = _without_calendar_time_lines(payload.get("description", ""))
        if LOCAL_ATTACHMENT_HEADER in description:
            body, marker, attachment_tail = description.partition(LOCAL_ATTACHMENT_HEADER)
            body = body.rstrip()
            description = (
                f"{body}\n\n{time_line}\n\n{marker}{attachment_tail}"
                if body
                else f"{time_line}\n\n{marker}{attachment_tail}"
            )
        else:
            description = f"{description}\n\n{time_line}" if description else time_line
        if len(description) > BODY_MAX:
            raise CheckError(["등록한 시각을 넣으면 일정 설명이 너무 길어짐"])
        payload["description"] = description
        result.append(replace(action, payload=payload))
    return result


def _target_label(profile, target: str) -> str:
    calendars = (profile or {}).get("calendars") or {}
    names = {
        "work_calendar": calendars.get("work_calendar_name", ""),
        "school_calendar": calendars.get("school_calendar_name", ""),
        "work_tasks": calendars.get("work_tasks_name", ""),
        "homeroom_tasks": calendars.get("homeroom_tasks_name", ""),
    }
    return names.get(target) or TARGET_FALLBACK_LABELS.get(target, target)


def _item_target(action, profile) -> str:
    if action.kind == "notice":
        if action.target == "personal":
            return f"학생 안내표 · 개인톡 ({action.payload.get('name', '')})"
        return "학생 안내표 · 단체톡"
    prefix = "캘린더" if action.kind == "calendar" else "할 일"
    return f"{prefix} · {_target_label(profile, action.target)}"


def _action_when(action) -> str:
    if action.kind == "calendar":
        start = action.payload.get("start") or {}
        return start.get("dateTime") or start.get("date") or ""
    return ""  # Tasks는 날짜·시간을 지정하지 않고, 안내는 시트에서 보낼 시점을 정한다.


def _action_title(action) -> str:
    if action.kind == "calendar":
        return action.payload.get("summary", "")
    if action.kind == "task":
        return action.payload.get("title", "")
    return action.payload.get("content", "")  # notice


def _items_from_report(report: ExecutionReport, profile) -> list:
    return [
        {
            "kind": row.action.kind,
            "target": _item_target(row.action, profile),
            "title": _action_title(row.action),
            "when": _action_when(row.action),
            "result": row.status,  # created | duplicate | failed
            "detail": row.detail,
        }
        for row in report.results
    ]


def _attachment_link_status(
    names: tuple[str, ...], report: ExecutionReport, *, link_ready: bool = True
) -> str:
    if not names:
        return ""
    calendar_rows = [row for row in report.results if row.action.kind == "calendar"]
    if not calendar_rows:
        return "no-calendar"
    if all(row.status in {"created", "duplicate"} for row in calendar_rows):
        return "confirmed" if link_ready else "helper-unavailable"
    return "failed"


def _split_attachment_names(record) -> tuple[tuple[str, ...], tuple[str, ...]]:
    analyzed = tuple(getattr(record, "attachment_names", ()) or ())
    remaining = list(analyzed)
    skipped = []
    for name in tuple(getattr(record, "local_attachment_names", ()) or ()):
        if name in remaining:
            remaining.remove(name)
        else:
            skipped.append(name)
    return analyzed, tuple(skipped)


def _attachment_file_rows(
    names: tuple[str, ...],
    analyzed_names: tuple[str, ...],
    skipped_names: tuple[str, ...],
    link_status: str,
) -> list[dict]:
    if link_status in {"confirmed", "helper-unavailable"}:
        link_result = "included"
    elif link_status == "no-calendar":
        link_result = "no-calendar"
    elif link_status == "not-registered":
        link_result = "not-registered"
    else:
        link_result = "not-confirmed"
    analyzed_left = list(analyzed_names)
    skipped_left = list(skipped_names)
    rows = []
    for name in names:
        if name in analyzed_left:
            analyzed_left.remove(name)
            analysis = "included"
        elif name in skipped_left:
            skipped_left.remove(name)
            analysis = "excluded-unsupported"
        else:
            analysis = "not-read"
        rows.append({"name": name, "analysis": analysis, "calendar_link": link_result})
    return rows


def _finish(
    config_dir: Path,
    result: FlowResult,
    source_hash: str,
    detail: str = "",
    *,
    summary: str = "",
    items: list | None = None,
    progress=None,
    attachment_count: int = 0,
    identity: dict | None = None,
    retry: str = "",
    original_when: str = "",
    attachment_names: tuple[str, ...] = (),
    attachment_analyzed_names: tuple[str, ...] = (),
    attachment_skipped_names: tuple[str, ...] = (),
    attachment_link_status: str = "",
    attachment_link_detail: str = "",
) -> FlowResult:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        status_log.append_log(paths.logs_dir(config_dir), result.ok, result.stage, source_hash, detail)
        status_log.write_last_status(
            paths.bridge_state_dir(config_dir),
            {"ok": result.ok, "stage": result.stage, "message": result.message, "when": now_text},
        )
    except OSError:
        pass  # 기록 실패가 성공한 처리를 "예상하지 못한 오류"로 둔갑시키면 안 된다
    entry = {
        "when": now_text,
        "ok": result.ok,
        "stage": result.stage,
        "source_hash": source_hash,
        "summary": summary or result.message,
        "items": items or [],
        "attachment_count": max(0, int(attachment_count or 0)),
        "attachment_names": list(attachment_names),
        "attachment_analyzed_names": list(attachment_analyzed_names),
        "attachment_skipped_names": list(attachment_skipped_names),
        "attachment_link_status": str(attachment_link_status or ""),
        "attachment_link_detail": str(attachment_link_detail or ""),
        "attachment_files": _attachment_file_rows(
            tuple(attachment_names),
            tuple(attachment_analyzed_names),
            tuple(attachment_skipped_names),
            str(attachment_link_status or ""),
        ),
    }
    if not result.ok:
        entry["reason"] = result.message
    if identity is not None:
        entry.update(
            {
                "message_sender": str(identity.get("sender", "")),
                "message_sent_at": str(identity.get("sent_at", "")),
                "message_preview": str(identity.get("preview", "")),
            }
        )
    if retry:
        entry["retry"] = retry
    if original_when:
        entry["original_when"] = original_when
    try:
        capture_store.append_capture(paths.bridge_state_dir(config_dir), entry)
    except (OSError, ValueError):
        pass  # 기록 실패가 처리 자체를 실패시키면 안 된다 (UnicodeDecodeError=ValueError 포함)
    if progress is not None:
        progress("done" if result.ok else "fail", result.message)
    return result


def _load_profile(config_dir: Path) -> dict | None:
    try:
        raw = json.loads(paths.profile_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _preflight_capture_fields(capture) -> tuple[str, dict, tuple[str, ...]]:
    body = str(getattr(capture, "body", "") or "").strip()
    names = tuple(str(name) for name in (getattr(capture, "attachments", ()) or ()) if str(name))
    source_hash = compute_source_hash("", "", "\n".join([body, *names])) if body or names else ""
    if body:
        record = MessageRecord("brity-screen", "", "", body, "", source_hash)
        identity = message_identity(record)
    else:
        identity = {"sender": "", "sent_at": "", "preview": CURRENT_UNREAD_PREVIEW}
    return source_hash, identity, names


def _attachment_preflight_guidance(message: str) -> tuple[str, str, str]:
    safe_hint = str(message or "")
    if "먼저 내려받" in safe_hint:
        return (
            "첨부파일이 이 컴퓨터에 없어 등록하지 않았어요.",
            "첨부파일을 내려받아야 함",
            "첨부파일을 모두 내려받은 뒤 같은 메시지에서 단축키를 다시 눌러 주세요.",
        )
    if "너무 커" in safe_hint:
        return (
            "첨부파일이 너무 커서 등록하지 않았어요.",
            "첨부파일 용량을 줄여야 함",
            "파일 용량을 줄이거나 작은 사본을 내려받은 뒤 같은 메시지에서 단축키를 다시 눌러 주세요.",
        )
    if "암호" in safe_hint or "상태" in safe_hint:
        return (
            "첨부파일이 잠겨 있거나 손상되어 등록하지 않았어요.",
            "첨부파일 상태 확인 필요",
            "파일을 직접 열어 암호를 풀거나 다시 내려받은 뒤 단축키를 다시 눌러 주세요.",
        )
    if "첨부파일 이름" in safe_hint:
        return (
            "첨부파일 이름을 모두 읽지 못해 등록하지 않았어요.",
            "첨부파일 이름을 읽지 못함",
            "첨부파일 목록이 모두 보이게 한 뒤 같은 메시지에서 단축키를 다시 눌러 주세요.",
        )
    return (
        "첨부파일을 확인하지 못해 등록하지 않았어요.",
        "첨부파일을 확인하지 못함",
        "첨부파일을 다시 내려받고 목록이 모두 보이게 한 뒤 단축키를 다시 눌러 주세요.",
    )


def record_preflight_failure(
    config_dir: Path,
    message: str,
    summary: str,
    *,
    capture=None,
) -> FlowResult:
    """Google 작업 전 첨부 확인 실패를 최근 상태에만 남긴다."""
    source_hash, identity, names = _preflight_capture_fields(capture)
    reason, safe_summary, retry = _attachment_preflight_guidance(message)
    return _finish(
        Path(config_dir),
        FlowResult(False, "attachment", reason),
        source_hash,
        summary=safe_summary,
        identity=identity,
        retry=retry,
        attachment_count=len(names),
        attachment_names=names,
        attachment_link_status="not-registered" if names else "",
    )


def record_screen_failure(config_dir: Path, capture, retry: str) -> FlowResult:
    source_hash, identity, names = _preflight_capture_fields(capture)
    if "첨부파일 이름" in str(getattr(capture, "reason", "")):
        reason = "첨부파일 이름을 모두 읽지 못해 등록하지 않았어요."
        summary = "첨부파일 이름을 읽지 못함"
    else:
        reason = "메시지 내용을 읽지 못해 등록하지 않았어요."
        summary = "메시지 내용을 읽지 못함"
    return _finish(
        Path(config_dir),
        FlowResult(False, "capture", reason),
        source_hash,
        summary=summary,
        identity=identity,
        retry=retry,
        attachment_count=len(names),
        attachment_names=names,
        attachment_link_status="not-registered" if names else "",
    )


def record_unexpected_failure(config_dir: Path, capture) -> FlowResult:
    source_hash, identity, names = _preflight_capture_fields(capture)
    reason = "메시지를 처리하는 중 예상하지 못한 문제가 생겼습니다."
    retry = (
        "같은 메시지를 바로 다시 누르지 말고 최근 기록을 확인한 뒤 "
        "Teacher Manager를 다시 시작해 주세요."
    )
    return _finish(
        Path(config_dir),
        FlowResult(False, "execute", reason),
        source_hash,
        summary="메시지 처리 중 문제 발생",
        identity=identity,
        retry=retry,
        attachment_count=len(names),
        attachment_names=names,
        attachment_link_status="not-confirmed" if names else "",
    )


NOTICE_SETUP_REQUIRED_DETAIL = "학생 안내표가 준비되지 않았어요 · 처음 설정 필요"
NOTICE_SCRIPT_UPDATE_REQUIRED_DETAIL = (
    "학생 안내표의 출결 기능 확인 또는 업데이트가 먼저 필요해요 · 출결 탭에서 최신 상태를 확인해 주세요"
)


def _load_notice_sheet(config_dir: Path) -> tuple[str, str]:
    """학생 안내 Sheet ID와 쓸 수 없을 때 보여 줄 이유를 함께 읽는다."""
    try:
        raw = load_attendance_install_record(
            paths.attendance_install_record_path(config_dir)
        )
        current_sha256 = target_bundle_sha256(
            bundle_paths.bundle_root() / "assets"
        )
    except (OSError, ValueError):
        return "", NOTICE_SETUP_REQUIRED_DETAIL
    if (
        raw.get("script_update_required") is True
        or not attendance_script_is_attested(raw, current_sha256)
    ):
        return "", NOTICE_SCRIPT_UPDATE_REQUIRED_DETAIL
    sheet_id = str(raw.get("spreadsheet_id") or "")
    return sheet_id, "" if sheet_id else NOTICE_SETUP_REQUIRED_DETAIL


def _remote_notice_script_preflight(
    config_dir: Path,
    runner,
    gws_executable: str,
    inspector=None,
) -> tuple[bool, str]:
    """학생 안내 줄을 쓰기 직전에 원격 원본과 실제 사용판을 다시 읽는다."""

    try:
        record = load_attendance_install_record(
            paths.attendance_install_record_path(config_dir)
        )
        expected_sha256 = target_bundle_sha256(
            bundle_paths.bundle_root() / "assets"
        )
        inspect = inspector or inspect_attendance_script_update

        def cwd_runner(args, _cwd):
            return runner(list(args))

        result = inspect(
            record.get("spreadsheet_id"),
            record.get("script_id"),
            record.get("deployment_id"),
            assets_dir=bundle_paths.bundle_root() / "assets",
            runner=cwd_runner,
            gws_executable=gws_executable,
        )
        value = (
            (lambda key: result.get(key))
            if isinstance(result, dict)
            else (lambda key: getattr(result, key, None))
        )
        exact_ids = all(
            value(key) == record.get(key)
            for key in ("spreadsheet_id", "script_id", "deployment_id")
        )
        exact_hashes = (
            value("current_bundle_sha256") == expected_sha256
            and value("target_bundle_sha256") == expected_sha256
        )
        if (
            value("state") == "current"
            and value("verified") is True
            and exact_ids
            and exact_hashes
        ):
            return True, ""
    except Exception:  # noqa: BLE001 - 원격 결과가 모호하면 쓰지 않고 쉬운 안내만 보인다
        pass
    return False, NOTICE_SCRIPT_UPDATE_REQUIRED_DETAIL


def _load_attendance_owner(config_dir: Path) -> str:
    """이 설정 폴더에서 처음 출결을 준비한 Google 계정. 옛 기록은 빈 값이다."""

    try:
        raw = json.loads(
            paths.attendance_setup_status_path(config_dir).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    return str(raw.get("account", "") or "").strip() if isinstance(raw, dict) else ""


def _summarize_report(report: ExecutionReport) -> str:
    created = report.created
    calendar_count = sum(row.action.kind == "calendar" for row in created)
    task_count = sum(row.action.kind == "task" for row in created)
    personal_count = sum(
        row.action.kind == "notice" and row.action.target == "personal" for row in created
    )
    class_count = sum(
        row.action.kind == "notice" and row.action.target == "class" for row in created
    )
    blocked_notices = sum(
        row.action.kind == "notice"
        and row.status == "failed"
        and "처음 설정 필요" in row.detail
        for row in report.results
    )
    update_blocked_notices = sum(
        row.action.kind == "notice"
        and row.status == "failed"
        and "출결 기능" in row.detail
        and ("확인" in row.detail or "업데이트" in row.detail)
        for row in report.results
    )
    parts = []
    if calendar_count:
        parts.append(f"캘린더 {calendar_count}건")
    if task_count:
        parts.append(f"할 일 {task_count}건")
    if personal_count or class_count:
        parts.append(
            f"학생 안내 {personal_count + class_count}건 "
            f"(개인톡 {personal_count} · 단체톡 {class_count})"
        )
    if blocked_notices:
        parts.append(f"학생 안내 옮기지 못함 {blocked_notices}건 · 처음 설정 필요")
    if update_blocked_notices:
        # 상태별로 버튼·할 일이 달라지므로(업데이트/마무리/다시 확인) 동작을
        # 나열하지 않고 화면의 실제 위치만 가리킨다 — bridge.py 안내문과 같은 규칙.
        parts.append(
            f"학생 안내 옮기지 못함 {update_blocked_notices}건 · "
            "출결 기능 확인 필요 (출결 탭 위쪽의 한 줄 안내)"
        )
    if report.duplicates:
        parts.append(f"기존 항목 {len(report.duplicates)}건")
    other_failures = len(report.failures) - blocked_notices - update_blocked_notices
    if other_failures:
        parts.append(f"실패 {other_failures}건")
    return " · ".join(parts) if parts else "만들 항목 없음"


def _notice_created_note(report: ExecutionReport) -> str:
    created = sum(1 for row in report.results if row.action.kind == "notice" and row.status == "created")
    if not created:
        return ""
    return (
        "\n학생 안내는 학생 안내표에 확인필요 상태로 옮겼어요. "
        "아직 학생에게 보내지 않았어요 — 안내표에서 확인 후 발송 대기로 바꿔 주세요."
    )


def _notice_blocked_note(report: ExecutionReport) -> str:
    count = sum(
        row.action.kind == "notice"
        and row.status == "failed"
        and "처음 설정 필요" in row.detail
        for row in report.results
    )
    update_count = sum(
        row.action.kind == "notice"
        and row.status == "failed"
        and "출결 기능" in row.detail
        and ("확인" in row.detail or "업데이트" in row.detail)
        for row in report.results
    )
    notes = []
    if count:
        notes.append(
            f"학생 안내 {count}건은 학생 안내표가 준비되지 않아 옮기지 못했어요 · 처음 설정 필요."
        )
    if update_count:
        notes.append(
            f"학생 안내 {update_count}건은 학생 안내표의 출결 기능을 먼저 확인하거나 업데이트해야 옮길 수 있어요."
        )
    return ("\n" + " ".join(notes)) if notes else ""


def _account_blocked_note(report: ExecutionReport) -> str:
    if any("처음 준비하던 Google 계정" in row.detail for row in report.failures):
        return "\n처음 준비하던 Google 계정으로 다시 로그인해 주세요."
    return ""


def run_capture_flow(
    context: CaptureContext,
    config_dir: Path,
    bridge_settings,
    gemini_transport=None,
    gws_runner=None,
    now: datetime | None = None,
    record=None,
    progress=None,
    *,
    gws_executable: str | None = None,
    attendance_script_inspector=None,
    attachment_link_ready: bool = True,
    attachment_link_failure: str = "",
) -> FlowResult:
    config_dir = Path(config_dir)
    now = now or datetime.now(KOREA_TIME)

    if record is None:
        try:
            record = parse_clipboard_message(context.clipboard_text, context.clipboard_html, now)
        except ValueError:
            return _finish(
                config_dir,
                FlowResult(
                    False,
                    "parse",
                    "읽을 메시지를 찾지 못했습니다. 브리티 대화방에 메시지가 보이게 한 뒤 단축키를 다시 눌러 주세요.",
                ),
                "",
                summary="메시지를 읽지 못함",
                progress=progress,
            )

    attachment_count = max(0, int(getattr(record, "attachment_count", 0) or 0))
    attachment_names = tuple(getattr(record, "local_attachment_names", ()) or ())
    attachment_analyzed_names, attachment_skipped_names = _split_attachment_names(record)
    identity = message_identity(record)
    history = HistoryStore(paths.history_path(config_dir))
    history.load()
    if history.is_completed(record.source_hash):
        original = capture_store.latest_successful_done(
            paths.bridge_state_dir(config_dir), record.source_hash
        )
        return _finish(
            config_dir,
            FlowResult(True, "duplicate", "이미 등록한 메시지입니다. 새로 만들지 않았습니다."),
            record.source_hash,
            summary="이미 등록한 메시지 (건너뜀)",
            items=list((original or {}).get("items") or []),
            progress=progress,
            attachment_count=attachment_count,
            identity=identity,
            original_when=str((original or {}).get("when", "")),
            attachment_names=tuple((original or {}).get("attachment_names") or attachment_names),
            attachment_analyzed_names=tuple(
                (original or {}).get("attachment_analyzed_names") or attachment_analyzed_names
            ),
            attachment_skipped_names=tuple(
                (original or {}).get("attachment_skipped_names") or attachment_skipped_names
            ),
            attachment_link_status=str((original or {}).get("attachment_link_status", "")),
            attachment_link_detail=str((original or {}).get("attachment_link_detail", "")),
        )

    if progress is not None:
        progress("analyze")

    profile = _load_profile(config_dir)
    if profile is None:
        return _finish(
            config_dir,
            FlowResult(
                False,
                "profile",
                "내 정보가 없습니다. Teacher Manager 처음 시작 설정을 먼저 끝내 주세요.",
            ),
            record.source_hash,
            summary="개인 설정이 없음",
            progress=progress,
            attachment_count=attachment_count,
            identity=identity,
            attachment_names=attachment_names,
            attachment_analyzed_names=attachment_analyzed_names,
            attachment_skipped_names=attachment_skipped_names,
            attachment_link_status="not-registered" if attachment_names else "",
        )

    try:
        proposal = run_gemini_analysis(
            record, profile, bridge_settings, paths.skill_root(), transport=gemini_transport, now=now
        )
    except AnalysisError as error:
        message = GEMINI_FAILURE_MESSAGES.get(error.reason, GEMINI_FAILURE_MESSAGES["shape"])
        return _finish(
            config_dir,
            FlowResult(False, "gemini", message),
            record.source_hash,
            detail=f"{error.reason}",
            summary="분석 실패",
            progress=progress,
            attachment_count=attachment_count,
            identity=identity,
            retry=RETRY_MESSAGE,
            attachment_names=attachment_names,
            attachment_analyzed_names=attachment_analyzed_names,
            attachment_skipped_names=attachment_skipped_names,
            attachment_link_status="not-registered" if attachment_names else "",
        )

    notice_sheet_id, notice_unavailable_message = _load_notice_sheet(config_dir)

    try:
        actions = check_proposal(
            proposal, profile, notice_sheet_id=notice_sheet_id, today=now.date()
        )
        actions = add_work_task_copies(actions, profile)
        actions = _remove_calendar_time_lines(actions)
        actions = add_local_attachment_links(actions, record.local_attachment_names)
        actions = _add_calendar_registration_time(actions, now)
    except CheckError as error:
        due_problem = any("due 형식" in problem for problem in error.problems)
        if due_problem:
            message = capture_store.CHECK_DUE_REASON
            summary = capture_store.CHECK_DUE_SUMMARY
            detail = "할 일 날짜 확인 실패"
        else:
            message = capture_store.CHECK_GENERIC_REASON
            summary = capture_store.CHECK_GENERIC_SUMMARY
            detail = "등록 내용 확인 실패"
        return _finish(
            config_dir,
            FlowResult(False, "check", message),
            record.source_hash,
            detail=detail,
            summary=summary,
            progress=progress,
            attachment_count=attachment_count,
            identity=identity,
            retry=RETRY_MESSAGE,
            attachment_names=attachment_names,
            attachment_analyzed_names=attachment_analyzed_names,
            attachment_skipped_names=attachment_skipped_names,
            attachment_link_status="not-registered" if attachment_names else "",
        )

    if not actions:
        result = FlowResult(True, "done", "등록할 일정이나 할 일이 없는 메시지입니다.")
        return _finish(
            config_dir, result, record.source_hash, detail="actions=0",
            summary="등록할 일정이 없는 메시지", progress=progress,
            attachment_count=attachment_count,
            identity=identity,
            attachment_names=attachment_names,
            attachment_analyzed_names=attachment_analyzed_names,
            attachment_skipped_names=attachment_skipped_names,
            attachment_link_status="no-calendar" if attachment_names else "",
        )

    if progress is not None:
        progress("register")

    report = execute_actions(
        actions,
        history,
        runner=gws_runner,
        source_hash=record.source_hash,
        gws_executable=gws_executable,
        notice_unavailable_message=notice_unavailable_message,
        expected_account=_load_attendance_owner(config_dir),
        notice_preflight=(
            (
                lambda runner, executable: _remote_notice_script_preflight(
                    config_dir,
                    runner,
                    executable,
                    attendance_script_inspector,
                )
            )
            if notice_sheet_id
            else None
        ),
    )
    summary = _summarize_report(report)
    items = _items_from_report(report, profile)
    notice_note = _notice_created_note(report)
    blocked_note = _notice_blocked_note(report)
    account_note = _account_blocked_note(report)
    if report.all_done:
        try:
            history.mark_completed(record.source_hash)
            history.save()
        except Exception:
            pass  # 항목별 중복 방지 기록은 이미 저장됐으므로 사용자 결과를 뒤집지 않는다
        if report.created:
            message = f"새로 등록했습니다 ({summary})."
        elif report.duplicates:
            message = (
                f"이미 등록된 항목 {len(report.duplicates)}건을 확인해 "
                "새로 만들지 않았습니다."
            )
        else:
            message = "새로 만들 항목이 없었습니다."
        link_status = _attachment_link_status(
            attachment_names, report, link_ready=attachment_link_ready
        )
        link_detail = ""
        if link_status == "helper-unavailable":
            link_detail = (
                ATTACHMENT_HELPER_PORT_MESSAGE
                if "다른 프로그램" in str(attachment_link_failure or "")
                else ATTACHMENT_HELPER_FAILURE_MESSAGE
            )
        result = FlowResult(
            True,
            "done",
            message + notice_note + blocked_note + account_note
            + (("\n" + link_detail) if link_detail else ""),
            report=report,
        )
        return _finish(
            config_dir, result, record.source_hash, detail=summary,
            summary=summary, items=items, progress=progress,
            attachment_count=attachment_count,
            identity=identity,
            attachment_names=attachment_names,
            attachment_analyzed_names=attachment_analyzed_names,
            attachment_skipped_names=attachment_skipped_names,
            attachment_link_status=link_status,
            attachment_link_detail=link_detail,
        )

    retry_blocked = any(
        not getattr(row, "retry_allowed", True) for row in report.failures
    )
    if retry_blocked:
        message = (
            f"등록 결과를 안전하게 기록하지 못했습니다 ({summary}). "
            + RESULT_RECORD_FAILURE_DETAIL
        )
    else:
        failure_lead = "일부만 처리됐습니다" if report.created or report.duplicates else "처리하지 못했습니다"
        message = (
            f"{failure_lead} ({summary}). 같은 메시지에서 다시 실행하면 실패한 항목만 다시 시도합니다."
        )
    result = FlowResult(
        False,
        "execute",
        message + notice_note + blocked_note + account_note,
        report=report,
    )
    return _finish(
        config_dir, result, record.source_hash, detail=summary,
        summary=summary, items=items, progress=progress,
        attachment_count=attachment_count,
        identity=identity,
        retry="" if retry_blocked else RETRY_MESSAGE,
        attachment_names=attachment_names,
        attachment_analyzed_names=attachment_analyzed_names,
        attachment_skipped_names=attachment_skipped_names,
        attachment_link_status=_attachment_link_status(
            attachment_names, report, link_ready=attachment_link_ready
        ),
    )
