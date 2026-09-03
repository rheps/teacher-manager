# skills/teacher-task-manager/scripts/dashboard/problem_guidance.py
"""실패 화면의 '지금 할 수 있는 일'을 작업 종류별로 정한다.

화면은 여기서 나온 문장과 버튼만 그린다. 오류 코드·시도 횟수·판 번호·식별번호는
로컬 기록과 개발자 보고에만 쓰고 교사에게 보이지 않는다(2026-09-02 사용자 결정).
"""
from __future__ import annotations

from dataclasses import dataclass

from brity_bridge import recovery

KIND_UPDATE = "update"
KIND_GOOGLE_READ = "google-read"
KIND_GOOGLE_WRITE = "google-write"
KIND_ATTENDANCE_SCRIPT = "attendance-script"
KIND_LOGIN = "login"
KIND_LOCAL = "local"
KIND_SHEET_CONNECTION_VALUE = "sheet-connection-value"
KIND_OTHER = "other"
ALL_KINDS = (
    KIND_UPDATE, KIND_GOOGLE_READ, KIND_GOOGLE_WRITE, KIND_ATTENDANCE_SCRIPT,
    KIND_LOGIN, KIND_LOCAL, KIND_SHEET_CONNECTION_VALUE, KIND_OTHER,
)

_REOPEN_STEP = "이 창을 닫았다가 다시 열면 프로그램이 자동으로 다시 확인해요."
_HELP_STEP = "그래도 같으면 아래 도움 요청을 눌러 주세요. 필요한 정보는 프로그램이 함께 보내요."

_OPERATION_KINDS = {
    "get_update_info": KIND_UPDATE,
    "start_update": KIND_UPDATE,
    "update_offer": KIND_UPDATE,
    "list_calendars": KIND_GOOGLE_READ,
    "list_tasklists": KIND_GOOGLE_READ,
    "google_status": KIND_GOOGLE_READ,
    "gws_login_status": KIND_GOOGLE_READ,
    "attendance_status": KIND_GOOGLE_READ,
    "attendance_status_cached": KIND_GOOGLE_READ,
    "attendance_chat_status": KIND_GOOGLE_READ,
    "attendance_chat_spaces": KIND_GOOGLE_READ,
    "attendance_script_update_status": KIND_GOOGLE_READ,
    "attendance_connection_candidates": KIND_GOOGLE_READ,
    "attendance_first_setup_status": KIND_GOOGLE_READ,
    "attendance_prepare_status": KIND_GOOGLE_READ,
    "ensure_attendance": KIND_GOOGLE_WRITE,
    "start_new_attendance": KIND_GOOGLE_WRITE,
    "attendance_prepare_start": KIND_GOOGLE_WRITE,
    "ensure_calendar_named": KIND_GOOGLE_WRITE,
    "ensure_tasklist_named": KIND_GOOGLE_WRITE,
    "attendance_script_update_apply": KIND_GOOGLE_WRITE,
    "select_attendance_connection": KIND_GOOGLE_WRITE,
    "select_attendance_connection_by_code": KIND_GOOGLE_WRITE,
    "attendance_chat_connect": KIND_GOOGLE_WRITE,
    "attendance_chat_set_space": KIND_GOOGLE_WRITE,
    "attendance_chat_create_space": KIND_GOOGLE_WRITE,
    "gws_login_start": KIND_GOOGLE_WRITE,
    "gws_logout": KIND_GOOGLE_WRITE,
    "save_identity": KIND_LOCAL,
    "save_timetable": KIND_LOCAL,
    "save_calendars": KIND_LOCAL,
    "save_tasks": KIND_LOCAL,
    "save_gemini": KIND_LOCAL,
    "save_messenger": KIND_LOCAL,
    "get_messenger_settings": KIND_LOCAL,
    "home_checks": KIND_LOCAL,
    "computer_status": KIND_LOCAL,
    "restart_helper": KIND_LOCAL,
    "open_logs": KIND_LOCAL,
    "hotkey_recording_start": KIND_LOCAL,
    "hotkey_recording_end": KIND_LOCAL,
    "retry_capture": KIND_LOCAL,
}


@dataclass(frozen=True)
class Guidance:
    reason: str
    steps: tuple[str, ...]
    actions: tuple[recovery.IssueAction, ...]


_GUIDANCE = {
    KIND_UPDATE: Guidance(
        reason="업데이트 정보를 받아 오지 못했어요.",
        steps=(
            "인터넷 연결을 확인해 주세요. 학교 망이면 잠시 뒤에 다시 열어 주세요.",
            _REOPEN_STEP,
            "급하면 아래 버튼으로 최신 설치 파일을 직접 내려받아 실행하면 돼요.",
        ),
        actions=(recovery.IssueAction("open-download-page", "다운로드 페이지 열기"),),
    ),
    KIND_GOOGLE_READ: Guidance(
        reason="Google 자료를 읽어 오지 못했어요.",
        steps=(
            "인터넷 연결을 확인해 주세요.",
            "설정에서 Google 로그인 상태가 정상인지 확인해 주세요.",
            _REOPEN_STEP,
        ),
        actions=(recovery.IssueAction("settings", "설정 열기"),),
    ),
    # 출석부 설정 탭의 Google Chat 연결값이 비어 있는 결정적 상태. 원인 설명 없이 선생님이
    # 할 일만 보인다(2026-09-04 사용자 결정). 시트 메뉴 [연결 상태 확인]이 비어 있던 번호·
    # 확인값을 다시 만들고, 새 확인값은 [연결하기]로 발송 서버에 다시 등록된다.
    KIND_SHEET_CONNECTION_VALUE: Guidance(
        reason="아래 순서대로 해 주세요.",
        steps=(
            "출석부를 열고 위 메뉴 [처음 한 번 설정하기] → [연결 상태 확인]을 눌러 주세요.",
            "Teacher Manager로 돌아와 이 창을 닫았다가 다시 열어 주세요.",
            "Google Chat 줄에 [연결하기]가 보이면 눌러 주세요.",
        ),
        actions=(recovery.IssueAction("open-current-attendance", "현재 출석부 열기"),),
    ),
    KIND_GOOGLE_WRITE: Guidance(
        reason="Google에 준비하거나 저장하는 일을 끝내지 못했어요.",
        steps=(
            "인터넷 연결을 확인해 주세요.",
            "이 창을 닫았다가 다시 열고 같은 버튼을 한 번 더 눌러 주세요. 이미 만든 것은 프로그램이 찾아서 이어 써요.",
            _HELP_STEP,
        ),
        actions=(recovery.IssueAction("settings", "설정 열기"),),
    ),
    KIND_ATTENDANCE_SCRIPT: Guidance(
        reason="현재 Google의 출결 기능이 이 프로그램과 맞는지 확인하지 못했어요.",
        steps=(
            "출결 탭 맨 위 한 줄 안내에 있는 버튼을 눌러 출결 기능을 확인해 주세요.",
            "끝나면 이 창을 닫았다가 다시 열어 주세요. 목록을 자동으로 다시 읽어요.",
        ),
        actions=(recovery.IssueAction("attendance-tab", "출결 탭으로"),),
    ),
    KIND_LOGIN: Guidance(
        reason="학교 Google 계정 로그인이 필요해요.",
        steps=(
            "설정에서 학교(@goedu.kr) 계정으로 로그인해 주세요.",
            "로그인이 끝나면 프로그램이 자동으로 이어서 진행해요.",
        ),
        actions=(recovery.IssueAction("google-login", "Google 로그인 설정 열기"),),
    ),
    KIND_LOCAL: Guidance(
        reason="이 컴퓨터에서 처리하는 일을 끝내지 못했어요.",
        steps=(
            "Teacher Manager를 닫았다가 다시 열어 주세요.",
            "그래도 같으면 컴퓨터를 다시 시작한 뒤 열어 주세요.",
            _HELP_STEP,
        ),
        actions=(),
    ),
    KIND_OTHER: Guidance(
        reason=recovery.UNEXPECTED_MESSAGE,
        steps=(
            "Teacher Manager를 닫았다가 다시 열어 주세요.",
            "그래도 같으면 컴퓨터를 다시 시작한 뒤 열어 주세요.",
            _HELP_STEP,
        ),
        actions=(),
    ),
}


_FOLLOW_MESSAGE_STEP = "위 안내대로 진행한 뒤 이 창을 닫았다가 다시 열어 주세요."


def kind_from_message(message: str) -> str:
    """준비된 안내문만으로 정해지는 종류. 해당 없으면 빈 문자열."""

    text = str(message or "")
    if "@goedu.kr" in text and "로그인" in text:
        return KIND_LOGIN
    if "처음 준비하던 Google 계정" in text:
        return KIND_LOGIN
    if "출결 기능" in text and ("출결 탭" in text or "업데이트" in text or "확인" in text):
        return KIND_ATTENDANCE_SCRIPT
    return ""


def kind_for(operation: str, message: str = "") -> str:
    """준비된 안내문이 있으면 그 문장이 작업 이름보다 우선한다."""

    return kind_from_message(message) or _OPERATION_KINDS.get(str(operation or ""), KIND_OTHER)


def guidance_for(kind: str) -> Guidance:
    return _GUIDANCE.get(kind, _GUIDANCE[KIND_OTHER])


def apply_guidance(issue: recovery.UserIssue, operation: str) -> recovery.UserIssue:
    """모든 화면 실패에 행동 단계를 채운다. 이미 정한 버튼·단계는 그대로 둔다."""

    if issue.state == "needs_user":
        # 사람이 할 일은 준비된 문장 자체다. 로그인·출결 기능처럼 문장으로 종류가
        # 정해지는 경우에만 그 종류의 단계와 버튼을 붙이고, 그 밖에는 문장을 따르게 한다.
        message_kind = kind_from_message(issue.message)
        if message_kind:
            guidance = guidance_for(message_kind)
            return issue.with_guidance(
                steps=issue.steps or guidance.steps,
                actions=issue.actions or guidance.actions,
            )
        return issue.with_guidance(steps=issue.steps or (_FOLLOW_MESSAGE_STEP,))
    guidance = guidance_for(kind_for(operation or issue.operation, issue.message))
    reason = issue.reason
    if not reason.strip() or reason == recovery.UNEXPECTED_MESSAGE:
        reason = guidance.reason
    return issue.with_guidance(steps=guidance.steps, actions=guidance.actions, reason=reason)
