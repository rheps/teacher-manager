# skills/teacher-task-manager/scripts/dashboard/central_chat.py
"""중앙 Google Chat 발송소 클라이언트 — 시트의 설정 값을 읽어 서버에 묻는다.

서버 코드는 바꾸지 않는다. Code.gs의 callCentralChatSender_와 같은 규약으로
sheetId + sheetSecret을 보낸다.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from brity_bridge import component_lock, gws_env, paths, process_win, tool_runtime

SETTINGS_RANGE = "설정!A1:D200"

# 새 설정 줄을 붙일 때 C열에 함께 적을 설명이다.
# install_attendance_automation.build_config_rows의 같은 이름 설명과 맞춰 둔다.
SETTINGS_DESCRIPTIONS = {
    "GEMINI_API_KEY": "AI 출결 입력이 쓰는 Gemini API 키입니다. 티처 매니저 연결 화면에 넣은 값이 자동으로 들어옵니다.",
}
TIMEOUT_SECONDS = 6
NOT_PREPARED_MESSAGE = "출결 준비가 아직 안 됐어요. 먼저 출결 준비 시작하기를 눌러 주세요."
CONFIG_BROKEN_MESSAGE = "출결 시트의 설정 값을 읽지 못했어요. 시트가 열리는지 확인해 주세요."
SERVER_ERROR_MESSAGE = "발송 서버와 연결하지 못했어요. 인터넷 연결을 확인해 주세요."
# 서버가 답을 준 경우는 인터넷 탓이 아니다. 답에 적힌 뜻을 그대로 옮긴다 —
# "인터넷을 확인하세요"라고만 하면 몇 번을 다시 눌러도 달라질 게 없다(2026-07-30 확인).
SHEET_MOVED_MESSAGE = (
    "Google Chat 발송은 새 정식 출석부로 이미 옮겼어요. "
    "Teacher Manager에서 현재 출석부를 열어 보내 주세요."
)
SHEET_AUTH_REQUIRED_MESSAGE = (
    "이 출석부는 아직 발송 서버에 등록되지 않았어요. 출결 준비를 먼저 마쳐 주세요."
)
SPACE_BLOCKED_MESSAGE = (
    "이 학교 계정으로는 프로그램이 방을 만들 수 없어요. Google Chat에서 직접 만들어 주세요."
)
SPACE_NAME_TAKEN_MESSAGE = "같은 이름의 방이 이미 있어요. 이름을 조금 바꿔서 다시 만들어 주세요."
SPACE_NAME_EMPTY_MESSAGE = "방 이름을 적어 주세요."
# 403(SPACE_CREATE_FORBIDDEN)·409(SPACE_NAME_TAKEN)가 아닌 나머지 실패는 전부 이 코드로 온다
# (services/central-chat-sender의 spaceCreateError 기본값).
SPACE_CREATE_FAILED_MESSAGE = "방을 만들지 못했어요. 잠시 뒤 다시 눌러 주세요."
SPACE_CREATE_STALE_MESSAGE = "학급 단톡방 선택이 다른 창에서 바뀌었어요. 현재 상태를 다시 확인해 주세요."
CLASS_SPACE_SELECTION_CHANGED_CODE = "CHAT_SPACE_SELECTION_CHANGED"
GOEDU_ACCOUNT_REQUIRED_MESSAGE = (
    "이 계정으로는 진행할 수 없어요. 교육디지털원패스 및 경기도교육청 "
    "클라우드 지원시스템에서 준비한 @goedu.kr 계정으로 다시 로그인해 주세요."
)
SERVER_ANSWER_MESSAGES = {
    "SHEET_MOVED": SHEET_MOVED_MESSAGE,
    "SHEET_AUTH_REQUIRED": SHEET_AUTH_REQUIRED_MESSAGE,
    "SPACE_CREATE_FORBIDDEN": SPACE_BLOCKED_MESSAGE,
    "SPACE_NAME_TAKEN": SPACE_NAME_TAKEN_MESSAGE,
    "SPACE_DISPLAY_NAME_REQUIRED": SPACE_NAME_EMPTY_MESSAGE,
    "SPACE_CREATE_FAILED": SPACE_CREATE_FAILED_MESSAGE,
    "SPACE_CREATE_STALE": SPACE_CREATE_STALE_MESSAGE,
    "GOEDU_ACCOUNT_REQUIRED": GOEDU_ACCOUNT_REQUIRED_MESSAGE,
}
UNKNOWN_SERVER_ANSWER_MESSAGE = (
    "발송 서버가 요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요."
)
CHAT_STATUS_FAILURE_MESSAGE = (
    "학급 단톡방 상태를 확인하지 못했어요. 잠시 뒤 다시 확인해 주세요."
)
CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE = (
    "Google Chat 연결이 중간에 멈춰 원래 대상 출석부 설정을 안전하게 확인할 수 없어요. "
    "다시 누르지 말고 출결 문제 해결에서 연결 상태를 확인해 주세요."
)
_SAFE_CENTRAL_MESSAGES = {
    NOT_PREPARED_MESSAGE,
    CONFIG_BROKEN_MESSAGE,
    SERVER_ERROR_MESSAGE,
    UNKNOWN_SERVER_ANSWER_MESSAGE,
    CHAT_STATUS_FAILURE_MESSAGE,
    CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE,
    *SERVER_ANSWER_MESSAGES.values(),
}

_KEY_MAP = {
    "CENTRAL_CHAT_SENDER_URL": "url",
    "CENTRAL_CHAT_SHEET_ID": "sheet_id",
    "CENTRAL_CHAT_SHEET_SECRET": "sheet_secret",
    "CLASS_CHAT_SPACE_ID": "class_space_id",
    "CLASS_CHAT_SPACE_NAME": "class_space_name",
    "TASK_LIST_ID": "task_list_id",
    "HOMEROOM_TASK_LIST_ID": "homeroom_task_list_id",
}
_ATTENDANCE_RECORD_NOT_SUPPLIED = object()
_CHAT_HANDOVER_RECOVERY_NAME = ".central-chat-handover-recovery.protected"


class CentralChatError(RuntimeError):
    pass


def _recovery_path(config_dir: Path) -> Path:
    return Path(config_dir) / _CHAT_HANDOVER_RECOVERY_NAME


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(data)
    source_blob = DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    protected = DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(protected)
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(data)
    source_blob = DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    unprotected = DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(unprotected)
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    try:
        return ctypes.string_at(unprotected.pbData, unprotected.cbData)
    finally:
        kernel32.LocalFree(unprotected.pbData)


def _write_handover_recovery(
    config_dir: Path,
    source_spreadsheet_id: str,
    target_spreadsheet_id: str,
    target_previous_settings: dict[str, str],
) -> None:
    path = _recovery_path(config_dir)
    if path.exists():
        saved_source, saved_target, _previous = _read_handover_recovery_payload(
            config_dir
        )
        if (
            saved_source != source_spreadsheet_id
            or saved_target != target_spreadsheet_id
        ):
            raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
        return
    payload = json.dumps({
        "source_spreadsheet_id": source_spreadsheet_id,
        "target_spreadsheet_id": target_spreadsheet_id,
        "target_previous_settings": target_previous_settings,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    protected = base64.b64encode(_dpapi_protect(payload)).decode("ascii")
    component_lock.atomic_write_text_unique(path, protected + "\n")


def _read_handover_recovery_payload(
    config_dir: Path,
) -> tuple[str, str, dict[str, str]]:
    try:
        protected = base64.b64decode(
            _recovery_path(config_dir).read_text(encoding="ascii").strip(), validate=True
        )
        payload = json.loads(_dpapi_unprotect(protected).decode("utf-8"))
        source = str(payload["source_spreadsheet_id"] or "").strip()
        target = str(payload["target_spreadsheet_id"] or "").strip()
        previous = dict(payload["target_previous_settings"])
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE) from error
    if (
        not source
        or not target
        or set(previous) != set(_CHAT_HANDOVER_KEYS)
        or not all(isinstance(previous[key], str) for key in _CHAT_HANDOVER_KEYS)
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    return source, target, previous


def _read_handover_recovery(
    config_dir: Path,
    source_spreadsheet_id: str,
    target_spreadsheet_id: str,
) -> dict[str, str]:
    source, target, previous = _read_handover_recovery_payload(config_dir)
    if (
        source != source_spreadsheet_id
        or target != target_spreadsheet_id
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    return previous


def _clear_handover_recovery(config_dir: Path) -> None:
    try:
        _recovery_path(config_dir).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


class ClassSpaceSelectionChangedError(CentralChatError):
    """다른 창의 정상 선택을 발견해 현재 요청이 더는 쓸 수 없을 때만 쓴다."""

    code = CLASS_SPACE_SELECTION_CHANGED_CODE

    def __init__(self):
        super().__init__(SPACE_CREATE_STALE_MESSAGE)


def _validated_spaces(response) -> list[dict]:
    """중앙 발송소의 방 목록을 빈 성공으로 오해하지 않고 한 모양으로 읽는다."""

    if not isinstance(response, dict) or "spaces" not in response:
        raise CentralChatError(CHAT_STATUS_FAILURE_MESSAGE)
    spaces = response["spaces"]
    if not isinstance(spaces, list):
        raise CentralChatError(CHAT_STATUS_FAILURE_MESSAGE)
    normalized = []
    for space in spaces:
        if not isinstance(space, dict):
            raise CentralChatError(CHAT_STATUS_FAILURE_MESSAGE)
        name = space.get("name")
        if not isinstance(name, str) or re.fullmatch(r"spaces/[A-Za-z0-9_-]+", name) is None:
            raise CentralChatError(CHAT_STATUS_FAILURE_MESSAGE)
        normalized.append({
            "name": name,
            "displayName": str(space.get("displayName", "") or ""),
        })
    return normalized


def _resolved_gws_executable(gws_executable: str | None = None) -> str:
    executable = str(gws_executable or tool_runtime.resolve_gws_executable())
    if not executable or not Path(executable).is_absolute():
        raise CentralChatError("Google 연결 도구를 찾지 못했어요. 설정에서 다시 점검해 주세요.")
    return executable


def _default_run_command(args, cwd=None):
    # 앱과 같은 곳에 gws 열쇠를 두게 고정한다 — 이 명령에만 넘긴다.
    # 출결 탭이 3초 간격으로 부르는 경로 — 창 숨김이 빠지면 검은 창이 계속 뜬다.
    result = subprocess.run(list(args), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", cwd=cwd, shell=False,
                            env=gws_env.gws_environ(),
                            **process_win.hidden_process_kwargs())
    if result.returncode != 0:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    return result.stdout


def _command_output(run_command, args) -> str:
    """두 종류의 명령 실행 결과를 한 모양으로 바꾸고 실패를 놓치지 않는다.

    이 파일 자체 실행기는 출력 문자열을 돌려주지만, 대시보드 bridge 실행기는
    ``(종료번호, 출력)``을 돌려준다. 예전에는 뒤 모양의 실패도 문자열처럼 넘겨
    Sheet 변경이 실패했는데 성공으로 표시될 수 있었다.
    """
    result = run_command(args)
    if isinstance(result, tuple):
        if len(result) < 2:
            raise CentralChatError(CONFIG_BROKEN_MESSAGE)
        code, output = result[0], result[1]
        try:
            succeeded = int(code) == 0
        except (TypeError, ValueError):
            succeeded = False
        if not succeeded:
            raise CentralChatError(CONFIG_BROKEN_MESSAGE)
        return str(output or "")
    return str(result or "")


def _load_record(config_dir: Path) -> dict:
    path = paths.attendance_install_record_path(Path(config_dir))
    if not path.exists():
        raise CentralChatError(NOT_PREPARED_MESSAGE)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE) from error
    if not isinstance(record, dict) or not record.get("spreadsheet_id"):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    return record


def _chosen_record(config_dir: Path, attendance_record) -> dict:
    """호출 시작 때 고정한 기록을 쓰고, 없으면 그때만 활성 파일을 읽는다."""

    if attendance_record is _ATTENDANCE_RECORD_NOT_SUPPLIED:
        return _load_record(config_dir)
    if attendance_record is None:
        raise CentralChatError(NOT_PREPARED_MESSAGE)
    if not isinstance(attendance_record, dict) or not attendance_record.get(
        "spreadsheet_id"
    ):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    return dict(attendance_record)


def _read_settings_rows(
    spreadsheet_id: str,
    run_command,
    gws_executable: str | None = None,
) -> list:
    gws = _resolved_gws_executable(gws_executable)
    output = _command_output(run_command, [
        gws, "sheets", "spreadsheets", "values", "get",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id, "range": SETTINGS_RANGE},
                               ensure_ascii=False),
        "--format", "json",
    ])
    try:
        # gws가 JSON 앞뒤로 안내 줄을 덧붙여도 첫 JSON만 읽는다(engine 경로와 동일한 견고성).
        response = process_win.parse_first_json(output) or {}
    except ValueError as error:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE) from error
    if not isinstance(response, dict):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    return response.get("values") or response.get("value") or []


def _spreadsheet_in_use(config_dir: Path, record_spreadsheet_id: str = "") -> str:
    """평상시 기능은 설치 기록에서 받은 현재 출결 번호만 사용한다."""

    return str(record_spreadsheet_id or "").strip()


def read_central_config(
    config_dir: Path,
    run_command=_default_run_command,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
) -> dict:
    record = _chosen_record(config_dir, attendance_record)
    spreadsheet_id = (
        _spreadsheet_in_use(config_dir, str(record["spreadsheet_id"]))
        or str(record["spreadsheet_id"])
    )
    gws = _resolved_gws_executable(gws_executable)
    rows = _read_settings_rows(spreadsheet_id, run_command, gws)
    config = {"spreadsheet_id": spreadsheet_id}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        mapped = _KEY_MAP.get(str(row[0]).strip())
        if mapped:
            config[mapped] = str(row[1]).strip()
    for required in ("url", "sheet_id", "sheet_secret"):
        if not config.get(required):
            raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    return config


def _post(url: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + path, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        # 서버가 답을 준 것이다 — 인터넷은 멀쩡하다.
        code = _server_error_code(error)
        failure = CentralChatError(_server_answer_message(code, error))
        # 번역 문구는 나라마다·표현마다 바뀔 수 있다 — 갈래를 가르는 쪽(create_class_space 등)은
        # 이 원 코드로 판단해야지, 사람에게 보여줄 문장과 완전일치를 비교하면 안 된다.
        failure.server_code = code
        raise failure from error
    except Exception as error:  # noqa: BLE001 - 정말 못 닿은 경우만 인터넷 탓을 한다
        raise CentralChatError(SERVER_ERROR_MESSAGE) from error


def _server_error_code(error) -> str:
    """서버가 돌려준 오류 코드 원문을 읽는다. 본문을 못 읽으면 빈 문자열."""
    try:
        body = json.loads(error.read().decode("utf-8", "replace") or "{}")
    except Exception:  # noqa: BLE001 - 본문을 못 읽어도 상태 번호는 알려준다
        return ""
    if isinstance(body, dict):
        return str(body.get("error", "") or "")
    return ""


def _server_answer_message(code: str, error) -> str:
    """서버가 돌려준 오류 코드를 선생님이 읽을 한 문장으로 바꾼다."""
    known = SERVER_ANSWER_MESSAGES.get(code)
    if known:
        return known
    return UNKNOWN_SERVER_ANSWER_MESSAGE


def _safe_central_error_detail(error, fallback: str = UNKNOWN_SERVER_ANSWER_MESSAGE) -> str:
    code = str(getattr(error, "server_code", "") or "")
    if code in SERVER_ANSWER_MESSAGES:
        return SERVER_ANSWER_MESSAGES[code]
    detail = str(error or "")
    return detail if detail in _SAFE_CENTRAL_MESSAGES else fallback


UNIFIED_TASK_LIST_TITLE = "조종례시 담임학급 안내사항"


def sync_task_list(
    config_dir: Path,
    run_command=_default_run_command,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
) -> bool:
    """옛 '출결 미제출 확인' 목록을 쓰는 시트를 조종례 목록으로 통합한다. 목록 자체는 지우지 않는다."""
    gws = _resolved_gws_executable(gws_executable)
    config = read_central_config(
        config_dir,
        run_command,
        gws_executable=gws,
        attendance_record=attendance_record,
    )
    homeroom_id = config.get("homeroom_task_list_id", "")
    if not homeroom_id or config.get("task_list_id", "") == homeroom_id:
        return False
    rows = _read_settings_rows(config["spreadsheet_id"], run_command, gws)
    _update_settings_values(
        config["spreadsheet_id"],
        rows,
        [
            ("TASK_LIST_ID", homeroom_id),
            ("TASK_LIST_TITLE", UNIFIED_TASK_LIST_TITLE),
        ],
        run_command,
        gws,
    )
    return True


def list_spaces(
    config_dir: Path,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
) -> list:
    gws = _resolved_gws_executable(gws_executable)
    config = read_central_config(
        config_dir,
        run_command,
        gws_executable=gws,
        attendance_record=attendance_record,
    )
    response = http_post(config["url"], "/v1/spaces", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
    })
    return _validated_spaces(response)


def _update_settings_value(
    spreadsheet_id: str,
    rows: list,
    key: str,
    value: str,
    run_command,
    gws_executable: str | None = None,
) -> None:
    gws = _resolved_gws_executable(gws_executable)
    for index, row in enumerate(rows):
        if isinstance(row, list) and row and str(row[0]).strip() == key:
            row_number = index + 1  # A1 표기 — values get 결과의 줄 번호 그대로
            _command_output(run_command, [
                gws, "sheets", "spreadsheets", "values", "update",
                "--params", json.dumps({
                    "spreadsheetId": spreadsheet_id,
                    "range": f"설정!B{row_number}",
                    "valueInputOption": "RAW",
                }, ensure_ascii=False),
                "--json", json.dumps({"majorDimension": "ROWS", "values": [[value]]},
                                     ensure_ascii=False),
                "--format", "json",
            ])
            return
    raise CentralChatError(CONFIG_BROKEN_MESSAGE)


def _update_settings_values(
    spreadsheet_id: str,
    rows: list,
    values: list[tuple[str, str]],
    run_command,
    gws_executable: str | None = None,
) -> None:
    """서로 짝인 설정값을 Google Sheet에 한 번에 저장한다.

    방 ID와 방 이름, Tasks 목록 ID와 이름처럼 둘이 함께 바뀌어야 하는 값은
    명령을 두 번 보내면 첫 번째만 저장된 채 멈출 수 있다. Google의 묶음 저장을
    사용해 둘 다 저장되거나 둘 다 그대로 남게 한다.
    """
    row_numbers: dict[str, int] = {}
    wanted = {str(key) for key, _value in values}
    for index, row in enumerate(rows):
        if not isinstance(row, list) or not row:
            continue
        key = str(row[0]).strip()
        if key in wanted:
            row_numbers[key] = index + 1
    if any(key not in row_numbers for key, _value in values):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)

    data = [
        {
            "range": f"설정!B{row_numbers[key]}",
            "majorDimension": "ROWS",
            "values": [[value]],
        }
        for key, value in values
    ]
    gws = _resolved_gws_executable(gws_executable)
    _command_output(run_command, [
        gws, "sheets", "spreadsheets", "values", "batchUpdate",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id}, ensure_ascii=False),
        "--json", json.dumps({"valueInputOption": "RAW", "data": data}, ensure_ascii=False),
        "--format", "json",
    ])


_CHAT_HANDOVER_KEYS = (
    "CENTRAL_CHAT_SENDER_URL",
    "CENTRAL_CHAT_SHEET_ID",
    "CENTRAL_CHAT_SHEET_SECRET",
    "CLASS_CHAT_SPACE_ID",
    "CLASS_CHAT_SPACE_NAME",
)


def _settings_values(rows: list) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        key = str(row[0]).strip()
        if key in _CHAT_HANDOVER_KEYS:
            if key in values:
                raise CentralChatError(CONFIG_BROKEN_MESSAGE)
            values[key] = str(row[1] or "").strip()
    return values


def _target_central_sheet_id(
    source_sheet_id: str,
    source_spreadsheet_id: str,
    target_spreadsheet_id: str,
) -> str:
    source = str(source_sheet_id or "").strip()
    source_spreadsheet = str(source_spreadsheet_id or "").strip()
    target = str(target_spreadsheet_id or "").strip()
    if (
        not target
        or not source_spreadsheet
        or re.fullmatch(r"[A-Za-z0-9_-]{3,200}", target) is None
        or re.fullmatch(r"[A-Za-z0-9_-]{3,200}", source_spreadsheet) is None
    ):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    actual_source, separator, suffix = source.partition(":")
    if (
        not separator
        or actual_source != source_spreadsheet
        or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", suffix) is None
    ):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    return f"{target}:{suffix}"


def _central_status(url: str, sheet_id: str, secret: str, http_post) -> dict:
    try:
        reply = http_post(
            url,
            "/v1/status",
            {"sheetId": sheet_id, "sheetSecret": secret},
        )
    except CentralChatError:
        raise
    except Exception as error:  # noqa: BLE001 - 외부 원문은 화면으로 보내지 않는다.
        raise CentralChatError(SERVER_ERROR_MESSAGE) from error
    if not isinstance(reply, dict):
        raise CentralChatError(UNKNOWN_SERVER_ANSWER_MESSAGE)
    return reply


def _connection_is_confirmed(status: dict, account: str) -> bool:
    return (
        status.get("registered") is True
        and status.get("connected") is True
        and str(status.get("account", "") or "").strip().casefold() == account
    )


def _confirm_or_return_connection_to_source(
    url: str,
    source_sheet_id: str,
    target_sheet_id: str,
    secret: str,
    account: str,
    http_post,
) -> bool:
    try:
        if _connection_is_confirmed(
            _central_status(url, source_sheet_id, secret, http_post), account
        ):
            return True
    except Exception:  # noqa: BLE001 - 원본 상태를 못 읽으면 정식 되돌리기를 시도한다.
        pass
    try:
        http_post(
            url,
            "/v1/sheet/move",
            {
                "sheetId": target_sheet_id,
                "sheetSecret": secret,
                "newSheetId": source_sheet_id,
            },
        )
    except Exception:  # noqa: BLE001 - 요청 응답보다 원본 상태 재확인이 우선이다.
        pass
    try:
        return _connection_is_confirmed(
            _central_status(url, source_sheet_id, secret, http_post), account
        )
    except Exception:  # noqa: BLE001 - 확인하지 못한 연결은 더 바꾸지 않는다.
        return False


def move_sheet_connection(
    config_dir: Path,
    target_spreadsheet_id: str,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
) -> dict:
    """현재 Sheet의 실제 Chat 연결과 단체방 선택을 새 연결 Sheet로 옮긴다."""

    source = read_central_config(
        config_dir,
        run_command,
        gws_executable=gws_executable,
        attendance_record=attendance_record,
    )
    source_spreadsheet_id = str(source["spreadsheet_id"])
    target_spreadsheet_id = str(target_spreadsheet_id or "").strip()
    if target_spreadsheet_id == source_spreadsheet_id:
        return {"outcome": "same", "moved": False}
    target_sheet_id = _target_central_sheet_id(
        str(source["sheet_id"]), source_spreadsheet_id, target_spreadsheet_id
    )
    source_status = _central_status(
        source["url"], source["sheet_id"], source["sheet_secret"], http_post
    )
    if not (
        source_status.get("registered") is True
        and source_status.get("connected") is True
    ):
        if source_status.get("moved") is True:
            gws = _resolved_gws_executable(gws_executable)
            target_rows = _read_settings_rows(
                target_spreadsheet_id, run_command, gws
            )
            target_current = _settings_values(target_rows)
            expected = {
                "CENTRAL_CHAT_SENDER_URL": source["url"],
                "CENTRAL_CHAT_SHEET_ID": target_sheet_id,
                "CENTRAL_CHAT_SHEET_SECRET": source["sheet_secret"],
                "CLASS_CHAT_SPACE_ID": str(source.get("class_space_id", "") or ""),
                "CLASS_CHAT_SPACE_NAME": str(source.get("class_space_name", "") or ""),
            }
            if any(
                target_current.get(key, "") != expected[key]
                for key in _CHAT_HANDOVER_KEYS
            ):
                raise CentralChatError(CONFIG_BROKEN_MESSAGE)
            target_before = _read_handover_recovery(
                config_dir, source_spreadsheet_id, target_spreadsheet_id
            )
            target_status = _central_status(
                source["url"], target_sheet_id, source["sheet_secret"], http_post
            )
            account = str(target_status.get("account", "") or "").strip().casefold()
            if not (
                target_status.get("registered") is True
                and target_status.get("connected") is True
                and account.endswith("@goedu.kr")
            ):
                raise CentralChatError(UNKNOWN_SERVER_ANSWER_MESSAGE)
            return {
                "outcome": "moved",
                "moved": True,
                "source_spreadsheet_id": source_spreadsheet_id,
                "target_spreadsheet_id": target_spreadsheet_id,
                "source_sheet_id": source["sheet_id"],
                "target_sheet_id": target_sheet_id,
                "sheet_secret": source["sheet_secret"],
                "url": source["url"],
                "account": account,
                "target_previous_settings": target_before,
                "handover_settings": expected,
            }
        return {"outcome": "not_registered", "moved": False}
    account = str(source_status.get("account", "") or "").strip().casefold()
    if not account.endswith("@goedu.kr"):
        raise CentralChatError(GOEDU_ACCOUNT_REQUIRED_MESSAGE)

    gws = _resolved_gws_executable(gws_executable)
    target_rows = _read_settings_rows(target_spreadsheet_id, run_command, gws)
    target_before = _settings_values(target_rows)
    target_url = target_before.get("CENTRAL_CHAT_SENDER_URL", "")
    target_id = target_before.get("CENTRAL_CHAT_SHEET_ID", "")
    target_secret = target_before.get("CENTRAL_CHAT_SHEET_SECRET", "")
    if target_url and target_url != source["url"]:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    if target_url and target_id and target_secret:
        target_status = _central_status(
            target_url, target_id, target_secret, http_post
        )
        if target_status.get("registered") or target_status.get("connected"):
            raise CentralChatError(
                "새로 고른 출석부에 다른 Google Chat 연결이 있어 자동으로 바꾸지 않았어요."
            )
    elif target_url or target_id or target_secret:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)

    handover_values = [
        ("CENTRAL_CHAT_SENDER_URL", source["url"]),
        ("CENTRAL_CHAT_SHEET_ID", target_sheet_id),
        ("CENTRAL_CHAT_SHEET_SECRET", source["sheet_secret"]),
        ("CLASS_CHAT_SPACE_ID", str(source.get("class_space_id", "") or "")),
        ("CLASS_CHAT_SPACE_NAME", str(source.get("class_space_name", "") or "")),
    ]
    _write_handover_recovery(
        config_dir, source_spreadsheet_id, target_spreadsheet_id, target_before
    )
    target_previous = _read_handover_recovery(
        config_dir, source_spreadsheet_id, target_spreadsheet_id
    )
    expected_handover = dict(handover_values)
    if not (
        all(
            target_before.get(key, "") == target_previous[key]
            for key in _CHAT_HANDOVER_KEYS
        )
        or all(
            target_before.get(key, "") == expected_handover[key]
            for key in _CHAT_HANDOVER_KEYS
        )
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    try:
        _update_settings_values(
            target_spreadsheet_id,
            target_rows,
            handover_values,
            run_command,
            gws,
        )
        http_post(
            source["url"],
            "/v1/sheet/move",
            {
                "sheetId": source["sheet_id"],
                "sheetSecret": source["sheet_secret"],
                "newSheetId": target_sheet_id,
            },
        )
    except Exception as error:  # noqa: BLE001 - 아래 재확인 전에는 성공·실패를 추측하지 않는다.
        move_error = error
    else:
        move_error = None
    try:
        target_status = _central_status(
            source["url"], target_sheet_id, source["sheet_secret"], http_post
        )
    except Exception as error:  # noqa: BLE001 - 확인 실패도 대상 설정을 되돌린다.
        target_status = {}
        status_error = error
    else:
        status_error = None
    if not (
        target_status.get("registered") is True
        and target_status.get("connected") is True
        and str(target_status.get("account", "") or "").strip().casefold() == account
    ):
        if not _confirm_or_return_connection_to_source(
            source["url"],
            source["sheet_id"],
            target_sheet_id,
            source["sheet_secret"],
            account,
            http_post,
        ):
            raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
        try:
            _update_settings_values(
                target_spreadsheet_id,
                target_rows,
                [(key, target_previous.get(key, "")) for key in _CHAT_HANDOVER_KEYS],
                run_command,
                gws,
            )
            restored = _settings_values(
                _read_settings_rows(target_spreadsheet_id, run_command, gws)
            )
            if any(
                restored.get(key, "") != target_previous.get(key, "")
                for key in _CHAT_HANDOVER_KEYS
            ):
                raise CentralChatError(CONFIG_BROKEN_MESSAGE)
        except Exception as error:  # noqa: BLE001 - 복구가 불명확하면 더 바꾸지 않는다.
            raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE) from error
        _clear_handover_recovery(config_dir)
        if status_error is not None:
            if isinstance(status_error, CentralChatError):
                raise status_error
            raise CentralChatError(SERVER_ERROR_MESSAGE) from status_error
        if move_error is not None:
            if isinstance(move_error, CentralChatError):
                raise move_error
            raise CentralChatError(SERVER_ERROR_MESSAGE) from move_error
        raise CentralChatError(UNKNOWN_SERVER_ANSWER_MESSAGE)
    return {
        "outcome": "moved",
        "moved": True,
        "source_spreadsheet_id": source_spreadsheet_id,
        "target_spreadsheet_id": target_spreadsheet_id,
        "source_sheet_id": source["sheet_id"],
        "target_sheet_id": target_sheet_id,
        "sheet_secret": source["sheet_secret"],
        "url": source["url"],
        "account": account,
        "target_previous_settings": target_previous,
        "handover_settings": expected_handover,
    }


def rollback_sheet_connection(
    config_dir: Path,
    move_result: dict,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
) -> bool:
    """로컬 연결 교체 실패 때 Chat 연결과 대상 설정을 이전 상태로 되돌린다."""

    if not isinstance(move_result, dict) or move_result.get("moved") is not True:
        return True
    try:
        source_sheet_id = str(move_result["source_sheet_id"]).strip()
        target_sheet_id = str(move_result["target_sheet_id"]).strip()
        target_spreadsheet_id = str(move_result["target_spreadsheet_id"]).strip()
        secret = str(move_result["sheet_secret"]).strip()
        url = str(move_result["url"]).strip()
        account = str(move_result["account"]).strip().casefold()
        previous = dict(move_result["target_previous_settings"])
        handover = dict(move_result["handover_settings"])
    except (KeyError, TypeError, ValueError) as error:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE) from error
    if (
        not source_sheet_id
        or not target_sheet_id
        or not target_spreadsheet_id
        or not secret
        or not url.startswith("https://")
        or not account.endswith("@goedu.kr")
        or set(previous) != set(_CHAT_HANDOVER_KEYS)
        or not all(isinstance(previous[key], str) for key in _CHAT_HANDOVER_KEYS)
        or set(handover) != set(_CHAT_HANDOVER_KEYS)
        or not all(isinstance(handover[key], str) for key in _CHAT_HANDOVER_KEYS)
        or handover["CENTRAL_CHAT_SENDER_URL"] != url
        or handover["CENTRAL_CHAT_SHEET_ID"] != target_sheet_id
        or handover["CENTRAL_CHAT_SHEET_SECRET"] != secret
    ):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)

    try:
        http_post(
            url,
            "/v1/sheet/move",
            {
                "sheetId": target_sheet_id,
                "sheetSecret": secret,
                "newSheetId": source_sheet_id,
            },
        )
    except Exception as error:  # noqa: BLE001 - 요청 결과와 별개로 원본 상태를 확인한다.
        move_error = error
    else:
        move_error = None
    try:
        source_status = _central_status(url, source_sheet_id, secret, http_post)
    except Exception as error:  # noqa: BLE001 - 확인 불가 상태에서는 대상 설정을 보존한다.
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE) from error
    if not _connection_is_confirmed(source_status, account):
        if move_error is not None:
            raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE) from move_error
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    gws = _resolved_gws_executable(gws_executable)
    target_rows = _read_settings_rows(target_spreadsheet_id, run_command, gws)
    target_current = _settings_values(target_rows)
    if (
        set(target_current) != set(_CHAT_HANDOVER_KEYS)
        or any(target_current[key] != handover[key] for key in _CHAT_HANDOVER_KEYS)
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    _update_settings_values(
        target_spreadsheet_id,
        target_rows,
        [(key, previous[key]) for key in _CHAT_HANDOVER_KEYS],
        run_command,
        gws,
    )
    verified_rows = _read_settings_rows(target_spreadsheet_id, run_command, gws)
    verified = _settings_values(verified_rows)
    if any(verified.get(key, "") != previous[key] for key in _CHAT_HANDOVER_KEYS):
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    _clear_handover_recovery(config_dir)
    return True


def complete_sheet_connection(config_dir: Path, move_result: dict) -> None:
    """컴퓨터 연결번호까지 바뀐 뒤, 중단 복구용 보호 기록을 지운다."""
    if not isinstance(move_result, dict) or move_result.get("moved") is not True:
        return
    try:
        _read_handover_recovery(
            config_dir,
            str(move_result["source_spreadsheet_id"]),
            str(move_result["target_spreadsheet_id"]),
        )
    except Exception:  # noqa: BLE001 - 남은 보호 기록은 다음 복구 판단에만 쓴다.
        return
    _clear_handover_recovery(config_dir)


def _upsert_settings_value(
    spreadsheet_id: str,
    rows: list,
    key: str,
    value: str,
    run_command,
    gws_executable: str | None = None,
) -> None:
    """설정 탭 값을 고치되, 그 이름 줄이 없으면 표 맨 아래에 새로 붙인다.

    이미 설치를 끝낸 시트에는 나중에 생긴 설정 이름이 아예 없다. 그럴 때 오류를 내면
    선생님이 시트를 열어 손으로 줄을 만들어야 하므로, 없으면 만들어 준다.
    같은 이름이 둘 이상이면 어느 줄이 진짜인지 알 수 없으므로 손대지 않는다.
    """
    matched = [
        index
        for index, row in enumerate(rows)
        if isinstance(row, list) and row and str(row[0]).strip() == key
    ]
    if len(matched) > 1:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    if matched:
        row_number = matched[0] + 1  # A1 표기 — values get 결과의 줄 번호 그대로
        params = {
            "spreadsheetId": spreadsheet_id,
            "range": f"설정!B{row_number}",
            "valueInputOption": "RAW",
        }
        values = [[value]]
    else:
        row_number = len(rows) + 1
        params = {
            "spreadsheetId": spreadsheet_id,
            "range": f"설정!A{row_number}:D{row_number}",
            "valueInputOption": "RAW",
        }
        values = [[key, value, SETTINGS_DESCRIPTIONS.get(key, ""), "자동 입력"]]
    gws = _resolved_gws_executable(gws_executable)
    _command_output(run_command, [
        gws, "sheets", "spreadsheets", "values", "update",
        "--params", json.dumps(params, ensure_ascii=False),
        "--json", json.dumps({"majorDimension": "ROWS", "values": values}, ensure_ascii=False),
        "--format", "json",
    ])


def _set_class_space_from_config(
    config: dict,
    space_name: str,
    display_name: str,
    run_command,
    http_post,
    gws_executable: str,
) -> dict:
    """이미 확인한 같은 출석부 설정으로 방 선택 저장을 끝낸다."""

    rows = _read_settings_rows(
        config["spreadsheet_id"], run_command, gws_executable
    )
    _update_settings_values(
        config["spreadsheet_id"],
        rows,
        [
            ("CLASS_CHAT_SPACE_ID", space_name),
            ("CLASS_CHAT_SPACE_NAME", display_name),
        ],
        run_command,
        gws_executable,
    )
    # 시트 저장이 실패했는데 서버만 선택된 상태가 되면 Apps Script 발송은 방을
    # 찾지 못한다. 그래서 선생님이 실제로 쓰는 시트를 먼저 완성한 뒤 서버에 알린다.
    http_post(config["url"], "/v1/class-space", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
        "spaceName": space_name, "displayName": display_name,
    })
    return {"space_name": space_name, "display_name": display_name}


def _server_class_space(config: dict, http_post) -> tuple[str, dict]:
    response = http_post(config["url"], "/v1/status", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
    })
    if not response.get("connected"):
        raise CentralChatError(CHAT_STATUS_FAILURE_MESSAGE)
    return str(response.get("classSpaceResource", "") or "").strip(), response


def _reconcile_class_space(
    config: dict,
    space_name: str,
    display_name: str,
    run_command,
    http_post,
    gws_executable: str,
    *,
    expected_current: str | None = None,
) -> dict:
    """Sheet와 서버 중 아직 비어 있는 한쪽만 채우고 두 값을 다시 확인한다."""

    expected = (
        str(config.get("class_space_id", "") or "").strip()
        if expected_current is None
        else str(expected_current or "").strip()
    )
    sheet_current = str(config.get("class_space_id", "") or "").strip()
    server_current, _status = _server_class_space(config, http_post)
    allowed = {expected, space_name}
    if sheet_current not in allowed or server_current not in allowed:
        raise ClassSpaceSelectionChangedError()

    if sheet_current != space_name:
        rows = _read_settings_rows(config["spreadsheet_id"], run_command, gws_executable)
        now = read_central_config(
            Path("."), run_command, gws_executable=gws_executable,
            attendance_record={"spreadsheet_id": config["spreadsheet_id"]},
        )
        now_space = str(now.get("class_space_id", "") or "").strip()
        if now_space == space_name:
            sheet_current = space_name
        elif now_space != expected:
            raise ClassSpaceSelectionChangedError()
        else:
            _update_settings_values(
                config["spreadsheet_id"], rows,
                [("CLASS_CHAT_SPACE_ID", space_name), ("CLASS_CHAT_SPACE_NAME", display_name)],
                run_command, gws_executable,
            )

    if server_current != space_name:
        http_post(config["url"], "/v1/class-space", {
            "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
            "spaceName": space_name, "displayName": display_name,
            "expectedCurrentSpace": expected,
        })

    final_rows = _read_settings_rows(config["spreadsheet_id"], run_command, gws_executable)
    final_sheet = ""
    for row in final_rows:
        if isinstance(row, list) and len(row) > 1 and str(row[0]).strip() == "CLASS_CHAT_SPACE_ID":
            final_sheet = str(row[1] or "").strip()
            break
    final_server, _status = _server_class_space(config, http_post)
    if final_sheet != space_name or final_server != space_name:
        raise CentralChatError("학급 단톡방 선택을 저장했는지 다시 확인하지 못했어요.")
    return {"space_name": space_name, "display_name": display_name}


def read_config_for_spreadsheet(
    spreadsheet_id: str, run_command=_default_run_command, *, gws_executable: str | None = None
) -> dict:
    return read_central_config(
        Path("."), run_command, gws_executable=gws_executable,
        attendance_record={"spreadsheet_id": str(spreadsheet_id)},
    )


def status_for_config(config: dict, http_post=_post) -> dict:
    return http_post(config["url"], "/v1/status", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
    })


def move_chat_connection(config: dict, new_sheet_id: str, http_post=_post) -> dict:
    return http_post(config["url"], "/v1/sheet/move", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
        "newSheetId": str(new_sheet_id),
    })


def prepare_handover_candidate(
    spreadsheet_id: str,
    original: dict,
    inherited: dict,
    run_command=_default_run_command,
    gws_executable: str | None = None,
) -> dict:
    """방금 만든 후보의 설치 직후 설정이 그대로일 때만 상속값을 한 번에 쓴다."""

    gws = _resolved_gws_executable(gws_executable)
    current = read_config_for_spreadsheet(
        spreadsheet_id, run_command, gws_executable=gws
    )
    keys = ("url", "sheet_id", "sheet_secret", "class_space_id", "class_space_name")
    if any(str(current.get(key, "") or "") != str(original.get(key, "") or "") for key in keys):
        raise CentralChatError("새 출석부의 Chat 설정이 달라 바꾸지 않았어요.")
    rows = _read_settings_rows(str(spreadsheet_id), run_command, gws)
    _update_settings_values(
        str(spreadsheet_id), rows,
        [
            ("CENTRAL_CHAT_SENDER_URL", str(inherited.get("url", "") or "")),
            ("CENTRAL_CHAT_SHEET_ID", str(inherited.get("sheet_id", "") or "")),
            ("CENTRAL_CHAT_SHEET_SECRET", str(inherited.get("sheet_secret", "") or "")),
            ("CLASS_CHAT_SPACE_ID", str(inherited.get("class_space_id", "") or "")),
            ("CLASS_CHAT_SPACE_NAME", str(inherited.get("class_space_name", "") or "")),
        ],
        run_command, gws,
    )
    saved = read_config_for_spreadsheet(
        spreadsheet_id, run_command, gws_executable=gws
    )
    if any(str(saved.get(key, "") or "") != str(inherited.get(key, "") or "") for key in keys):
        raise CentralChatError("새 출석부의 Chat 설정 저장을 다시 확인하지 못했어요.")
    return saved


def set_class_space(
    config_dir: Path,
    space_name: str,
    display_name: str,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
    expected_current: str | None = None,
) -> dict:
    """현재 목록과 양쪽 저장값을 다시 확인한 뒤 필요한 한쪽만 채운다."""
    gws = _resolved_gws_executable(gws_executable)
    config = read_central_config(
        config_dir,
        run_command,
        gws_executable=gws,
        attendance_record=attendance_record,
    )
    candidates = list_spaces(
        config_dir, run_command, http_post, gws_executable=gws,
        attendance_record=attendance_record,
    )
    chosen = next((row for row in candidates if row["name"] == str(space_name)), None)
    if chosen is None:
        raise CentralChatError("고른 학급 단톡방을 현재 목록에서 다시 확인하지 못했어요.")
    return _reconcile_class_space(
        config, str(space_name), chosen["displayName"], run_command, http_post, gws,
        expected_current=expected_current,
    )


def create_class_space(
    config_dir: Path,
    display_name: str,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
) -> dict:
    """학급 단톡방을 만들고 곧바로 그 방을 학급 단톡방으로 골라 둔다.

    만드는 것과 고르는 것을 한 번에 끝낸다 — 만든 뒤 목록에서 또 고르게 하면
    "만들었는데 왜 아직 안 골라졌나" 싶어진다.

    돌려주는 state는 셋이다.
      ok      — 만들고 고르기까지 끝났다
      blocked — 학교가 방 만들기를 막아 두었다. 화면은 손으로 만드는 순서를 보여준다
      failed  — 그 밖의 실패. detail에 이유가 있다
    학생 초대는 chat.memberships 권한이 없어 여기서 못 한다.
    """
    name = str(display_name or "").strip()
    if not name:
        return {"state": "failed", "space_name": "", "display_name": "",
                "detail": SPACE_NAME_EMPTY_MESSAGE}
    gws = _resolved_gws_executable(gws_executable)
    config = read_central_config(
        config_dir,
        run_command,
        gws_executable=gws,
        attendance_record=attendance_record,
    )
    # 만들기 전 목록 읽기가 실제로 성공해야 한다. 실패를 빈 목록으로 바꾸지 않는다.
    listed = http_post(config["url"], "/v1/spaces", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
    })
    _validated_spaces(listed)
    expected_current = str(config.get("class_space_id", "") or "").strip()
    try:
        response = http_post(config["url"], "/v1/spaces/create", {
            "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
            "displayName": name,
            "expectedCurrentSpace": expected_current,
        })
    except CentralChatError as error:
        detail = _safe_central_error_detail(error)
        # 번역 문구가 아니라 서버가 준 원 코드로 가른다 — 문구에 맥락이 덧붙어도 안 샌다.
        code = getattr(error, "server_code", "")
        state = (
            "blocked" if code == "SPACE_CREATE_FORBIDDEN"
            else "stale" if code == "SPACE_CREATE_STALE"
            else "failed"
        )
        return {"state": state, "space_name": "", "display_name": name, "detail": detail}
    response_state = str((response or {}).get("state", "") or "")
    if response_state in {"in-progress", "stale"}:
        return {"state": response_state, "space_name": "", "display_name": name,
                "detail": "방 만들기 상태를 다시 확인하고 있어요."}
    space = (response or {}).get("space") or {}
    space_name = str(space.get("name", "") or "").strip()
    if not space_name:
        return {"state": "failed", "space_name": "", "display_name": name,
                "detail": "방을 만들었는지 확인하지 못했어요. 목록을 다시 불러와 주세요."}
    shown = str(space.get("displayName", "") or "").strip() or name
    try:
        # 방을 만들기 전에 읽고 확인한 바로 그 출석부 설정을 끝까지 쓴다.
        # 중간에 활성 기록 파일이 바뀌어도 새 출석부에 방 선택을 잘못 쓰지 않는다.
        if response_state in {"created", "replayed"}:
            operation_expected = str(
                (response or {}).get("expectedCurrentSpace", expected_current) or ""
            ).strip()
            _reconcile_class_space(
                config, space_name, shown, run_command, http_post, gws,
                expected_current=operation_expected,
            )
        else:
            _set_class_space_from_config(
                config, space_name, shown, run_command, http_post, gws,
            )
    except CentralChatError as error:
        # 방은 만들어졌다. 만든 이름을 함께 돌려주어, 같은 이름으로 또 만들려다
        # SPACE_NAME_TAKEN을 만나지 않게 한다.
        reason = _safe_central_error_detail(error)
        return {"state": "failed", "space_name": space_name, "display_name": shown,
                "detail": "방은 만들었어요. 다만 학급 단톡방으로 저장하지 못했어요. " + reason}
    return {"state": "ok", "space_name": space_name, "display_name": shown, "detail": ""}


def start_auth(
    config_dir: Path,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
) -> str:
    # 출결 기록이 없으면 그 안내를 먼저 보여 준다. GWS 선택은 기록을 읽은 뒤
    # read_central_config 안에서 하므로 불필요한 실행 파일 확인도 일어나지 않는다.
    config = read_central_config(
        config_dir,
        run_command,
        gws_executable=gws_executable,
        attendance_record=attendance_record,
    )
    response = http_post(config["url"], "/v1/auth/start", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
    })
    auth_url = str(response.get("authUrl", "") or "")
    if not auth_url.startswith("https://"):
        raise CentralChatError(SERVER_ERROR_MESSAGE)
    return auth_url


def chat_status(
    config_dir: Path,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
) -> dict:
    try:
        config = read_central_config(
            config_dir,
            run_command,
            gws_executable=gws_executable,
        )
        response = http_post(config["url"], "/v1/status", {
            "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
        })
    except CentralChatError as error:
        return {"connected": False, "registered": False, "account": "",
                "class_space_name": "", "class_space_id": "",
                "moved": False,
                "reason": _safe_central_error_detail(
                    error, CHAT_STATUS_FAILURE_MESSAGE
                )}
    reason_code = str(response.get("reasonCode", "") or "")
    moved = bool(response.get("moved"))
    reason = SHEET_MOVED_MESSAGE if moved else SERVER_ANSWER_MESSAGES.get(reason_code)
    if not reason:
        supplied = str(response.get("reason", "") or "")
        reason = supplied if supplied in _SAFE_CENTRAL_MESSAGES else (
            CHAT_STATUS_FAILURE_MESSAGE if supplied or reason_code else ""
        )
    return {
        "connected": bool(response.get("connected")),
        "registered": bool(response.get("registered")),
        "account": str(response.get("account", "") or ""),
        # 서버에 예전 선택이 남아 있어도 현재 출결 시트에 방 ID가 없으면
        # "선택 완료"처럼 보이지 않는다. 실제 발송이 읽는 시트 값을 기준으로 삼는다.
        "class_space_name": (
            str(config.get("class_space_name", "") or "")
            if str(config.get("class_space_id", "") or "").strip()
            else ""
        ),
        "class_space_id": str(config.get("class_space_id", "") or "").strip(),
        "moved": moved,
        "reason": reason,
    }
