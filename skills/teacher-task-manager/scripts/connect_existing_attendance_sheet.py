"""이미 쓰던 출결 시트에 프로그램을 다시 연결한다.

시트에는 한 글자도 쓰지 않는다. `설정` 탭에 이미 들어 있는 값을 읽어
설치 기록만 다시 만든다. 하나라도 확인되지 않으면 멈춘다.

설치 도우미가 빈 시트를 새로 만들어 버리는 일을 막기 위한 반대편 절차다.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from attendance_install_record import (  # noqa: E402
    AttendanceInstallRecordError,
    ensure_create_only_install_backup,
    read_attendance_install_snapshot,
    validate_attendance_install_record,
    write_attendance_install_record,
)
from apps_script_version import (  # noqa: E402
    app_version_in_source,
    is_at_least,
    minimum_apps_script_version,
)
from brity_bridge import bundle_paths, gws_env, paths, process_win  # noqa: E402


SETTINGS_RANGE = "설정!A1:D200"
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
DOCUMENT_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"
SPREADSHEET_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

# 설치 기록의 연결값이 시트 설정의 어느 항목에서 오는지.
SETTING_TO_FIELD = {
    "TEMPLATE_DOC_ID": "template_doc_id",
    "DEST_FOLDER_ID": "folder_id",
    "TASK_LIST_ID": "task_list_id",
    "SCRIPT_ID": "script_id",
    "DEPLOYMENT_ID": "deployment_id",
}


class AttendanceConnectHold(ValueError):
    """확인되지 않은 상태를 자동으로 고치지 않고 멈출 때 발생한다."""

    code = "ATTENDANCE_CONNECT_HOLD"


@dataclass(frozen=True)
class ConnectResult:
    state: str
    detail: str
    spreadsheet_id: str
    account: str
    record: dict


def _hold(message: str, *, cause: Exception | None = None):
    error = AttendanceConnectHold(
        "ATTENDANCE_CONNECT_HOLD: "
        + str(message).strip()
        + " 설치 기록을 바꾸지 않았고 시트에도 쓰지 않았습니다."
    )
    if cause is not None:
        error.__cause__ = cause
    raise error


def _need(condition: Any, message: str) -> None:
    if not condition:
        _hold(message)


def _text(value: Any, label: str) -> str:
    _need(isinstance(value, str), f"{label}이 글자가 아닙니다.")
    clean = value.strip()
    _need(bool(clean), f"{label}이 비어 있습니다.")
    return clean


def _resolve_command(args: Sequence[str], which=shutil.which) -> list[str]:
    """Windows에서 "gws" 이름은 npm이 깐 gws.cmd를 못 찾는다."""
    args = list(args)
    if args and args[0] == "gws":
        args[0] = which("gws") or which("gws.cmd") or "gws"
    return args


def _default_run_command(args: Sequence[str]) -> tuple[int, str]:
    # 앱과 같은 곳에 gws 열쇠를 두게 고정한다 — 이 명령에만 넘긴다.
    return process_win.run_captured(
        _resolve_command(args), cwd=SCRIPTS_DIR, env=gws_env.gws_environ()
    )


def _run_json(run_command, args: Sequence[str], label: str) -> Any:
    try:
        result = run_command(list(args))
    except Exception as exc:
        _hold(f"{label} 요청을 실행하지 못했습니다.", cause=exc)
    _need(
        isinstance(result, tuple) and len(result) == 2,
        f"{label} 명령 결과 모양이 다릅니다.",
    )
    code, output = result
    _need(isinstance(code, int) and not isinstance(code, bool), f"{label} 종료값이 다릅니다.")
    _need(isinstance(output, str), f"{label} 출력이 글자가 아닙니다.")
    _need(code == 0, f"{label} 요청이 성공하지 않았습니다.")
    try:
        value = process_win.parse_first_json(output)
    except ValueError as exc:
        _hold(f"{label} 응답에서 JSON을 찾지 못했습니다.", cause=exc)
    _need(isinstance(value, dict), f"{label} 응답이 JSON 객체가 아닙니다.")
    return value


def _params(values: dict) -> str:
    return json.dumps(values, ensure_ascii=False)


def _read_settings(run_command, gws: str, spreadsheet_id: str) -> dict[str, str]:
    reply = _run_json(
        run_command,
        [
            gws, "sheets", "spreadsheets", "values", "get",
            "--params", _params({"spreadsheetId": spreadsheet_id, "range": SETTINGS_RANGE}),
            "--format", "json",
        ],
        "설정 탭 읽기",
    )
    rows = reply.get("values")
    _need(isinstance(rows, list) and rows, "설정 탭에서 값을 읽지 못했습니다.")
    settings: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        key = str(row[0]).strip()
        if not key:
            continue
        _need(key not in settings, f"설정 탭에 {key} 줄이 두 번 있습니다.")
        settings[key] = str(row[1]).strip()
    return settings


def _check_drive_file(
    run_command, gws: str, file_id: str, expected_mime: str, label: str
) -> None:
    reply = _run_json(
        run_command,
        [
            gws, "drive", "files", "get",
            "--params", _params(
                {"fileId": file_id, "fields": "id,name,mimeType,trashed", "supportsAllDrives": True}
            ),
            "--format", "json",
        ],
        f"{label} 확인",
    )
    _need(reply.get("id") == file_id, f"{label}의 ID가 요청과 다릅니다.")
    _need(reply.get("mimeType") == expected_mime, f"{label}의 종류가 다릅니다.")
    _need(reply.get("trashed") is False, f"{label}이 휴지통에 있습니다.")


def _check_script_version(run_command, gws: str, script_id: str) -> str:
    """시트에 붙은 Apps Script가 동봉 배포 정보의 하한선 이상인지 본다."""

    reply = _run_json(
        run_command,
        [
            gws, "script", "projects", "getContent",
            "--params", _params({"scriptId": script_id}),
            "--format", "json",
        ],
        "Apps Script 판 번호 확인",
    )
    files = reply.get("files")
    _need(isinstance(files, list) and files, "Apps Script 원본을 읽지 못했습니다.")
    found = None
    for item in files:
        if not isinstance(item, dict):
            continue
        found = app_version_in_source(item.get("source"))
        if found:
            break
    _need(
        bool(found),
        "Apps Script 원본에서 판 번호(APP_VERSION)를 찾지 못했습니다.",
    )
    try:
        minimum = minimum_apps_script_version(bundle_paths.bundle_root())
    except ValueError as exc:
        _hold("동봉된 배포 정보에서 최소 판 번호를 읽지 못했습니다.", cause=exc)
    _need(
        is_at_least(found, minimum),
        f"이 시트의 Apps Script는 {found} 판이라 최소 {minimum} 판에 못 미칩니다. "
        "설치 도우미로 스크립트를 올린 뒤 다시 연결해 주세요.",
    )
    return found


def connect_existing_attendance_sheet(
    config_dir: Path,
    spreadsheet_id: str,
    *,
    account: str,
    run_command: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    gws_executable: str = "gws",
) -> ConnectResult:
    """쓰던 시트의 설정을 읽어 설치 기록만 다시 만든다. 시트에는 쓰지 않는다."""

    config_dir = Path(config_dir)
    run_command = run_command or _default_run_command
    gws = _text(gws_executable, "gws 명령 이름")
    account = _text(account, "현재 Google 계정")
    sheet_id = _text(spreadsheet_id, "출결 시트 ID")
    _need(
        SPREADSHEET_ID_PATTERN.fullmatch(sheet_id) is not None,
        "출결 시트 ID 모양이 Google Sheet ID가 아닙니다.",
    )

    # 1) 시트가 실제로 내 것인 스프레드시트인지 확인한다.
    file_reply = _run_json(
        run_command,
        [
            gws, "drive", "files", "get",
            "--params", _params(
                {
                    "fileId": sheet_id,
                    "fields": "id,name,mimeType,trashed,owners(emailAddress)",
                    "supportsAllDrives": True,
                }
            ),
            "--format", "json",
        ],
        "출결 시트 확인",
    )
    _need(file_reply.get("id") == sheet_id, "출결 시트 ID가 요청과 다릅니다.")
    _need(file_reply.get("mimeType") == SPREADSHEET_MIME, "출결 시트가 스프레드시트가 아닙니다.")
    _need(file_reply.get("trashed") is False, "출결 시트가 휴지통에 있습니다.")
    owners = file_reply.get("owners")
    _need(isinstance(owners, list) and len(owners) == 1, "출결 시트 소유자를 하나로 확인하지 못했습니다.")
    _need(
        str(owners[0].get("emailAddress", "")).strip().casefold() == account.casefold(),
        "출결 시트 소유자가 현재 Google 계정과 다릅니다.",
    )

    # 2) 설정 탭에서 연결값을 읽는다.
    settings = _read_settings(run_command, gws, sheet_id)
    missing = sorted(key for key in SETTING_TO_FIELD if not settings.get(key))
    _need(not missing, "설정 탭에 연결값이 비어 있습니다: " + ", ".join(missing))

    # 3) 값이 가리키는 자료가 지금도 살아 있는지 확인한다.
    _check_drive_file(
        run_command, gws, settings["TEMPLATE_DOC_ID"], DOCUMENT_MIME, "신고서 템플릿 문서"
    )
    _check_drive_file(
        run_command, gws, settings["DEST_FOLDER_ID"], FOLDER_MIME, "출력 폴더"
    )
    script_reply = _run_json(
        run_command,
        [
            gws, "script", "projects", "get",
            "--params", _params({"scriptId": settings["SCRIPT_ID"]}),
            "--format", "json",
        ],
        "Apps Script 확인",
    )
    _need(script_reply.get("scriptId") == settings["SCRIPT_ID"], "Apps Script ID가 요청과 다릅니다.")
    _need(
        script_reply.get("parentId") == sheet_id,
        "Apps Script가 이 출결 시트에 묶여 있지 않습니다.",
    )
    _check_script_version(run_command, gws, settings["SCRIPT_ID"])
    tasks_reply = _run_json(
        run_command,
        [
            gws, "tasks", "tasklists", "get",
            "--params", _params({"tasklist": settings["TASK_LIST_ID"]}),
            "--format", "json",
        ],
        "Tasks 목록 확인",
    )
    _need(tasks_reply.get("id") == settings["TASK_LIST_ID"], "Tasks 목록 ID가 요청과 다릅니다.")

    # 4) 확인이 모두 끝난 뒤에만 설치 기록을 쓴다.
    record = {
        "spreadsheet_id": sheet_id,
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
        "template_doc_id": settings["TEMPLATE_DOC_ID"],
        "template_doc_url": (
            f"https://docs.google.com/document/d/{settings['TEMPLATE_DOC_ID']}/edit"
        ),
        "script_id": settings["SCRIPT_ID"],
        "deployment_id": settings["DEPLOYMENT_ID"],
        "folder_id": settings["DEST_FOLDER_ID"],
        "task_list_id": settings["TASK_LIST_ID"],
    }
    homeroom = settings.get("HOMEROOM_TASK_LIST_ID", "")
    if homeroom:
        record["homeroom_task_list_id"] = homeroom
    try:
        validate_attendance_install_record(record)
    except AttendanceInstallRecordError as exc:
        _hold("만들려던 설치 기록이 연결값 검사를 통과하지 못했습니다.", cause=exc)

    record_path = paths.attendance_install_record_path(config_dir)
    # 5) 지금 가리키던 시트를 덮어쓰기 전에 원본을 남긴다. 잘못 연결했을 때
    #    어느 시트를 보고 있었는지 이 파일로 되돌릴 수 있다.
    #    두 번째 연결이 첫 백업을 덮으면 가장 처음 쓰던 시트로는 돌아갈 수 없으므로,
    #    백업은 파일이 없을 때 딱 한 번만 만든다.
    backup_path = paths.attendance_connect_backup_path(config_dir)
    if record_path.exists() and not backup_path.exists():
        try:
            previous = read_attendance_install_snapshot(record_path)
            ensure_create_only_install_backup(backup_path, previous)
        except AttendanceInstallRecordError as exc:
            _hold("지금 쓰던 설치 기록을 백업하지 못했습니다.", cause=exc)

    try:
        written = write_attendance_install_record(record_path, record)
    except AttendanceInstallRecordError as exc:
        _hold("설치 기록을 안전하게 쓰지 못했습니다.", cause=exc)
    _need(
        written.get("spreadsheet_id") == sheet_id,
        "쓴 설치 기록을 다시 읽은 값이 이번 시트와 다릅니다.",
    )

    return ConnectResult(
        state="connected",
        detail=(
            "쓰시던 출결 시트의 설정을 읽어 프로그램 연결만 다시 맞췄습니다. "
            "시트에는 아무것도 쓰지 않았습니다."
        ),
        spreadsheet_id=sheet_id,
        account=account,
        record=dict(written),
    )


__all__ = [
    "AttendanceConnectHold",
    "ConnectResult",
    "connect_existing_attendance_sheet",
]
