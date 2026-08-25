"""중앙 Google Chat 발송 연결을 확인된 새 출결 후보로 한 번만 옮긴다.

옮길 실제 연결이 없으면 서버 자료를 만들지도 바꾸지도 않고 `등록 없음`으로 끝낸다.
결과가 불명확하면 같은 요청을 자동으로 다시 보내지 않는다.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


SHEET_ID_KEY = "CENTRAL_CHAT_SHEET_ID"
SHEET_SECRET_KEY = "CENTRAL_CHAT_SHEET_SECRET"
MOVE_PATH = "/v1/sheet/move"
STATUS_PATH = "/v1/status"
ALLOWED_OUTCOMES = {"not_registered", "moved"}
SPREADSHEET_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
# 이동은 한 번뿐인 요청이라 서버가 깨어나는 시간까지 기다린다.
HTTP_TIMEOUT_SECONDS = 30


class AttendanceCentralMoveHold(ValueError):
    """중앙 연결 상태를 확실히 모를 때 자동으로 고치지 않고 멈춘다."""

    code = "ATTENDANCE_CENTRAL_MOVE_HOLD"


@dataclass(frozen=True)
class CentralMoveResult:
    outcome: str
    account: str
    source_spreadsheet_id: str
    candidate_spreadsheet_id: str
    new_sheet_id: str = ""


@dataclass(frozen=True)
class CentralRouteResult:
    route: str


def _hold(message: str, *, cause: Exception | None = None):
    error = AttendanceCentralMoveHold(
        "ATTENDANCE_CENTRAL_MOVE_HOLD: "
        + str(message).strip()
        + " 같은 요청을 자동으로 다시 보내지 않았습니다."
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
    _need(bool(clean) and clean == value, f"{label}이 비었거나 앞뒤가 흐립니다.")
    return clean


def _spreadsheet_id(value: Any, label: str) -> str:
    text = _text(value, label)
    _need(
        SPREADSHEET_ID_PATTERN.fullmatch(text) is not None,
        f"{label}의 모양이 Google Sheet ID가 아닙니다.",
    )
    return text


def _email(value: Any, label: str) -> str:
    text = _text(value, label)
    _need(
        text.count("@") == 1 and not text.startswith("@") and not text.endswith("@"),
        f"{label}의 이메일 모양이 다릅니다.",
    )
    return text.casefold()


def _split_sheet_identity(sheet_id: str, source_spreadsheet_id: str) -> str:
    """`<스프레드시트ID>:<uuid>`에서 뒤쪽 구분값만 떼어 낸다."""

    marker = source_spreadsheet_id + ":"
    _need(
        sheet_id.startswith(marker),
        "설정의 중앙 발송 시트 번호가 이번 원본 Sheet로 시작하지 않습니다.",
    )
    suffix = sheet_id[len(marker):]
    _need(
        bool(suffix) and ":" not in suffix,
        "설정의 중앙 발송 시트 번호 뒤쪽 구분값을 읽지 못했습니다.",
    )
    return suffix


def _default_read_config(config_dir: Path) -> dict[str, Any]:
    from dashboard import central_chat

    return central_chat.read_central_config(Path(config_dir))


def _default_read_rows(spreadsheet_id: str) -> list:
    from dashboard import central_chat

    return central_chat._read_settings_rows(
        spreadsheet_id,
        central_chat._default_run_command,
    )


def _default_update_setting(
    spreadsheet_id: str,
    rows: list,
    key: str,
    value: str,
) -> None:
    from dashboard import central_chat

    central_chat._update_settings_value(
        spreadsheet_id,
        rows,
        key,
        value,
        central_chat._default_run_command,
    )


def _default_http_post(url: str, path: str, payload: dict) -> dict:
    """출결 탭의 3초 주기 호출보다 넉넉히 기다린다.

    이동 요청 하나는 서버가 깨어나는 시간, 저장된 Google 연결로 새 Sheet를 열어 보는
    시간, 두 문서를 한 거래로 바꾸는 시간을 모두 포함한다. 여기서 일찍 끊으면 서버가
    이미 옮긴 뒤에도 결과를 못 받은 상태가 되고, 그때는 사람이 직접 확인해야 한다.
    """

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _settings_value(rows: Any, key: str) -> str:
    """설정 시트에서 같은 이름이 정확히 한 번 나오는 값만 읽는다."""

    _need(isinstance(rows, list), "후보 설정 시트 내용을 읽지 못했습니다.")
    found: list[str] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        if str(row[0]).strip() == key:
            found.append(str(row[1]).strip())
    _need(len(found) == 1, f"후보 설정 시트에서 {key} 값을 하나만 찾지 못했습니다.")
    return found[0]


def _reply(value: Any, label: str) -> dict[str, Any]:
    _need(isinstance(value, dict), f"{label} 응답이 JSON 객체가 아닙니다.")
    return value


def _authenticated_status_active(
    status: dict[str, Any], *, account: str, label: str
) -> bool:
    _need(
        {"registered", "connected", "account"}.issubset(status)
        and type(status.get("registered")) is bool
        and type(status.get("connected")) is bool
        and isinstance(status.get("account"), str),
        f"{label} 응답의 필수 상태값이 빠졌거나 모양이 다릅니다.",
    )
    registered = status["registered"]
    connected = status["connected"]
    status_account = status["account"]
    if registered is True and connected is True:
        _need(
            _email(status_account, f"{label} 계정") == account,
            f"{label} 계정이 현재 Google 계정과 다릅니다.",
        )
        return True
    _need(
        registered is False and connected is False and status_account == "",
        f"{label}가 명시적인 미등록 상태가 아닙니다.",
    )
    return False


def _point_candidate_at_new_sheet_id(
    candidate_spreadsheet_id: str,
    *,
    new_sheet_id: str,
    sheet_secret: str,
    read_rows: Callable[[str], list],
    update_setting: Callable[[str, list, str, str], Any],
) -> None:
    """후보 설정 시트의 중앙 발송 시트 번호 한 칸만 새 값으로 맞춘다."""

    try:
        rows = read_rows(candidate_spreadsheet_id)
    except Exception as exc:
        _hold("후보 설정 시트를 읽지 못했습니다.", cause=exc)
    _need(
        _settings_value(rows, SHEET_SECRET_KEY) == sheet_secret,
        "후보 설정 시트의 발송 확인값이 기존 시트와 다릅니다.",
    )
    current = _settings_value(rows, SHEET_ID_KEY)
    if current == new_sheet_id:
        return
    try:
        update_setting(candidate_spreadsheet_id, rows, SHEET_ID_KEY, new_sheet_id)
    except Exception as exc:
        _hold("후보 설정 시트의 중앙 발송 시트 번호를 바꾸지 못했습니다.", cause=exc)
    try:
        rechecked = read_rows(candidate_spreadsheet_id)
    except Exception as exc:
        _hold("후보 설정 시트를 다시 읽지 못했습니다.", cause=exc)
    _need(
        _settings_value(rechecked, SHEET_ID_KEY) == new_sheet_id,
        "후보 설정 시트를 다시 읽은 중앙 발송 시트 번호가 다릅니다.",
    )
    _need(
        _settings_value(rechecked, SHEET_SECRET_KEY) == sheet_secret,
        "후보 설정 시트를 다시 읽은 발송 확인값이 달라졌습니다.",
    )


def inspect_central_chat_route(
    config_dir: Path,
    *,
    account: str,
    source_spreadsheet_id: str,
    candidate_spreadsheet_id: str,
    read_config: Callable[[Path], dict] | None = None,
    http_post: Callable[[str, str, dict], dict] | None = None,
) -> CentralRouteResult:
    """변경 요청 없이 중앙 발송 경로가 원본·후보·미등록 중 어디인지 확인한다."""

    config_dir = Path(config_dir)
    account = _email(account, "현재 Google 계정")
    source_id = _spreadsheet_id(source_spreadsheet_id, "원본 Sheet ID")
    candidate_id = _spreadsheet_id(candidate_spreadsheet_id, "후보 Sheet ID")
    _need(source_id != candidate_id, "원본과 후보 Sheet ID가 같습니다.")
    read_config = read_config or _default_read_config
    http_post = http_post or _default_http_post
    try:
        config = read_config(config_dir)
    except Exception as exc:
        _hold("중앙 발송 설정을 읽지 못했습니다.", cause=exc)
    _need(isinstance(config, dict), "중앙 발송 설정의 모양이 다릅니다.")
    _need(
        _spreadsheet_id(config.get("spreadsheet_id"), "설정을 읽은 Sheet ID")
        == source_id,
        "중앙 발송 설정을 읽은 Sheet가 이번 원본과 다릅니다.",
    )
    url = _text(config.get("url"), "중앙 발송소 주소")
    _need(url.startswith("https://"), "중앙 발송소 주소가 https가 아닙니다.")
    original_sheet_id = _text(config.get("sheet_id"), "원본 중앙 발송 시트 번호")
    sheet_secret = _text(config.get("sheet_secret"), "중앙 발송 확인값")
    suffix = _split_sheet_identity(original_sheet_id, source_id)
    candidate_sheet_id = f"{candidate_id}:{suffix}"

    def active(sheet_id: str, label: str) -> bool:
        try:
            status = _reply(
                http_post(
                    url,
                    STATUS_PATH,
                    {"sheetId": sheet_id, "sheetSecret": sheet_secret},
                ),
                label,
            )
        except AttendanceCentralMoveHold:
            raise
        except Exception as exc:
            _hold(f"{label}를 읽지 못했습니다.", cause=exc)
        return _authenticated_status_active(
            status,
            account=account,
            label=label,
        )

    source_active = active(original_sheet_id, "원본 중앙 발송 상태")
    candidate_active = active(candidate_sheet_id, "후보 중앙 발송 상태")
    _need(not (source_active and candidate_active), "원본과 후보 중앙 발송 경로가 동시에 보입니다.")
    if source_active:
        return CentralRouteResult("source")
    if candidate_active:
        return CentralRouteResult("candidate")
    return CentralRouteResult("not_registered")


def move_central_chat_connection(
    config_dir: Path,
    *,
    account: str,
    source_spreadsheet_id: str,
    candidate_spreadsheet_id: str,
    read_config: Callable[[Path], dict] | None = None,
    read_rows: Callable[[str], list] | None = None,
    update_setting: Callable[[str, list, str, str], Any] | None = None,
    http_post: Callable[[str, str, dict], dict] | None = None,
) -> CentralMoveResult:
    """등록과 연결이 실제로 있는 사용자만 기존 발송 연결을 후보로 옮긴다."""

    config_dir = Path(config_dir)
    account = _email(account, "현재 Google 계정")
    source_id = _spreadsheet_id(source_spreadsheet_id, "원본 Sheet ID")
    candidate_id = _spreadsheet_id(candidate_spreadsheet_id, "후보 Sheet ID")
    _need(source_id != candidate_id, "원본과 후보 Sheet ID가 같습니다.")

    read_config = read_config or _default_read_config
    read_rows = read_rows or _default_read_rows
    update_setting = update_setting or _default_update_setting
    http_post = http_post or _default_http_post

    try:
        config = read_config(config_dir)
    except Exception as exc:
        _hold("중앙 발송 설정을 읽지 못했습니다.", cause=exc)
    _need(isinstance(config, dict), "중앙 발송 설정의 모양이 다릅니다.")
    _need(
        _spreadsheet_id(config.get("spreadsheet_id"), "설정을 읽은 Sheet ID")
        == source_id,
        "중앙 발송 설정을 읽은 Sheet가 이번 원본과 다릅니다.",
    )
    url = _text(config.get("url"), "중앙 발송소 주소")
    _need(url.startswith("https://"), "중앙 발송소 주소가 https가 아닙니다.")
    sheet_id = _text(config.get("sheet_id"), "중앙 발송 시트 번호")
    sheet_secret = _text(config.get("sheet_secret"), "중앙 발송 확인값")
    suffix = _split_sheet_identity(sheet_id, source_id)
    new_sheet_id = f"{candidate_id}:{suffix}"

    def _not_registered() -> CentralMoveResult:
        return CentralMoveResult(
            outcome="not_registered",
            account=account,
            source_spreadsheet_id=source_id,
            candidate_spreadsheet_id=candidate_id,
        )

    try:
        status = _reply(
            http_post(
                url,
                STATUS_PATH,
                {"sheetId": sheet_id, "sheetSecret": sheet_secret},
            ),
            "중앙 발송 상태",
        )
    except AttendanceCentralMoveHold:
        raise
    except Exception as exc:
        _hold("중앙 발송 등록 상태를 확인하지 못했습니다.", cause=exc)
    source_active = _authenticated_status_active(
        status,
        account=account,
        label="중앙 발송 상태",
    )

    # 후보는 옛 시트의 중앙 발송 번호를 그대로 들고 있으면 안 된다.
    # 등록이 없어도 후보가 자기 번호를 갖도록 이 한 칸은 항상 맞춘다.
    _point_candidate_at_new_sheet_id(
        candidate_id,
        new_sheet_id=new_sheet_id,
        sheet_secret=sheet_secret,
        read_rows=read_rows,
        update_setting=update_setting,
    )

    if not source_active:
        return _not_registered()

    try:
        moved = _reply(
            http_post(
                url,
                MOVE_PATH,
                {
                    "sheetId": sheet_id,
                    "sheetSecret": sheet_secret,
                    "newSheetId": new_sheet_id,
                },
            ),
            "중앙 발송 연결 이동",
        )
    except AttendanceCentralMoveHold:
        raise
    except Exception as exc:
        _hold("중앙 발송 연결 이동 결과를 받지 못했습니다.", cause=exc)

    _need(moved.get("ok") is True, "중앙 발송 연결 이동이 성공으로 확인되지 않았습니다.")
    outcome = _text(moved.get("outcome"), "중앙 발송 연결 이동 결과")
    _need(
        outcome in ALLOWED_OUTCOMES,
        "중앙 발송 연결 이동 결과가 확실한 두 상태 중 하나가 아닙니다.",
    )
    if outcome == "not_registered":
        return _not_registered()
    _need(
        _text(moved.get("newSheetId"), "옮긴 뒤 시트 번호") == new_sheet_id,
        "옮긴 뒤 시트 번호가 이번 후보 번호와 다릅니다.",
    )
    return CentralMoveResult(
        outcome="moved",
        account=account,
        source_spreadsheet_id=source_id,
        candidate_spreadsheet_id=candidate_id,
        new_sheet_id=new_sheet_id,
    )


def rollback_central_chat_connection(
    config_dir: Path,
    *,
    account: str,
    source_spreadsheet_id: str,
    candidate_spreadsheet_id: str,
    read_config: Callable[[Path], dict] | None = None,
    http_post: Callable[[str, str, dict], dict] | None = None,
) -> bool:
    """후보로 옮긴 중앙 발송 연결을 원본으로 되돌리고 상태를 다시 확인한다."""

    config_dir = Path(config_dir)
    account = _email(account, "현재 Google 계정")
    source_id = _spreadsheet_id(source_spreadsheet_id, "원본 Sheet ID")
    candidate_id = _spreadsheet_id(candidate_spreadsheet_id, "후보 Sheet ID")
    _need(source_id != candidate_id, "원본과 후보 Sheet ID가 같습니다.")
    read_config = read_config or _default_read_config
    http_post = http_post or _default_http_post

    try:
        config = read_config(config_dir)
    except Exception as exc:
        _hold("되돌릴 중앙 발송 설정을 읽지 못했습니다.", cause=exc)
    _need(isinstance(config, dict), "되돌릴 중앙 발송 설정의 모양이 다릅니다.")
    _need(
        _spreadsheet_id(config.get("spreadsheet_id"), "설정을 읽은 Sheet ID")
        == source_id,
        "되돌릴 중앙 발송 설정이 이번 원본과 다릅니다.",
    )
    url = _text(config.get("url"), "중앙 발송소 주소")
    _need(url.startswith("https://"), "중앙 발송소 주소가 https가 아닙니다.")
    original_sheet_id = _text(config.get("sheet_id"), "원본 중앙 발송 시트 번호")
    sheet_secret = _text(config.get("sheet_secret"), "중앙 발송 확인값")
    suffix = _split_sheet_identity(original_sheet_id, source_id)
    candidate_sheet_id = f"{candidate_id}:{suffix}"

    try:
        moved = _reply(
            http_post(
                url,
                MOVE_PATH,
                {
                    "sheetId": candidate_sheet_id,
                    "sheetSecret": sheet_secret,
                    "newSheetId": original_sheet_id,
                },
            ),
            "중앙 발송 연결 되돌리기",
        )
    except AttendanceCentralMoveHold:
        raise
    except Exception as exc:
        _hold("중앙 발송 연결을 되돌린 결과를 받지 못했습니다.", cause=exc)
    _need(moved.get("ok") is True, "중앙 발송 연결 되돌리기가 성공으로 확인되지 않았습니다.")
    _need(
        _text(moved.get("outcome"), "중앙 발송 연결 되돌리기 결과") == "moved",
        "중앙 발송 연결 되돌리기 결과가 확실하지 않습니다.",
    )
    _need(
        _text(moved.get("newSheetId"), "되돌린 뒤 시트 번호")
        == original_sheet_id,
        "되돌린 뒤 중앙 발송 시트 번호가 원본과 다릅니다.",
    )

    try:
        status = _reply(
            http_post(
                url,
                STATUS_PATH,
                {"sheetId": original_sheet_id, "sheetSecret": sheet_secret},
            ),
            "되돌린 중앙 발송 상태",
        )
    except AttendanceCentralMoveHold:
        raise
    except Exception as exc:
        _hold("되돌린 중앙 발송 상태를 다시 읽지 못했습니다.", cause=exc)
    _need(
        status.get("registered") is True and status.get("connected") is True,
        "되돌린 중앙 발송 연결이 등록되고 연결된 상태가 아닙니다.",
    )
    _need(
        _email(status.get("account"), "되돌린 중앙 발송 계정") == account,
        "되돌린 중앙 발송 계정이 현재 Google 계정과 다릅니다.",
    )
    return True


__all__ = [
    "AttendanceCentralMoveHold",
    "CentralMoveResult",
    "CentralRouteResult",
    "inspect_central_chat_route",
    "move_central_chat_connection",
    "rollback_central_chat_connection",
]
