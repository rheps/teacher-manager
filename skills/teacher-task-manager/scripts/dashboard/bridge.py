# skills/teacher-task-manager/scripts/dashboard/bridge.py
"""pywebview js_api — 화면(JS)이 부르는 유일한 문.

규칙: 이 파일은 표시 문구 조립과 engine 호출만 한다. 화면 로직을 JS에,
업무 로직을 engine 밖에 두지 않는다. 모든 공개 메서드는 guarded로 감싸
{"ok": true, "data": ...} / {"ok": false, "error": "한국어 한 문장"}만 돌려준다.
"""
from __future__ import annotations

import functools
import json
import hashlib
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brity_bridge import (
    account_sessions,
    bundle_paths,
    capture_store,
    component_lock,
    dns_warm,
    error_reports,
    google_account,
    paths,
    pipeline,
    process_win,
    recovery,
)
from brity_bridge.settings import load_settings
from dashboard import attendance_ai
from dashboard import engine
from dashboard import external_url
from dashboard import problem_guidance
from dashboard import server_session
from dashboard import version

SETUP_STATE_NAME = "setup-state.json"
SETUP_STATE_VERSION = 3
SETUP_LAST_STEP = 9
_FRESH_STATE = {
    "version": SETUP_STATE_VERSION,
    "completed": False,
    "step": 1,
    "max_step": 1,
    "draft": {"profile": {}, "grid": [], "bridge": {}},
}
_V1_STEP_TO_V2 = {1: 1, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 9}
_ATTENDANCE_AI_PROOF_MESSAGE = (
    "출석부 안의 연결 확인 표시가 없거나 맞지 않아요. 출석부를 열고 "
    "[처음 한 번 설정하기]에서 [처음 설정 한 번에 끝내기]를 누른 뒤 "
    "연결 확인하고 계속해 주세요."
)
# 실패 화면의 제목이다. 무엇을 못 했는지 한 문장만 적고, 사용자가 할 일은 여기에
# 쓰지 않는다 — 행동 안내는 problem_guidance의 작업 종류 표가 정한다(2026-09-02).
_DEFAULT_SCREEN_FAILURE = "작업을 마치지 못했어요."
_SCREEN_FAILURES = {
    "get_app_info": "프로그램을 여는 데 필요한 정보를 읽지 못했어요.",
    "save_setup_state": "처음 설정 내용을 저장하지 못했어요.",
    "finish_setup": "처음 설정을 마무리하지 못했어요.",
    "read_profile": "이 컴퓨터에 저장된 내 정보를 읽지 못했어요.",
    "read_grid": "이 컴퓨터에 저장된 시간표를 읽지 못했어요.",
    "get_messenger_settings": "이 컴퓨터에 저장된 메신저 설정을 읽지 못했어요.",
    "save_messenger": "이 컴퓨터 설정을 저장하지 못했어요.",
    "apply_all": "이 컴퓨터 설정을 저장하고 적용하지 못했어요.",
    "choose_attachment_folder": "첨부파일 폴더를 열지 못했어요.",
    "check_attachment_folder": "첨부파일 폴더 상태를 확인하지 못했어요.",
    "attendance_chat_status": "학급 단톡방 상태를 확인하지 못했어요.",
    "attendance_chat_spaces": "학급 단톡방 목록을 가져오지 못했어요.",
    "attendance_chat_start": "학생들과 채팅을 시작하지 못했어요.",
    "open_attendance_chat": "Google Chat을 열지 못했어요.",
    "student_chat_connect_status": "학생 연결 상태를 읽지 못했어요.",
    "student_chat_connect_resolve": "확인한 결과를 저장하지 못했어요.",
    "computer_status": "이 컴퓨터의 준비 상태를 확인하지 못했어요.",
    "google_status": "Google 연결 상태를 확인하지 못했어요.",
    "list_calendars": "캘린더 목록을 가져오지 못했어요.",
    "list_tasklists": "할 일 목록을 가져오지 못했어요.",
    "gws_login_start": "Google 로그인을 시작하지 못했어요.",
    "gws_login_status": "Google 로그인 상태를 확인하지 못했어요.",
    "gws_logout": "Google 로그아웃을 마치지 못했어요.",
    "ensure_calendar_named": "캘린더를 만들지 못했어요.",
    "ensure_tasklist_named": "할 일 목록을 만들지 못했어요.",
    "open_url": "안전한 https 주소만 열 수 있어요.",
    "open_picture_guide": "방법 안내를 기본 브라우저에서 열지 못했어요.",
    "retry_capture": "실패한 항목을 다시 처리하지 못했어요.",
}


class ScreenSafeError(RuntimeError):
    """A fixed Korean sentence intentionally prepared for the screen."""


class ServerSessionEnded(ScreenSafeError):
    """The server refused this PC's session: the screen reads the status and asks for 다시 로그인."""

    code = "SIGNIN_REQUIRED"


class GooglePermissionRequired(ScreenSafeError):
    """A target route answered that the grant lacks a capability: 권한 다시 승인, not a login."""

    code = "GOOGLE_PERMISSION_REQUIRED"


class AttendanceScriptChangedOutsideError(ScreenSafeError):
    """편집본이 프로그램 밖에서 바뀐 결정적 상태 — 되풀이하지 않고 출결 탭의 현재 상태 확인을 안내한다."""


class _AttendanceRemoteWorkLease:
    """백그라운드 로그인 종료까지 공용 출결 잠금의 소유권을 보관한다."""

    def __init__(self, context):
        self._context = context
        self._release_lock = threading.Lock()

    @classmethod
    def acquire(cls, config_dir, **lock_options):
        context = engine.attendance_remote_work_lock(config_dir, **lock_options)
        context.__enter__()
        return cls(context)

    def release(self):
        with self._release_lock:
            context = self._context
            self._context = None
        if context is not None:
            context.__exit__(None, None, None)


def _ok(data):
    return {"ok": True, "data": data}


_GRANT_MISSING_TEXT = "Google 권한을 다시 승인해 주세요."
_TARGET_ROUTES = {
    "calendar": ("/v1/data/targets/calendars", "calendars"),
    "tasklist": ("/v1/data/targets/tasklists", "tasklists"),
}
_TARGET_CHECK_LIMIT = 10
_TARGET_STATE_DETAILS = {
    ("calendar", "ready"): "연결됨",
    ("tasklist", "ready"): "연결됨",
    ("calendar", "unavailable"): "저장된 캘린더에 일정을 등록할 수 없어요. 접근 권한을 확인하거나 다른 캘린더를 골라 주세요.",
    ("tasklist", "unavailable"): "저장된 할 일 목록을 사용할 수 없어요. 접근 권한을 확인하거나 다른 목록을 골라 주세요.",
}


def _server_error_code(body) -> str:
    return str(body.get("error") or "") if isinstance(body, dict) else ""


def _target_row(kind, row) -> dict:
    """The row shape the screen has always had. `owned` is the server's rule for a named target."""
    if kind == "calendar":
        return {**row, "id": str(row["id"]), "name": str(row.get("name") or ""), "owned": row.get("accessRole") == "owner"}
    return {**row, "id": str(row["id"]), "name": str(row.get("title") or row.get("name") or ""), "owned": True}


def _signed_in_identity(email, me) -> dict:
    """The fields of GET /v1/me under their own names. Declined scopes do not stop a sign-in:
    each missing capability asks for 권한 다시 승인 where it is needed (SIGN-IN)."""
    return {
        "logged_in": True,
        "account_allowed": True,
        "user": email,
        "login_state": "logged_in",
        "error_code": "",
        "authorization_state": "ready",
        "authorization_detail": "로그인과 필요한 Google 권한을 확인했어요.",
        **{key: me.get(key) for key in ("subjectKey", "activeYear", "hasAnyYear", "currentYearReady", "grant")},
    }


def _signed_out_identity(ended_code="") -> dict:
    return {
        "logged_in": False,
        "account_allowed": False,
        "user": "",
        "login_state": "logged_out",
        "error_code": ended_code,
        "authorization_state": "login_required",
        "authorization_detail": server_session.SESSION_ENDED_TEXT if ended_code else "Google 로그인이 필요해요.",
        "session_ended": bool(ended_code),
    }


def _identity_unread(*, server_unreachable) -> dict:
    """The check did not finish, which says nothing about the sign-in (LOGIN-17, LOGIN-32)."""
    return {
        "logged_in": False,
        "account_allowed": False,
        "user": "",
        "login_state": "error",
        "error_code": "GWS_AUTH_STATUS_FAILED",
        "authorization_state": "check_failed",
        "authorization_detail": engine.GOOGLE_PERMISSION_CHECK_MESSAGE,
        "server_unreachable": server_unreachable,
    }


def _fail(error, operation: str = "", config_dir=None, reporter=None):
    error_text = None
    if isinstance(error, (recovery.UserActionRequired, recovery.FinalOperationFailure)):
        issue = error.issue
    else:
        message = _SCREEN_FAILURES.get(str(operation or ""), _DEFAULT_SCREEN_FAILURE)
        if isinstance(error, (ScreenSafeError, account_sessions.AccountSessionError)):
            # 화면용으로 준비된 문장을 작업 이름 기반 고정 제목으로 덮지 않는다.
            # 준비된 안내를 사람 행동 안내(needs_user)로 그대로 보여 준다.
            issue = _screen_safe_user_issue(error, operation)
        else:
            issue = recovery.unexpected_final_issue(
                operation=str(operation or "unknown_operation"),
                title=message,
                change_status="작업 결과를 확인하지 못했어요. 현재 화면과 저장된 자료를 확인해 주세요.",
                app_version=version.APP_VERSION,
            )
            # 옛 한 문장 응답(error)은 무엇을 못 했는지(제목)로 유지한다.
            error_text = message
    # 모든 실패는 교사가 할 수 있는 일로 바뀐다. 코드·횟수·식별번호는 기록에만 남는다.
    issue = problem_guidance.apply_guidance(issue, str(operation or issue.operation))
    _record_issue_journal(config_dir, issue, error)
    if reporter is not None:
        try:
            report_state = reporter(issue, error)
            if report_state == "queued":
                issue = issue.mark_report_queued()
            elif report_state is True or report_state == "sent":
                issue = issue.mark_reported()
        except Exception:  # noqa: BLE001 - 보고 실패가 안내를 막으면 안 된다.
            pass
    reply = _issue_reply(issue, error_text=error_text)
    if isinstance(error, external_url.ExternalUrlOpenError):
        reply["error"] = str(error)
        reply["code"] = external_url.NO_EXTERNAL_BROWSER
    elif isinstance(error, engine.AttendanceRemoteWorkBusyError):
        reply["error"] = str(error)
        # Refused before the protected work began: the screen may wait and follow it (PEND-46).
        reply["issue"]["code"] = "ATTENDANCE_REMOTE_BUSY"
    elif isinstance(error, (ServerSessionEnded, GooglePermissionRequired)):
        reply["issue"]["code"] = error.code
    else:
        try:
            from dashboard import central_chat

            if isinstance(error, central_chat.CentralChatError):
                reply["error"] = central_chat._safe_central_error_detail(error)
        except ImportError:
            pass
    return reply


_JOURNAL_MAX_BYTES = 512 * 1024
_JOURNAL_KEEP_LINES = 200
_JOURNAL_FINGERPRINT_KEYS = (
    "operation", "state", "diagnostic_id", "title", "reason", "error_detail",
)
_ATTENDANCE_UI_LOG_LOCK = threading.Lock()
_ATTENDANCE_UI_LOG_MAX_BYTES = 256 * 1024
_ATTENDANCE_UI_EVENTS = frozenset({
    "mount", "unmount", "submit", "queued", "pump_start", "answer", "settled", "removed", "page_exception",
})
_ATTENDANCE_UI_STAGES = _ATTENDANCE_UI_EVENTS | frozenset({"error", "unhandledrejection"})
_ATTENDANCE_UI_STATES = frozenset({
    "queued", "running", "success", "blocked", "error", "uncertain", "stale", "hidden", "removed",
})
_ATTENDANCE_UI_ERROR_TYPES = frozenset({
    "Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "URIError", "EvalError",
    "AggregateError", "DOMException", "Unknown",
})
_ATTENDANCE_UI_SCRIPTS = frozenset({"app.js", "attendance.js", "preact.umd.js", "htm.umd.js"})


def _record_attendance_ui_event(config_dir, payload) -> bool:
    """Append only allowlisted structural facts; user data and raw exceptions never reach disk."""
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str) or payload["event"] not in _ATTENDANCE_UI_EVENTS:
        return False
    try:
        now = datetime.now(recovery.KOREA_TIME)
        entry = {"at": now.isoformat(timespec="milliseconds"), "event": payload["event"]}
        for key in ("mountId", "queueId", "generation", "line", "column"):
            value = payload.get(key)
            if type(value) is int and 0 < value <= 9007199254740991:
                entry[key] = value
        for key, allowed in (("stage", _ATTENDANCE_UI_STAGES), ("state", _ATTENDANCE_UI_STATES)):
            value = payload.get(key)
            if isinstance(value, str) and value in allowed:
                entry[key] = value
        if entry["event"] == "page_exception":
            error_type = payload.get("errorType")
            entry["errorType"] = error_type if isinstance(error_type, str) and error_type in _ATTENDANCE_UI_ERROR_TYPES else "Unknown"
            script = payload.get("script")
            if isinstance(script, str) and script in _ATTENDANCE_UI_SCRIPTS:
                entry["script"] = script
        data = (json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
        journal = paths.logs_dir(config_dir) / f"attendance-ui-{now:%Y-%m-%d}.log"
        with _ATTENDANCE_UI_LOG_LOCK:
            journal.parent.mkdir(parents=True, exist_ok=True)
            if journal.is_symlink() or journal.parent.is_symlink():
                return False
            if journal.exists() and journal.stat().st_size + len(data) > _ATTENDANCE_UI_LOG_MAX_BYTES:
                with journal.open("rb") as source:
                    source.seek(max(0, journal.stat().st_size - _ATTENDANCE_UI_LOG_MAX_BYTES // 2))
                    tail = source.read(_ATTENDANCE_UI_LOG_MAX_BYTES // 2).split(b"\n", 1)[-1]
                journal.write_bytes(tail)
            with journal.open("ab") as target:
                target.write(data)
        return True
    except Exception:  # noqa: BLE001 - diagnostics must not affect the screen or start error reporting.
        return False


def _journal_error_chain(error) -> list:
    """원인 사슬(from/context)을 따라가되 같은 예외를 두 번 보지 않는다."""

    chain = []
    seen = set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _with_network_note(detail: str) -> str:
    """이름 확인이 느렸거나 아직 진행 중이면 보고의 상세에 그 시간을 덧붙인다."""

    try:
        status = dns_warm.shared_status()
    except Exception:  # noqa: BLE001 - 보고는 최선 노력이다.
        return detail
    if status.get("state") not in ("slow", "warming"):
        return detail
    seconds = status.get("first_lookup_seconds")
    if isinstance(seconds, (int, float)):
        note = f"인터넷 이름 확인 첫 조회 {seconds:.1f}초"
    else:
        note = f"인터넷 이름 확인 진행 중 {float(status.get('elapsed_seconds') or 0):.0f}초"
    return f"{detail} | {note}" if detail else note


def _journal_error_detail(error) -> str:
    """식별번호 역추적에 필요한 안전 문구만 모은다. 원문 예외는 넣지 않는다.

    세 번 처리 뒤의 FinalOperationFailure는 마지막 재시도 예외를 원인으로 달고
    오므로, 사슬을 따라가야 어떤 확인이 어떻게 어긋났는지 남는다.
    """

    try:
        from dashboard import central_chat
    except ImportError:  # pragma: no cover - 배포 묶음에는 항상 들어 있다.
        central_chat = None
    parts = []
    for current in _journal_error_chain(error):
        if isinstance(current, ScreenSafeError):
            parts.append(str(current))
        elif isinstance(current, recovery.RetryableOperationError):
            parts.append(f"{current.code}: {current.safe_reason}")
        elif central_chat is not None and isinstance(current, central_chat.CentralChatError):
            parts.append(central_chat._safe_central_error_detail(current))
        extra = str(getattr(current, "diagnostic_detail", "") or "")
        if extra:
            parts.append(extra)
    return " | ".join(part for part in parts if part)


def _journal_fingerprint(entry: dict) -> tuple:
    return tuple(entry.get(key) for key in _JOURNAL_FINGERPRINT_KEYS)


def _record_issue_journal(config_dir, issue: recovery.UserIssue, error) -> None:
    """화면에 보인 오류 식별번호를 로컬에서 역추적할 최소 기록.

    기록 실패가 오류 안내 자체를 막으면 안 되므로 어떤 예외도 밖으로 내지 않는다.
    같은 안내가 화면에 다시 표면화될 뿐이면 줄을 늘리지 않고, 파일은 상한을 넘으면
    최근 기록만 남긴다.
    """

    if config_dir is None:
        return
    try:
        entry = {
            "at": datetime.now(recovery.KOREA_TIME).strftime("%Y-%m-%d %H:%M:%S"),
            "operation": issue.operation,
            "state": issue.state,
            "attempt_count": issue.attempt_count,
            "diagnostic_id": issue.diagnostic_id,
            "title": issue.title,
            "reason": issue.reason,
            "error_type": type(error).__name__,
            "error_chain": " > ".join(
                type(item).__name__ for item in _journal_error_chain(error)
            ),
            "error_detail": _journal_error_detail(error),
        }
        journal = paths.bridge_state_dir(config_dir) / "logs" / "error-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        previous = journal.read_text(encoding="utf-8") if journal.exists() else ""
        lines = [line for line in previous.splitlines() if line.strip()]
        if lines:
            try:
                last = json.loads(lines[-1])
            except ValueError:
                last = {}
            if isinstance(last, dict) and _journal_fingerprint(last) == _journal_fingerprint(entry):
                return
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        if len(previous.encode("utf-8")) + len(line.encode("utf-8")) > _JOURNAL_MAX_BYTES:
            kept = lines[-_JOURNAL_KEEP_LINES:]
            journal.write_text("".join(f"{row}\n" for row in kept) + line, encoding="utf-8")
            return
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:  # noqa: BLE001 - 진단 기록은 최선 노력으로만 남긴다.
        pass


def _issue_reply(issue: recovery.UserIssue, *, error_text: str | None = None) -> dict:
    """Keep the old safe sentence while moving JS consumers to the issue record."""

    return {
        "ok": False,
        "issue": issue.to_dict(),
        "error": error_text or issue.message or issue.title,
    }


def _screen_safe_user_issue(error, operation: str) -> recovery.UserIssue:
    """화면용 고정 문장은 그대로 보여 주고, 정해진 문구에는 이동 단추를 단다."""

    message = str(error).strip()
    if isinstance(error, GooglePermissionRequired):
        return recovery.UserIssue.needs_user(
            operation=str(operation or "google_permission"),
            title=message,
            message=message,
            change_status="확인된 자료는 바꾸지 않았습니다.",
            actions=(recovery.IssueAction("reauthorize-google", "권한 다시 승인"),),
        )
    if isinstance(error, AttendanceScriptChangedOutsideError):
        # 결정적 상태라 되풀이하지 않는다. 출결 탭에는 누를 버튼이 없으므로
        # 현재 상태 확인만 안내한다(2026-09-03).
        return recovery.UserIssue.needs_user(
            operation=str(operation or "attendance_script_changed_outside"),
            title="출결 기능 확인이 필요해요.",
            message=message,
            change_status="기존 출결 자료와 현재 연결은 그대로입니다.",
            actions=(recovery.IssueAction("attendance-tab", "출결 탭으로"),),
            resume="attendance-tab",
        ).with_guidance(steps=(
            "출결 탭 맨 위 한 줄 안내에서 출결 기능 상태를 확인해 주세요.",
            "현재 자료와 입력한 값을 그대로 보관해 주세요.",
        ))
    if message == engine.ATTENDANCE_ACCOUNT_MESSAGE:
        return recovery.UserIssue.needs_user(
            operation=str(operation or "attendance_account"),
            title="Google 계정을 확인해 주세요.",
            message=message,
            change_status="기존 출결 자료는 그대로입니다.",
            actions=(recovery.IssueAction("google-login", "Google 로그인 설정 열기"),),
            resume="google-login",
        )
    if message == _ATTENDANCE_AI_PROOF_MESSAGE:
        return recovery.UserIssue.needs_user(
            operation=str(operation or "attendance_first_setup"),
            title="출석부의 처음 설정을 마쳐 주세요.",
            message=message,
            change_status="기존 출결 자료는 그대로입니다.",
            actions=(recovery.IssueAction("attendance-first-setup", "출석부 설정 확인"),),
            resume="attendance-first-setup",
        )
    from dashboard import central_chat

    if message == central_chat.CONFIG_VALUE_MISSING_MESSAGE:
        # 설정 탭 값이 비어 있는 결정적 상태 — 되풀이하지 않고, 원인 문장 대신 선생님이
        # 할 일 세 가지와 [현재 출석부 열기]만 보인다(2026-09-04 사용자 결정).
        guidance = problem_guidance.guidance_for(
            problem_guidance.KIND_SHEET_CONNECTION_VALUE
        )
        return recovery.UserIssue.needs_user(
            operation=str(operation or "attendance_chat_status"),
            title="학급 단톡방 상태를 확인하지 못했어요.",
            message=guidance.reason,
            change_status="기존 출결 자료와 현재 연결은 그대로입니다.",
            actions=guidance.actions,
        ).with_guidance(steps=guidance.steps)
    if message == google_account.GOEDU_ACCOUNT_REQUIRED_MESSAGE or (
        ("@goedu.kr" in message or "Google 계정" in message) and "로그인" in message
    ):
        # Google 계정 로그인이 없거나 다른 계정이면 설정의 로그인 단계로 보낸다.
        return recovery.UserIssue.needs_user(
            operation=str(operation or "google_login"),
            title="Google 계정으로 로그인해 주세요.",
            message=message,
            change_status="확인된 자료는 바꾸지 않았습니다.",
            actions=(recovery.IssueAction("google-login", "Google 로그인 설정 열기"),),
            resume="google-login",
        )
    # 그 밖의 화면 안전 문장도 시도 0회 '예기치 않은 문제'로 바꾸지 않는다.
    # 실제 원인과 무관한 로그인 확인 지시가 붙는 것을 막는다 (AGENTS.md 세 번 실패 절).
    fallback = _SCREEN_FAILURES.get(str(operation or ""), _DEFAULT_SCREEN_FAILURE)
    return recovery.UserIssue.needs_user(
        operation=str(operation or "screen_safe_stop"),
        title="확인이 필요해요.",
        message=message or fallback,
        change_status="현재 화면과 저장된 자료를 확인해 주세요.",
        actions=(),
    )


_SESSION_TRANSITIONS = frozenset({
    "google_status", "gws_login_start", "gws_login_status", "gws_login_cancel",
    "gws_login_submit_code", "gws_reapprove_start", "gws_logout", "get_app_info",
})
_SESSION_GLOBAL = _SESSION_TRANSITIONS | frozenset({
    "get_update_info", "update_offer", "decline_update", "start_update", "quit_app", "error_report_status",
    "computer_status", "network_status", "open_url", "open_picture_guide",
    "restart_helper",
    "log_attendance_ui_event",
})
_SESSION_EMPTY_READS = frozenset({
    "read_profile", "read_grid", "get_messenger_settings",
    "recent_captures", "capture_history_page", "capture_progress",
})

_SESSION_READS = _SESSION_EMPTY_READS | frozenset({
    "home_checks", "attendance_chat_status", "attendance_chat_spaces",
    "list_calendars", "list_tasklists", "google_target_statuses", "verify_google_target_candidate",
    "verify_gemini_key", "probe_hotkey", "check_attachment_folder", "choose_attachment_folder",
    "open_attendance_chat", "student_chat_connect_status", "student_chat_connect_progress",
    # Their writes run on the server under this account's session, so they take no local write
    # lock. The account token is still compared before and after the call.
    "data_call", "attendance_ai_input",
})


# Only observations may be continued by the desktop. No writes, starts, creates,
# open-browser actions, or local-storage recovery belong in this set.
_CONTINUABLE_READS = frozenset({
    "google_status", "gws_login_status", "list_calendars", "list_tasklists",
    "google_target_statuses", "attendance_chat_status", "attendance_chat_spaces",
})


def _can_continue_read(name, error, transport_pending=False):
    if name not in _CONTINUABLE_READS:
        return False
    chain = _journal_error_chain(error)
    # A completed three-attempt backend operation must not become a new batch.
    if any(isinstance(item, recovery.FinalOperationFailure) for item in chain):
        return False
    if any(isinstance(item, account_sessions.AccountSessionError) for item in chain):
        return False
    for item in chain:
        status = getattr(item, "status", getattr(item, "code", None))
        if isinstance(status, int) and 400 <= status < 500 and status not in {408, 429}:
            return False
    pending_identity = transport_pending and any(
        isinstance(item, recovery.UserActionRequired) and item.issue.operation == "google_auth_status"
        for item in chain
    )
    return pending_identity or recovery.transient_read_error(error)


# One messenger-tab read batch (list_calendars, list_tasklists, google_target_statuses
# sharing a screen-issued id) reuses one verified `gws auth status` of the same account
# and generation. Writes and calls without an id keep their own check.
# The attendance-tab reads and the Home check join the same mechanism (2026-09-28).
_AUTH_BATCH_ARG = {"list_calendars": 0, "list_tasklists": 0, "google_target_statuses": 1,
                   "attendance_chat_status": 0,
                   "attendance_chat_spaces": 0, "home_checks": 0,
                   "student_chat_connect_status": 0}


def _auth_batch_id(name, args, kwargs):
    index = _AUTH_BATCH_ARG.get(name)
    if index is None:
        return ""
    value = kwargs.get("batch", args[index] if len(args) > index else "")
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else ""


def guarded(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        request_token = None
        request_dir = None
        capture_gate = None
        previous_dir = getattr(self._session_context, "config_dir", None)
        previous_observed = getattr(self._session_context, "observed", None)
        previous_delayed = getattr(self._session_context, "delayed_reads", None)
        previous_batch = getattr(self._session_context, "auth_batch", None)
        name = method.__name__
        delayed_reads = set() if name in _CONTINUABLE_READS else None
        self._session_context.delayed_reads = delayed_reads
        try:
            if name == "retry_capture":
                if not self._capture_retry_lock.acquire(blocking=False):
                    raise ScreenSafeError("다른 실패 기록을 다시 처리하고 있어요. 끝난 뒤 눌러 주세요.")
                capture_gate = self._capture_retry_lock
            expected = getattr(self._session_context, "expected", None)
            # Status is observable while a write runs. Only writes exclude
            # credential changes; per-operation duplicate locks remain in place.
            writes = name not in _SESSION_READS and name not in _SESSION_GLOBAL
            credential_change = name in {"gws_login_start", "gws_login_cancel", "gws_logout"}
            lock = account_sessions.session_lock(self._account_root, timeout=self._session_timeout()) if writes or credential_change else nullcontext()
            with lock:
                try:
                    state = account_sessions.read_state(self._account_root)
                    request_token = (state["account"], state["generation"])
                    request_dir = account_sessions.active_config_dir(self._account_root, state)
                except account_sessions.AccountSessionStorageError:
                    if name not in {"google_status", "gws_login_start", "gws_login_status", "gws_login_cancel",
                                    "gws_login_submit_code", "gws_reapprove_start"}:
                        raise
                    state = {"managed": True, "phase": "unavailable"}
                    request_dir = self._account_root
                self._session_context.config_dir = request_dir
                self._session_context.observed = request_token
                batch_id = _auth_batch_id(name, args, kwargs)
                self._session_context.auth_batch = (
                    (batch_id, *request_token) if batch_id and request_token and request_token[0] else None)
                if (not batch_id and name in _AUTH_BATCH_ARG and previous_batch
                        and tuple(previous_batch[1:]) == tuple(request_token or ())):
                    # home_checks' own attendance_status read stays in the Home check's batch.
                    self._session_context.auth_batch = previous_batch
                if name not in {"google_status", "get_app_info"} and expected is not None and tuple(expected) != request_token:
                    raise account_sessions.AccountSessionError()
                if name not in _SESSION_GLOBAL:
                    if self._local_settings_error and self._local_settings_error_token == request_token:
                        raise account_sessions.AccountSessionError(self._local_settings_error)
                    known = self._verified_google_account
                    if known and state.get("managed") and known != state.get("account"):
                        raise account_sessions.AccountSessionError("이 컴퓨터에 새 계정 설정을 저장하지 못했어요. 설정의 저장 폴더 안내를 확인해 주세요.")
                    if state.get("managed") and state.get("phase") != "active":
                        if name in _SESSION_EMPTY_READS and state.get("phase") == "signed_out":
                            return self._signed_out_read(name, request_token)
                        if name not in _SESSION_READS:
                            raise account_sessions.AccountSessionError("설정에서 Google 연결을 확인한 뒤 다시 진행해 주세요.")
                result = _ok(method(self, *args, **kwargs))
                try:
                    current_token = account_sessions.token(self._account_root)
                except account_sessions.AccountSessionStorageError:
                    if name not in _SESSION_TRANSITIONS:
                        raise
                    current_token = request_token or ("", "")
                if name not in {"google_status", "gws_login_start", "gws_login_status", "gws_login_submit_code", "gws_logout"} and current_token != request_token:
                    raise account_sessions.AccountSessionError()
                local_problem = self._local_settings_error and self._local_settings_error_token == request_token
                empty_problem_bootstrap = name == "get_app_info" and bool(result.get("data", {}).get("local_settings_error"))
                if local_problem and (name not in _SESSION_GLOBAL or (name == "get_app_info" and not empty_problem_bootstrap)):
                    raise account_sessions.AccountSessionError(self._local_settings_error)
                result["session"] = list(current_token)
                data = result.get("data")
                settled_read = isinstance(data, dict) and data.get("read_pending") is False
                if delayed_reads and not settled_read:
                    return {"ok": False, "read_pending": True, "session": list(current_token)}
                return result
        except Exception as error:  # noqa: BLE001 - JS에는 사람이 읽을 안내만 보낸다
            if request_token is not None and not isinstance(error, account_sessions.AccountSessionError):
                try:
                    if request_token == account_sessions.token(self._account_root):
                        if _can_continue_read(name, error, bool(delayed_reads)):
                            # The bounded attempt ended, not the user's read.
                            # No final error/report and no secret-bearing cause.
                            return {"ok": False, "read_pending": True,
                                    "session": list(request_token)}
                        return _fail(error, name, request_dir, self._report_issue)
                except account_sessions.AccountSessionError:
                    pass
            return _fail(error, name)
        finally:
            self._session_context.config_dir = previous_dir
            self._session_context.observed = previous_observed
            self._session_context.delayed_reads = previous_delayed
            self._session_context.auth_batch = previous_batch
            if previous_delayed is not None and delayed_reads:
                previous_delayed.update(delayed_reads)
            if capture_gate is not None:
                capture_gate.release()

    wrapper._session_guarded = True
    return wrapper


@dataclass
class BridgeDeps:
    """전 필드 주입 가능. None이면 engine/표준 기본 구현을 쓴다."""

    gemini_transport: object = None
    hotkey_register: object = None
    hotkey_unregister: object = None
    hotkey_modifier_probe: object = None
    home_check_deps: object = None
    apply_deps: object = None
    server_request: object = None             # (method, url, body, headers, timeout) -> (status, object)
    helper_restart: object = None
    helper_window_exists: object = None
    recovery_sleeper: object = None
    helper_stop: object = None
    helper_hotkey_pause: object = None
    helper_hotkey_resume: object = None
    autostart_checker: object = None
    autostart_enable: object = None
    autostart_disable: object = None
    url_opener: object = None
    edge_exe_url_opener: object = None
    edge_url_opener: object = None
    external_url_platform: object = None
    https_handler_available: object = None
    edge_protocol_available: object = None
    folder_picker: object = None
    environ: object = None
    update_checker: object = None
    update_starter: object = None
    update_completion_reader: object = None
    update_opener: object = None
    update_launcher: object = None
    update_dest_dir: object = None
    update_helper_is_running: object = None
    update_helper_resume: object = None
    update_setup_is_open: object = None
    update_setup_process_paths: object = None
    gemini_key_pusher: object = None
    attendance_remote_work_timeout_seconds: object = None
    support_mail_opener: object = None
    error_report_poster: object = None        # (url, body) -> HTTP 상태 코드
    error_report_flush_runner: object = None  # fn -> None; 기본은 데몬 스레드
    windows_version: object = None            # () -> str


class Api:
    def __init__(self, config_dir, deps: BridgeDeps | None = None):
        self._account_root = account_sessions.root_config_dir(config_dir)
        self._session_context = threading.local()
        self._session_identity_available = None
        self._verified_google_account = None
        self._local_settings_error = ""
        self._local_settings_error_token = None
        self._deps = deps or BridgeDeps()
        self._signin = None
        self._signin_finish_lock = threading.RLock()
        self._server_session = None
        self._server_session_lock = threading.Lock()
        self._server_revocation_unconfirmed = False
        # Why this PC signed out, kept until the next sign-in: the second read after the logout
        # no longer sees it, and the screen must keep saying 다시 로그인 (SIGN-IN).
        self._session_ended_code = ""
        # (account, hasAnyYear) from the last answer of the server. A failed read changes nothing (MAIN §7.14).
        self._known_year = None
        self._auth_batch_lock = threading.Lock()
        self._auth_batch_results = {}
        self._app_update_offer = None
        self._app_update_offer_key = ""
        self._gws_login_start_lock = threading.Lock()
        self._capture_retry_lock = threading.Lock()
        self._support_mail_opener = self._deps.support_mail_opener
        self._error_report_flush_lock = threading.Lock()
        self._error_report_retry_ids: set[str] = set()

    @property
    def _config_dir(self):
        pinned = getattr(self._session_context, "config_dir", None)
        return pinned if pinned is not None else account_sessions.active_config_dir(self._account_root)

    def _signed_out_read(self, name, token):
        empty = {
            "read_profile": {}, "read_grid": [], "get_messenger_settings": {},
            "recent_captures": [], "capture_history_page": {"items": [], "total": 0, "page": 1},
            "capture_progress": {},
        }
        return {"ok": True, "data": empty[name], "session": list(token)}

    def _session_timeout(self):
        configured = self._deps.attendance_remote_work_timeout_seconds
        return 10.0 if configured is None else max(0.01, float(configured))

    def account_call(self, name, args, expected_token=None):
        """Bind a screen request to its displayed account before any mutation."""
        target = getattr(self, str(name), None) if isinstance(name, str) and not name.startswith("_") else None
        if not callable(target) or not getattr(target, "_session_guarded", False) or not isinstance(args, list):
            return _fail(ScreenSafeError("이 화면의 작업을 확인하지 못했어요."), "account_call")
        if expected_token is not None and (
            not isinstance(expected_token, list) or len(expected_token) != 2
            or not all(isinstance(value, str) for value in expected_token)
        ):
            return _fail(ScreenSafeError("현재 Google 계정을 다시 확인해 주세요."), str(name))
        self._session_context.expected = expected_token
        try:
            return target(*args)
        finally:
            self._session_context.expected = None


    def _reset_account_results(self):
        # This PC's family and helper child end at the server, and nothing at Google (SIGNIN-02).
        # A 5.x attendance session file stays as it is: a return to 5.x must find it (P2 rollback).
        self._server_revocation_unconfirmed = not self._server_session_client().end_family()
        self._server_session = None

    def _server_session_client(self):
        """The session client of the active account folder, never a folder pinned earlier (LOGIN-28)."""
        store = server_session.open_store(account_sessions.active_config_dir(self._account_root))
        with self._server_session_lock:
            client = self._server_session
            if client is None or client.folder != store.path.parent:
                client = server_session.ServerSessionClient(
                    server_session.server_base_url(self._gws_base_environ()),
                    store,
                    request=self._deps.server_request,
                    sleeper=self._deps.recovery_sleeper or time.sleep,
                    app_version=version.APP_VERSION,
                )
                self._server_session = client
            return client

    def _server_session_usable(self, state) -> bool:
        """True when the active owner of this folder has a session here. Reads no network."""
        account = str(state.get("account") or "")
        if state.get("phase") != "active" or not account or state.get("settings_owner") != account:
            return False
        return self._server_session_client().restore(account)


    def _open_external_url(self, url) -> dict:
        return external_url.open_external_url(
            url,
            default_opener=self._deps.url_opener,
            edge_exe_opener=self._deps.edge_exe_url_opener,
            edge_opener=self._deps.edge_url_opener,
            platform=self._deps.external_url_platform,
            https_handler_available=self._deps.https_handler_available,
            edge_protocol_available=self._deps.edge_protocol_available,
        )

    # ----- setup-state -----

    def _state_path(self) -> Path:
        return self._config_dir / SETUP_STATE_NAME

    def _fresh_state(self) -> dict:
        return json.loads(json.dumps(_FRESH_STATE))

    def _write_state(self, state: dict) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        component_lock.atomic_write_text_unique(
            self._state_path(),
            json.dumps(state, ensure_ascii=False, indent=1) + "\n",
        )

    def _migrate_state(self, state: dict) -> tuple[dict, bool]:
        """Keep saved input while mapping old progress to the nine-step flow."""

        merged = self._fresh_state()
        merged.update(state)
        try:
            saved_version = int(state.get("version") or 1)
        except (TypeError, ValueError):
            saved_version = 1

        def bounded_step(value, default=1) -> int:
            try:
                number = int(value)
            except (TypeError, ValueError):
                number = default
            return max(1, min(SETUP_LAST_STEP, number))

        if saved_version == 2:
            def insert_roster(value):
                old = min(8, bounded_step(value))
                return old + 1 if old >= 6 else old
            merged["step"] = insert_roster(merged.get("step"))
            merged["max_step"] = max(merged["step"], insert_roster(merged.get("max_step")))
            merged["version"] = SETUP_STATE_VERSION
            if merged.get("completed"):
                merged["step"] = merged["max_step"] = SETUP_LAST_STEP
            return merged, True

        if saved_version >= SETUP_STATE_VERSION:
            if bool(merged.get("completed")):
                # 완료 기록은 중간 단계 숫자가 깨져도 기존 사용자를 처음 화면으로
                # 되돌리지 않는다. 초안과 미래 보조값은 merged에 그대로 남긴다.
                merged["step"] = SETUP_LAST_STEP
                merged["max_step"] = SETUP_LAST_STEP
                return merged, merged != state
            merged["step"] = bounded_step(merged.get("step"), 1)
            merged["max_step"] = max(
                merged["step"],
                bounded_step(merged.get("max_step"), merged["step"]),
            )
            return merged, merged != state

        completed = bool(merged.get("completed"))

        def move(value) -> int:
            try:
                old = int(value or 1)
            except (TypeError, ValueError):
                old = 1
            if completed and old >= 7:
                return SETUP_LAST_STEP
            if old >= 7:
                # Incomplete legacy setup returns to Google connection, which now
                # includes student preparation and the first-setup completion gate.
                return SETUP_LAST_STEP - 1
            mapped = _V1_STEP_TO_V2.get(max(1, old), 1)
            return mapped + 1 if mapped >= 6 else mapped

        merged["version"] = SETUP_STATE_VERSION
        merged["step"] = move(merged.get("step"))
        merged["max_step"] = max(
            merged["step"], move(merged.get("max_step") or merged["step"])
        )
        return merged, True

    def _load_state(self) -> dict:
        path = self._state_path()
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    merged, changed = self._migrate_state(state)
                    if changed:
                        self._write_state(merged)
                    return merged
            except (TypeError, ValueError):
                pass
            if paths.profile_path(self._config_dir).exists():
                # 읽을 수 없는 진행 파일을 빈 값으로 덮지 않는다. 실제 사용자 정보가
                # 있으면 화면만 완료 상태로 열고, 망가진 원본은 그대로 남겨 복구할 수 있게 한다.
                state = self._fresh_state()
                state["completed"] = True
                state["step"] = SETUP_LAST_STEP
                state["max_step"] = SETUP_LAST_STEP
                return state
            return self._fresh_state()
        if paths.profile_path(self._config_dir).exists():
            # 기존 사용자(웹 UI 이전 설치): 마법사로 끌고 가지 않는다.
            state = self._fresh_state()
            state["completed"] = True
            state["step"] = SETUP_LAST_STEP
            state["max_step"] = SETUP_LAST_STEP
            self._write_state(state)
            return state
        return self._fresh_state()

    @guarded
    def get_app_info(self):
        session = account_sessions.read_state(self._config_dir)
        local_problem = self._local_settings_error if self._local_settings_error_token == (session.get("account"), session.get("generation")) else ""
        # A failed /v1/me says nothing about the sign-in. The session saved on this PC decides, so a
        # teacher who finished setup is not sent back to sign-in by it (LOGIN-17, LOGIN-32, C6, C8).
        verified = session.get("phase") == "active" and not local_problem and (
            (self._session_identity_available is True and self._verified_google_account == session.get("account"))
            or self._saved_session_answers_for(session))
        inactive = not verified
        signed_out = False  # Home kept after logout on a set-up PC.
        state = self._fresh_state() if inactive else self._load_state()
        if inactive:
            # An empty first installation still begins with its introduction.
            # Existing saved data stays hidden until identity is established.
            if session.get("managed") or paths.profile_path(self._config_dir).exists() or self._state_path().exists():
                state.update(step=2, max_step=2)
                # The owner (2026-11-09): a PC that finished setup keeps its Home after logout, and the
                # teacher signs in again from 설정 there, as before. Only an unfinished setup shows step 2.
                saved = self._load_state()
                if saved.get("completed"):
                    state, signed_out = saved, True
        elif session.get("managed") and not state["completed"]:
            state["step"] = max(3, state["step"])
            state["max_step"] = max(3, state.get("max_step", 1))
        elif self._year_missing_for_homeroom(session):
            # MAIN §7.14: ACCT-01 reuses a finished folder, which would open Home with no year.
            # The state file is not rewritten; the wizard's own saves replace it.
            state = {**state, "completed": False, "step": 3, "max_step": 3, "draft": self._saved_values_draft()}
        # 켤 때 못 보낸 오류 보고가 남아 있으면 이 기회에 다시 보낸다(화면을 막지 않음).
        if not inactive:
            self._start_error_report_flush()
        return {
            "version": version.APP_VERSION,
            "branding": dict(version.BRANDING),
            "mode": "home" if state["completed"] else "wizard",
            "step": state["step"],
            "max_step": int(state.get("max_step") or state["step"]),
            "draft": state["draft"],
            "account_session": session.get("phase", "legacy"),
            "local_settings_error": local_problem,
            "signed_out": signed_out,
            # 'logout': the teacher logged out here. Otherwise this PC has no server session for an
            # active folder: an update from the sheet version (5.x) or an ended session.
            "signed_out_reason": ("logout" if session.get("phase") == "signed_out" else "signin-required") if signed_out else "",
            "features": {
                "attendance_ui_enabled": engine.attendance_ui_enabled(),
            },
        }

    def _saved_session_answers_for(self, session) -> bool:
        try:
            return self._server_session_usable(session)
        except account_sessions.AccountSessionStorageError:
            return False

    def _year_missing_for_homeroom(self, session) -> bool:
        if not session.get("account") or self._known_year != (session.get("account"), False):
            return False
        try:
            return engine.read_profile_values(self._config_dir).get("담임여부") == "예"
        except (OSError, ValueError):
            return False

    def _saved_values_draft(self) -> dict:
        """The wizard's fields filled from this folder's saved files."""
        draft = self._fresh_state()["draft"]
        draft["profile"] = engine.read_profile_values(self._config_dir)
        try:
            draft["grid"] = engine.read_timetable_grid(self._config_dir)
        except (OSError, ValueError):
            draft["grid"] = None
        draft["bridge"] = self._messenger_settings_view()
        return draft

    def _remember_year(self, identity) -> None:
        if identity.get("logged_in") and isinstance(identity.get("hasAnyYear"), bool):
            self._known_year = (str(identity.get("user") or "").strip().casefold(), identity["hasAnyYear"])

    @guarded
    def error_report_status(self, report_id=""):
        """The failure panel footer follows the real send result (sent/sending/retry/queued).

        The panel is drawn before the background send finishes, so the reply alone
        can only say "queued"; the screen reads this afterwards (UPD-01 footer).
        """
        wanted = str(report_id or "").strip()
        if not wanted:
            return {"state": "unknown"}
        rows = error_reports.queue_rows(self._config_dir)
        row = next((row for row in rows if str(row["report"].get("reportId") or "") == wanted), None)
        if row is None:
            return {"state": "unknown"}
        if row.get("status") == "sent":
            return {"state": "sent"}
        if self._error_report_flush_lock.locked():
            return {"state": "sending"}
        if wanted in self._error_report_retry_ids:
            return {"state": "retry"}
        return {"state": "queued"}

    @guarded
    def get_update_info(self):
        return self._read_app_update_offer(
            "get_update_info",
            "업데이트를 확인하지 못했어요.",
        )

    @guarded
    def update_offer(self, fetch=None, today=None):
        """켤 때 한 번 묻기 위한 정보. 물을 일이 없으면 ask=False.

        '버전 및 제작 정보' 화면의 배너·상태도 이 결과 하나로 채운다 — 부팅할 때
        get_update_info를 따로 또 부르면 같은 배포 정보를 인터넷에서 두 번 받아 오게
        된다. status/available/latest/notes/url/sha256은 오늘 이미 취소했든 아니든
        실제 확인 결과를 그대로 담고, ask만 "지금 확인창을 띄워도 되는지"를 가른다.
        """
        import datetime as _dt

        day = str(today or _dt.date.today().isoformat())
        try:
            info = self._read_app_update_offer(
                "update_offer",
                "업데이트를 확인하지 못했어요.",
                fetch=fetch,
            )
        except recovery.FinalOperationFailure as error:
            # 켤 때의 자동 확인은 사용을 막거나 문제 화면을 띄우지 않는다.
            # 사용자가 직접 누르는 get_update_info만 공통 문제 화면으로 이어진다.
            info = {
                "status": "failed",
                "code": "UPDATE_INFO_UNAVAILABLE",
                "available": False,
                "latest": "",
                "notes": "",
                "url": "",
                "sha256": "",
                "reason": str(error.issue.reason or "업데이트 확인을 하지 못했어요."),
            }
        # 확인 자체가 실패(인터넷 끊김 등)했으면 '오늘 확인함'으로 남기지 않는다 —
        # 남기면 와이파이가 돌아온 뒤에도 그날 하루는 다시 확인할 길이 없어진다.
        if info.get("status") != "failed":
            engine.remember_update_checked(self._config_dir, day)
        base = {
            "status": info.get("status", "failed"),
            "code": str(info.get("code") or ""),
            "available": bool(info.get("available")),
            "latest": str(info.get("latest", "") or ""),
            "notes": str(info.get("notes", "") or ""),
            "url": info.get("url", "") or "",
            "sha256": info.get("sha256", "") or "",
            "reason": info.get("reason", "") or "",
        }
        if not info.get("available"):
            return {**base, "ask": False}
        if not engine.should_ask_update(self._config_dir, base["latest"], day):
            return {**base, "ask": False}
        return {**base, "ask": True}

    @guarded
    def decline_update(self, latest="", today=""):
        """오늘은 그만 물어 달라는 뜻. latest가 비어 있으면 기록에 아무것도 남기지 않는다 —
        빈 문자열이 declined_version으로 저장되면 다음 확인 때 헷갈릴 수 있다.

        today는 update_offer와 같은 뜻이고 같은 자리에서 온다. 화면은 안 넘기고
        오늘 날짜를 그대로 쓰지만, 시험이 update_offer에만 날짜를 넣고 여기엔 못 넣으면
        두 날짜가 어긋나서 그날그날 결과가 달라진다.
        """
        import datetime as _dt

        latest = str(latest or "").strip()
        if latest:
            when = str(today or "").strip() or _dt.date.today().isoformat()
            engine.remember_update_declined(self._config_dir, latest, when)
        return {"ok": True}

    @guarded
    def start_update(self, url="", latest="", sha256=""):
        # 화면이 이미 아는 url을 넘겨받아 재조회 없이 바로 받는다(통신 깜빡임 오안내 방지).
        shown = {
            "url": str(url or ""),
            "latest": str(latest or ""),
            "sha256": str(sha256 or ""),
        }
        if (
            self._app_update_offer is None
            or self._app_offer_key(shown) != self._app_update_offer_key
        ):
            return self._component_operation(
                "start_update",
                "앱 업데이트를 시작하지 못했어요.",
                "update_setup_launch",
                lambda: {
                    "started": False,
                    "code": "UPDATE_OFFER_CHANGED",
                    "latest": shown["latest"],
                    "reason": "화면에서 확인한 업데이트 정보가 달라 안전하게 중단했어요.",
                },
                change_status="현재 앱과 기존 설정은 그대로입니다.",
            )

        exact = dict(self._app_update_offer)
        starter = self._deps.update_starter or engine.start_update
        completion_reader = (
            self._deps.update_completion_reader or engine.verify_update_run_stage
        )
        start_kwargs = {
            **exact,
            "stop_before_launch": self._deps.helper_stop or engine.stop_helper,
            "config_dir": self._config_dir,
        }
        if self._deps.update_opener is not None:
            start_kwargs["opener"] = self._deps.update_opener
        if self._deps.update_launcher is not None:
            start_kwargs["launch"] = self._deps.update_launcher
        if self._deps.update_dest_dir is not None:
            start_kwargs["dest_dir"] = self._deps.update_dest_dir
        if self._deps.update_helper_is_running is not None:
            start_kwargs["helper_is_running"] = self._deps.update_helper_is_running
        if self._deps.update_helper_resume is not None:
            start_kwargs["resume_after_launch_failure"] = self._deps.update_helper_resume
        if self._deps.update_setup_is_open is not None:
            start_kwargs["setup_is_open"] = self._deps.update_setup_is_open
        else:
            start_kwargs["setup_is_open"] = lambda path: engine.setup_process_is_open(
                path,
                process_paths=self._deps.update_setup_process_paths,
            )

        def start_once():
            result = dict(starter(version.APP_VERSION, **start_kwargs) or {})
            code = str(
                result.get("code")
                or ("UPDATE_SETUP_LAUNCHED" if result.get("started") else "UPDATE_DOWNLOAD_UNAVAILABLE")
            )
            result["code"] = code
            if code == "UPDATE_SETUP_LAUNCHED":
                result.update({"started": True, "stage": "setup_opened", "reason": ""})
            return result

        def readback():
            complete, status = completion_reader(
                self._config_dir,
                **exact,
                expected_stage="setup_opened",
            )
            if not complete:
                return False, status
            return True, {
                **status,
                "started": True,
                "stage": "setup_opened",
                "reason": "",
            }

        def update_stage(value):
            code = str(value.get("code") or "") if isinstance(value, dict) else ""
            if code == "UPDATE_HELPER_BUSY":
                return "update_helper_stop"
            if code in {"UPDATE_SETUP_LAUNCHED", "UPDATE_SETUP_LAUNCH_FAILED"}:
                return "update_setup_launch"
            return "update_download"

        return self._component_operation(
            "start_update",
            "앱 업데이트를 시작하지 못했어요.",
            update_stage,
            start_once,
            verify=readback,
            change_status="현재 앱과 기존 설정은 그대로입니다.",
        )

    @guarded
    def quit_app(self):
        # 설치 창이 뜬 뒤 프로그램이 스스로 닫힌다 — 응답을 먼저 보내려고 잠깐 늦춘다.
        import threading

        def _close():
            try:
                import webview

                for window in list(webview.windows):
                    window.destroy()
            except Exception:  # noqa: BLE001 - 닫기 실패는 설치기가 대신 닫아준다
                pass

        threading.Timer(0.4, _close).start()
        return True


    @guarded
    def save_setup_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("설정 진행 상태 모양이 올바르지 않아요")
        previous = self._load_state()
        combined = dict(previous)
        combined.update(state)
        # 이 문은 작성 중인 칸만 저장한다. 화면이 completed=true를 보내더라도
        # 완료 권한을 주지 않으며, 이미 끝낸 사용자는 늦은 저장으로 되돌리지 않는다.
        combined["completed"] = bool(previous.get("completed"))
        merged, _changed = self._migrate_state(combined)
        self._write_state(merged)
        return True

    @guarded
    def finish_setup(self):
        if not self._server_session_usable(account_sessions.read_state(self._account_root)):
            raise ScreenSafeError("Google 계정으로 먼저 로그인해 주세요.")
        state = self._load_state()
        state["version"] = SETUP_STATE_VERSION
        state["completed"] = True
        state["step"] = SETUP_LAST_STEP
        state["max_step"] = SETUP_LAST_STEP
        state["draft"] = self._fresh_state()["draft"]
        self._write_state(state)  # draft(키 포함)를 비운 채 기록
        return True


    # ----- 조회·검증 -----


    def _gws_base_environ(self) -> dict:
        """GWS가 물려받을 Windows 환경값의 읽기 전용 사본."""

        return dict(os.environ if self._deps.environ is None else self._deps.environ)


    @guarded
    def home_checks(self, batch=""):
        # GET /v1/me, one check of the saved targets and this PC's own files (plan step 8).
        identity = self._read_server_identity()
        identity.pop("local_settings_error", None)
        statuses = None
        if identity.get("logged_in"):
            try:
                statuses = self._server_target_statuses(engine.saved_target_ids(self._config_dir))
            except ServerSessionEnded:
                statuses = None
        results = self._local_read(
            "home_checks", "이 컴퓨터의 점검 내용을 읽지 못했어요.",
            lambda: engine.home_checks(self._config_dir, deps=self._deps.home_check_deps,
                                       identity=identity, target_statuses=statuses),
        )
        return [asdict(r) for r in results]


    @guarded
    def open_attendance_chat(self):
        """Open Chat with the account signed in to the server (P6: no local Google tool)."""
        state = account_sessions.read_state(self._account_root)
        if not self._server_session_usable(state):
            raise ScreenSafeError("Google 계정으로 먼저 로그인해 주세요.")
        account = state.get("account") or ""
        # A bare authuser falls back to the browser's default account (LOGIN-23).
        return self._open_external_url(
            external_url.with_google_account("https://chat.google.com/", account)
        )


    def _chat_data(self, route, body):
        _account, call = self._bound_server_call()
        reply = call(route, body)
        if reply.get("status") != 200 or not isinstance(reply.get("body"), dict):
            if not reply.get("status") or reply.get("status", 0) >= 500:
                # The client already exhausted the safe-read budget. Record a failed observation,
                # never an approval/logout instruction and never another outer retry loop.
                raise recovery.RetryableOperationError("CHAT_STATUS_UNAVAILABLE", "Google Chat 확인하지 못했어요")
            raise ScreenSafeError("Google Chat 확인하지 못했어요")
        return reply["body"]

    @guarded
    def attendance_chat_status(self, batch=""):
        value = self._chat_data("/v1/data/chat/status", {})
        space = value.get("classSpace") or value.get("savedClassSpace") or {}
        return {**value, "class_space_id": space.get("name", ""), "class_space_name": space.get("displayName", ""),
                "read_failed": value.get("connected") is None}


    CHAT_SPACES_LOCK_WAIT_SECONDS = 60.0

    @guarded
    def attendance_chat_spaces(self, batch=""):
        value = self._chat_data("/v1/data/chat/status", {})
        if value.get("connected") is False:
            raise ScreenSafeError("Google 권한을 다시 승인해 주세요.")
        if value.get("groupState") in {"unverified", "denied"}:
            raise ScreenSafeError("학급 단톡방 확인하지 못했어요")
        space = value.get("classSpace")
        return [space] if space else []


    @guarded
    def attendance_chat_start(self, expected_count=0, group_text="", personal_text="", keys=None, use_existing_room=False, preview_token=""):
        if not preview_token:
            raise ScreenSafeError("미리보기")
        return self._chat_data("/v1/data/chat/send", {"previewToken": preview_token})

    # ---- 학생과 Chat 처음 연결하기 (2026-10-29) ---------------------------------
    @guarded
    def student_chat_connect_status(self, batch=""):
        from dashboard import central_chat
        value = self._chat_data("/v1/data/read", {})
        roster = [{"name": x.get("name", ""), "number": str(x.get("number", "")), "combined": str(x.get("number", "")) + x.get("name", ""), "email": x.get("email", "")}
                  for x in value.get("students", []) if x.get("status") == "재적"]
        records = {x["id"][2:]: x for x in value.get("greetings", []) if x["id"].startswith("p_")}
        records = {key: dict(row) for key, row in records.items()}
        effects = {x["id"]: x for x in value.get("effects", [])}
        now_ms = datetime.fromisoformat(value["serverNow"].replace("Z", "+00:00")).timestamp() * 1000
        for record in records.values():
            effect = effects.get(record.get("effectId"), {})
            if (effect.get("phase") == "CLAIMED" and effect.get("leaseUntil", 0) > now_ms) or (effect.get("phase") == "DISPATCHED_OR_UNKNOWN" and now_ms - datetime.fromisoformat(effect["admittedAt"].replace("Z", "+00:00")).timestamp() * 1000 < 600000):
                record["state"] = "pending"
        summary = central_chat.scc_summarize(roster, records)
        summary["pending"] = sum(1 for row in summary["unknown"] if records.get(row["key"], {}).get("state") == "pending")
        summary["unknown"] = [row for row in summary["unknown"] if records.get(row["key"], {}).get("state") != "pending"]
        by_key = {central_chat.scc_email_key(x.get("email", "")): x["id"] for x in value.get("students", []) if x.get("email")}
        for row in summary["fixed"] + summary["targets"]:
            row["studentId"] = by_key.get(row["key"], "")
        doc = value.get("yearDoc") or {}
        group_text, personal_text = central_chat.class_greeting_defaults({"담임학년": doc.get("grade"), "담임반": doc.get("classNumber")})
        space = doc.get("classSpace") or {}
        class_key = "c_" + hashlib.sha256(json.dumps([space.get("name", "")], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        greeting = next((x for x in value.get("greetings", []) if x["id"] == class_key), {})
        greeting = dict(greeting)
        effect = effects.get(greeting.get("effectId"), {})
        if (effect.get("phase") == "CLAIMED" and effect.get("leaseUntil", 0) > now_ms) or (effect.get("phase") == "DISPATCHED_OR_UNKNOWN" and now_ms - datetime.fromisoformat(effect["admittedAt"].replace("Z", "+00:00")).timestamp() * 1000 < 600000):
            greeting["state"] = "pending"
        return {**summary, "greeting": personal_text, "group_text": group_text, "personal_text": personal_text,
                "member_count": summary["total"], "min": 2, "max": 49,
                "class_greeting": {"space": space.get("name", ""), "state": greeting.get("state", "")}}

    @guarded
    def student_chat_connect_progress(self):
        done, total = getattr(self, "_student_connect_progress", (0, 0))
        return {"done": done, "total": total}


    @guarded
    def student_chat_connect_resolve(self, key="", received=False):
        value = self._chat_data("/v1/data/read", {})
        records = value.get("greetings", [])
        if key == "__class__":
            space = (value.get("yearDoc") or {}).get("classSpace") or {}
            wanted = "c_" + hashlib.sha256(json.dumps([space.get("name", "")], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        else:
            wanted = "p_" + str(key)
        record = next((x for x in records if x["id"] == wanted), {})
        effect = next((x for x in value.get("effects", []) if x["id"] == record.get("effectId")), None)
        if not effect:
            raise ScreenSafeError("전달됐는지 모르는 메시지")
        versions = []
        for ref in effect["targetRefs"]:
            col, ident = ref.split("/")[-2:]
            versions.append(next((x["version"] for x in value.get(col, []) if x["id"] == ident), None))
        return self._chat_data("/v1/data/effects/" + effect["id"] + "/decide", {"year": value["year"], "choice": "sent" if received else "not-sent", "versions": versions})

    @guarded
    def computer_status(self):
        return engine.computer_readiness()

    @guarded
    def network_status(self):
        """배경 이름 확인(예열)의 상태. 화면은 첫 확인이 느릴 때 기다려 달라고 알린다."""

        return dns_warm.shared_status()

    @guarded
    def google_status(self):
        return self._google_status_payload()

    def _status_payload(self, identity, session, local_settings_error=""):
        return {
            "authorization_reason": "",
            "session_ended": False,
            "server_unreachable": False,
            **identity,
            "account_session": session.get("phase", "legacy"),
            "local_settings_error": local_settings_error,
        }

    def _read_server_identity(self) -> dict:
        """`restore` plus GET /v1/me replace `gws auth status`. One try: the screen owns the three (OBS-04)."""
        try:
            state = account_sessions.read_state(self._account_root)
            if not self._server_session_usable(state):
                # An active folder with no session means the session ended without a logout here.
                return _signed_out_identity("SIGNIN_REQUIRED" if state.get("phase") == "active" else "")
            client = self._server_session_client()
            answer = client.me()
        except server_session.ServerSessionStorageError as error:
            return {**_identity_unread(server_unreachable=False), "local_settings_error": str(error)}
        except account_sessions.AccountSessionStorageError:
            return _identity_unread(server_unreachable=False)
        if answer.session_ended:
            # Only a refused resume token is a logout. A failed /v1/me is not (LOGIN-32).
            return _signed_out_identity(str((answer.body or {}).get("error") or "SIGNIN_REQUIRED"))
        if answer.status == 200 and isinstance(answer.body, dict) and answer.body.get("subjectKey") == client.subject_key:
            return _signed_in_identity(client.email, answer.body)
        return _identity_unread(server_unreachable=answer.status == 0 or answer.status >= 500)

    def _finish_signin(self):
        """ACCT-01 steps 3 and 4, on the thread of a screen call, once a redeem answer waits.

        The answer stays in the flow until its session is saved, so a busy or failed local write
        can be finished by the next call. Returns the status, or None when nothing waits.
        """
        flow = self._signin
        if flow is None or flow.answer() is None:
            return None
        with self._signin_finish_lock:
            answer = flow.answer()
            if answer is None:
                return None
            email = str(answer["email"]).strip().casefold()
            if flow.purpose == "reapprove":
                # The same family continues; only the server's grant changed.
                flow.forget_answer()
                identity = _signed_in_identity(answer["email"], answer)
                self._remember_year(identity)
                return self._status_payload(identity, account_sessions.read_state(self._account_root))
            try:
                with account_sessions.auth_observation(self._account_root, flow.account_token, email, timeout=self._session_timeout()):
                    switched = account_sessions.activate_verified(self._account_root, email, explicit_login=True)
                    if switched.get("changed"):
                        self._reset_account_results()  # Nothing of this answer is stored yet to revoke.
                    self._server_session_client().adopt_signin(answer, flow.device_id)
                    account_sessions.record_subject_key(self._account_root, email, answer["subjectKey"])
                    flow.forget_answer()
                    self._session_identity_available = True
                    self._verified_google_account = email
                    self._local_settings_error = ""
                    self._local_settings_error_token = None
                    self._session_ended_code = ""
                    session = account_sessions.read_state(self._account_root)
                identity = _signed_in_identity(answer["email"], answer)
                self._remember_year(identity)
                return self._status_payload(identity, session)
            except account_sessions.AccountSessionBusy as error:
                self._local_settings_error = str(error)
                self._local_settings_error_token = getattr(self._session_context, "observed", None)
                return {**self._status_payload(_identity_unread(server_unreachable=False), {"phase": "unavailable"}, str(error)),
                        "signin_finishing": True}
            except account_sessions.AccountSessionStorageError as error:
                self._local_settings_error = str(error)
                self._local_settings_error_token = getattr(self._session_context, "observed", None)
                return self._status_payload(_identity_unread(server_unreachable=False), {"phase": "unavailable"}, str(error))
            except account_sessions.AccountSessionError:
                # This sign-in no longer owns the account state, so nothing here will hold its family.
                flow.forget_answer()
                self._server_session_client().revoke_unheld(answer)
                return None

    def _google_status_payload(self):
        finished = self._finish_signin()
        if finished is not None:
            return finished
        identity = self._read_server_identity()
        self._remember_year(identity)
        if identity.get("session_ended"):
            self._session_ended_code = str(identity.get("error_code") or "")
        elif self._session_ended_code and identity.get("login_state") == "logged_out":
            identity = _signed_out_identity(self._session_ended_code)
        local_settings_error = identity.pop("local_settings_error", "")
        account = str(identity.get("user") or "").strip().casefold() if identity.get("logged_in") else ""
        expected = getattr(self._session_context, "observed", None)
        # A failed read changes nothing, so it needs no wait for the write lock (PEND-32).
        observed_account = account or (str(expected[0]) if expected and identity.get("login_state") == "error" else "")
        try:
            with account_sessions.auth_observation(self._account_root, expected, observed_account, timeout=self._session_timeout()) as session:
                # Assign no shared identity/result fields until this observation
                # is known to belong to the still-current local generation.
                self._session_identity_available = bool(account)
                self._verified_google_account = account or None
                self._local_settings_error = ""
                self._local_settings_error_token = None
                try:
                    if (not account and session.get("managed") and identity.get("login_state") != "error"
                            and session.get("phase") not in {"login_pending", "signed_out"}):
                        account_sessions.begin_logout(self._account_root)
                        account_sessions.complete_logout(self._account_root)
                        self._reset_account_results()
                    session = account_sessions.read_state(self._account_root)
                except account_sessions.AccountSessionStorageError as error:
                    self._local_settings_error = local_settings_error = str(error)
                    self._local_settings_error_token = expected
                    session = {"phase": "unavailable"}
        except (account_sessions.AccountSessionStorageError, account_sessions.AccountSessionBusy) as error:
            # Storage/active-write problems remain separate from the server's reply.
            # A stale observation is deliberately not caught here.
            session = {"phase": "unavailable"}
            local_settings_error = str(error)
            # Bind a local problem to the observed generation, so a delayed
            # failure cannot hide or relabel a newer account's configuration.
            self._local_settings_error = local_settings_error
            self._local_settings_error_token = expected
        return self._status_payload(identity, session, local_settings_error)


    @guarded
    def read_profile(self):
        return self._local_read(
            "read_profile", "이 컴퓨터에 저장된 내 정보를 읽지 못했어요.",
            lambda: engine.read_profile_values(self._config_dir, strict=True),
        )

    @guarded
    def read_grid(self):
        return self._local_read(
            "read_grid", "이 컴퓨터에 저장된 시간표를 읽지 못했어요.",
            lambda: engine.read_timetable_grid(self._config_dir, strict=True),
        )

    # ----- Calendar and Tasks targets, through /v1/data/targets (D5: the choice stays on this PC) ----

    def _target_answer(self, kind, body):
        """One call: data_call sends these POSTs once, so a read's three tries are its caller's."""
        self._server_session_usable(account_sessions.read_state(self._account_root))
        answer = self._server_session_client().data_call(_TARGET_ROUTES[kind][0], body)
        code = _server_error_code(answer.body)
        if answer.status == 401:
            raise ServerSessionEnded(server_session.SESSION_ENDED_TEXT)
        if code == "GOOGLE_PERMISSION_REQUIRED":
            raise GooglePermissionRequired(_GRANT_MISSING_TEXT)
        return answer, code

    def _target_rows_once(self, kind) -> list[dict]:
        answer, _code = self._target_answer(kind, {"action": "list"})
        rows = answer.body.get(_TARGET_ROUTES[kind][1]) if answer.status == 200 and isinstance(answer.body, dict) else None
        if not isinstance(rows, list):
            # A failure is never an empty list (C1, C2).
            raise recovery.RetryableOperationError("TARGET_LIST_READ", engine.GOOGLE_LISTS_FINAL_FAILURE)
        return [_target_row(kind, row) for row in rows if isinstance(row, dict) and row.get("id")]

    def _target_rows(self, kind) -> list[dict]:
        return recovery.run_operation(
            "google_lists",
            engine.GOOGLE_LISTS_FINAL_FAILURE,
            lambda: self._target_rows_once(kind),
            delays=recovery.NETWORK_DELAYS,
            change_status=engine.GOOGLE_UNCHANGED,
            app_version=version.APP_VERSION,
            **self._network_recovery_options(),
        )

    @guarded
    def list_calendars(self, batch=""):
        return [{key: value for key, value in row.items() if key != "owned"} for row in self._target_rows("calendar")]

    @guarded
    def list_tasklists(self, batch=""):
        return [{key: value for key, value in row.items() if key != "owned"} for row in self._target_rows("tasklist")]

    def _server_target_statuses(self, targets) -> dict:
        """Saved targets checked with GETs at the server; a target is never erased or recreated.

        Only an authoritative answer changes a row: anything else stays check_failed (C6, C8).
        """
        selected = {field: str(targets.get(field) or "").strip()
                    for field in engine.GOOGLE_SAVED_TARGET_KINDS if targets.get(field)}
        results = {field: {"id": target_id, "state": "check_failed", "detail": engine.GOOGLE_TARGET_CHECK_FAILED}
                   for field, target_id in selected.items()}
        for kind in ("calendar", "tasklist"):
            fields = [field for field in selected if engine.GOOGLE_SAVED_TARGET_KINDS[field] == kind]
            ids = sorted({selected[field] for field in fields})
            answers = {}
            for start in range(0, len(ids), _TARGET_CHECK_LIMIT):
                try:
                    answer, _code = self._target_answer(kind, {"action": "check", "ids": ids[start:start + _TARGET_CHECK_LIMIT]})
                except GooglePermissionRequired:
                    continue
                rows = answer.body.get("targets") if answer.status == 200 and isinstance(answer.body, dict) else None
                for row in rows if isinstance(rows, list) else []:
                    if isinstance(row, dict) and row.get("state") in {"ready", "unavailable"}:
                        answers[row.get("id")] = row["state"]
            for field in fields:
                state = answers.get(selected[field])
                if state:
                    results[field].update(state=state, detail=_TARGET_STATE_DETAILS[kind, state])
        return results

    @guarded
    def google_target_statuses(self, targets, batch=""):
        if not isinstance(targets, dict):
            raise ValueError("저장된 연결 정보를 확인해 주세요")
        return self._server_target_statuses(targets)

    @guarded
    def verify_google_target_candidate(self, kind, candidate_id, name, account):
        if kind not in _TARGET_ROUTES:
            raise ValueError("Google 연결 종류를 다시 확인해 주세요")
        if not all(isinstance(value, str) and value.strip() for value in (candidate_id, name, account)):
            raise ValueError("Google 연결 후보를 다시 확인해 주세요")
        client = self._server_session_client()
        if not client.restore(account.strip()):
            return {"verified": False}
        rows = self._target_rows(kind)
        return {"verified": any(row["owned"] and row["id"] == candidate_id.strip() and row["name"] == name.strip() for row in rows)}

    def _ensure_target_named(self, kind, name, confirm_new_attempt, empty_text, noun) -> dict:
        """One press, one server call. The server lists first and holds one insert per name (C7).

        `confirm_new_attempt` comes only from the teacher's answer on screen; nothing here sets it.
        """
        name = str(name or "").strip()
        if not name:
            raise ScreenSafeError(empty_text)
        body = {"action": "ensure", "name": name}
        if confirm_new_attempt is True:
            body["confirmNewAttempt"] = True
        answer, code = self._target_answer(kind, body)
        value = answer.body if isinstance(answer.body, dict) else {}
        target = value.get("target") if answer.status == 200 else None
        if isinstance(target, dict) and target.get("id"):
            return {"id": str(target["id"]), "name": name}
        if code == "TARGET_CREATE_PENDING":
            return {"state": "pending", "retry_after": value.get("retryAfterSeconds")}
        if code == "TARGET_CREATE_CONFIRM_REQUIRED":
            return {"state": "confirm-required"}
        if code == "TARGET_NAME_AMBIGUOUS":
            matches = value.get("matches")
            rows = [_target_row(kind, row) for row in (matches if isinstance(matches, list) else []) if isinstance(row, dict) and row.get("id")]
            raise engine._target_selection_issue(kind, rows)
        if answer.unknown or code == "TARGET_CREATE_UNKNOWN":
            raise ScreenSafeError(f"{noun}을 만들었는지 확인하지 못했어요. 2분 뒤 다시 눌러 주세요. 같은 이름이 이미 있으면 그대로 연결해요.")
        if code == "TARGET_NAME_INVALID":
            raise ScreenSafeError(f"{noun} 이름을 확인해 주세요.")
        raise ScreenSafeError(f"{noun}을 만들지 못했어요. 이름을 확인하고 잠시 뒤 다시 시도해 주세요.")

    @guarded
    def ensure_calendar_named(self, name, confirm_new_attempt=False):
        return self._ensure_target_named("calendar", name, confirm_new_attempt, "캘린더 이름을 적어 주세요", "캘린더")

    @guarded
    def ensure_tasklist_named(self, name, confirm_new_attempt=False):
        return self._ensure_target_named("tasklist", name, confirm_new_attempt, "할일 목록 이름을 적어 주세요", "할 일 목록")

    @guarded
    def verify_gemini_key(self, key, model=None):
        saved = load_settings(paths.settings_path(self._config_dir))
        status, detail = engine.verify_gemini_key(
            key, model or saved.gemini_model, transport=self._deps.gemini_transport
        )
        return {"status": status, "detail": detail}

    @guarded
    def probe_hotkey(self, text):
        return {
            "status": engine.probe_hotkey(
                text,
                register=self._deps.hotkey_register,
                unregister=self._deps.hotkey_unregister,
                modifier_probe=self._deps.hotkey_modifier_probe,
            )
        }

    @guarded
    def recent_captures(self, limit=20):
        rows = self._local_read(
            "recent_captures", "처리한 메시지 기록을 읽지 못했어요.",
            lambda: capture_store.read_captures(paths.bridge_state_dir(self._config_dir), int(limit)),
        )
        return [self._capture_retry_state(row) for row in rows]

    def _capture_retry_state(self, row):
        shown = dict(row)
        from brity_bridge import outbox
        delivery = outbox.local_state(self._config_dir, str(shown.get("source_hash") or ""))
        if delivery is not None:
            return {**shown, "retryable": False, "outbox": delivery}
        shown["retryable"] = pipeline.saved_capture_retry_available(
            self._config_dir,
            str(shown.get("source_hash") or ""),
            str(shown.get("when") or ""),
        )
        return shown

    @guarded
    def brity_outbox_decide(self, source_hash, action_key, choice):
        from brity_bridge import outbox
        observed = getattr(self._session_context, "observed", None)
        with account_sessions.work(self._account_root, expected_token=observed):
            return outbox.decide(self._config_dir, str(source_hash), str(action_key), str(choice))

    @guarded
    def capture_history_page(self, page=1, page_size=10):
        result = self._local_read(
            "capture_history_page", "처리한 메시지 기록을 읽지 못했어요.",
            lambda: capture_store.read_capture_page(
                paths.bridge_state_dir(self._config_dir), int(page), int(page_size)
            ),
        )
        result["items"] = [self._capture_retry_state(row) for row in result["items"]]
        return result

    @guarded
    def capture_progress(self):
        return self._local_read(
            "capture_progress", "처리 상태를 읽지 못했어요.",
            lambda: capture_store.read_progress(paths.bridge_state_dir(self._config_dir)),
        )

    @guarded
    def retry_capture(self, source_hash, capture_when=""):
        source_hash = str(source_hash or "")
        capture_when = str(capture_when or "")
        result = pipeline.retry_saved_capture(
            self._config_dir,
            source_hash,
            capture_when=capture_when,
        )
        return {
            "success": bool(result.ok),
            "stage": str(result.stage),
            "message": str(result.message),
        }

    # ----- 행동 -----

    def _success(self, ok, detail):
        return {"success": bool(ok), "detail": detail}


    @staticmethod
    def _app_offer_key(offer) -> str:
        if not isinstance(offer, dict):
            return ""
        exact = {
            "latest": str(offer.get("latest") or ""),
            "url": str(offer.get("url") or ""),
            "sha256": str(offer.get("sha256") or ""),
        }
        if not all(exact.values()):
            return ""
        return json.dumps(exact, sort_keys=True, separators=(",", ":"))

    def _remember_app_update_offer(self, info) -> None:
        if not isinstance(info, dict) or info.get("available") is not True:
            self._app_update_offer = None
            self._app_update_offer_key = ""
            return
        exact = {
            "latest": str(info.get("latest") or ""),
            "url": str(info.get("url") or ""),
            "sha256": str(info.get("sha256") or ""),
        }
        key = self._app_offer_key(exact)
        if not key:
            self._app_update_offer = None
            self._app_update_offer_key = ""
            return
        self._app_update_offer = exact
        self._app_update_offer_key = key

    def _read_app_update_offer(self, operation: str, title: str, *, fetch=None):
        checker = self._deps.update_checker or engine.check_update

        def read_once():
            kwargs = {"fetch": fetch} if fetch is not None else {}
            return checker(version.APP_VERSION, **kwargs)

        info = self._component_operation(
            operation,
            title,
            "update_offer",
            read_once,
            change_status="현재 앱과 기존 설정은 그대로입니다.",
        )
        if isinstance(info, dict):
            engine.record_update_transport(self._config_dir, "update_info", str(info.get("transport") or ""))
        self._remember_app_update_offer(info)
        return info


    def _new_signin_flow(self, client, on_complete=None):
        return server_session.SigninFlow(
            client,
            device_id=server_session.load_device_id(self._account_root),
            device_label_text=server_session.device_label(),
            app_version=version.APP_VERSION,
            opener=self._open_external_url,
            on_complete=on_complete,
            sleeper=self._deps.recovery_sleeper or time.sleep,
        )

    def _discard_unfinished_signin(self) -> None:
        """A new sign-in replaces a redeemed answer that could not be saved. Its family ends."""
        flow = self._signin
        if flow is None or flow.answer() is None:
            return
        with self._signin_finish_lock:
            answer = flow.answer()
            if answer is not None:
                flow.forget_answer()
                self._server_session_client().revoke_unheld(answer)

    @guarded
    def gws_login_start(self):
        with self._gws_login_start_lock:
            if self._signin is not None and self._signin.snapshot().get("running") is True:
                return self._signin.snapshot()
            self._discard_unfinished_signin()
            lease = _AttendanceRemoteWorkLease.acquire(self._config_dir, **self._attendance_remote_lock_options())
            flow = None
            try:
                state = account_sessions.read_state(self._account_root)
                flow = self._signin = self._new_signin_flow(self._server_session_client(), on_complete=lease.release)
                # The server answers first. If it cannot, nothing changes and this PC's session
                # stays (SIGN-IN 'What the teacher sees': server unreachable at sign-in).
                if flow.start(login_hint=state.get("account", "")):
                    account_sessions.begin_login(self._account_root)
                    self._reset_account_results()
                    flow.account_token = account_sessions.token(self._account_root)
                    flow.run()
                return flow.snapshot()
            except Exception:
                if flow is not None:
                    flow.abort()
                lease.release()
                raise

    @guarded
    def gws_reapprove_start(self):
        """권한 다시 승인: the same browser steps from a session. This PC's family continues."""
        with self._gws_login_start_lock:
            if self._signin is not None and self._signin.snapshot().get("running") is True:
                return self._signin.snapshot()
            self._discard_unfinished_signin()
            client = self._server_session_client()
            client.restore(account_sessions.read_state(self._account_root).get("account", ""))
            flow = self._signin = self._new_signin_flow(client)
            if flow.start(purpose="reapprove"):
                flow.run()
            return flow.snapshot()


    @guarded
    def gws_login_status(self):
        flow = self._signin
        snapshot = flow.snapshot() if flow is not None else server_session.idle_snapshot()
        if snapshot.get("ok") is not True:
            return snapshot
        # The redeem answer supplies both completion and the screen (LOGIN-19): no second read.
        status = self._google_status_payload()
        if status.get("signin_finishing"):
            # A local write holds the lock. That wait is no failure, so the screen keeps polling.
            return {**snapshot, "running": True, "ok": None, "phase": "redeeming"}
        return {**snapshot, "google_status": status,
                "local_settings_error": status.get("local_settings_error", "")}

    @guarded
    def gws_login_cancel(self):
        cancelled = self._signin.cancel() if self._signin is not None else False
        # An unredeemed attempt changes nothing at the server and expires by itself.
        return {"cancelled": cancelled}

    @guarded
    def gws_login_submit_code(self, code=""):
        """The fallback when the browser could not reach this PC (SIGN-IN 'Browser binding')."""
        return {"accepted": self._signin is not None and self._signin.submit_code(code)}

    @guarded
    def attendance_export(self):
        """D36: read all years under this account, then save an inert xlsx chosen by the teacher."""
        import webview
        account, call = self._bound_server_call()
        token = account_sessions.token(self._account_root)
        reply = call("/v1/data/export", {})
        if reply.get("status") != 200 or not isinstance((reply.get("body") or {}).get("years"), list):
            raise ScreenSafeError("전체 자료를 불러오지 못했어요. 잠시 뒤 다시 해 주세요.")
        if not webview.windows:
            raise ScreenSafeError("저장할 창을 확인하지 못했어요.")
        selected = webview.windows[0].create_file_dialog(webview.FileDialog.SAVE, save_filename="출결 전체 자료.xlsx", file_types=("Excel (*.xlsx)",))
        if not selected:
            return {"saved": False}
        if account_sessions.token(self._account_root) != token:
            raise account_sessions.AccountSessionError()
        path = Path(selected if isinstance(selected, str) else selected[0])
        engine.write_attendance_export(path, reply["body"])
        return {"saved": True}

    @guarded
    def data_call(self, route, body=None):
        """One server call for the screens: {"status": int, "body": dict or None, "unknown": bool}.

        `status` is the HTTP status after one session refresh, 0 when no answer came. `body` keeps a
        coded refusal as {error, message?, ...details}. `unknown` means the write may have been
        applied: keep its requestId, lock its input and replay once on ↻ (SCREENS G4). A status of
        401 means sign in again. Only /v1/data/* and /v1/me* go through here.
        """
        account, call = self._bound_server_call()
        reply = call(route, body)
        if route == "/v1/data/years/setup" and reply.get("status") == 200:
            self._known_year = (account, True)
        return reply

    def _bound_server_call(self):
        """The server call of the account this request began under, and that account (ACCT-01).

        `guarded` took the account and its folder at the start. The client comes from that folder and
        is kept for the whole operation. An account change before it is made raises at once. Each send
        then checks the account again, so a sign-in that happens during a long run stops the write
        itself, not only the answer that comes back. No lock is held: the run may take minutes.
        """
        token = getattr(self._session_context, "observed", None)
        if not token or account_sessions.token(self._account_root) != tuple(token):
            raise account_sessions.AccountSessionError()
        state = account_sessions.read_state(self._account_root)
        self._server_session_usable(state)
        client = self._server_session_client()
        if client.folder != server_session.open_store(self._config_dir).path.parent:
            raise account_sessions.AccountSessionError()

        def call(route, body):
            if account_sessions.token(self._account_root) != tuple(token):
                raise account_sessions.AccountSessionError()
            return client.data_call(route, body).reply()

        return token[0], call

    @guarded
    def attendance_ai_input(self, args=None):
        """One press of [AI로 입력], or with `resume` one replay of a lost apply (attendance_ai.py).

        `resume` stays in the window's memory and comes back here; it is never on disk. A run can
        take minutes, and the key goes only into the Gemini header.
        """
        args = args if isinstance(args, dict) else {}
        _account, call = self._bound_server_call()
        config_dir = self._config_dir
        options = {}
        if self._deps.gemini_transport is not None:
            options["transport"] = self._deps.gemini_transport
        if self._deps.recovery_sleeper is not None:
            options["sleep"] = self._deps.recovery_sleeper
        resume = args.get("resume")
        return attendance_ai.run_attendance_ai_input(
            call,
            lambda: load_settings(paths.settings_path(config_dir)).gemini_api_key,
            str(args.get("sentence") or ""),
            year=args.get("year"), resume=resume if isinstance(resume, dict) else None, **options,
        )


    def _attendance_remote_lock_options(self):
        timeout = self._deps.attendance_remote_work_timeout_seconds
        if timeout is None:
            return {}
        return {"timeout_seconds": float(timeout)}

    @guarded
    def gws_logout(self):
        with engine.attendance_remote_work_lock(
            self._config_dir, **self._attendance_remote_lock_options()
        ):
            account_sessions.begin_logout(self._config_dir)
            self._reset_account_results()
            account_sessions.complete_logout(self._config_dir)
            self._session_ended_code = ""
            message = "Teacher Manager에서 로그아웃했어요."
            if self._server_revocation_unconfirmed:
                message += " 이 컴퓨터의 세션은 지웠지만 서버에서 끝났는지는 확인하지 못했어요."
            return self._success(True, message)

    @guarded
    def apply_all(self, profile, grid, bridge_updates):
        results = engine.apply_all(
            self._config_dir, dict(profile), list(grid), dict(bridge_updates), deps=self._deps.apply_deps
        )
        return [
            {"key": r.key, "label": r.label, "status": r.status, "detail": r.detail} for r in results
        ]


    @guarded
    def save_identity(self, updates):
        return engine.save_identity(self._config_dir, dict(updates))

    @guarded
    def save_timetable(self, grid):
        return engine.save_timetable(self._config_dir, list(grid))

    @guarded
    def save_calendars(self, updates):
        return engine.save_calendars(self._config_dir, dict(updates))

    @guarded
    def save_tasks(self, updates):
        return engine.save_tasks(self._config_dir, dict(updates))

    @guarded
    def save_gemini(self, updates):
        return engine.save_gemini(
            self._config_dir,
            dict(updates),
            push_key=self._deps.gemini_key_pusher,
        )

    @guarded
    def get_messenger_settings(self):
        return self._messenger_settings_view()

    def _messenger_settings_view(self) -> dict:
        checker = self._deps.autostart_checker or engine.autostart_enabled
        saved, autostart = self._local_read(
            "get_messenger_settings", "이 컴퓨터에 저장된 메신저 설정을 읽지 못했어요.",
            lambda: (engine.read_messenger_settings(self._config_dir), bool(checker())),
        )
        return {
            "gemini_api_key": saved.gemini_api_key,
            "gemini_model": saved.gemini_model,
            "hotkey": saved.hotkey,
            "autostart": autostart,
            "brity_download_dir": saved.brity_download_dir,
            "error_reports_enabled": saved.error_reports_enabled,
        }

    @guarded
    def check_attachment_folder(self, path_text):
        return engine.attachment_folder_status(str(path_text or ""))

    @guarded
    def choose_attachment_folder(self, current_path):
        picker = self._deps.folder_picker or engine.choose_attachment_folder
        selected = picker(str(current_path or ""))
        return {"path": selected, "cancelled": not bool(selected)}

    @guarded
    def save_messenger(self, updates):
        return engine.save_messenger_settings(
            self._config_dir,
            dict(updates),
            register=self._deps.hotkey_register,
            unregister=self._deps.hotkey_unregister,
            modifier_probe=self._deps.hotkey_modifier_probe,
            restart=self._deps.helper_restart,
            autostart_checker=self._deps.autostart_checker,
            autostart_enable=self._deps.autostart_enable,
            autostart_disable=self._deps.autostart_disable,
            helper_exists=self._deps.helper_window_exists,
            stop=self._deps.helper_stop,
        )

    @guarded
    def restart_helper(self):
        return engine.restart_helper_verified(
            stop=self._deps.helper_stop,
            start=self._deps.helper_restart,
            exists=self._deps.helper_window_exists,
            app_version=version.APP_VERSION,
        )

    def _journal_error_code(self, error) -> str:
        for item in _journal_error_chain(error):
            if isinstance(item, recovery.RetryableOperationError):
                return item.code
        return type(error).__name__

    def _report_issue(self, issue: recovery.UserIssue, error) -> str | bool:
        """실패 보고를 저장하면 queued. 실제 전달 완료로 표시하지 않는다.

        동의 화면 없이 자동으로 보내되(2026-09-02 사용자 결정), 설정의 스위치가
        꺼져 있으면 적지도 보내지도 않는다. 보고는 최선 노력이라 어떤 예외도
        화면 안내를 막지 않는다.
        """

        try:
            if not load_settings(paths.settings_path(self._config_dir)).error_reports_enabled:
                return False
            saved = engine._read_setup_status(self._config_dir)
            profile = engine.read_profile_values(self._config_dir)
            windows = self._deps.windows_version or platform.version
            report = error_reports.build_report(
                issue,
                app_version=version.APP_VERSION,
                windows_version=str(windows() or ""),
                account=str(saved.get("account", "") or ""),
                teacher={
                    "name": profile.get("선생님이름", ""),
                    "school": profile.get("학교명", ""),
                    "grade": profile.get("담임학년", ""),
                    "class": profile.get("담임반", ""),
                },
                kind=problem_guidance.kind_for(issue.operation, issue.message),
                code=self._journal_error_code(error),
                error_chain=" > ".join(
                    type(item).__name__ for item in _journal_error_chain(error)
                ),
                detail=_with_network_note(_journal_error_detail(error)),
            )
            if not report["reportId"]:
                return False
            if not error_reports.enqueue(self._config_dir, report):
                return False
            self._start_error_report_flush()
            return "queued"
        except Exception:  # noqa: BLE001 - 보고는 최선 노력이다.
            return False

    def _start_error_report_flush(self) -> None:
        """대기 중인 보고를 배경에서 보낸다. 주소가 없거나 https가 아니면 조용히 넘긴다."""

        session = account_sessions.read_state(self._account_root)
        local_problem = self._local_settings_error and self._local_settings_error_token == (session.get("account"), session.get("generation"))
        if (self._session_identity_available is not True or local_problem
                or session.get("phase") != "active" or self._verified_google_account != session.get("account")):
            return
        try:
            environ = (
                dict(os.environ) if self._deps.environ is None else dict(self._deps.environ)
            )
            # 동봉 release.json의 실제 서버 주소는 설치본에서만 쓴다. 소스로 실행하는
            # 개발·자동 시험은 CENTRAL_CHAT_SENDER_URL 환경값이 있을 때만 보낸다.
            release = (
                bundle_paths.bundle_root() / "release.json"
                if bundle_paths.is_frozen()
                else None
            )
            base = error_reports.sender_base_url(environ, release)
            endpoint = error_reports.endpoint_url(base)
        except Exception:  # noqa: BLE001 - 주소가 없으면 보고를 미룬다.
            return
        poster = self._deps.error_report_poster or error_reports.default_poster
        session_token = account_sessions.token(self._config_dir)

        def work():
            if not self._error_report_flush_lock.acquire(blocking=False):
                return
            try:
                with account_sessions.work(self._config_dir, expected_token=session_token):
                    local_problem = self._local_settings_error and self._local_settings_error_token == session_token
                    if (self._session_identity_available is not True or local_problem
                            or self._verified_google_account != session_token[0]):
                        return
                    before = {str(row.get("reportId") or "") for row in error_reports.pending(self._config_dir)}
                    try:
                        error_reports.flush(self._config_dir, endpoint, poster)
                    finally:
                        # A report still pending after a real send attempt waits for the next start.
                        after = {str(row.get("reportId") or "") for row in error_reports.pending(self._config_dir)}
                        self._error_report_retry_ids = (self._error_report_retry_ids | (before & after)) - (before - after)
            except Exception:  # noqa: BLE001 - 다음 기회에 다시 보낸다.
                pass
            finally:
                self._error_report_flush_lock.release()

        runner = self._deps.error_report_flush_runner
        if runner is not None:
            runner(work)
            return
        threading.Thread(target=work, name="tm-error-reports", daemon=True).start()

    def _local_read(self, operation: str, title: str, reader):
        """Run the shared three-cycle local recovery before returning a UI issue."""
        def once():
            try:
                return reader()
            except recovery.RetryableOperationError:
                raise
            except (OSError, ValueError) as error:
                raise recovery.RetryableOperationError(
                    "LOCAL_READ", "이 컴퓨터의 저장 내용을 다시 읽고 있어요."
                ) from error

        kwargs = {}
        if self._deps.recovery_sleeper is not None:
            kwargs["sleeper"] = self._deps.recovery_sleeper
        return recovery.run_operation(
            operation, title, once, delays=recovery.LOCAL_DELAYS,
            change_status="확인한 화면 내용은 그대로입니다.",
            app_version=version.APP_VERSION, **kwargs,
        )

    def _network_recovery_options(self) -> dict:
        if self._deps.recovery_sleeper is None:
            return {}
        return {"sleeper": self._deps.recovery_sleeper}

    def _component_operation(
        self,
        operation: str,
        title: str,
        stage: str,
        action,
        *,
        verify=None,
        return_stop_result: bool = False,
        change_status: str,
    ):
        """Run one component step through the shared three recovery cycles.

        The lower-level component code remains responsible for downloading,
        validating, installing, and reading the exact active state.  This
        adapter only turns its stable result code into complete/retry/stop and
        lets ``recovery.run_operation`` own the three-cycle timing.
        """
        cycle = 0
        verified_this_cycle = False

        def stop_now(code, detail):
            del code  # The support report deliberately contains no component detail.
            raise recovery.FinalOperationFailure(
                recovery.final_issue(
                    operation=operation,
                    title=title,
                    attempts=max(1, min(3, cycle)),
                    reason=str(detail or "현재 상태를 안전하게 확인하지 못했습니다."),
                    change_status=change_status,
                    app_version=version.APP_VERSION,
                )
            )

        def action_once():
            nonlocal cycle, verified_this_cycle
            if not verified_this_cycle:
                cycle += 1
            verified_this_cycle = False
            value = action()
            code = str(value.get("code") or "") if isinstance(value, dict) else ""
            current_stage = stage(value) if callable(stage) else stage
            disposition = engine.component_recovery_disposition(current_stage, code)
            if disposition == "complete":
                return value
            detail = (
                # GWS 컴포넌트 결과는 "detail" 키, 앱 업데이트 확인·시작 결과는
                # "reason" 키에 실제 원인을 담는다. 어느 쪽이든 화면까지 전달한다.
                str(value.get("detail") or value.get("reason") or "")
                if isinstance(value, dict)
                else ""
            )
            # Which update transport failed goes to the journal and report only (UPD-01).
            transport = str(value.get("transport") or "") if isinstance(value, dict) else ""
            if disposition == "retry":
                retry = recovery.RetryableOperationError(
                    code,
                    detail or "현재 상태를 다시 확인하고 있어요.",
                )
                if transport:
                    retry.diagnostic_detail = f"transport={transport}"
                raise retry
            if return_stop_result:
                return value
            try:
                stop_now(code, detail)
            except recovery.FinalOperationFailure as final:
                if transport:
                    final.diagnostic_detail = f"transport={transport}"
                raise

        def verify_once():
            nonlocal cycle, verified_this_cycle
            cycle += 1
            verified_this_cycle = True
            try:
                return verify()
            except engine.ComponentRecoveryStop as error:
                stop_now(error.code, error.detail)

        return recovery.run_operation(
            operation,
            title,
            action_once,
            verify=verify_once if verify is not None else None,
            delays=recovery.NETWORK_DELAYS,
            change_status=change_status,
            app_version=version.APP_VERSION,
            **self._network_recovery_options(),
        )


    @guarded
    def hotkey_recording_start(self):
        pause = self._deps.helper_hotkey_pause or engine.pause_helper_hotkey
        return bool(pause())

    @guarded
    def hotkey_recording_end(self):
        resume = self._deps.helper_hotkey_resume or engine.resume_helper_hotkey
        return bool(resume())

    @guarded
    def log_attendance_ui_event(self, payload=None):
        """Best-effort local diagnostics shared by all mounts, without account identifiers."""
        return _record_attendance_ui_event(self._account_root, payload)


    @guarded
    def open_picture_guide(self, kind):
        """Open one bundled, account-independent guide in the default browser."""
        import webbrowser

        filename = {
            "login": "google-login.html",
            "setup": "attendance-first-setup.html",
            "chat": "google-chat-space.html",
        }.get(kind) if isinstance(kind, str) else None
        if filename is None:
            raise ScreenSafeError("열 수 있는 방법 안내를 확인해 주세요.")
        guide_root = Path(__file__).resolve().parent / "web" / "guides"
        guide = (guide_root / filename).resolve()
        if guide.parent != guide_root or not guide.is_file():
            raise ScreenSafeError("방법 안내 파일을 찾지 못했어요. 프로그램 설치 상태를 확인해 주세요.")
        opener = self._deps.url_opener or webbrowser.open
        try:
            if opener(guide.as_uri()) is False:
                raise RuntimeError()
        except Exception:
            raise ScreenSafeError("기본 브라우저에서 방법 안내를 열지 못했어요. Windows의 기본 브라우저 설정을 확인해 주세요.") from None
        return {"opened": True, "method": "default"}

    @guarded
    def open_url(self, url):
        try:
            return self._open_external_url(url)
        except ValueError as error:
            # "https 주소만 열 수 있어요" 같은 검증 문장은 화면용으로 준비된 것이다.
            raise ScreenSafeError(str(error)) from error


