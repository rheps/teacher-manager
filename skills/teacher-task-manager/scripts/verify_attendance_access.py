"""출결 연결을 바꾸기 직전에 Google 계정과 공유 상태를 읽기만 한다."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from attendance_install_record import (  # noqa: E402
    AttendanceInstallRecordError,
    load_attendance_install_record,
)
from brity_bridge import gws_env, process_win, tool_runtime  # noqa: E402


SHEET_MIME = "application/vnd.google-apps.spreadsheet"
FILE_FIELDS = (
    "kind,id,name,mimeType,webViewLink,driveId,ownedByMe,"
    "owners(emailAddress,displayName,me),"
    "capabilities(canCopy,canEdit,canShare),trashed"
)
PERMISSION_FIELDS = (
    "kind,nextPageToken,permissions("
    "kind,id,type,role,emailAddress,domain,displayName,"
    "allowFileDiscovery,expirationTime,deleted,pendingOwner,view,"
    "inheritedPermissionsDisabled,"
    "permissionDetails(permissionType,inheritedFrom,role,inherited))"
)
COMMENT_FIELDS = "kind,nextPageToken,comments(kind,id,deleted)"
REMOTE_COMMAND_TIMEOUT_SECONDS = 120.0
KNOWN_PERMISSION_TYPES = {"user", "group", "domain", "anyone"}
MY_DRIVE_PERMISSION_ROLES = {"owner", "writer", "commenter", "reader"}
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
    r"[.][A-Za-z]{2,}(?![\w.-])"
)

RunCommand = Callable[[Sequence[str]], tuple[int, str]]


@dataclass(frozen=True)
class PermissionDetail:
    permission_type: str
    role: str
    inherited: bool
    inherited_from: str = ""


@dataclass(frozen=True)
class VisiblePermission:
    file_kind: str
    permission_id: str
    permission_type: str
    role: str
    display_name: str = ""
    email_address: str = ""
    domain: str = ""
    allow_file_discovery: bool | None = None
    expiration_time: str = ""
    pending_owner: bool = False
    deleted: bool = False
    view: str = ""
    inherited_permissions_disabled: bool | None = None
    permission_details: tuple[PermissionDetail, ...] = ()


@dataclass(frozen=True)
class AttendanceAccessCheckResult:
    state: str
    verified: bool
    account: str
    source_spreadsheet_id: str
    copy_spreadsheet_id: str
    source_permissions: tuple[VisiblePermission, ...]
    copy_permissions: tuple[VisiblePermission, ...]
    source_comments_state: str = "clear"
    copy_comments_state: str = "clear"


@dataclass(frozen=True)
class _Inputs:
    account: str
    source_id: str
    source_url: str
    copy_id: str
    copy_url: str


class AttendanceAccessHold(ValueError):
    """확실한 읽기 결과가 아니어서 연결 변경을 멈출 때 발생한다."""

    code = "ATTENDANCE_ACCESS_HOLD"


def _hold(message: str, *, cause: Exception | None = None):
    error = AttendanceAccessHold(
        "ATTENDANCE_ACCESS_HOLD: "
        + str(message).strip()
        + " 자동으로 고치거나 연결을 바꾸지 않았습니다."
    )
    if cause is not None:
        error.__cause__ = cause
    raise error


def _need(condition: Any, message: str) -> None:
    if not condition:
        _hold(message)


def _default_run_command(args: Sequence[str]) -> tuple[int, str]:
    # 앱과 같은 곳에 gws 열쇠를 두게 고정한다 — 이 명령에만 넘긴다.
    return process_win.run_captured(
        list(args),
        cwd=SCRIPTS_DIR,
        timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        env=gws_env.gws_environ(),
    )


def _locked(config_dir: Path):
    from dashboard.engine import attendance_setup_lock

    return attendance_setup_lock(config_dir)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _hold(f"상태 파일에 같은 항목이 두 번 적혀 있습니다: {key}")
        result[key] = value
    return result


def _bad_json_number(value: str):
    _hold(f"상태 파일에 JSON으로 쓸 수 없는 숫자가 있습니다: {value}")


def _strict_json_object(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _hold(f"{label} 파일을 끝까지 읽지 못했습니다.", cause=exc)
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_pairs,
            parse_constant=_bad_json_number,
        )
    except AttendanceAccessHold:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        _hold(f"{label} 파일의 JSON 모양이 올바르지 않습니다.", cause=exc)
    _need(isinstance(value, dict), f"{label} 파일 맨 바깥은 JSON 객체여야 합니다.")
    return value


def _strict_text(value: Any, label: str) -> str:
    _need(isinstance(value, str), f"{label} 값은 글자여야 합니다.")
    text = value.strip()
    _need(bool(text), f"{label} 값이 비어 있습니다.")
    return text


def _strict_email(value: Any, label: str) -> str:
    email = _strict_text(value, label)
    _need(
        EMAIL_PATTERN.fullmatch(email) is not None,
        f"{label} 값이 온전한 이메일 주소가 아닙니다.",
    )
    return email


def _same_email(first: str, second: str) -> bool:
    return first.casefold() == second.casefold()


def _sheet_id_from_url(value: Any, label: str) -> str:
    url = _strict_text(value, label)
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        _hold(f"{label} 값이 Google Sheet 주소가 아닙니다.", cause=exc)
    _need(
        parsed.scheme == "https" and parsed.netloc == "docs.google.com",
        f"{label} 값이 공식 Google Sheet 주소가 아닙니다.",
    )
    match = re.fullmatch(r"/spreadsheets/d/([^/?#]+)(?:/.*)?", parsed.path)
    _need(match is not None, f"{label} 값에서 Sheet ID를 읽지 못했습니다.")
    sheet_id = match.group(1)
    _need(bool(sheet_id.strip()), f"{label} 값에서 Sheet ID를 읽지 못했습니다.")
    return sheet_id


def _load_inputs(config_dir: Path) -> _Inputs:
    config_dir = Path(config_dir)
    install_path = config_dir / "attendance-install.generated.json"
    try:
        install = load_attendance_install_record(install_path)
    except AttendanceInstallRecordError as exc:
        _hold("기존 출결 설치 기록을 안전하게 읽지 못했습니다.", cause=exc)

    setup = _strict_json_object(
        config_dir / "attendance-setup-status.generated.json",
        "출결 준비 상태",
    )
    copy_state = _strict_json_object(
        config_dir / "attendance-copy-status.generated.json",
        "출결 사본 상태",
    )

    _need(setup.get("state") == "ready", "출결 준비 상태가 완료가 아닙니다.")
    setup_account = _strict_email(setup.get("account"), "설치 때 Google 계정")

    _need(
        copy_state.get("state") == "complete",
        "출결 사본 준비 상태가 완료가 아닙니다.",
    )
    copy_account = _strict_email(
        copy_state.get("account"),
        "사본을 만든 Google 계정",
    )
    _need(
        _same_email(setup_account, copy_account),
        "설치 때 계정과 사본을 만든 계정이 다릅니다.",
    )
    _need(
        type(copy_state.get("code_gs_hold")) is bool,
        "사본의 기존 자동화 차단 여부가 참/거짓인지 확인하지 못했습니다.",
    )
    _need(
        copy_state.get("local_connection_switched") is False,
        "로컬 출결 연결이 아직 원본을 가리키는지 확인하지 못했습니다.",
    )

    source_id = _strict_text(install.get("spreadsheet_id"), "원본 Sheet ID")
    source_url = _strict_text(install.get("spreadsheet_url"), "원본 Sheet 주소")
    _need(
        _sheet_id_from_url(source_url, "원본 Sheet 주소") == source_id,
        "원본 Sheet 주소와 ID가 서로 다릅니다.",
    )
    saved_source_id = _strict_text(
        copy_state.get("source_spreadsheet_id"),
        "사본 상태의 원본 Sheet ID",
    )
    _need(
        saved_source_id == source_id,
        "설치 기록과 사본 상태의 원본 Sheet ID가 다릅니다.",
    )

    copy_id = _strict_text(
        copy_state.get("copy_spreadsheet_id"),
        "사본 Sheet ID",
    )
    _need(copy_id != source_id, "원본과 사본 Sheet ID가 같습니다.")
    copy_url = _strict_text(
        copy_state.get("copy_spreadsheet_url"),
        "사본 Sheet 주소",
    )
    _need(
        _sheet_id_from_url(copy_url, "사본 Sheet 주소") == copy_id,
        "사본 Sheet 주소와 ID가 서로 다릅니다.",
    )
    return _Inputs(
        account=setup_account,
        source_id=source_id,
        source_url=source_url,
        copy_id=copy_id,
        copy_url=copy_url,
    )


def _run(
    run_command: RunCommand,
    args: Sequence[str],
    label: str,
) -> str:
    try:
        result = run_command(list(args))
    except Exception as exc:
        _hold(f"{label} 요청을 실행하지 못했습니다.", cause=exc)
    _need(
        isinstance(result, tuple) and len(result) == 2,
        f"{label} 명령 결과 모양을 확인하지 못했습니다.",
    )
    code, output = result
    _need(
        isinstance(code, int) and not isinstance(code, bool),
        f"{label} 명령 종료값을 확인하지 못했습니다.",
    )
    _need(isinstance(output, str), f"{label} 명령 출력이 글자가 아닙니다.")
    _need(code == 0, f"{label} 요청이 성공하지 않았습니다.")
    return output


def _first_json(output: str, label: str) -> Any:
    decoder = json.JSONDecoder(
        object_pairs_hook=_strict_pairs,
        parse_constant=_bad_json_number,
    )
    for index, character in enumerate(output):
        if character not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(output[index:])
            return value
        except AttendanceAccessHold:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    _hold(f"{label} 응답에서 JSON 부분을 찾지 못했습니다.")


def _json_values_in_text(output: str) -> list[Any]:
    decoder = json.JSONDecoder(
        object_pairs_hook=_strict_pairs,
        parse_constant=_bad_json_number,
    )
    values: list[Any] = []
    cursor = 0
    while cursor < len(output):
        starts = [
            index
            for index in (output.find("{", cursor), output.find("[", cursor))
            if index >= 0
        ]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(output[start:])
        except AttendanceAccessHold:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            cursor = start + 1
            continue
        values.append(value)
        cursor = start + end
    return values


def _auth_status_objects(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if "logged_in" in value or "user" in value:
            return [value]
        found: list[Mapping[str, Any]] = []
        for nested in value.values():
            found.extend(_auth_status_objects(nested))
        return found
    if isinstance(value, list):
        found = []
        for nested in value:
            found.extend(_auth_status_objects(nested))
        return found
    return []


def _run_json(
    run_command: RunCommand,
    gws_executable: str,
    parts: Sequence[str],
    params: Mapping[str, Any],
    label: str,
) -> Any:
    args = [
        gws_executable,
        *parts,
        "--params",
        json.dumps(
            dict(params),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ),
        "--format",
        "json",
    ]
    return _first_json(_run(run_command, args, label), label)


def _legacy_auth_output_confirms_login(output: str, account: str) -> bool:
    """JSON을 주지 않던 예전 gws의 명시적인 로그인 완료 문구만 인정한다."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    lowered = output.casefold()
    negative_markers = (
        "logged out",
        "signed out",
        "not logged in",
        "not signed in",
        "not authenticated",
        "no credentials",
        "login required",
        "authentication required",
    )
    if any(marker in lowered for marker in negative_markers):
        return False

    account_key = account.casefold()

    def line_parts(line: str) -> tuple[str, str] | None:
        matches = list(EMAIL_PATTERN.finditer(line))
        if len(matches) != 1:
            return None
        match = matches[0]
        if line[match.end():].strip():
            return None
        return (
            line[:match.start()].strip().casefold().rstrip(":").strip(),
            match.group(0).casefold(),
        )

    for line in lines:
        parts = line_parts(line)
        if parts and parts[0] in {"signed in as", "logged in as"} and parts[1] == account_key:
            return True

    has_logged_in_line = any(line.casefold() == "logged in" for line in lines)
    has_matching_user_line = any(
        parts is not None and parts[0] == "user" and parts[1] == account_key
        for parts in (line_parts(line) for line in lines)
    )
    return has_logged_in_line and has_matching_user_line


def _current_gws_account(
    run_command: RunCommand,
    gws_executable: str,
) -> str:
    output = _run(
        run_command,
        [gws_executable, "auth", "status"],
        "현재 Google 계정 확인",
    )
    found: dict[str, str] = {}
    for match in EMAIL_PATTERN.finditer(output):
        email = match.group(0)
        found.setdefault(email.casefold(), email)
    _need(
        len(found) == 1,
        "현재 Google 계정을 하나로 분명하게 확인하지 못했습니다.",
    )
    raw_account = next(iter(found.values()))
    status_objects: list[Mapping[str, Any]] = []
    for value in _json_values_in_text(output):
        status_objects.extend(_auth_status_objects(value))
    _need(
        len(status_objects) <= 1,
        "현재 Google 로그인 상태를 나타내는 JSON 객체가 여러 개입니다.",
    )
    if status_objects:
        status = status_objects[0]
        _need(
            "user" in status,
            "현재 Google 로그인 상태 JSON에 계정 항목이 없습니다.",
        )
        # 로그인 완료를 알리는 항목 이름은 gws 판마다 다르다.
        # gws 0.22.5는 logged_in 없이 auth_method와 token_valid로 알린다.
        # 있는 항목은 모두 로그인됨을 가리켜야 하고, 적어도 하나는 있어야 한다.
        signals = 0
        if "logged_in" in status:
            _need(
                status["logged_in"] is True,
                "현재 Google 로그인 완료 여부를 참으로 확인하지 못했습니다.",
            )
            signals += 1
        if "token_valid" in status:
            _need(
                status["token_valid"] is True,
                "현재 Google 로그인 토큰이 유효하지 않습니다.",
            )
            signals += 1
        if "auth_method" in status:
            method = status["auth_method"]
            _need(
                isinstance(method, str) and method.strip() not in ("", "none"),
                "현재 Google 로그인 방식을 확인하지 못했습니다.",
            )
            signals += 1
        _need(
            signals > 0,
            "현재 Google 로그인 완료를 확인할 항목이 하나도 없습니다.",
        )
        status_account = _strict_email(
            status["user"],
            "현재 Google 로그인 상태의 계정",
        )
        _need(
            _same_email(status_account, raw_account),
            "현재 Google 로그인 상태의 계정과 출력 속 계정이 다릅니다.",
        )
    else:
        _need(
            _legacy_auth_output_confirms_login(output, raw_account),
            "현재 Google 로그인 완료를 확인하는 문구가 없습니다.",
        )
    return raw_account


def _get_file(
    file_kind: str,
    file_id: str,
    expected_url: str,
    account: str,
    *,
    run_command: RunCommand,
    gws_executable: str,
) -> dict[str, Any]:
    label = "원본 Sheet" if file_kind == "source" else "사본 Sheet"
    reply = _run_json(
        run_command,
        gws_executable,
        ["drive", "files", "get"],
        {
            "fileId": file_id,
            "fields": FILE_FIELDS,
            "supportsAllDrives": True,
        },
        f"{label} 정보 읽기",
    )
    _need(isinstance(reply, dict), f"{label} 정보 응답이 객체가 아닙니다.")
    _need(reply.get("kind") == "drive#file", f"{label} 응답 종류가 다릅니다.")
    _need(
        _strict_text(reply.get("id"), f"{label} 응답 ID") == file_id,
        f"{label} 응답 ID가 요청한 ID와 다릅니다.",
    )
    _need(
        reply.get("mimeType") == SHEET_MIME,
        f"{label}이 Google Sheet가 아닙니다.",
    )
    _strict_text(reply.get("name"), f"{label} 이름")
    actual_url = _strict_text(reply.get("webViewLink"), f"{label} 현재 주소")
    _need(
        _sheet_id_from_url(actual_url, f"{label} 현재 주소") == file_id,
        f"{label} 현재 주소와 ID가 다릅니다.",
    )
    _need(
        _sheet_id_from_url(expected_url, f"{label} 저장 주소") == file_id,
        f"{label} 저장 주소와 ID가 다릅니다.",
    )
    _need(reply.get("trashed") is False, f"{label}이 휴지통에 있거나 상태를 모릅니다.")
    _need(reply.get("ownedByMe") is True, f"현재 계정이 {label} 소유자가 아닙니다.")
    _need("driveId" not in reply, f"{label}이 개인 Drive 자료인지 확인하지 못했습니다.")

    owners = reply.get("owners")
    _need(
        isinstance(owners, list) and len(owners) == 1,
        f"{label} 소유자가 정확히 한 명인지 확인하지 못했습니다.",
    )
    owner = owners[0]
    _need(isinstance(owner, dict), f"{label} 소유자 정보 모양이 다릅니다.")
    owner_email = _strict_email(
        owner.get("emailAddress"),
        f"{label} 소유자 계정",
    )
    _need(
        _same_email(owner_email, account),
        f"{label} 소유자가 설치 계정과 다릅니다.",
    )
    _need(owner.get("me") is True, f"{label} 소유자가 현재 계정인지 확인하지 못했습니다.")
    if "displayName" in owner:
        _need(
            isinstance(owner["displayName"], str),
            f"{label} 소유자 표시 이름 모양이 다릅니다.",
        )

    capabilities = reply.get("capabilities")
    _need(isinstance(capabilities, dict), f"{label} 사용 가능 작업을 읽지 못했습니다.")
    for name in ("canCopy", "canEdit", "canShare"):
        _need(
            name in capabilities and type(capabilities[name]) is bool,
            f"{label}의 {name} 값을 분명하게 확인하지 못했습니다.",
        )
    if file_kind == "source":
        _need(capabilities["canCopy"] is True, "현재 계정으로 원본을 복사할 수 없습니다.")
    else:
        _need(capabilities["canEdit"] is True, "현재 계정으로 사본을 수정할 수 없습니다.")
    _need(
        capabilities["canShare"] is True,
        f"학교 정책상 {label} 공유 상태를 다룰 수 없습니다.",
    )
    return reply


def _optional_text(raw: Mapping[str, Any], name: str, label: str) -> str:
    if name not in raw:
        return ""
    _need(isinstance(raw[name], str), f"{label}의 {name} 값이 글자가 아닙니다.")
    return raw[name].strip()


def _optional_bool(
    raw: Mapping[str, Any],
    name: str,
    label: str,
    *,
    default: bool = False,
) -> bool:
    if name not in raw:
        return default
    _need(type(raw[name]) is bool, f"{label}의 {name} 값이 참/거짓이 아닙니다.")
    return raw[name]


def _rfc3339(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        _hold(f"{label} 만료 시각이 올바르지 않습니다.", cause=exc)
    _need(parsed.tzinfo is not None, f"{label} 만료 시각에 시간대가 없습니다.")
    return value


def _visible_permission(
    file_kind: str,
    raw: Any,
) -> VisiblePermission:
    label = "원본 권한" if file_kind == "source" else "사본 권한"
    _need(isinstance(raw, dict), f"{label} 한 항목의 모양이 다릅니다.")
    _need(raw.get("kind") == "drive#permission", f"{label} 응답 종류가 다릅니다.")
    permission_id = _strict_text(raw.get("id"), f"{label} ID")
    permission_type = _strict_text(raw.get("type"), f"{label} 대상 종류")
    _need(
        permission_type in KNOWN_PERMISSION_TYPES,
        f"{label} 대상 종류를 알 수 없습니다: {permission_type}",
    )
    role = _strict_text(raw.get("role"), f"{label} 역할")
    _need(
        role in MY_DRIVE_PERMISSION_ROLES,
        f"개인 Drive의 {label} 역할로 허용할 수 없습니다: {role}",
    )

    display_name = _optional_text(raw, "displayName", label)
    email_address = ""
    domain = ""
    allow_file_discovery: bool | None = None
    if permission_type in {"user", "group"}:
        email_address = _strict_email(raw.get("emailAddress"), f"{label} 계정")
        if "allowFileDiscovery" in raw:
            allow_file_discovery = _optional_bool(
                raw,
                "allowFileDiscovery",
                label,
            )
    elif permission_type == "domain":
        domain = _strict_text(raw.get("domain"), f"{label} 학교 도메인")
        _need(
            re.fullmatch(r"[A-Za-z0-9.-]+", domain) is not None
            and "." in domain,
            f"{label} 학교 도메인 모양이 올바르지 않습니다.",
        )
        _need(
            "allowFileDiscovery" in raw,
            f"{label} 검색 공개 여부가 빠졌습니다.",
        )
        allow_file_discovery = _optional_bool(
            raw,
            "allowFileDiscovery",
            label,
        )
    else:
        _need(
            "allowFileDiscovery" in raw,
            f"{label} 링크 공개 여부가 빠졌습니다.",
        )
        allow_file_discovery = _optional_bool(
            raw,
            "allowFileDiscovery",
            label,
        )

    expiration_time = _optional_text(raw, "expirationTime", label)
    if expiration_time:
        _need(
            permission_type in {"user", "group"},
            f"{label}의 만료 시각이 허용되지 않는 대상에 붙어 있습니다.",
        )
        expiration_time = _rfc3339(expiration_time, label)
    pending_owner = _optional_bool(raw, "pendingOwner", label)
    deleted = _optional_bool(raw, "deleted", label)
    _need(not pending_owner, f"{label}에서 소유권 이전 대기 상태를 찾았습니다.")
    _need(not deleted, f"{label}에서 삭제된 계정 권한을 찾았습니다.")

    view = _optional_text(raw, "view", label)
    _need(
        not view,
        f"Google Sheet의 {label}에는 보기 전용 권한 값을 허용할 수 없습니다.",
    )
    inherited_permissions_disabled: bool | None = None
    if "inheritedPermissionsDisabled" in raw:
        _need(
            type(raw["inheritedPermissionsDisabled"]) is bool,
            f"{label}의 제한된 접근 여부가 참/거짓이 아닙니다.",
        )
        inherited_permissions_disabled = raw[
            "inheritedPermissionsDisabled"
        ]

    details_raw = raw.get("permissionDetails")
    _need(
        isinstance(details_raw, list) and bool(details_raw),
        f"{label}의 직접 공유·상속 상세 목록이 빠졌거나 모양이 다릅니다.",
    )
    details: list[PermissionDetail] = []
    for index, item in enumerate(details_raw):
        item_label = f"{label} 상세 {index + 1}"
        _need(isinstance(item, dict), f"{item_label} 모양이 다릅니다.")
        detail_type = _strict_text(
            item.get("permissionType"),
            f"{item_label} 종류",
        )
        _need(
            detail_type == "file",
            f"개인 Drive의 {item_label} 종류로 허용할 수 없습니다.",
        )
        detail_role = _strict_text(item.get("role"), f"{item_label} 역할")
        _need(
            detail_role in MY_DRIVE_PERMISSION_ROLES,
            f"개인 Drive의 {item_label} 역할로 허용할 수 없습니다.",
        )
        _need(
            type(item.get("inherited")) is bool,
            f"{item_label}의 상속 여부를 확인하지 못했습니다.",
        )
        inherited_from = _optional_text(
            item,
            "inheritedFrom",
            item_label,
        )
        _need(
            not inherited_from,
            f"개인 Drive의 {item_label}에 공유 드라이브 상속 출처가 있습니다.",
        )
        details.append(
            PermissionDetail(
                permission_type=detail_type,
                role=detail_role,
                inherited=item["inherited"],
                inherited_from=inherited_from,
            )
        )

    # 한 사람에게 상세가 여럿 붙는 것은 정상이다. 폴더에서 물려받은 writer와
    # 파일에 직접 붙은 owner가 함께 온다. 위쪽 role은 그중 실제로 적용되는 값이므로
    # 어느 상세에도 없는 역할이 위쪽에 적혀 있으면 읽은 값을 믿을 수 없다.
    _need(
        any(detail.role == role for detail in details),
        f"{label}의 역할을 실제로 주는 상세가 없습니다.",
    )

    return VisiblePermission(
        file_kind=file_kind,
        permission_id=permission_id,
        permission_type=permission_type,
        role=role,
        display_name=display_name,
        email_address=email_address,
        domain=domain,
        allow_file_discovery=allow_file_discovery,
        expiration_time=expiration_time,
        pending_owner=pending_owner,
        deleted=deleted,
        view=view,
        inherited_permissions_disabled=inherited_permissions_disabled,
        permission_details=tuple(details),
    )


def _list_all_permissions(
    file_kind: str,
    file_id: str,
    *,
    run_command: RunCommand,
    gws_executable: str,
) -> tuple[VisiblePermission, ...]:
    label = "원본 공유 목록" if file_kind == "source" else "사본 공유 목록"
    page_token: str | None = None
    seen_tokens: set[str] = set()
    seen_ids: set[str] = set()
    permissions: list[VisiblePermission] = []
    while True:
        params: dict[str, Any] = {
            "fileId": file_id,
            "pageSize": 100,
            "supportsAllDrives": True,
            "includePermissionsForView": "published",
            "fields": PERMISSION_FIELDS,
        }
        if page_token is not None:
            params["pageToken"] = page_token
        reply = _run_json(
            run_command,
            gws_executable,
            ["drive", "permissions", "list"],
            params,
            label,
        )
        _need(isinstance(reply, dict), f"{label} 응답이 객체가 아닙니다.")
        _need(
            reply.get("kind") == "drive#permissionList",
            f"{label} 응답 종류가 다릅니다.",
        )
        _need(
            "permissions" in reply and isinstance(reply["permissions"], list),
            f"{label}의 권한 목록이 빠졌거나 모양이 다릅니다.",
        )
        for raw in reply["permissions"]:
            visible = _visible_permission(file_kind, raw)
            _need(
                visible.permission_id not in seen_ids,
                f"{label}에서 같은 권한 ID가 두 번 나왔습니다.",
            )
            seen_ids.add(visible.permission_id)
            permissions.append(visible)

        if "nextPageToken" not in reply:
            break
        next_token = _strict_text(
            reply.get("nextPageToken"),
            f"{label} 다음 페이지 표시",
        )
        _need(
            next_token not in seen_tokens,
            f"{label}의 다음 페이지 표시가 반복됐습니다.",
        )
        seen_tokens.add(next_token)
        page_token = next_token
    return tuple(permissions)


def _validate_permission_owner(
    file_kind: str,
    permissions: tuple[VisiblePermission, ...],
    account: str,
) -> None:
    label = "원본" if file_kind == "source" else "사본"
    owners = [item for item in permissions if item.role == "owner"]
    _need(
        len(owners) == 1,
        f"{label} 권한 목록에서 소유자를 정확히 한 명 확인하지 못했습니다.",
    )
    owner = owners[0]
    _need(
        owner.permission_type == "user"
        and _same_email(owner.email_address, account),
        f"{label} 권한 목록의 소유자가 설치 계정과 다릅니다.",
    )
    _need(
        not owner.expiration_time,
        f"{label} 소유자 권한에 만료 시각이 있습니다.",
    )
    # 소유자에게도 폴더에서 물려받은 writer 상세가 함께 붙는다.
    # 그러니 상세 전부가 owner일 필요는 없고, 파일에 직접 붙은 owner 상세가
    # 하나 있어야 한다. 상세 하나하나의 종류·역할·상속 출처는 위에서 이미 검사한다.
    _need(
        any(
            detail.permission_type == "file"
            and detail.role == "owner"
            and detail.inherited is False
            and not detail.inherited_from
            for detail in owner.permission_details
        ),
        f"{label} 소유자 권한이 직접 부여된 owner인지 확인하지 못했습니다.",
    )
    if file_kind == "copy":
        _need(
            len(permissions) == 1,
            "사본에 소유자 외 공유 권한이 있습니다.",
        )


def _comments_clear(
    file_kind: str,
    file_id: str,
    *,
    run_command: RunCommand,
    gws_executable: str,
) -> None:
    label = "원본 댓글" if file_kind == "source" else "사본 댓글"
    page_token: str | None = None
    seen_tokens: set[str] = set()
    seen_ids: set[str] = set()
    while True:
        params: dict[str, Any] = {
            "fileId": file_id,
            "includeDeleted": True,
            "pageSize": 100,
            "fields": COMMENT_FIELDS,
        }
        if page_token is not None:
            params["pageToken"] = page_token
        reply = _run_json(
            run_command,
            gws_executable,
            ["drive", "comments", "list"],
            params,
            label,
        )
        _need(isinstance(reply, dict), f"{label} 응답이 객체가 아닙니다.")
        _need(
            reply.get("kind") == "drive#commentList",
            f"{label} 응답 종류가 다릅니다.",
        )
        _need(
            "comments" in reply and isinstance(reply["comments"], list),
            f"{label} 목록이 빠졌거나 모양이 다릅니다.",
        )
        for raw in reply["comments"]:
            _need(isinstance(raw, dict), f"{label} 한 항목의 모양이 다릅니다.")
            _need(
                raw.get("kind") == "drive#comment",
                f"{label} 한 항목의 종류가 다릅니다.",
            )
            comment_id = _strict_text(raw.get("id"), f"{label} ID")
            _need(
                comment_id not in seen_ids,
                f"{label}에서 같은 댓글 ID가 두 번 나왔습니다.",
            )
            seen_ids.add(comment_id)
            _need(
                type(raw.get("deleted")) is bool,
                f"{label}의 삭제 여부를 확인하지 못했습니다.",
            )
            _hold(
                f"{label} 이력이 있어 자동으로 옮길 수 없습니다."
            )

        if "nextPageToken" not in reply:
            return
        next_token = _strict_text(
            reply.get("nextPageToken"),
            f"{label} 다음 페이지 표시",
        )
        _need(
            next_token not in seen_tokens,
            f"{label}의 다음 페이지 표시가 반복됐습니다.",
        )
        seen_tokens.add(next_token)
        page_token = next_token


def _check_attendance_access_once(
    config_dir: Path,
    *,
    run_command: RunCommand = _default_run_command,
    gws_executable: str | None = None,
) -> AttendanceAccessCheckResult:
    """호출자가 출결 잠금을 잡았을 때 같은 잠금 안에서 바로 다시 확인한다."""

    config_dir = Path(config_dir)
    inputs = _load_inputs(config_dir)
    gws = _strict_text(
        tool_runtime.resolve_gws_executable()
        if gws_executable is None
        else gws_executable,
        "Google Workspace 실행 파일",
    )
    _need(Path(gws).is_absolute(), "Google Workspace 실행 파일이 전체 경로가 아닙니다.")
    current_account = _current_gws_account(run_command, gws)
    _need(
        _same_email(current_account, inputs.account),
        "현재 Google 계정이 설치 때 사용한 계정과 다릅니다.",
    )

    _get_file(
        "source",
        inputs.source_id,
        inputs.source_url,
        inputs.account,
        run_command=run_command,
        gws_executable=gws,
    )
    _get_file(
        "copy",
        inputs.copy_id,
        inputs.copy_url,
        inputs.account,
        run_command=run_command,
        gws_executable=gws,
    )

    source_permissions = _list_all_permissions(
        "source",
        inputs.source_id,
        run_command=run_command,
        gws_executable=gws,
    )
    copy_permissions = _list_all_permissions(
        "copy",
        inputs.copy_id,
        run_command=run_command,
        gws_executable=gws,
    )
    _validate_permission_owner("source", source_permissions, inputs.account)
    _validate_permission_owner("copy", copy_permissions, inputs.account)

    _comments_clear(
        "source",
        inputs.source_id,
        run_command=run_command,
        gws_executable=gws,
    )
    _comments_clear(
        "copy",
        inputs.copy_id,
        run_command=run_command,
        gws_executable=gws,
    )
    return AttendanceAccessCheckResult(
        state="ready",
        verified=True,
        account=current_account,
        source_spreadsheet_id=inputs.source_id,
        copy_spreadsheet_id=inputs.copy_id,
        source_permissions=source_permissions,
        copy_permissions=copy_permissions,
    )


def verify_attendance_access(
    config_dir: Path,
    *,
    run_command: RunCommand = _default_run_command,
    gws_executable: str | None = None,
    lock_factory: Callable[[Path], Any] | None = None,
) -> AttendanceAccessCheckResult:
    """출결 잠금을 잡고 계정·소유자·공유 사용자·댓글을 읽기만 한다."""

    config_dir = Path(config_dir)
    lock = lock_factory or _locked
    with lock(config_dir):
        return _check_attendance_access_once(
            config_dir,
            run_command=run_command,
            gws_executable=gws_executable,
        )


__all__ = [
    "AttendanceAccessCheckResult",
    "AttendanceAccessHold",
    "PermissionDetail",
    "VisiblePermission",
    "_check_attendance_access_once",
    "verify_attendance_access",
]
