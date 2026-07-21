# skills/teacher-task-manager/scripts/dashboard/central_chat.py
"""중앙 Google Chat 발송소 클라이언트 — 시트의 설정 값을 읽어 서버에 묻는다.

서버 코드는 바꾸지 않는다. Code.gs의 callCentralChatSender_와 같은 규약으로
sheetId + sheetSecret을 보낸다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

from brity_bridge import paths, process_win

SETTINGS_RANGE = "설정!A1:D200"
TIMEOUT_SECONDS = 6
NOT_PREPARED_MESSAGE = "출결 준비가 아직 안 됐어요. 먼저 출결 준비 시작하기를 눌러 주세요."
CONFIG_BROKEN_MESSAGE = "출결 시트의 설정 값을 읽지 못했어요. 시트가 열리는지 확인해 주세요."
SERVER_ERROR_MESSAGE = "발송 서버와 연결하지 못했어요. 인터넷 연결을 확인해 주세요."

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
    # 출결 탭이 3초 간격으로 부르는 경로 — 창 숨김이 빠지면 검은 창이 계속 뜬다.
    result = subprocess.run(_resolve_command(args), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", cwd=cwd, shell=False,
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


def read_central_config(config_dir: Path, run_command=_default_run_command) -> dict:
    record = _load_record(config_dir)
    spreadsheet_id = str(record["spreadsheet_id"])
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
    except Exception as error:  # noqa: BLE001 - 화면에는 한국어 한 문장만
        raise CentralChatError(SERVER_ERROR_MESSAGE) from error


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
