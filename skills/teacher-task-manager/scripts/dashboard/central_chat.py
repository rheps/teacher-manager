# skills/teacher-task-manager/scripts/dashboard/central_chat.py
"""중앙 Google Chat 발송소 클라이언트 — 시트의 설정 값을 읽어 서버에 묻는다.

서버 코드는 바꾸지 않는다. Code.gs의 callCentralChatSender_와 같은 규약으로
sheetId + sheetSecret을 보낸다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from brity_bridge import gws_env, paths, process_win

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
    "Google Chat 발송은 새 출석부 사본으로 이미 옮겼어요. "
    '이름에 "AI 입력 준비 사본"이 들어 있는 시트에서 보내 주세요.'
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
SERVER_ANSWER_MESSAGES = {
    "SHEET_MOVED": SHEET_MOVED_MESSAGE,
    "SHEET_AUTH_REQUIRED": SHEET_AUTH_REQUIRED_MESSAGE,
    "SPACE_CREATE_FORBIDDEN": SPACE_BLOCKED_MESSAGE,
    "SPACE_NAME_TAKEN": SPACE_NAME_TAKEN_MESSAGE,
    "SPACE_DISPLAY_NAME_REQUIRED": SPACE_NAME_EMPTY_MESSAGE,
    "SPACE_CREATE_FAILED": SPACE_CREATE_FAILED_MESSAGE,
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


class CentralChatError(RuntimeError):
    pass


def _resolve_command(args, which=shutil.which):
    """Windows에서 "gws" 이름은 npm의 gws.cmd를 못 찾는다 — 실제 경로로 바꿔 실행한다."""
    args = list(args)
    if args and args[0] == "gws":
        args[0] = which("gws") or "gws.cmd"
    return args


def _default_run_command(args, cwd=None):
    # 앱과 같은 곳에 gws 열쇠를 두게 고정한다 — 이 명령에만 넘긴다.
    # 출결 탭이 3초 간격으로 부르는 경로 — 창 숨김이 빠지면 검은 창이 계속 뜬다.
    result = subprocess.run(_resolve_command(args), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", cwd=cwd, shell=False,
                            env=gws_env.gws_environ(),
                            **process_win.hidden_process_kwargs())
    if result.returncode != 0:
        raise CentralChatError(CONFIG_BROKEN_MESSAGE)
    return result.stdout


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


def _read_settings_rows(spreadsheet_id: str, run_command) -> list:
    output = run_command([
        "gws", "sheets", "spreadsheets", "values", "get",
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
    """지금 실제로 쓰는 출석부를 고른다 — 사본 전환이 끝났으면 사본이다.

    옛 출석부의 설정 탭을 계속 읽으면, 발송 서버는 그 시트를 "사본으로 옮김"으로
    알고 있어 409를 돌려준다. 연결은 멀쩡한데 화면에는 연결 실패로 보인다.

    사본은 지금 기록의 사본일 때만 따라간다 — 새 출석부로 바뀐 뒤에 옛 사본
    상태 파일이 남아 있으면 남의 설정 탭을 읽게 된다(2026-07-30).
    """
    import prepare_attendance_copy

    path = prepare_attendance_copy.attendance_copy_status_path(Path(config_dir))
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(saved, dict) or saved.get("state") != "complete":
        return ""  # 만들다 만 사본에는 설정 탭이 아직 없다
    source = str(saved.get("source_spreadsheet_id", "") or "").strip()
    if record_spreadsheet_id and source and source != str(record_spreadsheet_id):
        return ""  # 다른 출석부의 사본이다
    return str(saved.get("copy_spreadsheet_id", "") or "").strip()


def read_central_config(config_dir: Path, run_command=_default_run_command) -> dict:
    record = _load_record(config_dir)
    spreadsheet_id = (
        _spreadsheet_in_use(config_dir, str(record["spreadsheet_id"]))
        or str(record["spreadsheet_id"])
    )
    rows = _read_settings_rows(spreadsheet_id, run_command)
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
    # 모르는 답이면 감추지 않는다. 상태 번호가 있어야 무엇을 물어볼지 정할 수 있다.
    tail = f" ({code})" if code else ""
    return f"발송 서버가 처리하지 못했어요 — 서버 응답 {getattr(error, 'code', '?')}{tail}."


UNIFIED_TASK_LIST_TITLE = "조종례시 담임학급 안내사항"


def sync_task_list(config_dir: Path, run_command=_default_run_command) -> bool:
    """옛 '출결 미제출 확인' 목록을 쓰는 시트를 조종례 목록으로 통합한다. 목록 자체는 지우지 않는다."""
    config = read_central_config(config_dir, run_command)
    homeroom_id = config.get("homeroom_task_list_id", "")
    if not homeroom_id or config.get("task_list_id", "") == homeroom_id:
        return False
    rows = _read_settings_rows(config["spreadsheet_id"], run_command)
    _update_settings_value(config["spreadsheet_id"], rows, "TASK_LIST_ID", homeroom_id, run_command)
    _update_settings_value(config["spreadsheet_id"], rows, "TASK_LIST_TITLE", UNIFIED_TASK_LIST_TITLE, run_command)
    return True


def list_spaces(config_dir: Path, run_command=_default_run_command, http_post=_post) -> list:
    config = read_central_config(config_dir, run_command)
    response = http_post(config["url"], "/v1/spaces", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
    })
    spaces = response.get("spaces") or []
    return [
        {"name": str(s.get("name", "") or ""), "displayName": str(s.get("displayName", "") or "")}
        for s in spaces if isinstance(s, dict) and s.get("name")
    ]


def _update_settings_value(spreadsheet_id: str, rows: list, key: str, value: str, run_command) -> None:
    for index, row in enumerate(rows):
        if isinstance(row, list) and row and str(row[0]).strip() == key:
            row_number = index + 1  # A1 표기 — values get 결과의 줄 번호 그대로
            run_command([
                "gws", "sheets", "spreadsheets", "values", "update",
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


def _upsert_settings_value(spreadsheet_id: str, rows: list, key: str, value: str, run_command) -> None:
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
    run_command([
        "gws", "sheets", "spreadsheets", "values", "update",
        "--params", json.dumps(params, ensure_ascii=False),
        "--json", json.dumps({"majorDimension": "ROWS", "values": values}, ensure_ascii=False),
        "--format", "json",
    ])


def set_class_space(config_dir: Path, space_name: str, display_name: str,
                    run_command=_default_run_command, http_post=_post) -> dict:
    """Code.gs의 [학급 단톡방 고르기]와 같은 두 저장 경로 — 서버 등록 + 시트 설정."""
    config = read_central_config(config_dir, run_command)
    http_post(config["url"], "/v1/class-space", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
        "spaceName": space_name, "displayName": display_name,
    })
    rows = _read_settings_rows(config["spreadsheet_id"], run_command)
    _update_settings_value(config["spreadsheet_id"], rows, "CLASS_CHAT_SPACE_ID", space_name, run_command)
    _update_settings_value(config["spreadsheet_id"], rows, "CLASS_CHAT_SPACE_NAME", display_name, run_command)
    return {"space_name": space_name, "display_name": display_name}


def create_class_space(config_dir: Path, display_name: str,
                       run_command=_default_run_command, http_post=_post) -> dict:
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
    config = read_central_config(config_dir, run_command)
    try:
        response = http_post(config["url"], "/v1/spaces/create", {
            "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
            "displayName": name,
        })
    except CentralChatError as error:
        detail = str(error)
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
        set_class_space(config_dir, space_name, shown, run_command, http_post)
    except CentralChatError as error:
        # 방은 만들어졌다. 만든 이름을 함께 돌려주어, 같은 이름으로 또 만들려다
        # SPACE_NAME_TAKEN을 만나지 않게 한다.
        return {"state": "failed", "space_name": space_name, "display_name": shown,
                "detail": f"방은 만들었어요. 다만 골라 두지 못했어요 — {error}"}
    return {"state": "ok", "space_name": space_name, "display_name": shown, "detail": ""}


def start_auth(config_dir: Path, run_command=_default_run_command, http_post=_post) -> str:
    config = read_central_config(config_dir, run_command)
    response = http_post(config["url"], "/v1/auth/start", {
        "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
    })
    auth_url = str(response.get("authUrl", "") or "")
    if not auth_url.startswith("https://"):
        raise CentralChatError(SERVER_ERROR_MESSAGE)
    return auth_url


def chat_status(config_dir: Path, run_command=_default_run_command, http_post=_post) -> dict:
    try:
        config = read_central_config(config_dir, run_command)
        response = http_post(config["url"], "/v1/status", {
            "sheetId": config["sheet_id"], "sheetSecret": config["sheet_secret"],
        })
    except CentralChatError as error:
        return {"connected": False, "registered": False, "account": "",
                "class_space_name": "", "reason": str(error)}
    return {
        "connected": bool(response.get("connected")),
        "registered": bool(response.get("registered")),
        "account": str(response.get("account", "") or ""),
        "class_space_name": str(response.get("classSpaceName", "") or ""),
        "reason": "",
    }
