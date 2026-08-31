# skills/teacher-task-manager/scripts/dashboard/bridge.py
"""pywebview js_api — 화면(JS)이 부르는 유일한 문.

규칙: 이 파일은 표시 문구 조립과 engine 호출만 한다. 화면 로직을 JS에,
업무 로직을 engine 밖에 두지 않는다. 모든 공개 메서드는 guarded로 감싸
{"ok": true, "data": ...} / {"ok": false, "error": "한국어 한 문장"}만 돌려준다.
"""
from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brity_bridge import (
    ai_skill_install,
    bundle_paths,
    capture_store,
    component_lock,
    gws_env,
    paths,
    pipeline,
    process_win,
    recovery,
)
from brity_bridge.settings import load_settings
from dashboard import engine
from dashboard import external_url
from dashboard import version

SETUP_STATE_NAME = "setup-state.json"
SETUP_STATE_VERSION = 2
SETUP_LAST_STEP = 9
_FRESH_STATE = {
    "version": SETUP_STATE_VERSION,
    "completed": False,
    "step": 1,
    "max_step": 1,
    "draft": {"profile": {}, "grid": [], "bridge": {}},
}
_V1_STEP_TO_V2 = {1: 1, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 9}
_ATTENDANCE_AUTH_BLOCKED_STATES = {
    "gws-required", "login-required", "account-required", "auth-error"
}
_ATTENDANCE_UPDATE_PERMISSION_MESSAGE = (
    "출결 기능 업데이트에 필요한 Google 권한을 다시 승인해야 해요. "
    "설정에서 현재 Google 계정을 로그아웃한 뒤 같은 @goedu.kr 계정으로 다시 로그인해 주세요. "
    "기존 출석부와 감지기는 그대로입니다."
)
_ATTENDANCE_AI_PROOF_MESSAGE = (
    "출석부 안의 연결 확인 표시가 없거나 맞지 않아요. 출석부를 열고 "
    "[처음 한 번 설정하기]에서 [처음 설정 한 번에 끝내기]를 누른 뒤 "
    "연결 확인하고 계속해 주세요."
)
_ATTENDANCE_ROSTER_MIGRATION_MESSAGE = (
    "새 출결 기능은 올라갔지만 기존 출석부의 학생명단·월별 학생 선택·메신저 상태 정리를 "
    "끝내지 못했어요. 기존 학생 자료는 그대로입니다. Teacher Manager에서 [설정]을 열고 "
    "[경기도교육청 클라우드 아이디로 Google 로그인(@goedu.kr)] 줄에서 로그아웃한 뒤, "
    "본인의 @goedu.kr 계정으로 로그인하고 Google Sheets 권한을 허용해 주세요."
)
_ATTENDANCE_WORKBOOK_LAYOUT_UPDATE_MESSAGE = (
    "기존 출석부의 학생 선택목록과 메신저 상태를 현재 방식으로 정리해야 해요. "
    "기존 학생·출결·메시지 내용은 그대로 둡니다."
)
_DEFAULT_SCREEN_FAILURE = (
    "작업을 마치지 못했어요. Teacher Manager를 다시 시작한 뒤 다시 시도해 주세요."
)
_SCREEN_FAILURES = {
    "get_app_info": (
        "프로그램을 여는 데 필요한 정보를 읽지 못했어요. "
        "Teacher Manager를 다시 시작해 주세요."
    ),
    "save_setup_state": "처음 설정 내용을 저장하지 못했어요. 프로그램을 다시 시작한 뒤 다시 시도해 주세요.",
    "finish_setup": "처음 설정을 마무리하지 못했어요. @goedu.kr Google 로그인과 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "restart_setup": "처음 설정 안내를 다시 열지 못했어요. Teacher Manager를 다시 시작해 주세요.",
    "read_profile": "이 컴퓨터에 저장된 내 정보를 읽지 못했어요. 프로그램을 다시 시작해 주세요.",
    "read_grid": "이 컴퓨터에 저장된 시간표를 읽지 못했어요. 프로그램을 다시 시작해 주세요.",
    "get_messenger_settings": "이 컴퓨터에 저장된 메신저 설정을 읽지 못했어요. 프로그램을 다시 시작해 주세요.",
    "save_profile_grid": "이 컴퓨터 설정을 저장하지 못했어요. 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "save_messenger": "이 컴퓨터 설정을 저장하지 못했어요. 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "apply_all": "이 컴퓨터 설정을 저장하고 적용하지 못했어요. 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "choose_attachment_folder": (
        "첨부파일 폴더를 열지 못했어요. 폴더 위치를 직접 입력하거나 "
        "Teacher Manager를 다시 시작해 주세요."
    ),
    "check_attachment_folder": "첨부파일 폴더 상태를 확인하지 못했어요. 폴더 위치를 다시 확인해 주세요.",
    "attendance_status": "출결 상태를 확인하지 못했어요. 현재 Windows 계정의 Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "attendance_status_cached": "저장된 출결 상태를 확인하지 못했어요. Teacher Manager를 다시 시작해 주세요.",
    "attendance_connection_candidates": "기존 정식 출석부 목록을 확인하지 못했어요. 현재 연결과 Google 파일은 그대로입니다.",
    "select_attendance_connection": "고른 출석부 연결을 확인하지 못했어요. 현재 연결과 Google 파일은 그대로입니다.",
    "select_attendance_connection_by_code": "붙여 넣은 확인번호로 출석부 연결을 확인하지 못했어요. 현재 연결과 Google 파일은 그대로입니다.",
    "ensure_attendance": "출결 자료를 준비하지 못했어요. 현재 Windows 계정의 Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "start_new_attendance": "새 학년도 출석부를 시작하지 못했어요. 기존 자료는 그대로입니다. Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "attendance_prepare_start": "출결 준비를 시작하지 못했어요. 이 컴퓨터 설정과 Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_prepare_status": "출결 준비 상태를 확인하지 못했어요. 설정에서 Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "attendance_first_setup_status": "출석부의 처음 설정 상태를 확인하지 못했어요. 출석부가 열리는지 확인한 뒤 다시 시도해 주세요.",
    "attendance_script_update_status": "출결 기능 상태를 확인하지 못했어요. 처음 준비하던 @goedu.kr Google 계정으로 로그인한 뒤 다시 시도해 주세요. 기존 자료는 그대로입니다.",
    "attendance_script_update_apply": "출결 기능을 바꾸지 못했어요. 처음 준비하던 @goedu.kr Google 계정으로 로그인한 뒤 다시 시도해 주세요. 기존 자료는 그대로입니다.",
    "attendance_chat_status": "학급 단톡방 상태를 확인하지 못했어요. 출결 준비와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_connect": "학급 단톡방 연결을 시작하지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_spaces": "학급 단톡방 목록을 가져오지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_set_space": "학급 단톡방 선택을 저장하지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_create_space": "학급 단톡방을 만들지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "computer_status": "이 컴퓨터의 준비 상태를 확인하지 못했어요. Teacher Manager를 다시 시작해 주세요.",
    "google_status": "Google 연결 상태를 확인하지 못했어요. 현재 Windows 계정의 설정과 인터넷 연결을 확인해 주세요.",
    "list_calendars": "캘린더 목록을 가져오지 못했어요. 현재 Windows 계정의 @goedu.kr Google 로그인을 확인해 주세요.",
    "list_tasklists": "할 일 목록을 가져오지 못했어요. 현재 Windows 계정의 @goedu.kr Google 로그인을 확인해 주세요.",
    "gws_login_start": "Google 로그인 준비 파일을 확인하지 못했어요. 현재 Windows 계정의 설정을 점검한 뒤 다시 시도해 주세요.",
    "gws_login_status": "Google 로그인 상태를 확인하지 못했어요. 설정에서 다시 점검해 주세요.",
    "gws_logout": "Google 로그아웃을 마치지 못했어요. 현재 Windows 계정의 Google 도구 상태를 점검해 주세요.",
    "ensure_calendar_named": "캘린더를 만들지 못했어요. 이름과 Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "ensure_tasklist_named": "할 일 목록을 만들지 못했어요. 이름과 Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "open_logs": "기록 폴더를 열지 못했어요. Teacher Manager를 다시 시작한 뒤 다시 시도해 주세요.",
    "open_url": "안전한 https 주소만 열 수 있어요. 주소를 다시 확인해 주세요.",
    "retry_capture": "실패한 항목을 다시 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.",
}


class ScreenSafeError(RuntimeError):
    """A fixed Korean sentence intentionally prepared for the screen."""


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


def _fail(error, operation: str = ""):
    if isinstance(error, (recovery.UserActionRequired, recovery.FinalOperationFailure)):
        return _issue_reply(error.issue)

    message = _SCREEN_FAILURES.get(str(operation or ""), _DEFAULT_SCREEN_FAILURE)
    if isinstance(error, ScreenSafeError):
        needs_user = _screen_safe_user_issue(error, operation)
        if needs_user is not None:
            return _issue_reply(needs_user)

    issue = recovery.unexpected_final_issue(
        operation=str(operation or "unknown_operation"),
        title=message,
        change_status="확인된 자료는 바꾸지 않았습니다.",
        app_version=version.APP_VERSION,
    )
    reply = _issue_reply(issue, error_text=message)
    if isinstance(error, external_url.ExternalUrlOpenError):
        reply["error"] = str(error)
        reply["code"] = external_url.NO_EXTERNAL_BROWSER
    elif isinstance(
        error,
        (ScreenSafeError, gws_env.GwsAccountStorageError, engine.AttendanceRemoteWorkBusyError),
    ):
        reply["error"] = str(error)
    else:
        try:
            from dashboard import central_chat

            if isinstance(error, central_chat.CentralChatError):
                reply["error"] = central_chat._safe_central_error_detail(error)
        except ImportError:
            pass
    return reply


def _issue_reply(issue: recovery.UserIssue, *, error_text: str | None = None) -> dict:
    """Keep the old safe sentence while moving JS consumers to the issue record."""

    return {
        "ok": False,
        "issue": issue.to_dict(),
        "error": error_text or issue.message or issue.title,
    }


def _screen_safe_user_issue(error: ScreenSafeError, operation: str) -> recovery.UserIssue | None:
    """Only fixed messages that ask for a teacher action become direct actions."""

    message = str(error)
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
    return None


def _write_support_clipboard(text: str) -> None:
    """Use the Windows clipboard writer only after the report has been rebuilt."""

    from brity_bridge import clipboard_win

    clipboard_win.write_text_for_test(text)


def _attendance_workbook_layout_is_current(
    *, runner, workdir, gws_executable, spreadsheet_id
) -> bool:
    """기존 출석부의 학생 선택과 메신저 상태가 현재 모양인지 읽기만 한다."""

    gws = str(gws_executable or "").strip()
    spreadsheet = str(spreadsheet_id or "").strip()
    if not (callable(runner) and gws and spreadsheet):
        return False

    from attendance_script_update import _run_one_json

    def read_values(a1_range: str) -> list:
        result = _run_one_json(
            runner,
            [
                gws,
                "sheets",
                "spreadsheets",
                "values",
                "get",
                "--params",
                json.dumps(
                    {"spreadsheetId": spreadsheet, "range": a1_range},
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            Path(workdir),
        )
        values = result.get("values", []) if isinstance(result, dict) else []
        return values if isinstance(values, list) else []

    def first_column(values: list) -> list[str]:
        return [
            str(row[0] if isinstance(row, list) and row else "").strip()
            for row in values
        ]

    def quote_sheet(name: str) -> str:
        return "'" + str(name).replace("'", "''") + "'"

    settings = read_values("설정!A:B")
    settings_map = {
        str(row[0] or "").strip(): str(row[1] or "").strip()
        for row in settings
        if isinstance(row, list) and len(row) >= 2 and str(row[0] or "").strip()
    }
    roster_name = settings_map.get("ROSTER_SHEET_NAME") or "학생명단"
    roster = read_values(f"{quote_sheet(roster_name)}!A:D")

    def row4(row) -> list[str]:
        values = [str(value or "").strip() for value in list(row or [])[:4]]
        return values + [""] * (4 - len(values))

    rows = [row4(row) for row in roster]
    if (
        not rows
        or rows[0][:3] != ["번호", "이름", "학생 Google 이메일"]
        or any(row[3] for row in rows)
        or len(rows) - 1 > 199
    ):
        return False

    combined = [row[0] + row[1] for row in rows[1:] if row[0] and row[1]]
    if first_column(read_values("'드롭다운'!J1:J200")) != [
        "학생_번호이름",
        *combined,
    ]:
        return False
    if settings_map.get("STUDENT_DROPDOWN_RANGE") != "J2:J200":
        return False

    queue_status_options = ["대기", "발송중", "제외", "보냄", "실패"]
    if first_column(read_values("'드롭다운'!G1:G6")) != [
        "쪽지_상태",
        *queue_status_options,
    ]:
        return False
    for status_range in (
        "'메신저 개인톡 내용'!G:G",
        "'메신저 단체톡 내용'!E:E",
    ):
        if "확인필요" in first_column(read_values(status_range)):
            return False

    month_names = [
        value.strip()
        for value in settings_map.get(
            "MONTH_SHEET_NAMES",
            "3월,4월,5월,6월,7월,8월,9월,10월,11월,12월,1월,2월",
        ).split(",")
        if value.strip()
    ]
    validation_ranges = [f"{quote_sheet(name)}!B3" for name in month_names]
    validation_ranges.extend(
        ["'메신저 개인톡 내용'!G2", "'메신저 단체톡 내용'!E2"]
    )
    validation_state = _run_one_json(
        runner,
        [
            gws,
            "sheets",
            "spreadsheets",
            "get",
            "--params",
            json.dumps(
                {
                    "spreadsheetId": spreadsheet,
                    "ranges": validation_ranges,
                    "includeGridData": True,
                    "fields": "sheets(properties(title),data(rowData(values(dataValidation))))",
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        Path(workdir),
    )
    validations = {}
    for item in validation_state.get("sheets", []) if isinstance(validation_state, dict) else []:
        title = str(((item.get("properties") or {}).get("title")) or "")
        try:
            rule = item["data"][0]["rowData"][0]["values"][0]["dataValidation"]
        except (KeyError, IndexError, TypeError):
            rule = None
        if title and isinstance(rule, dict):
            validations[title] = rule

    def condition_values(rule: dict) -> list[str]:
        condition = rule.get("condition") if isinstance(rule, dict) else None
        return [
            str(item.get("userEnteredValue") or "")
            for item in (condition or {}).get("values", [])
            if isinstance(item, dict)
        ]

    for name in month_names:
        rule = validations.get(name)
        condition = rule.get("condition") if isinstance(rule, dict) else None
        values = [
            value.replace("'드롭다운'", "드롭다운")
            for value in condition_values(rule)
        ]
        if (
            not isinstance(condition, dict)
            or condition.get("type") != "ONE_OF_RANGE"
            or values != ["=드롭다운!$J$2:$J$200"]
            or rule.get("strict") is True
        ):
            return False
    for name in ("메신저 개인톡 내용", "메신저 단체톡 내용"):
        rule = validations.get(name)
        condition = rule.get("condition") if isinstance(rule, dict) else None
        if (
            not isinstance(condition, dict)
            or condition.get("type") != "ONE_OF_LIST"
            or condition_values(rule) != queue_status_options
            or rule.get("strict") is not True
        ):
            return False
    return True


def _migrate_attendance_roster_layout(
    *, runner, workdir, gws_executable, spreadsheet_id
) -> bool:
    """Apps Script 실행 API 없이 기존 출결 시트의 연결 목록까지 함께 갱신한다."""

    gws = str(gws_executable or "").strip()
    spreadsheet = str(spreadsheet_id or "").strip()
    if not (callable(runner) and gws and spreadsheet):
        return False
    from attendance_script_update import _run_one_json

    def read_values(a1_range: str) -> list:
        reply = _run_one_json(
            runner,
            [
                gws,
                "sheets",
                "spreadsheets",
                "values",
                "get",
                "--params",
                json.dumps(
                    {"spreadsheetId": spreadsheet, "range": a1_range},
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            Path(workdir),
        )
        values = reply.get("values") if isinstance(reply, dict) else None
        return values if isinstance(values, list) else []

    settings = read_values("설정!A:B")
    settings_map = {
        str(row[0] or "").strip(): str(row[1] or "").strip()
        for row in settings
        if isinstance(row, list) and len(row) >= 2 and str(row[0] or "").strip()
    }
    roster_name = "학생명단"
    if settings_map.get("ROSTER_SHEET_NAME"):
        roster_name = settings_map["ROSTER_SHEET_NAME"]

    def quote_sheet(name: str) -> str:
        return "'" + str(name).replace("'", "''") + "'"

    quoted_roster = quote_sheet(roster_name)
    roster_range = f"{quoted_roster}!A:D"
    before = read_values(roster_range)
    if not before or not isinstance(before[0], list):
        return False

    def row4(row) -> list[str]:
        values = list(row) if isinstance(row, list) else []
        values.extend([""] * (4 - len(values)))
        return [str(value or "").strip() for value in values[:4]]

    rows = [row4(row) for row in before]
    headers = rows[0][:3]
    current_headers = ["번호", "이름", "학생 Google 이메일"]
    if headers == current_headers and all(not row[3] for row in rows):
        migrated = rows
    elif headers == ["번호", "이름", "번호+이름"]:
        migrated = [current_headers + [""]]
        migrated.extend([[row[0], row[1], row[3], ""] for row in rows[1:]])
    elif rows[0][0] == "번호+이름":
        migrated = [current_headers + [""]]
        for row in rows[1:]:
            match = re.match(r"^(\d+)\s*(.+)$", row[0])
            parsed_number = match.group(1) if match else ""
            parsed_name = match.group(2).strip() if match else row[0]
            migrated.append(
                [row[1] or parsed_number, row[2] or parsed_name, row[3], ""]
            )
    else:
        return False

    if len(migrated) - 1 > 199:
        return False

    personal_sheet = "메신저 개인톡 내용"
    class_sheet = "메신저 단체톡 내용"
    personal_status_range = f"{quote_sheet(personal_sheet)}!G:G"
    class_status_range = f"{quote_sheet(class_sheet)}!E:E"
    personal_statuses = read_values(personal_status_range)
    class_statuses = read_values(class_status_range)
    if not personal_statuses or not class_statuses:
        return False

    def queue_status_values(values: list) -> list[list[str]]:
        normalized = []
        for row in values:
            value = str(row[0] if isinstance(row, list) and row else "").strip()
            normalized.append(["대기" if value == "확인필요" else value])
        return normalized

    metadata = _run_one_json(
        runner,
        [
            gws,
            "sheets",
            "spreadsheets",
            "get",
            "--params",
            json.dumps(
                {
                    "spreadsheetId": spreadsheet,
                    "includeGridData": False,
                    "fields": "sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))",
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        Path(workdir),
    )
    sheet_properties = {}
    for item in metadata.get("sheets", []) if isinstance(metadata, dict) else []:
        props = item.get("properties") if isinstance(item, dict) else None
        title = str((props or {}).get("title") or "")
        if title:
            sheet_properties[title] = props

    month_names = [
        value.strip()
        for value in settings_map.get(
            "MONTH_SHEET_NAMES", "3월,4월,5월,6월,7월,8월,9월,10월,11월,12월,1월,2월"
        ).split(",")
        if value.strip()
    ]
    required_sheets = month_names + [personal_sheet, class_sheet]
    if any(name not in sheet_properties for name in required_sheets):
        return False

    combined = [
        row[0] + row[1]
        for row in migrated[1:]
        if str(row[0]).strip() and str(row[1]).strip()
    ]
    hidden_students = [["학생_번호이름"]]
    hidden_students.extend([[value] for value in combined])
    hidden_students.extend([[""]] * (200 - len(hidden_students)))
    queue_status_options = ["대기", "발송중", "제외", "보냄", "실패"]
    value_updates = [
        {
            "range": "'드롭다운'!J1:J200",
            "majorDimension": "ROWS",
            "values": hidden_students,
        },
        {
            "range": "'드롭다운'!G1:G6",
            "majorDimension": "ROWS",
            "values": [["쪽지_상태"]] + [[value] for value in queue_status_options],
        },
        {
            "range": f"{quote_sheet(personal_sheet)}!G1:G{len(personal_statuses)}",
            "majorDimension": "ROWS",
            "values": queue_status_values(personal_statuses),
        },
        {
            "range": f"{quote_sheet(class_sheet)}!E1:E{len(class_statuses)}",
            "majorDimension": "ROWS",
            "values": queue_status_values(class_statuses),
        },
    ]
    if migrated != rows:
        value_updates.insert(
            0,
            {
                "range": f"{quoted_roster}!A1:D{len(migrated)}",
                "majorDimension": "ROWS",
                "values": migrated,
            },
        )
    dropdown_setting_found = False
    for row_number, row in enumerate(settings, start=1):
        if (
            isinstance(row, list)
            and row
            and str(row[0] or "").strip() == "STUDENT_DROPDOWN_RANGE"
        ):
            dropdown_setting_found = True
            value_updates.append(
                {
                    "range": f"'설정'!B{row_number}",
                    "majorDimension": "ROWS",
                    "values": [["J2:J200"]],
                }
            )
            break
    if not dropdown_setting_found:
        setting_row = len(settings) + 1
        value_updates.append(
            {
                "range": f"'설정'!A{setting_row}:B{setting_row}",
                "majorDimension": "ROWS",
                "values": [["STUDENT_DROPDOWN_RANGE", "J2:J200"]],
            }
        )

    _run_one_json(
        runner,
        [
            gws,
            "sheets",
            "spreadsheets",
            "values",
            "batchUpdate",
            "--params",
            json.dumps({"spreadsheetId": spreadsheet}, ensure_ascii=False),
            "--json",
            json.dumps(
                {"valueInputOption": "RAW", "data": value_updates},
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        Path(workdir),
    )

    def validation_rule(values: list[str], *, allow_invalid: bool) -> dict:
        return {
            "condition": {
                "type": "ONE_OF_RANGE" if len(values) == 1 and values[0].startswith("=") else "ONE_OF_LIST",
                "values": [{"userEnteredValue": value} for value in values],
            },
            "strict": not allow_invalid,
            "showCustomUi": True,
        }

    validation_requests = []
    for name in month_names:
        props = sheet_properties[name]
        validation_requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": int(props["sheetId"]),
                        "startRowIndex": 2,
                        "endRowIndex": int(props["gridProperties"]["rowCount"]),
                        "startColumnIndex": 1,
                        "endColumnIndex": 2,
                    },
                    "rule": validation_rule(
                        ["='드롭다운'!$J$2:$J$200"], allow_invalid=True
                    ),
                }
            }
        )
    for name, column_index in ((personal_sheet, 6), (class_sheet, 4)):
        props = sheet_properties[name]
        validation_requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": int(props["sheetId"]),
                        "startRowIndex": 1,
                        "endRowIndex": int(props["gridProperties"]["rowCount"]),
                        "startColumnIndex": column_index,
                        "endColumnIndex": column_index + 1,
                    },
                    "rule": validation_rule(queue_status_options, allow_invalid=False),
                }
            }
        )

    _run_one_json(
        runner,
        [
            gws,
            "sheets",
            "spreadsheets",
            "batchUpdate",
            "--params",
            json.dumps({"spreadsheetId": spreadsheet}, ensure_ascii=False),
            "--json",
            json.dumps({"requests": validation_requests}, ensure_ascii=False),
            "--format",
            "json",
        ],
        Path(workdir),
    )

    def first_column(values: list) -> list[str]:
        return [
            str(row[0] if isinstance(row, list) and row else "").strip()
            for row in values
        ]

    expected_hidden = ["학생_번호이름"] + combined
    if first_column(read_values("'드롭다운'!J1:J200")) != expected_hidden:
        return False
    if first_column(read_values("'드롭다운'!G1:G6")) != [
        "쪽지_상태",
        *queue_status_options,
    ]:
        return False
    if first_column(read_values(personal_status_range)) != first_column(
        queue_status_values(personal_statuses)
    ):
        return False
    if first_column(read_values(class_status_range)) != first_column(
        queue_status_values(class_statuses)
    ):
        return False
    if migrated != rows:
        after_roster = [row4(row) for row in read_values(roster_range)]
        if after_roster != migrated:
            return False

    verified_settings = read_values("설정!A:B")
    verified_settings_map = {
        str(row[0] or "").strip(): str(row[1] or "").strip()
        for row in verified_settings
        if isinstance(row, list) and len(row) >= 2 and str(row[0] or "").strip()
    }
    if verified_settings_map.get("STUDENT_DROPDOWN_RANGE") != "J2:J200":
        return False

    validation_ranges = [f"{quote_sheet(name)}!B3" for name in month_names]
    validation_ranges.extend(
        [f"{quote_sheet(personal_sheet)}!G2", f"{quote_sheet(class_sheet)}!E2"]
    )
    validation_state = _run_one_json(
        runner,
        [
            gws,
            "sheets",
            "spreadsheets",
            "get",
            "--params",
            json.dumps(
                {
                    "spreadsheetId": spreadsheet,
                    "ranges": validation_ranges,
                    "includeGridData": True,
                    "fields": "sheets(properties(title),data(rowData(values(dataValidation))))",
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        Path(workdir),
    )

    validations = {}
    for item in validation_state.get("sheets", []) if isinstance(validation_state, dict) else []:
        title = str(((item.get("properties") or {}).get("title")) or "")
        try:
            rule = item["data"][0]["rowData"][0]["values"][0]["dataValidation"]
        except (KeyError, IndexError, TypeError):
            rule = None
        if title and isinstance(rule, dict):
            validations[title] = rule

    def condition_values(rule: dict) -> list[str]:
        condition = rule.get("condition") if isinstance(rule, dict) else None
        return [
            str(item.get("userEnteredValue") or "")
            for item in (condition or {}).get("values", [])
            if isinstance(item, dict)
        ]

    for name in month_names:
        rule = validations.get(name)
        condition = rule.get("condition") if isinstance(rule, dict) else None
        values = [value.replace("'드롭다운'", "드롭다운") for value in condition_values(rule)]
        if (
            not isinstance(condition, dict)
            or condition.get("type") != "ONE_OF_RANGE"
            or values != ["=드롭다운!$J$2:$J$200"]
            or rule.get("strict") is True
        ):
            return False
    for name in (personal_sheet, class_sheet):
        rule = validations.get(name)
        condition = rule.get("condition") if isinstance(rule, dict) else None
        if (
            not isinstance(condition, dict)
            or condition.get("type") != "ONE_OF_LIST"
            or condition_values(rule) != queue_status_options
            or rule.get("strict") is not True
        ):
            return False
    return True


def guarded(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return _ok(method(self, *args, **kwargs))
        except Exception as error:  # noqa: BLE001 - JS에는 한국어 한 문장만 보낸다
            return _fail(error, method.__name__)

    return wrapper


@dataclass
class BridgeDeps:
    """전 필드 주입 가능. None이면 engine/표준 기본 구현을 쓴다."""

    run_command: object = None
    gemini_transport: object = None
    hotkey_register: object = None
    hotkey_unregister: object = None
    hotkey_modifier_probe: object = None
    home_check_deps: object = None
    apply_deps: object = None
    attendance_deps: object = None
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
    dir_opener: object = None
    popen_factory: object = None
    folder_picker: object = None
    environ: object = None
    gws_config_dir: object = None
    bundled_oauth_client_path: object = None
    node_local_app_data: object = None
    node_opener: object = None
    node_run_command: object = None
    node_preparer: object = None
    node_runtime_resolver: object = None
    ai_skill_approval_loader: object = None
    ai_skill_installer: object = None
    ai_skill_completion_reader: object = None
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
    gws_update_checker: object = None
    gws_update_installer: object = None
    gws_runtime_resolver: object = None
    gws_component_root: object = None
    gemini_key_pusher: object = None
    attendance_script_updater: object = None
    attendance_script_runner: object = None
    attendance_roster_inspector: object = None
    attendance_roster_migrator: object = None
    attendance_ai_inspector: object = None
    attendance_remote_work_timeout_seconds: object = None
    support_clipboard_writer: object = None
    support_mail_opener: object = None


class Api:
    def __init__(self, config_dir, deps: BridgeDeps | None = None):
        self._config_dir = Path(config_dir)
        self._deps = deps or BridgeDeps()
        self._login = engine.LoginSession()
        self._gws_update_offer = None
        self._gws_update_offer_key = ""
        self._gws_update_last_status = None
        self._gws_update_install_lock = threading.Lock()
        self._app_update_offer = None
        self._app_update_offer_key = ""
        self._attendance_script_update_lock = threading.Lock()
        self._gws_login_start_lock = threading.Lock()
        self._attendance_prepare_lock = threading.Lock()
        self._capture_retry_lock = threading.Lock()
        self._attendance_prepare_thread = None
        self._attendance_prepare_result = None
        self._attendance_prepare_issue = None
        # 완료 확인 폴링용 gws 경로 캐시 — 3초 폴마다 resolve_gws(동봉본 SHA-256 검증
        # + 판 확인 실행)를 통째로 다시 돌리지 않는다(검토 C7). 승인된 갱신을 설치하면
        # 실행 파일이 바뀔 수 있어 install_gws_update 성공 시 비운다.
        self._attendance_gws_cache = None
        self._support_clipboard_writer = (
            self._deps.support_clipboard_writer or _write_support_clipboard
        )
        self._support_mail_opener = self._deps.support_mail_opener

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
        """예전 7단계 기록을 9단계로 옮기되 작성 중인 값은 그대로 둔다."""

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
                # 예전 마지막 화면까지 왔지만 완료하지 않은 사람은 새로 생긴
                # 학생 계정·학급 단체톡방 준비 안내(8단계)를 먼저 본다.
                return SETUP_LAST_STEP - 1
            return _V1_STEP_TO_V2.get(max(1, old), 1)

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
        state = self._load_state()
        return {
            "version": version.APP_VERSION,
            "branding": dict(version.BRANDING),
            "mode": "home" if state["completed"] else "wizard",
            "step": state["step"],
            "max_step": int(state.get("max_step") or state["step"]),
            "draft": state["draft"],
            "features": {
                "ai_skill_install_enabled": engine.ai_skill_install_enabled(),
                "attendance_ui_enabled": engine.attendance_ui_enabled(),
            },
        }

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
    def ai_tools_status(self):
        return engine.ai_tools_status()

    @guarded
    def ai_node_status(self):
        kwargs = {}
        if self._deps.node_local_app_data is not None:
            kwargs["local_app_data"] = self._deps.node_local_app_data
        if self._deps.node_run_command is not None:
            kwargs["run_command"] = self._deps.node_run_command
        if self._deps.node_runtime_resolver is not None:
            kwargs["resolver"] = self._deps.node_runtime_resolver
        return self._component_operation(
            "ai_node_status",
            "AI 연결 도구의 현재 상태를 안전하게 확인하지 못했어요.",
            "node_status",
            lambda: engine.ai_node_status(**kwargs),
            change_status="기존 AI 설정과 선택한 연결 대상은 그대로입니다.",
        )

    @guarded
    def ai_node_prepare(self):
        kwargs = {}
        if self._deps.node_local_app_data is not None:
            kwargs["local_app_data"] = self._deps.node_local_app_data
        if self._deps.node_opener is not None:
            kwargs["opener"] = self._deps.node_opener
        if self._deps.node_run_command is not None:
            kwargs["run_command"] = self._deps.node_run_command
        prepare = self._deps.node_preparer or engine.ai_node_prepare
        return self._component_operation(
            "ai_node_prepare",
            "AI 연결 도구를 준비하지 못했어요.",
            "node_prepare",
            lambda: prepare(**kwargs),
            verify=lambda: engine.verify_managed_node_completion(
                local_app_data=self._deps.node_local_app_data,
                run_command=(self._deps.node_run_command or engine.process_win.run_captured),
                resolver=self._deps.node_runtime_resolver,
            ),
            change_status="기존 AI 설정과 선택한 연결 대상은 그대로입니다.",
        )

    @guarded
    def ai_skills_install(self, keys, permission_ack=False):
        requested = {str(key) for key in (keys or [])}
        selected = [
            tool["key"] for tool in engine.AI_TOOLS if tool["key"] in requested
        ]
        # Missing selection and permission are explicit teacher decisions, not
        # temporary failures.  Keep the existing ordinary response for them.
        if not selected or permission_ack is not True:
            return engine.ai_skills_install(
                keys,
                permission_ack=permission_ack,
                **(
                    {"run_command": self._deps.node_run_command}
                    if self._deps.node_run_command is not None
                    else {}
                ),
            )
        if self._deps.ai_skill_installer is None and not engine.ai_skill_install_enabled():
            return engine.ai_skills_install(
                selected,
                permission_ack=True,
                **(
                    {"run_command": self._deps.node_run_command}
                    if self._deps.node_run_command is not None
                    else {}
                ),
            )

        kwargs = {}
        if self._deps.node_run_command is not None:
            kwargs["run_command"] = self._deps.node_run_command
        installer = self._deps.ai_skill_installer or engine.ai_skills_install
        completion_reader = (
            self._deps.ai_skill_completion_reader
            or engine.verify_ai_skills_completion
        )
        approval_loader = self._deps.ai_skill_approval_loader or (
            lambda: ai_skill_install.load_approved_skill(
                bundle_paths.bundle_root() / engine.AI_SKILL_APPROVAL_FILENAME
            )
        )
        approval_box = []

        def exact_approval():
            if not approval_box:
                approval_box.append(approval_loader())
            return approval_box[0]

        def readback():
            return completion_reader(selected, approval=exact_approval())

        def install_once():
            try:
                approval = exact_approval()
            except ai_skill_install.AiSkillInstallError as error:
                return {
                    "success": False,
                    "code": error.code,
                    "detail": error.detail,
                    "version": "",
                }
            result = installer(
                selected,
                permission_ack=True,
                approval=approval,
                **kwargs,
            )
            result = {
                **result,
                "selected": list(selected),
                "receipt": str(getattr(approval, "commit", "") or ""),
            }
            if result.get("code") == "AI_SKILLS_BACKUP_CLEANUP_INCOMPLETE":
                complete, verified = readback()
                if complete:
                    return {
                        **verified,
                        "cleanup_warning": str(result.get("detail") or ""),
                    }
            return result

        return self._component_operation(
            "ai_skills_install",
            "선택한 AI 연결을 준비하지 못했어요.",
            "ai_skill_install",
            install_once,
            verify=readback,
            change_status="선택하지 않은 AI와 기존 개인 설정은 바꾸지 않았습니다.",
        )

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
        self._resolve_goedu_gws_or_fail()
        state = self._load_state()
        state["version"] = SETUP_STATE_VERSION
        state["completed"] = True
        state["step"] = SETUP_LAST_STEP
        state["max_step"] = SETUP_LAST_STEP
        state["draft"] = self._fresh_state()["draft"]
        self._write_state(state)  # draft(키 포함)를 비운 채 기록
        return True

    @guarded
    def restart_setup(self):
        self._write_state(self._fresh_state())
        return True

    # ----- 조회·검증 -----

    def _run(self):
        if self._deps.run_command is not None:
            return self._deps.run_command
        base = self._gws_base_environ()
        environment = gws_env.gws_environ(
            base,
            gws_config_dir=self._gws_config_dir(base),
        )

        def run(args):
            return process_win.run_captured(args, env=environment)

        return run

    def _attendance_remote_run(self):
        """출결 Google 명령은 자식 작업까지 실제 제한 시간이 있는 길로 실행한다."""

        if self._deps.run_command is not None:
            return self._deps.run_command
        base = self._gws_base_environ()
        environment = gws_env.gws_environ(
            base,
            gws_config_dir=self._gws_config_dir(base),
        )

        def run(args):
            return engine.attendance_remote_command(args, environment=environment)

        return run

    def _attendance_script_runner(self):
        """출결 시트·Apps Script 명령을 작업 폴더와 함께 제한 시간 안에 실행한다."""

        if self._deps.attendance_script_runner is not None:
            return self._deps.attendance_script_runner
        base = self._gws_base_environ()
        environment = gws_env.gws_environ(
            base,
            gws_config_dir=self._gws_config_dir(base),
        )

        def script_runner(args, cwd):
            try:
                return engine.attendance_remote_runner(
                    args, cwd, environment=environment
                )
            except subprocess.CalledProcessError as error:
                output = error.stderr or error.output or ""
                try:
                    process_win.write_process_log(
                        paths.logs_dir(self._config_dir),
                        list(error.cmd)
                        if isinstance(error.cmd, (list, tuple))
                        else list(args),
                        int(error.returncode),
                        str(output),
                    )
                except OSError:
                    pass
                raise

        return script_runner

    def _gws_base_environ(self) -> dict:
        """GWS가 물려받을 Windows 환경값의 읽기 전용 사본."""

        return dict(os.environ if self._deps.environ is None else self._deps.environ)

    def _gws_config_dir(self, base: dict | None = None) -> Path:
        """현재 Windows 계정만 쓰는 GWS 로그인 폴더."""

        if self._deps.gws_config_dir is not None:
            return Path(self._deps.gws_config_dir)
        return gws_env.default_gws_config_dir(
            self._gws_base_environ() if base is None else base
        )

    def _unsafe_gws_account_storage(self) -> tuple[str, ...]:
        return gws_env.unsafe_account_storage_overrides(self._gws_base_environ())

    def _require_safe_gws_account_storage(self) -> None:
        if self._unsafe_gws_account_storage():
            raise gws_env.GwsAccountStorageError(
                gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
            )

    @guarded
    def home_checks(self):
        self._require_safe_gws_account_storage()
        results = self._local_read(
            "home_checks", "이 컴퓨터의 점검 내용을 읽지 못했어요.",
            lambda: engine.home_checks(self._config_dir, deps=self._deps.home_check_deps),
        )
        return [asdict(r) for r in results]

    @guarded
    def attendance_status(self):
        self._require_safe_gws_account_storage()
        status_value = engine.read_attendance_status(
            self._config_dir,
            self._run(),
        )
        if status_value.state in _ATTENDANCE_AUTH_BLOCKED_STATES:
            return asdict(status_value)
        status = asdict(status_value)
        if status_value.state == "ready":
            from attendance_install_record import load_attendance_install_record

            record = load_attendance_install_record(
                paths.attendance_install_record_path(self._config_dir)
            )
            layout_inspector = (
                self._deps.attendance_roster_inspector
                or _attendance_workbook_layout_is_current
            )
            try:
                _run, gws, _account = (
                    self._resolve_attendance_goedu_gws_context_or_fail()
                )
                layout_current = layout_inspector(
                    runner=self._attendance_script_runner(),
                    workdir=self._config_dir,
                    gws_executable=gws,
                    spreadsheet_id=record["spreadsheet_id"],
                )
            except Exception:  # noqa: BLE001 - Google 원문은 화면에 내보내지 않는다.
                layout_current = None
            if layout_current is not True:
                status["state"] = (
                    "script-update-required"
                    if layout_current is False
                    else "script-check-required"
                )
                status["detail"] = (
                    _ATTENDANCE_WORKBOOK_LAYOUT_UPDATE_MESSAGE
                    if layout_current is False
                    else engine.ATTENDANCE_SCRIPT_CHECK_REQUIRED_MESSAGE
                )
        # 다음에 켤 때 "확인하는 중…" 없이 이 상태부터 보여준다.
        engine.save_attendance_status_cache(self._config_dir, status)
        return status

    @guarded
    def attendance_status_cached(self):
        """켠 직후 화면이 먼저 집는 저장본 — 없으면 None(화면이 확인 문구를 보인다)."""
        return engine.load_attendance_status_cache(self._config_dir)

    @guarded
    def attendance_connection_candidates(self):
        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        return asdict(
            self._attendance_operation(
                "attendance_connection_candidates",
                "기존 정식 출석부 목록을 확인하지 못했어요.",
                lambda: engine.attendance_connection_candidates(
                self._config_dir,
                deps=deps,
                include_row_counts=False,
                ),
            )
        )

    @guarded
    def select_attendance_connection(self, spreadsheet_id):
        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        result = self._attendance_operation(
            "select_attendance_connection",
            "고른 출석부 연결을 확인하지 못했어요.",
            lambda: engine.select_attendance_connection(
                self._config_dir, str(spreadsheet_id or ""), deps=deps
            ),
        )
        if result.state == "selected":
            try:
                (self._config_dir / engine.ATTENDANCE_STATUS_CACHE_NAME).unlink()
            except OSError:
                # 정본 선택은 이미 원자 교체로 끝났다. 옛 화면 저장본을 치우지
                # 못했다는 이유로 선택 성공을 실패처럼 돌려주지 않는다. 저장본은
                # 다음 읽기에서 현재 정본 URL과 대조되어 맞지 않으면 버려진다.
                pass
        return asdict(result)

    @guarded
    def select_attendance_connection_by_code(self, connection_code):
        """붙여 넣은 확인번호를 정식 후보 하나와 대조해 현재 연결을 바꾼다."""

        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        def action():
            return engine.select_attendance_connection_by_code(
                self._config_dir, str(connection_code or ""), deps=deps
            )
        checked_code = str(connection_code or "").strip().upper()
        if re.fullmatch(r"TM-[0-9A-F]{6}-[0-9A-F]{6}", checked_code) is None:
            # 모양이 틀린 번호는 선생님이 바로 고칠 일이다. Google 재시도로
            # 바꾸거나 공통 문제 화면으로 보내지 않는다.
            result = action()
        else:
            result = self._attendance_operation(
                "select_attendance_connection_by_code",
                "확인번호로 출석부 연결을 확인하지 못했어요.",
                action,
            )
        if result.state == "selected":
            try:
                (self._config_dir / engine.ATTENDANCE_STATUS_CACHE_NAME).unlink()
            except OSError:
                pass
        return asdict(result)

    @guarded
    def ensure_attendance(self):
        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        status = asdict(self._attendance_operation(
            "ensure_attendance",
            "출결 자료를 준비하지 못했어요.",
            lambda: engine.ensure_attendance(self._config_dir, deps=deps),
        ))
        # 다음에 켤 때 저장본부터 보여주는 화면이 방금 만든 결과를 곧바로 보게 한다.
        if status.get("state") not in _ATTENDANCE_AUTH_BLOCKED_STATES:
            engine.save_attendance_status_cache(self._config_dir, status)
        return status

    @guarded
    def start_new_attendance(self):
        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        status = asdict(self._attendance_operation(
            "start_new_attendance",
            "새 학년도 출석부를 시작하지 못했어요.",
            lambda: engine.start_new_attendance(self._config_dir, deps=deps),
        ))
        # 다음에 켤 때 저장본부터 보여주는 화면이 방금 만든 결과를 곧바로 보게 한다.
        if status.get("state") not in _ATTENDANCE_AUTH_BLOCKED_STATES:
            engine.save_attendance_status_cache(self._config_dir, status)
        return status

    @guarded
    def attendance_prepare_start(self, profile, grid, bridge_updates):
        """메신저 탭 [다음] — 입력 저장 후 출결 준비를 뒤에서 시작한다. 여러 번 불려도 안전."""
        self._require_safe_gws_account_storage()
        # pywebview는 js_api 호출마다 새 스레드를 만든다 — [다음] 더블클릭이면
        # is_alive 확인과 start() 사이(저장은 수백 ms~수 초)에 둘 다 지나가
        # 준비 스레드가 2개 생기고, 진 쪽이 잠금 대기로 죽은 뒤 그 죽은 스레드가
        # _attendance_prepare_thread에 남는다. 확인→저장→시작 전체를 직렬화한다.
        with self._attendance_prepare_lock:
            thread = self._attendance_prepare_thread
            if thread is not None and thread.is_alive():
                return {"started": True, "reason": "이미 준비하는 중이에요"}
            save_deps = self._deps.apply_deps or engine.ApplyDeps(
                run_command=self._attendance_remote_run()
            )
            try:
                ok, reason = engine.save_wizard_inputs(
                    self._config_dir, dict(profile), list(grid), dict(bridge_updates),
                    deps=save_deps,
                )
            except RuntimeError:
                # 로그인 문제(require_goedu_gws_session)는 guarded의 오류 응답이 아니라
                # started=False + 사연으로 화면에 가야 배너를 띄울 수 있다.
                return {
                    "started": False,
                    "reason": (
                        "이 컴퓨터 설정을 저장하지 못했어요. 현재 Windows 계정의 "
                        "@goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요."
                    ),
                }
            if not ok:
                return {"started": False, "reason": reason}
            att_deps = self._deps.attendance_deps or engine.AttendanceDeps(
                run_command=self._attendance_remote_run()
            )

            def _prepare():
                # 예외로 조용히 죽으면 화면은 running=False + 사유 0글자만 본다.
                # 성공이든 실패든 결과를 남겨 attendance_prepare_status가 보여준다.
                try:
                    status = asdict(
                        self._attendance_operation(
                            "attendance_prepare_start",
                            "출결 자료를 준비하지 못했어요.",
                            lambda: engine.ensure_attendance(
                                self._config_dir, deps=att_deps
                            ),
                        )
                    )
                    # ensure_attendance와 같은 규칙: 허용 계정으로 만든 결과만 저장본에 남긴다.
                    if status.get("state") not in _ATTENDANCE_AUTH_BLOCKED_STATES:
                        engine.save_attendance_status_cache(self._config_dir, status)
                except Exception as error:  # noqa: BLE001 - 사람이 읽을 문장으로 바꾼다
                    if isinstance(
                        error,
                        (recovery.UserActionRequired, recovery.FinalOperationFailure),
                    ):
                        self._attendance_prepare_issue = error
                        return
                    failed_service, detail = engine.friendly_attendance_error(error)
                    status = asdict(engine.AttendanceStatus(
                        state="failed", failed_service=failed_service,
                        detail=detail[:engine.ATTENDANCE_DETAIL_LIMIT],
                    ))
                self._attendance_prepare_result = status

            self._attendance_prepare_result = None
            self._attendance_prepare_issue = None
            thread = threading.Thread(
                target=_prepare, name="attendance-prepare", daemon=True
            )
            self._attendance_prepare_thread = thread
            thread.start()
            return {"started": True, "reason": ""}

    @guarded
    def attendance_prepare_status(self):
        """뒤에서 도는 출결 준비의 진행 여부와 현재 출결 상태.

        도는 동안에는 gws를 부르지 않는다 — 3초 폴마다 gws 3회 실행과 동봉본
        SHA-256 해시가 쌓이기 때문. 로컬 진행 기록(attendance-setup-status)만 읽고,
        끝난 뒤에는 준비 스레드가 남긴 결과를 그대로 보여준다.
        """
        thread = self._attendance_prepare_thread
        if thread is not None and thread.is_alive():
            setup = engine._read_setup_status(self._config_dir)
            progress = setup.get("progress")
            return {"running": True, "status": {
                "state": "installing",
                "progress": dict(progress) if isinstance(progress, dict) else {},
            }}
        result = self._attendance_prepare_result
        if result is not None:
            return {"running": False, "status": dict(result)}
        issue = self._attendance_prepare_issue
        if issue is not None:
            raise issue
        # 이 창에서 준비를 돌린 적이 없을 때(재시작 등)만 실제 상태를 읽는다.
        # 폴마다 부를 수 있는 네트워크 명령이므로 제한 시간 없는 self._run() 대신
        # 자식 작업까지 제한 시간이 있는 감독 실행 경로를 쓴다(검토 C7).
        self._require_safe_gws_account_storage()
        status_value = engine.read_attendance_status(
            self._config_dir, self._attendance_remote_run()
        )
        return {"running": False, "status": asdict(status_value)}

    @guarded
    def attendance_first_setup_status(self):
        """시트의 [처음 설정 한 번에 끝내기] 완료 표시 — 마법사 출결 탭이 폴링한다."""
        self._require_safe_gws_account_storage()
        # 폴마다 부르는 네트워크 명령이므로 제한 시간 없는 self._run() 대신
        # 자식 작업까지 제한 시간이 있는 감독 실행 경로를 쓴다.
        run = self._attendance_remote_run()
        if self._attendance_gws_cache is None:
            # gws 경로 찾기(동봉본 검증 포함)는 폴마다가 아니라 한 번만(검토 C7).
            self._attendance_gws_cache = str(engine.resolve_gws(run))

        def read_once():
            saved = engine._read_setup_status(self._config_dir)
            owner = str(saved.get("account", "") or "").strip()
            engine._require_google_target_account(
                run, self._attendance_gws_cache, owner
            )
            return engine.read_first_time_setup_done(
                self._config_dir, run, self._attendance_gws_cache
            )

        return recovery.run_operation(
            "attendance_first_setup_status",
            "출석부의 처음 설정 상태를 확인하지 못했어요.",
            read_once,
            delays=recovery.NETWORK_DELAYS,
            change_status="기존 출결 자료는 그대로입니다.",
            app_version=version.APP_VERSION,
            **self._network_recovery_options(),
        )

    def _attendance_script_update(
        self,
        *,
        apply: bool,
        record_snapshot=None,
        resolved=None,
        account: str = "",
    ):
        """기존 출결 Sheet의 Apps Script만 확인하거나 명시적으로 갱신한다."""

        record_path = paths.attendance_install_record_path(self._config_dir)
        if record_snapshot is None and not record_path.exists():
            return {
                "state": "not-ready",
                "verified": False,
                "detail": "출결 준비를 먼저 마쳐 주세요.",
            }
        from attendance_install_record import (
            mark_attendance_script_current,
            read_attendance_install_snapshot,
            validate_verified_canonical_record,
        )

        if record_snapshot is None:
            record_snapshot = read_attendance_install_snapshot(record_path)
        try:
            record = validate_verified_canonical_record(record_snapshot.record)
        except Exception:
            return {
                "state": "connection-repair-required",
                "verified": False,
                "detail": engine.ATTENDANCE_CONNECTION_REPAIR_MESSAGE,
            }
        run, gws = (
            resolved
            if resolved is not None
            else self._resolve_attendance_goedu_gws_or_fail()
        )
        mutation_guard = self._attendance_update_mutation_guard(
            run, gws, account
        ) if apply else None
        if apply and not mutation_guard():
            return {
                "state": "permission-required",
                "verified": False,
                "detail": _ATTENDANCE_UPDATE_PERMISSION_MESSAGE,
            }
        updater = self._deps.attendance_script_updater
        if updater is None:
            from attendance_script_update import inspect_or_update_attendance_script

            updater = inspect_or_update_attendance_script
        script_runner = self._attendance_script_runner()
        assets_dir = bundle_paths.bundle_root() / "assets"
        update_options = {
            "assets_dir": assets_dir,
            "apply": apply,
            "runner": script_runner,
            "gws_executable": gws,
        }
        if apply:
            update_options["mutation_guard"] = mutation_guard
        result = updater(
            record,
            **update_options,
        )
        payload = dict(result) if isinstance(result, dict) else asdict(result)
        if apply and payload.get("state") == "permission-required":
            return {
                "state": "permission-required",
                "verified": False,
                "detail": _ATTENDANCE_UPDATE_PERMISSION_MESSAGE,
            }
        if (
            apply
            and payload.get("verified") is True
            and payload.get("state") in {"current", "updated"}
        ):
            # 결과 글자만 믿지 않는다. 현재 프로그램에 실제로 든 코드 지문, 원격
            # 확인 결과, 처음 읽은 세 연결 ID가 모두 같을 때만 준비 증명을 남긴다.
            expected_sha256 = engine.current_attendance_script_bundle_sha256()
            from attendance_script_update import verified_same_attendance_connection

            if not verified_same_attendance_connection(
                payload, record, expected_sha256
            ):
                raise ScreenSafeError(
                    "확인한 출결 기능이 지금 프로그램에 든 파일과 달라서 준비 완료로 바꾸지 않았어요."
                )
            if not mutation_guard():
                return {
                    "state": "permission-required",
                    "verified": False,
                    "detail": _ATTENDANCE_UPDATE_PERMISSION_MESSAGE,
                }
            roster_migrator = self._deps.attendance_roster_migrator
            if roster_migrator is None:
                roster_migrator = _migrate_attendance_roster_layout
            try:
                roster_migrated = roster_migrator(
                    runner=script_runner,
                    workdir=self._config_dir,
                    gws_executable=gws,
                    spreadsheet_id=record["spreadsheet_id"],
                )
            except Exception:  # noqa: BLE001 - Google 원문은 화면에 내보내지 않는다.
                roster_migrated = False
            if roster_migrated is not True:
                payload["state"] = "hold"
                payload["verified"] = False
                payload["detail"] = _ATTENDANCE_ROSTER_MIGRATION_MESSAGE
                return payload
            ai_inspector = self._deps.attendance_ai_inspector
            if ai_inspector is None:
                from attendance_ai_setup import inspect_attendance_ai_setup

                ai_inspector = inspect_attendance_ai_setup
            ai_args = {
                "runner": script_runner,
                "workdir": self._config_dir,
                "gws_executable": gws,
                "spreadsheet_id": record["spreadsheet_id"],
            }
            try:
                ai_status = ai_inspector(**ai_args)
            except Exception:  # noqa: BLE001 - 외부 원문을 화면에 내보내지 않는다.
                ai_status = None
            if not self._attendance_ai_setup_is_current(ai_status):
                payload["state"] = "ai-action-required"
                payload["verified"] = False
                payload["detail"] = _ATTENDANCE_AI_PROOF_MESSAGE
                payload["spreadsheet_url"] = str(
                    record.get("spreadsheet_url", "") or ""
                )
                return payload
            if not mutation_guard():
                return {
                    "state": "permission-required",
                    "verified": False,
                    "detail": _ATTENDANCE_UPDATE_PERMISSION_MESSAGE,
                }
            # 업데이트를 시작할 때 읽은 기록이 지금도 정확히 같을 때만 증명을 쓰고
            # 옛판 표식을 함께 지운다. 다른 창의 새 기록은 건드리지 않는다.
            mark_attendance_script_current(
                record_path, record_snapshot, expected_sha256
            )
        return payload

    def _attendance_update_mutation_guard(self, run, gws, expected_account):
        expected = str(expected_account or "").strip().casefold()

        def guard():
            if not expected:
                return False
            try:
                current = engine.require_goedu_gws_session(run, gws)
            except Exception:  # noqa: BLE001 - 인증 원문은 호출자에게 내보내지 않는다.
                return False
            return (
                current.casefold() == expected
                and engine.has_current_gws_scope_grant(
                    self._config_dir, expected_account
                )
            )

        return guard

    @staticmethod
    def _attendance_ai_setup_is_current(status) -> bool:
        return (
            getattr(status, "ok", None) is True
            and getattr(status, "state", None) == "verified"
            and getattr(status, "spreadsheet_matches", None) is True
            and getattr(status, "target_matches", None) is True
            and type(getattr(status, "trigger_count", None)) is int
            and status.trigger_count == 1
            and getattr(status, "setup_done", None) is True
        )

    def _require_current_attendance_script(self, record=None) -> None:
        """예전 공식 출결 기능을 되찾은 직후에는 Chat 쓰기를 먼저 막는다."""

        record_path = paths.attendance_install_record_path(self._config_dir)
        if record is None and not record_path.exists():
            return
        from attendance_install_record import (
            attendance_script_is_attested,
            load_attendance_install_record,
        )

        if record is None:
            record = load_attendance_install_record(record_path)
        if (
            record.get("script_update_required") is True
            or not attendance_script_is_attested(
                record, engine.current_attendance_script_bundle_sha256()
            )
        ):
            raise ScreenSafeError(
                "기존 자료는 그대로 두었지만 출결 기능 확인 또는 업데이트가 먼저 필요해요. "
                "출결 탭 위쪽의 한 줄 안내에 보이는 버튼을 눌러 주세요."
            )

    def _require_current_remote_attendance_script(
        self,
        record_snapshot,
        resolved,
    ) -> None:
        """Chat 직전에 HEAD·고정 배포판·현재 프로그램 파일을 다시 읽어 맞춘다."""

        payload = self._attendance_script_update(
            apply=False,
            record_snapshot=record_snapshot,
            resolved=resolved,
        )
        record = record_snapshot.record
        expected_sha256 = engine.current_attendance_script_bundle_sha256()
        same_ids = all(
            payload.get(key) == record.get(key)
            for key in ("spreadsheet_id", "script_id", "deployment_id")
        )
        if not (
            payload.get("state") == "current"
            and payload.get("verified") is True
            and same_ids
            and payload.get("current_bundle_sha256") == expected_sha256
            and payload.get("target_bundle_sha256") == expected_sha256
        ):
            raise ScreenSafeError(
                "현재 Google의 출결 기능을 안전하게 다시 확인하지 못해 "
                "Chat 작업을 시작하지 않았어요. "
                "출결 탭 위쪽의 한 줄 안내에 보이는 버튼을 눌러 주세요."
            )

    def _run_attendance_chat_action(self, action):
        """긴 작업은 별도 잠금으로 직렬화하고 설치 기록 잠금은 짧게만 쓴다."""

        from attendance_install_record import (
            attendance_install_record_lock,
            read_attendance_install_snapshot,
        )

        record_path = paths.attendance_install_record_path(self._config_dir)
        timeout = self._deps.attendance_remote_work_timeout_seconds
        lock_options = {}
        if timeout is not None:
            lock_options["timeout_seconds"] = float(timeout)
        # 출결 새 준비·연결 교체·Chat 작업은 같은 원격 작업 잠금을 쓴다. 반면
        # 설치 기록 파일은 처음 snapshot과 마지막 대조 순간에만 잠근다.
        with engine.attendance_remote_work_lock(self._config_dir, **lock_options):
            resolved = (
                self._resolve_attendance_goedu_gws_or_fail()
                if record_path.exists()
                else None
            )
            with attendance_install_record_lock(record_path):
                record_snapshot = (
                    read_attendance_install_snapshot(record_path)
                    if record_path.exists()
                    else None
                )

            if record_snapshot is not None:
                if resolved is None:
                    # 파일이 없다고 본 직후 다른 과정이 새 연결을 놓은 드문 경우다.
                    # 그 새 연결을 이 버튼이 우연히 이어 쓰지 않고 다시 눌러 확인시킨다.
                    raise ScreenSafeError(
                        "출결 연결이 방금 바뀌었어요. 현재 출결 상태를 다시 확인해 주세요."
                    )
                run, gws = resolved
                self._require_current_attendance_script(record_snapshot.record)
                self._require_current_remote_attendance_script(
                    record_snapshot, (run, gws)
                )
            else:
                run, gws = self._attendance_remote_run(), None

            result = action(
                run,
                gws,
                None if record_snapshot is None else record_snapshot.record,
            )

            if record_snapshot is not None:
                with attendance_install_record_lock(record_path):
                    changed = not record_path.exists()
                    if not changed:
                        current = read_attendance_install_snapshot(record_path)
                        changed = (
                            current.raw != record_snapshot.raw
                            or current.sha256 != record_snapshot.sha256
                        )
                if changed:
                    raise ScreenSafeError(
                        "Chat 작업 중 다른 창에서 출결 연결이 바뀌었어요. "
                        "새 연결에는 결과를 쓰지 않았습니다. 현재 출결 상태를 다시 확인해 주세요."
                    )
            return result

    @guarded
    def attendance_script_update_status(self):
        # 이 길은 Apps Script와 배포 상태만 읽는다. Sheet나 설치 기록은 쓰지 않는다.
        return self._attendance_operation(
            "attendance_script_update_status",
            "출결 기능 상태를 확인하지 못했어요.",
            lambda: self._attendance_script_update(apply=False),
            retry_states={"hold"},
        )

    @guarded
    def attendance_script_update_apply(self):
        # 화면의 별도 확인창을 통과해 여기로 왔을 때만 쓰기 동작을 허용한다.
        if not self._attendance_script_update_lock.acquire(blocking=False):
            raise ScreenSafeError("출결 기능을 이미 업데이트하고 있어요. 잠시만 기다려 주세요.")
        try:
            # 다른 대시보드 창과 새 학년도 출결 준비도 같은 기록과 Google Script를
            # 만질 수 있다. 공용 잠금 안에서 계정부터 다시 확인하고 한 번씩 실행한다.
            timeout = self._deps.attendance_remote_work_timeout_seconds
            lock_options = {}
            if timeout is not None:
                lock_options["timeout_seconds"] = float(timeout)
            with engine.attendance_remote_work_lock(
                self._config_dir, **lock_options
            ):
                if not paths.attendance_install_record_path(
                    self._config_dir
                ).exists():
                    return self._attendance_operation(
                        "attendance_script_update_apply",
                        "출결 기능을 바꾸지 못했어요.",
                        lambda: self._attendance_script_update(apply=True),
                        retry_states=frozenset(),
                    )
                run, gws, account = (
                    self._resolve_attendance_goedu_gws_context_or_fail()
                )
                return self._attendance_operation(
                    "attendance_script_update_apply",
                    "출결 기능을 바꾸지 못했어요.",
                    lambda: self._attendance_script_update(
                        apply=True,
                        resolved=(run, gws),
                        account=account,
                    ),
                    retry_states=frozenset(),
                )
        finally:
            self._attendance_script_update_lock.release()

    @guarded
    def attendance_chat_status(self):
        from dashboard import central_chat
        # 상태 조회는 화면에 보여 줄 값만 읽고, Google 시트는 바꾸지 않는다.
        if paths.attendance_install_record_path(self._config_dir).exists():
            run, gws = self._resolve_attendance_goedu_gws_or_fail()
        else:
            run, gws = self._run(), None
        def read_status():
            value = central_chat.chat_status(
                self._config_dir,
                run,
                gws_executable=gws,
            )
            if value.get("reason") == central_chat.CHAT_STATUS_FAILURE_MESSAGE:
                raise recovery.RetryableOperationError(
                    "CHAT_STATUS_READ",
                    central_chat.CHAT_STATUS_FAILURE_MESSAGE,
                )
            return value

        return self._attendance_operation(
            "attendance_chat_status",
            "학급 단톡방 상태를 확인하지 못했어요.",
            read_status,
            retry_states=frozenset(),
        )

    @guarded
    def attendance_chat_connect(self):
        from dashboard import central_chat
        auth_url = self._run_attendance_chat_action(
            lambda run, gws, record: central_chat.start_auth(
                self._config_dir,
                run,
                gws_executable=gws,
                attendance_record=record,
            )
        )
        return self._open_external_url(auth_url)

    @guarded
    def attendance_chat_spaces(self):
        from dashboard import central_chat

        def read_spaces():
            try:
                return self._run_attendance_chat_action(
                    lambda run, gws, record: central_chat.list_spaces(
                        self._config_dir,
                        run,
                        gws_executable=gws,
                        attendance_record=record,
                    )
                )
            except (ScreenSafeError, gws_env.GwsAccountStorageError):
                # 로그인·계정·현재 출결 연결처럼 선생님이 바로잡아야 하는 일은
                # 같은 읽기를 세 번 되풀이하지 않고 원래 안내를 그대로 보낸다.
                raise
            except central_chat.CentralChatError as error:
                raise recovery.RetryableOperationError(
                    "CHAT_SPACE_LIST_READ",
                    central_chat._safe_central_error_detail(error),
                ) from error
            except Exception as error:  # noqa: BLE001 - 방 목록 전 준비 과정도 같은 세 번 안에 둔다.
                # 실제 설치 기록이 있으면 목록 호출 전에 계정, 로컬 출결 기능,
                # Google Apps Script 현재 상태를 읽는다. 이 앞단의 잠깐 겹침·시간
                # 초과·실행기 오류도 목록 호출과 똑같이 세 번 안에서 처리해야 한다.
                raise recovery.RetryableOperationError(
                    "CHAT_SPACE_PREFLIGHT_READ",
                    "학급 단톡방 목록을 읽기 위한 Google 확인을 다시 진행하고 있어요.",
                ) from error

        return self._attendance_operation(
            "attendance_chat_spaces",
            "학급 단톡방 목록을 가져오지 못했어요.",
            read_spaces,
            retry_states=frozenset(),
        )

    @guarded
    def attendance_chat_set_space(self, space_name, display_name, expected_current=""):
        from dashboard import central_chat

        def reconcile_selection():
            try:
                return self._run_attendance_chat_action(
                    lambda run, gws, record: central_chat.set_class_space(
                        self._config_dir,
                        str(space_name),
                        str(display_name),
                        run,
                        gws_executable=gws,
                        attendance_record=record,
                        expected_current=str(expected_current or ""),
                    )
                )
            except central_chat.CentralChatError as error:
                if getattr(error, "code", "") == (
                    central_chat.CLASS_SPACE_SELECTION_CHANGED_CODE
                ):
                    raise recovery.UserActionRequired(recovery.UserIssue.needs_user(
                        operation="attendance_chat_set_space",
                        title="학급 단톡방 선택이 다른 창에서 바뀌었어요.",
                        message="현재 상태와 방 목록을 다시 확인해 주세요.",
                        change_status="다른 창에서 고른 학급 단톡방은 그대로입니다.",
                        actions=(recovery.IssueAction(
                            "chat-space-list", "학급 단체톡방 다시 확인"
                        ),),
                        resume="chat-space-list",
                    )) from error
                raise recovery.RetryableOperationError(
                    "CHAT_SPACE_SELECTION",
                    central_chat._safe_central_error_detail(error),
                ) from error

        return self._attendance_operation(
            "attendance_chat_set_space",
            "학급 단톡방 선택을 저장하지 못했어요.",
            reconcile_selection,
            retry_states=frozenset(),
        )

    @guarded
    def attendance_chat_create_space(self, display_name=""):
        from dashboard import central_chat

        def create_or_resume():
            try:
                return self._run_attendance_chat_action(
                    lambda run, gws, record: central_chat.create_class_space(
                        self._config_dir,
                        str(display_name),
                        run,
                        gws_executable=gws,
                        attendance_record=record,
                    )
                )
            except central_chat.CentralChatError as error:
                raise recovery.RetryableOperationError(
                    "CHAT_SPACE_CREATE",
                    central_chat._safe_central_error_detail(error),
                ) from error

        return self._attendance_operation(
            "attendance_chat_create_space",
            "학급 단톡방을 만들지 못했어요.",
            create_or_resume,
            retry_states={"failed", "in-progress"},
        )

    @guarded
    def computer_status(self):
        return engine.computer_readiness(self._run())

    @guarded
    def google_status(self):
        base, config_dir, _bundled, selection = self._oauth_context()
        credential_override = bool(
            str(base.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE") or "").strip()
        )
        if gws_env.unsafe_account_storage_overrides(base):
            # 파일 위치를 고치기 전에는 gws --version이나 auth status조차 실행하지
            # 않는다. 공용/다른 계정의 토큰을 우연히 읽는 일을 먼저 막는다.
            return {
                "gws_runtime_ready": False,
                "oauth_client_ready": bool(selection.ready),
                "oauth_client_conflict": selection.source == "conflict",
                "credential_override_present": credential_override,
                "account_storage_override_unsafe": True,
                "login_state": "error",
                "error_code": gws_env.ACCOUNT_STORAGE_ERROR_CODE,
                "logged_in": False,
                "account_allowed": False,
                "user": "",
            }

        run = self._run()
        runtime_error = ""
        try:
            gws = engine.resolve_gws(run)
        except engine.tool_runtime.GwsRuntimeError as error:
            gws = ""
            runtime_error = error.code
        auth = (
            engine.gws_auth_status(run, gws)
            if gws
            else {
                "logged_in": False,
                "account_allowed": False,
                "user": "",
                "login_state": "not_checked",
                "error_code": "",
            }
        )
        error_code = runtime_error or selection.error_code or auth.get("error_code", "")
        return {
            "gws_runtime_ready": bool(gws),
            "oauth_client_ready": bool(selection.ready),
            "oauth_client_conflict": selection.source == "conflict",
            "credential_override_present": credential_override,
            "account_storage_override_unsafe": False,
            "login_state": auth.get("login_state", "not_checked"),
            "error_code": error_code,
            "logged_in": bool(auth["logged_in"]),
            "account_allowed": bool(auth.get("account_allowed")),
            "user": auth["user"],
        }

    @guarded
    def gws_update_status(self):
        # 새 확인을 시작한 순간 이전 승인 제안은 더 이상 설치 근거가 아니다.
        # 최종 문제로 중간에 끝나도 옛 화면 버튼이 그 값을 다시 쓸 수 없게 먼저 비운다.
        self._gws_update_offer = None
        self._gws_update_offer_key = ""
        if self._unsafe_gws_account_storage():
            self._gws_update_last_status = {
                "success": False,
                "code": gws_env.ACCOUNT_STORAGE_ERROR_CODE,
                "detail": gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE,
                "checked_on": "",
                "offer": None,
                "runtime_ready": False,
                "can_continue": False,
                "repair_required": True,
                "current_version": "",
                "current_source": "",
                "runtime_error_code": gws_env.ACCOUNT_STORAGE_ERROR_CODE,
            }
            return dict(self._gws_update_last_status)
        exact = {"offer": None}

        def read_once():
            try:
                status, exact_offer = engine.read_gws_update_status(
                    version.APP_VERSION,
                    self._run(),
                    component_root=self._deps.gws_component_root,
                    checker=self._deps.gws_update_checker,
                    resolver=self._deps.gws_runtime_resolver,
                )
            except (OSError, TimeoutError) as error:
                raise recovery.RetryableOperationError(
                    "NETWORK_TIMEOUT",
                    "Google 연결 기능의 갱신 상태를 다시 확인하고 있어요.",
                ) from error
            exact["offer"] = exact_offer
            return status

        status = self._component_operation(
            "gws_update_status",
            "Google 연결 기능의 갱신 상태를 확인하지 못했어요.",
            "gws_check",
            read_once,
            change_status="현재 Google 연결 기능과 기존 설정은 그대로입니다.",
        )
        exact_offer = exact["offer"]
        self._gws_update_offer = exact_offer
        self._gws_update_offer_key = self._screen_offer_key(status.get("offer"))
        self._gws_update_last_status = dict(status)
        return status

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

    @guarded
    def list_calendars(self):
        run, gws = self._resolve_gws_or_fail()
        return engine.list_calendars(run, gws)

    @guarded
    def list_tasklists(self):
        run, gws = self._resolve_gws_or_fail()
        return engine.list_tasklists(run, gws)

    @guarded
    def verify_google_target_candidate(self, kind, candidate_id, name, account):
        if kind not in {"calendar", "tasklist"}:
            raise ValueError("Google 연결 종류를 다시 확인해 주세요")
        if not all(isinstance(value, str) and value.strip() for value in (candidate_id, name, account)):
            raise ValueError("Google 연결 후보를 다시 확인해 주세요")
        run, gws = self._resolve_gws_or_fail()
        verified = engine.verify_google_target_candidate(
            run,
            gws,
            account.strip(),
            kind,
            candidate_id.strip(),
            name.strip(),
            **self._network_recovery_options(),
        )
        return {"verified": verified}

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
        shown["retryable"] = pipeline.saved_capture_retry_available(
            self._config_dir,
            str(shown.get("source_hash") or ""),
            str(shown.get("when") or ""),
        )
        return shown

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
        if not self._capture_retry_lock.acquire(blocking=False):
            raise ScreenSafeError("다른 실패 기록을 다시 처리하고 있어요. 끝난 뒤 눌러 주세요.")
        try:
            result = pipeline.retry_saved_capture(
                self._config_dir,
                source_hash,
                capture_when=capture_when,
            )
        finally:
            self._capture_retry_lock.release()
        return {
            "success": bool(result.ok),
            "stage": str(result.stage),
            "message": str(result.message),
        }

    # ----- 행동 -----

    def _success(self, ok, detail):
        return {"success": bool(ok), "detail": detail}

    @staticmethod
    def _screen_offer_key(offer) -> str:
        if not isinstance(offer, dict):
            return ""
        try:
            return json.dumps(
                offer,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return ""

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
        self._remember_app_update_offer(info)
        return info

    def _gws_update_failure(self, code: str, detail: str) -> dict:
        previous = self._gws_update_last_status or {}
        return {
            "success": False,
            "code": code,
            "detail": detail,
            "runtime_ready": bool(previous.get("runtime_ready")),
            "can_continue": bool(previous.get("can_continue")),
            "repair_required": bool(previous.get("repair_required")),
            "current_version": str(previous.get("current_version") or ""),
            "current_source": str(previous.get("current_source") or ""),
            "runtime_error_code": str(previous.get("runtime_error_code") or ""),
        }

    @guarded
    def install_gws_update(self, offer):
        self._require_safe_gws_account_storage()
        if not self._gws_update_install_lock.acquire(blocking=False):
            return self._gws_update_failure(
                "COMPONENT_UPDATE_BUSY",
                "다른 Google 도구 갱신이 끝난 뒤 다시 눌러 주세요.",
            )
        try:
            shown_key = self._screen_offer_key(offer)
            if (
                self._gws_update_offer is None
                or not shown_key
                or shown_key != self._gws_update_offer_key
            ):
                return self._gws_update_failure(
                    "UPDATE_OFFER_CHANGED",
                    "처음 확인한 승인 정보와 달라 적용하지 않았어요. 다시 점검해 주세요.",
                )
            exact_offer = self._gws_update_offer
            result = self._component_operation(
                "install_gws_update",
                "Google 연결 기능을 갱신하지 못했어요.",
                "gws_install",
                lambda: engine.apply_gws_update(
                    exact_offer,
                    self._run(),
                    component_root=self._deps.gws_component_root,
                    installer=self._deps.gws_update_installer,
                    resolver=self._deps.gws_runtime_resolver,
                ),
                verify=lambda: engine.verify_gws_update_completion(
                    exact_offer,
                    self._run(),
                    component_root=self._deps.gws_component_root,
                    resolver=self._deps.gws_runtime_resolver,
                ),
                return_stop_result=True,
                change_status="현재 Google 연결 기능과 기존 로그인 정보는 그대로입니다.",
            )
            self._gws_update_last_status = {
                **(self._gws_update_last_status or {}),
                **result,
            }
            if result.get("success"):
                self._gws_update_offer = None
                self._gws_update_offer_key = ""
                self._gws_update_last_status["offer"] = None
                # 실행 파일이 바뀌었을 수 있다 — 완료 확인 폴링의 gws 경로 캐시를 비운다.
                self._attendance_gws_cache = None
            return result
        finally:
            self._gws_update_install_lock.release()

    @guarded
    def gws_login_start(self):
        run, gws = self._resolve_gws_or_fail()
        with self._gws_login_start_lock:
            snapshot = self._login.snapshot()
            if snapshot.get("running") is True:
                return snapshot
            lock_options = self._attendance_remote_lock_options()
            lease = _AttendanceRemoteWorkLease.acquire(
                self._config_dir, **lock_options
            )
            try:
                # 잠금을 얻은 뒤 다시 읽어 기다리는 동안 바뀐 로그인 준비도 반영한다.
                self._discard_broken_gws_config_client(self._gws_config_dir())
                base, config_dir, _bundled, selection = self._oauth_context()
                if not selection.ready:
                    if selection.error_code == "OAUTH_CLIENT_CONFLICT":
                        raise ScreenSafeError(
                            "기존 Google 로그인 설정과 Teacher Manager의 로그인 설정이 서로 달라요. "
                            "로그인 설정을 확인해 주세요."
                        )
                    if selection.error_code == "OAUTH_CLIENT_MISSING":
                        raise ScreenSafeError(
                            "이 확인용 Teacher Manager에는 Google 로그인 준비 파일이 없어요."
                        )
                    raise ScreenSafeError(
                        "Google 로그인 준비 파일을 안전하게 읽지 못했어요."
                    )
                child_env = gws_env.login_environ(
                    base,
                    selection,
                    gws_config_dir=config_dir,
                )
                started = self._login.start(
                    engine.login_command(gws),
                    popen=self._deps.popen_factory,
                    env=child_env,
                    auth_url_opener=self._open_external_url,
                    on_complete=lease.release,
                )
                if not started:
                    lease.release()
                return self._login.snapshot()
            except Exception:
                lease.release()
                raise

    def _discard_broken_gws_config_client(self, config_dir) -> None:
        """gws가 로그인 실패로 남긴 반쪽짜리 client_secret.json만 치운다.
        올바른 기존 로그인 준비 파일과, gws auth login이 동봉 client를
        받아 적은 파일(빈 project_id)은 절대 건드리지 않는다."""
        path = Path(config_dir) / gws_env.UPSTREAM_CLIENT_FILE_NAME
        try:
            if not path.is_file() or gws_env.is_valid_desktop_client_file(path):
                return
            if gws_env.is_gws_login_echo_of_client(path, self._bundled_client_path()):
                return
            path.unlink()
        except OSError:
            pass

    def _bundled_client_path(self):
        """Release 동봉 OAuth client 파일 위치. 없으면 None."""
        if self._deps.bundled_oauth_client_path is False:
            return None
        if self._deps.bundled_oauth_client_path is not None:
            return Path(self._deps.bundled_oauth_client_path)
        candidate = bundle_paths.bundle_root() / "assets" / gws_env.CLIENT_FILE_NAME
        return candidate if candidate.is_file() else None

    def _oauth_context(self):
        """화면 상태와 로그인 시작이 똑같은 OAuth 준비 판정을 함께 쓴다."""
        base = self._gws_base_environ()
        config_dir = self._gws_config_dir(base)
        bundled = self._bundled_client_path()
        selection = gws_env.select_desktop_oauth_client(base, config_dir, bundled)
        return base, config_dir, bundled, selection

    @guarded
    def gws_login_status(self):
        snapshot = self._login.snapshot()
        if snapshot.get("ok") is True:
            run, gws = self._resolve_gws_or_fail()
            auth = engine.gws_auth_status(run, gws)
            if auth.get("logged_in") and auth.get("account_allowed"):
                engine.record_gws_scope_grant(self._config_dir, auth.get("user", ""))
        return engine.annotate_login_snapshot(snapshot)

    @guarded
    def gws_login_cancel(self):
        return {"cancelled": self._login.cancel()}

    @guarded
    def gws_repair_oauth_client(self):
        """사용자가 정리를 누르면 gws가 남긴 깨진 준비 파일만 치운다.
        올바른 기존 준비 파일은 건드리지 않는다. 화면은 뒤이어 다시 점검한다."""
        self._discard_broken_gws_config_client(self._gws_config_dir())
        return {"cleared": True}

    def _resolve_gws_or_fail(self):
        self._require_safe_gws_account_storage()
        run = self._run()
        gws = engine.resolve_gws(run)
        if not gws:
            raise ScreenSafeError("Google 연결 도구가 아직 없어요. 설정에서 준비해 주세요.")
        return run, gws

    def _resolve_goedu_gws_or_fail(self):
        run, gws = self._resolve_gws_or_fail()
        engine.require_goedu_gws_session(run, gws)
        return run, gws

    def _resolve_attendance_goedu_gws_context_or_fail(self):
        """출결 자료는 처음 준비한 학교 계정으로만 읽거나 바꾼다."""

        self._require_safe_gws_account_storage()
        run = self._attendance_remote_run()
        gws = engine.resolve_gws(run)
        if not gws:
            raise ScreenSafeError("Google 연결 도구가 아직 없어요. 설정에서 준비해 주세요.")
        current = engine.require_goedu_gws_session(run, gws)
        saved = engine._read_setup_status(self._config_dir)
        owner = str(saved.get("account", "") or "").strip()
        if owner and owner.casefold() != current.casefold():
            raise ScreenSafeError(engine.ATTENDANCE_ACCOUNT_MESSAGE)
        return run, gws, current

    def _resolve_attendance_goedu_gws_or_fail(self):
        run, gws, _account = self._resolve_attendance_goedu_gws_context_or_fail()
        return run, gws

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
            run, gws = self._resolve_gws_or_fail()
            return self._success(*engine.gws_logout(run, gws))

    @guarded
    def ensure_calendar_named(self, name):
        name = str(name or "").strip()
        if not name:
            raise ValueError("캘린더 이름을 적어 주세요")
        run, gws = self._resolve_gws_or_fail()
        account = engine.gws_auth_status(run, gws).get("user", "")
        made_id = engine.ensure_calendar_verified(
            run, gws, account, name, **self._network_recovery_options()
        )
        if not made_id:
            raise ScreenSafeError("캘린더를 만들지 못했어요. 이름을 확인하고 잠시 뒤 다시 시도해 주세요.")
        return {"id": made_id, "name": name}

    @guarded
    def ensure_tasklist_named(self, name):
        name = str(name or "").strip()
        if not name:
            raise ValueError("할일 목록 이름을 적어 주세요")
        run, gws = self._resolve_gws_or_fail()
        account = engine.gws_auth_status(run, gws).get("user", "")
        made_id = engine.ensure_tasklist_verified(
            run, gws, account, name, **self._network_recovery_options()
        )
        if not made_id:
            raise ScreenSafeError("할 일 목록을 만들지 못했어요. 이름을 확인하고 잠시 뒤 다시 시도해 주세요.")
        return {"id": made_id, "name": name}

    @guarded
    def apply_all(self, profile, grid, bridge_updates):
        results = engine.apply_all(
            self._config_dir, dict(profile), list(grid), dict(bridge_updates), deps=self._deps.apply_deps
        )
        return [
            {"key": r.key, "label": r.label, "status": r.status, "detail": r.detail} for r in results
        ]

    @guarded
    def save_profile_grid(self, profile, grid, require_links=True):
        engine.write_profile_values(self._config_dir, dict(profile))
        engine.write_timetable_grid(self._config_dir, list(grid))
        parsed, detail = engine.run_parser(self._config_dir, require_links=bool(require_links))
        return {"parsed": parsed, "detail": detail}

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
        )

    @guarded
    def restart_helper(self):
        return engine.restart_helper_verified(
            stop=self._deps.helper_stop,
            start=self._deps.helper_restart,
            exists=self._deps.helper_window_exists,
            app_version=version.APP_VERSION,
        )

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
                str(value.get("detail") or "")
                if isinstance(value, dict)
                else ""
            )
            if disposition == "retry":
                raise recovery.RetryableOperationError(
                    code,
                    detail or "현재 상태를 다시 확인하고 있어요.",
                )
            if return_stop_result:
                return value
            stop_now(code, detail)

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

    def _attendance_operation(
        self,
        operation: str,
        title: str,
        action,
        *,
        retry_states=frozenset({"failed"}),
    ):
        """Run one public attendance action over the shared three cycles."""

        def once():
            value = action()
            state = (
                value.get("state", "")
                if isinstance(value, dict)
                else getattr(value, "state", "")
            )
            if state in retry_states:
                detail = (
                    value.get("detail", "")
                    if isinstance(value, dict)
                    else getattr(value, "detail", "")
                )
                raise recovery.RetryableOperationError(
                    "ATTENDANCE_OPERATION",
                    str(detail or "Google 출결 자료를 다시 확인하고 있어요."),
                )
            return value

        return recovery.run_operation(
            operation,
            title,
            once,
            delays=recovery.NETWORK_DELAYS,
            change_status="기존 출결 자료와 현재 연결은 그대로입니다.",
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
    def open_logs(self):
        logs = paths.settings_path(self._config_dir).parent / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        opener = self._deps.dir_opener or os.startfile  # noqa: S606 - Windows 전용 기본
        opener(str(logs))
        return True

    @guarded
    def open_url(self, url):
        return self._open_external_url(url)

    @guarded
    def copy_support_report(self, issue):
        text = recovery.support_report_text(dict(issue or {}))
        self._support_clipboard_writer(text)
        return {"copied": True}

    @guarded
    def open_support_email(self, issue):
        text = recovery.support_report_text(dict(issue or {}))
        return external_url.open_fixed_support_email(
            text, opener=self._support_mail_opener
        )

    @guarded
    def open_current_attendance(self):
        """화면에 남은 주소 대신 누른 순간의 검증된 정본 출석부만 연다."""

        from attendance_install_record import (
            AttendanceInstallRecordError,
            read_verified_canonical_record,
        )

        record_path = paths.attendance_install_record_path(self._config_dir)
        try:
            record = read_verified_canonical_record(record_path)
        except (OSError, AttendanceInstallRecordError) as error:
            raise ScreenSafeError(
                "현재 출석부 연결을 먼저 바로잡아 주세요."
            ) from error
        return self._open_external_url(record["spreadsheet_url"])
