# skills/teacher-task-manager/scripts/dashboard/central_chat.py
"""중앙 Google Chat 발송소 클라이언트 — 시트의 설정 값을 읽어 서버에 묻는다.

서버 코드는 바꾸지 않는다. Code.gs의 callCentralChatSender_와 같은 규약으로
sheetId + sheetSecret을 보낸다.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from brity_bridge import gws_env, paths, process_win, tool_runtime

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
    "GOEDU_ACCOUNT_REQUIRED": GOEDU_ACCOUNT_REQUIRED_MESSAGE,
}
UNKNOWN_SERVER_ANSWER_MESSAGE = (
    "발송 서버가 요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요."
)
CHAT_STATUS_FAILURE_MESSAGE = (
    "학급 단톡방 상태를 확인하지 못했어요. 잠시 뒤 다시 확인해 주세요."
)
_SAFE_CENTRAL_MESSAGES = {
    NOT_PREPARED_MESSAGE,
    CONFIG_BROKEN_MESSAGE,
    SERVER_ERROR_MESSAGE,
    UNKNOWN_SERVER_ANSWER_MESSAGE,
    CHAT_STATUS_FAILURE_MESSAGE,
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


class CentralChatError(RuntimeError):
    pass


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
    spaces = response.get("spaces") or []
    return [
        {"name": str(s.get("name", "") or ""), "displayName": str(s.get("displayName", "") or "")}
        for s in spaces if isinstance(s, dict) and s.get("name")
    ]


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


def set_class_space(
    config_dir: Path,
    space_name: str,
    display_name: str,
    run_command=_default_run_command,
    http_post=_post,
    *,
    gws_executable: str | None = None,
    attendance_record=_ATTENDANCE_RECORD_NOT_SUPPLIED,
) -> dict:
    """학급 단톡방을 시트에 온전히 저장한 뒤 발송 서버에도 등록한다."""
    gws = _resolved_gws_executable(gws_executable)
    config = read_central_config(
        config_dir,
        run_command,
        gws_executable=gws,
        attendance_record=attendance_record,
    )
    return _set_class_space_from_config(
        config,
        space_name,
        display_name,
        run_command,
        http_post,
        gws,
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
    try:
        response = http_post(config["url"], "/v1/spaces/create", {
            "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
            "displayName": name,
        })
    except CentralChatError as error:
        detail = _safe_central_error_detail(error)
        # 번역 문구가 아니라 서버가 준 원 코드로 가른다 — 문구에 맥락이 덧붙어도 안 샌다.
        state = "blocked" if getattr(error, "server_code", "") == "SPACE_CREATE_FORBIDDEN" else "failed"
        return {"state": state, "space_name": "", "display_name": name, "detail": detail}
    space = (response or {}).get("space") or {}
    space_name = str(space.get("name", "") or "").strip()
    if not space_name:
        return {"state": "failed", "space_name": "", "display_name": name,
                "detail": "방을 만들었는지 확인하지 못했어요. 목록을 다시 불러와 주세요."}
    shown = str(space.get("displayName", "") or "").strip() or name
    try:
        # 방을 만들기 전에 읽고 확인한 바로 그 출석부 설정을 끝까지 쓴다.
        # 중간에 활성 기록 파일이 바뀌어도 새 출석부에 방 선택을 잘못 쓰지 않는다.
        _set_class_space_from_config(
            config,
            space_name,
            shown,
            run_command,
            http_post,
            gws,
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
            "expectedClassSpaceId": str(config.get("class_space_id", "") or ""),
        })
    except CentralChatError as error:
        original_code = str(getattr(error, "server_code", "") or "")
        return {"connected": False, "registered": False, "account": "",
                "reason_code": original_code or "SERVER_UNREACHABLE", "sheet_id": "",
                "server_sheet_id": "", "class_space_id": "",
                "class_space_matches": None, "server_class_space_id": "",
                "class_space_name": "",
                "reason": _safe_central_error_detail(
                    error, CHAT_STATUS_FAILURE_MESSAGE
                )}
    reason_code = str(response.get("reasonCode", "") or "")
    reason = SERVER_ANSWER_MESSAGES.get(reason_code)
    if not reason:
        supplied = str(response.get("reason", "") or "")
        reason = supplied if supplied in _SAFE_CENTRAL_MESSAGES else (
            CHAT_STATUS_FAILURE_MESSAGE if supplied or reason_code else ""
        )
    return {
        "connected": bool(response.get("connected")),
        "registered": bool(response.get("registered")),
        "account": str(response.get("account", "") or ""),
        # 비교에는 번역문이 아니라 서버의 원래 코드와 전체 ID를 쓴다. 비밀값은
        # 어느 반환 필드에도 넣지 않는다.
        "reason_code": reason_code,
        "sheet_id": str(config.get("sheet_id", "") or ""),
        "server_sheet_id": str(response.get("sheetId", "") or ""),
        "class_space_id": str(config.get("class_space_id", "") or ""),
        "class_space_matches": (
            response.get("classSpaceMatches")
            if type(response.get("classSpaceMatches")) is bool
            else None
        ),
        # 서버는 학급방 전체 ID를 돌려주지 않는다. 옛/알 수 없는 응답에 ID처럼
        # 보이는 값이 있어도 화면 결과로 전달하지 않는다.
        "server_class_space_id": "",
        # 서버에 예전 선택이 남아 있어도 현재 출결 시트에 방 ID가 없으면
        # "선택 완료"처럼 보이지 않는다. 실제 발송이 읽는 시트 값을 기준으로 삼는다.
        "class_space_name": (
            str(config.get("class_space_name", "") or "")
            if str(config.get("class_space_id", "") or "").strip()
            else ""
        ),
        "reason": reason,
    }
