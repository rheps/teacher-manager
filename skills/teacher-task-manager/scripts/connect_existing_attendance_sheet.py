"""이미 쓰던 출결 시트에 프로그램을 다시 연결한다.

시트에는 한 글자도 쓰지 않는다. `설정` 탭에 이미 들어 있는 값을 읽어
설치 기록만 다시 만든다. 하나라도 확인되지 않으면 멈춘다.

설치 도우미가 빈 시트를 새로 만들어 버리는 일을 막기 위한 반대편 절차다.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from attendance_install_record import (  # noqa: E402
    AttendanceInstallRecordError,
    CONNECTION_FIELDS,
    SCRIPT_ATTESTATION_FIELD,
    SCRIPT_UPDATE_REQUIRED_FIELD,
    build_script_attestation,
    ensure_create_only_install_backup,
    read_attendance_install_snapshot,
    replace_attendance_install_record,
    validate_attendance_install_record,
    write_attendance_install_record,
)
from attendance_script_update import inspect_attendance_script_update  # noqa: E402
from apps_script_version import app_version_in_source  # noqa: E402
from brity_bridge import (  # noqa: E402
    bundle_paths,
    gws_env,
    paths,
    process_win,
    tool_runtime,
)


SETTINGS_RANGE = "설정!A1:D200"
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
DOCUMENT_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"
SPREADSHEET_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
REMOTE_COMMAND_TIMEOUT_SECONDS = 120.0

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


def _default_run_command(args: Sequence[str]) -> tuple[int, str]:
    # 앱과 같은 곳에 gws 열쇠를 두게 고정한다 — 이 명령에만 넘긴다.
    return process_win.run_captured(
        list(args),
        cwd=SCRIPTS_DIR,
        timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        env=gws_env.gws_environ(),
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


def _script_update_required(
    run_command,
    gws: str,
    spreadsheet_id: str,
    script_id: str,
    deployment_id: str,
) -> tuple[bool, str]:
    """Sheet 부모·현재 편집본·실제 배포판을 모두 읽어 연결 가능 여부를 정한다."""

    def runner(args, _cwd):
        return run_command(args)

    inspection = inspect_attendance_script_update(
        spreadsheet_id,
        script_id,
        deployment_id,
        assets_dir=bundle_paths.bundle_root() / "assets",
        runner=runner,
        gws_executable=gws,
    )
    if inspection.verified and inspection.state == "current":
        return False, inspection.target_bundle_sha256
    if inspection.verified and inspection.state == "update_available":
        return True, ""
    found_version = ""
    try:
        reply = _run_json(
            run_command,
            [
                gws, "script", "projects", "getContent",
                "--params", _params({"scriptId": script_id}),
                "--format", "json",
            ],
            "Apps Script 판 번호 확인",
        )
        for item in reply.get("files") or []:
            if isinstance(item, dict):
                found_version = app_version_in_source(item.get("source")) or ""
                if found_version:
                    break
    except AttendanceConnectHold:
        found_version = ""
    version_note = (
        f" 화면에서 읽힌 판은 {found_version}입니다."
        if found_version
        else ""
    )
    _hold(
        "Apps Script가 공식 배포 코드인지, 이 출결 시트에 묶여 있는지, "
        "현재 편집본과 실제 배포판이 같은지를 모두 확인하지 못했습니다. "
        "사용자 수정 코드는 자동으로 덮어쓰지 않습니다."
        + version_note
    )


def connect_existing_attendance_sheet(
    config_dir: Path,
    spreadsheet_id: str,
    *,
    account: str,
    run_command: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    gws_executable: str | None = None,
) -> ConnectResult:
    """쓰던 시트의 설정을 읽어 설치 기록만 다시 만든다. 시트에는 쓰지 않는다."""

    config_dir = Path(config_dir)
    run_command = run_command or _default_run_command
    account = _text(account, "현재 Google 계정")
    sheet_id = _text(spreadsheet_id, "출결 시트 ID")
    _need(
        SPREADSHEET_ID_PATTERN.fullmatch(sheet_id) is not None,
        "출결 시트 ID 모양이 Google Sheet ID가 아닙니다.",
    )
    gws = _text(
        tool_runtime.resolve_gws_executable()
        if gws_executable is None
        else gws_executable,
        "Google Workspace 실행 파일",
    )
    _need(Path(gws).is_absolute(), "Google Workspace 실행 파일이 전체 경로가 아닙니다.")

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
    script_update_required, script_bundle_sha256 = _script_update_required(
        run_command,
        gws,
        sheet_id,
        settings["SCRIPT_ID"],
        settings["DEPLOYMENT_ID"],
    )
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
    if script_update_required:
        record[SCRIPT_UPDATE_REQUIRED_FIELD] = True
    if script_bundle_sha256:
        record[SCRIPT_ATTESTATION_FIELD] = build_script_attestation(
            record, script_bundle_sha256
        )
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
    previous = None
    if record_path.exists():
        try:
            previous = read_attendance_install_snapshot(record_path)
            if not backup_path.exists():
                ensure_create_only_install_backup(backup_path, previous)
        except AttendanceInstallRecordError as exc:
            _hold("지금 쓰던 설치 기록을 백업하지 못했습니다.", cause=exc)

    try:
        if previous is None:
            written = write_attendance_install_record(record_path, record)
        else:
            merged = dict(previous.record)
            for key in CONNECTION_FIELDS:
                merged[key] = record[key]
            for key, value in record.items():
                if (
                    key not in CONNECTION_FIELDS
                    and key not in {
                        SCRIPT_UPDATE_REQUIRED_FIELD,
                        SCRIPT_ATTESTATION_FIELD,
                    }
                    and key not in merged
                ):
                    merged[key] = value
            # 연결 대상이 바뀌었으므로 이전 시트에 대한 증명과 표식은 먼저 버리고,
            # 이번 원격 확인 결과만 다시 남긴다.
            merged.pop(SCRIPT_UPDATE_REQUIRED_FIELD, None)
            merged.pop(SCRIPT_ATTESTATION_FIELD, None)
            if script_update_required:
                merged[SCRIPT_UPDATE_REQUIRED_FIELD] = True
            if script_bundle_sha256:
                merged[SCRIPT_ATTESTATION_FIELD] = build_script_attestation(
                    merged, script_bundle_sha256
                )
            validate_attendance_install_record(merged)
            written = replace_attendance_install_record(
                record_path, merged, previous
            ).record
    except AttendanceInstallRecordError as exc:
        _hold("설치 기록을 안전하게 쓰지 못했습니다.", cause=exc)
    _need(
        written.get("spreadsheet_id") == sheet_id,
        "쓴 설치 기록을 다시 읽은 값이 이번 시트와 다릅니다.",
    )

    return ConnectResult(
        state=(
            "script-update-required" if script_update_required else "ready"
        ),
        detail=(
            (
                "쓰시던 공식 출결 시트를 연결했습니다. 출결 기능을 최신판으로 "
                "올린 뒤 사용할 수 있습니다. "
            )
            if script_update_required
            else "쓰시던 출결 시트의 설정을 읽어 프로그램 연결만 다시 맞췄습니다. "
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
