"""기존 출결 Sheet의 비공개 사본에서 표식을 바꾸고 맨 위 한 행을 준비한다."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from attendance_chat_marker import (  # noqa: E402
    CHAT_RESULT_HEADERS,
    normalize_google_sheet_date,
    plan_signature_migration,
)
from attendance_install_record import validate_attendance_install_record  # noqa: E402
from install_attendance_automation import default_runner, run_json  # noqa: E402

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
INPUT_HEADERS = (
    "날짜", "번호+이름", "구분", "종류", "사유", "교시", "신고서", "첨부",
)
DEFAULT_MONTHS = (
    "3월", "4월", "5월", "6월", "7월", "8월",
    "9월", "10월", "11월", "12월", "1월", "2월",
)
# 예전 판이 A1에 넣던 문구다. 이미 만들어진 사본을 알아보는 데만 쓴다.
AI_INPUT_PLACEHOLDER = "AI 출결 입력 (준비 중)"
# assets/Code.gs의 MONTHLY_ATTENDANCE_AI_INPUT_* 상수와 항상 같아야 한다.
AI_INPUT_LABEL = "AI 출결 입력"
AI_INPUT_HINT = '여기에 "3월 12일 김철수 병결" 처럼 적고 Enter를 누르세요'
HINT_COLOR = {"red": 0.604, "green": 0.627, "blue": 0.651}  # #9AA0A6
AI_INPUT_BOX_FIRST_COLUMN = 1   # B
AI_INPUT_BOX_LAST_COLUMN = 10   # K
ROW_ONE_COLUMN_COUNT = 16       # A~P
# 시도시각 칸의 숫자는 날짜 서식일 때만 정상으로 본다.
DATE_NUMBER_FORMATS = frozenset({"DATE", "DATE_TIME"})
ATTEMPTED_AT_COLUMN = len(INPUT_HEADERS) + list(CHAT_RESULT_HEADERS).index(
    "Google Chat 시도시각"
)
SOURCE_FIELDS = (
    "id,name,mimeType,webViewLink,parents,"
    "owners(emailAddress,displayName,me),capabilities(canCopy),trashed"
)
COPY_FIELDS = (
    "id,name,mimeType,webViewLink,parents,"
    "owners(emailAddress,displayName,me),trashed"
)
PERMISSION_FIELDS = (
    "permissions(id,type,role,emailAddress,domain,displayName,"
    "permissionDetails),nextPageToken"
)
GRID_FIELDS = (
    "sheets(properties(sheetId,title),data(startRow,startColumn,"
    "rowData(values(effectiveValue,effectiveFormat(numberFormat(type))))))"
)
Runner = Callable[[Sequence[str], Path], str]
Writer = Callable[[Path, dict[str, Any]], Any]


@dataclass(frozen=True)
class AttendanceCopyResult:
    state: str
    detail: str
    copy_spreadsheet_id: str = ""
    copy_spreadsheet_url: str = ""
    code_gs_hold: bool = True
    local_connection_switched: bool = False


class AttendanceCopyError(ValueError):
    pass


def attendance_copy_status_path(config_dir: Path) -> Path:
    return Path(config_dir) / "attendance-copy-status.generated.json"


def _need(condition: Any, message: str) -> None:
    if not condition:
        raise AttendanceCopyError(message)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _email(value: Any) -> str:
    return _text(value).casefold()


def _result(state: str, detail: str, status=None) -> AttendanceCopyResult:
    status = status if isinstance(status, Mapping) else {}
    return AttendanceCopyResult(
        state=state,
        detail=detail,
        copy_spreadsheet_id=_text(status.get("copy_spreadsheet_id")),
        copy_spreadsheet_url=_text(status.get("copy_spreadsheet_url")),
    )


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, f"사본 진행 기록의 {key} 항목이 중복됐습니다.")
        result[key] = value
    return result


def _bad_number(value):
    raise AttendanceCopyError(f"사본 진행 기록의 숫자가 올바르지 않습니다: {value}")


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_bad_number,
        )
    except AttendanceCopyError:
        raise
    except Exception as exc:
        raise AttendanceCopyError("사본 진행 기록을 끝까지 읽지 못했습니다.") from exc
    _need(isinstance(value, dict), "사본 진행 기록의 바깥 모양이 올바르지 않습니다.")
    return value


def _atomic_state(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        _need(_load_state(temporary) == data, "임시 진행 기록을 다시 읽은 값이 다릅니다.")
        os.replace(temporary, path)
        return data
    except AttendanceCopyError:
        raise
    except Exception as exc:
        raise AttendanceCopyError("사본 진행 기록을 안전하게 바꾸지 못했습니다.") from exc
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _save(writer: Writer, path: Path, data: dict[str, Any]) -> bool:
    try:
        writer(path, data)
        return True
    except Exception:
        return False


def _locked(config_dir: Path):
    from dashboard.engine import attendance_setup_lock
    return attendance_setup_lock(config_dir)


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _call(
    runner: Runner,
    workdir: Path,
    gws: str,
    parts: Sequence[str],
    params: Mapping[str, Any],
    body: Mapping[str, Any] | None = None,
):
    args = [gws, *parts, "--params", _dump(params)]
    if body is not None:
        args += ["--json", _dump(body)]
    args += ["--format", "json"]
    return run_json(runner, args, workdir)


def _sheet_url(url: Any, sheet_id: str) -> bool:
    match = re.match(
        r"^https://docs[.]google[.]com/spreadsheets/d/([^/?#]+)(?:/|$)",
        _text(url),
    )
    return bool(match and match.group(1) == sheet_id)


def _is_owner(owners: Any, account: str) -> bool:
    if not isinstance(owners, list) or not owners:
        return False
    for owner in owners:
        if not isinstance(owner, dict):
            return False
        owner_email = _email(owner.get("emailAddress"))
        if owner_email == _email(account):
            return True
        if owner.get("me") is True and not owner_email:
            return True
    return False


def _source_name(reply, source_id, account, installed_account) -> str:
    _need(isinstance(reply, dict), "원본 정보를 읽지 못했습니다.")
    _need(_text(reply.get("id")) == source_id, "원본 ID가 설치 기록과 다릅니다.")
    _need(reply.get("mimeType") == SHEET_MIME, "원본이 Google Sheet가 아닙니다.")
    _need(reply.get("trashed") is False, "원본이 휴지통에 있거나 상태를 모릅니다.")
    capabilities = reply.get("capabilities")
    _need(
        isinstance(capabilities, dict) and capabilities.get("canCopy") is True,
        "현재 계정으로 원본을 복사할 수 없습니다.",
    )
    if not installed_account:
        _need(
            _is_owner(reply.get("owners"), account),
            "기존 설치 계정이 없어 현재 계정이 원본 소유자인지 확인하지 못했습니다.",
        )
    name = _text(reply.get("name"))
    _need(name, "원본 이름을 확인하지 못했습니다.")
    return name


def _copy_values(reply, source_id, copy_name, account) -> tuple[str, str]:
    _need(isinstance(reply, dict) and reply, "사본 응답이 비어 있습니다.")
    copy_id = _text(reply.get("id"))
    copy_url = _text(reply.get("webViewLink"))
    _need(copy_id and copy_id != source_id, "새 사본 ID를 확인하지 못했습니다.")
    _need(reply.get("mimeType") == SHEET_MIME, "사본이 Google Sheet가 아닙니다.")
    _need(reply.get("trashed") is False, "사본이 휴지통에 있거나 상태를 모릅니다.")
    _need(_sheet_url(copy_url, copy_id), "사본의 Google Sheet 주소가 맞지 않습니다.")
    _need(_text(reply.get("name")) == copy_name, "사본 이름이 요청값과 다릅니다.")
    _need(_is_owner(reply.get("owners"), account), "현재 계정이 사본 소유자가 아닙니다.")
    return copy_id, copy_url


def _current_copy(runner, workdir, gws, status, account):
    copy_id = _text(status.get("copy_spreadsheet_id"))
    reply = _call(
        runner, workdir, gws, ["drive", "files", "get"],
        {
            "fileId": copy_id,
            "fields": COPY_FIELDS,
            "supportsAllDrives": True,
        },
    )
    actual_id, actual_url = _copy_values(
        reply,
        _text(status.get("source_spreadsheet_id")),
        _text(status.get("copy_name")),
        account,
    )
    _need(actual_id == copy_id, "현재 사본 ID가 저장된 값과 다릅니다.")
    _need(
        actual_url == _text(status.get("copy_spreadsheet_url")),
        "현재 사본 주소가 저장된 값과 다릅니다.",
    )


def _base_state(state, account, source_id, copy_name, copy_id="", copy_url=""):
    return {
        "state": state,
        "account": account,
        "source_spreadsheet_id": source_id,
        "copy_name": copy_name,
        "copy_spreadsheet_id": copy_id,
        "copy_spreadsheet_url": copy_url,
        "code_gs_hold": True,
        "local_connection_switched": False,
    }


def _state_as(status, state, **values):
    updated = copy.deepcopy(dict(status))
    updated.update(values)
    updated.update({
        "state": state,
        "code_gs_hold": True,
        "local_connection_switched": False,
    })
    return updated


def _private_copy(runner, workdir, gws, copy_id, account):
    reply = _call(
        runner, workdir, gws, ["drive", "permissions", "list"],
        {
            "fileId": copy_id,
            "fields": PERMISSION_FIELDS,
            "pageSize": 100,
            "supportsAllDrives": True,
        },
    )
    _need(isinstance(reply, dict), "사본 권한 목록을 읽지 못했습니다.")
    _need(not _text(reply.get("nextPageToken")), "사본 권한을 전부 읽지 못했습니다.")
    permissions = reply.get("permissions")
    _need(
        isinstance(permissions, list) and len(permissions) == 1,
        "사본에 현재 소유자 외 공유 권한이 있습니다.",
    )
    owner = permissions[0]
    _need(isinstance(owner, dict), "사본 소유자 권한 모양이 올바르지 않습니다.")
    _need(
        owner.get("type") == "user"
        and owner.get("role") == "owner"
        and _email(owner.get("emailAddress")) == _email(account),
        "현재 계정 한 명만 사본 소유자인지 확인하지 못했습니다.",
    )


def _months(runner, workdir, gws, copy_id) -> list[str]:
    reply = _call(
        runner, workdir, gws, ["sheets", "spreadsheets", "values", "get"],
        {
            "spreadsheetId": copy_id,
            "range": "'설정'!A:B",
            "majorDimension": "ROWS",
            "valueRenderOption": "UNFORMATTED_VALUE",
        },
    )
    _need(isinstance(reply, dict), "설정 탭 A:B를 읽지 못했습니다.")
    values = reply.get("values", [])
    _need(isinstance(values, list), "설정 탭의 행 목록이 올바르지 않습니다.")
    found = []
    for row in values:
        _need(isinstance(row, list), "설정 탭의 한 행 모양이 올바르지 않습니다.")
        if row and _text(row[0]) == "MONTH_SHEET_NAMES":
            found.append(row[1] if len(row) > 1 else "")
    _need(len(found) <= 1, "MONTH_SHEET_NAMES 설정이 중복됐습니다.")
    if not found:
        return list(DEFAULT_MONTHS)
    _need(isinstance(found[0], str) and found[0].strip(), "월 탭 설정값이 비었습니다.")
    names = [name.strip() for name in found[0].split(",")]
    _need(
        all(names) and len(names) == len(set(names)),
        "월 탭 이름이 비었거나 중복됐습니다.",
    )
    return names


def _metadata(runner, workdir, gws, copy_id, month_names):
    reply = _call(
        runner, workdir, gws, ["sheets", "spreadsheets", "get"],
        {
            "spreadsheetId": copy_id,
            "includeGridData": False,
            "fields": (
                "properties(title,locale,timeZone),"
                "sheets(properties(sheetId,title,index,"
                "gridProperties(rowCount,columnCount,frozenRowCount)))"
            ),
        },
    )
    _need(
        isinstance(reply, dict) and isinstance(reply.get("sheets"), list),
        "사본의 탭 목록을 읽지 못했습니다.",
    )
    by_title = {}
    for sheet in reply["sheets"]:
        properties = sheet.get("properties") if isinstance(sheet, dict) else None
        _need(isinstance(properties, dict), "탭 정보 모양이 올바르지 않습니다.")
        by_title.setdefault(_text(properties.get("title")), []).append(properties)
    result = []
    for title in month_names:
        matches = by_title.get(title, [])
        # 지난 달 탭을 지우는 것은 선생님이 흔히 하는 일이다. 시트 쪽 getInputSheets_도
        # 있는 탭만 골라 쓴다. 없는 탭은 건너뛰고, 이름이 겹치면 어느 탭인지 알 수 없으니 멈춘다.
        if not matches:
            continue
        _need(len(matches) == 1, f"{title} 탭 이름이 중복됐습니다.")
        properties = matches[0]
        sheet_id = properties.get("sheetId")
        grid = properties.get("gridProperties")
        _need(
            isinstance(sheet_id, int) and not isinstance(sheet_id, bool)
            and isinstance(grid, dict),
            f"{title} 탭의 Google 번호를 확인하지 못했습니다.",
        )
        row_count = grid.get("rowCount")
        column_count = grid.get("columnCount")
        _need(
            isinstance(row_count, int) and not isinstance(row_count, bool)
            and row_count >= 2
            and isinstance(column_count, int) and not isinstance(column_count, bool)
            and column_count >= 12,
            f"{title} 탭 A:L 전체 범위를 읽을 수 없습니다.",
        )
        result.append({"title": title, "sheet_id": sheet_id, "row_count": row_count})
    _need(bool(result), "설정에 적힌 월 탭이 시트에 하나도 없습니다.")
    return result


def _cell(cell) -> tuple[Any, str, str]:
    _need(isinstance(cell, dict), "Google 셀 값 모양이 올바르지 않습니다.")
    effective = cell.get("effectiveValue")
    if effective is None:
        value = ""
        value_kind = "blank"
    else:
        _need(isinstance(effective, dict) and len(effective) == 1, "셀 값을 하나로 확인하지 못했습니다.")
        _need("errorValue" not in effective, "오류가 든 Google 셀이 있습니다.")
        if "stringValue" in effective:
            value = effective["stringValue"]
            _need(isinstance(value, str), "글자 셀 값이 올바르지 않습니다.")
            value_kind = "string"
        elif "numberValue" in effective:
            value = effective["numberValue"]
            _need(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(float(value)),
                "숫자 셀 값이 올바르지 않습니다.",
            )
            value = int(value) if float(value).is_integer() else float(value)
            value_kind = "number"
        elif "boolValue" in effective:
            value = effective["boolValue"]
            _need(isinstance(value, bool), "참/거짓 셀 값이 올바르지 않습니다.")
            value_kind = "boolean"
        else:
            raise AttendanceCopyError("알 수 없는 Google 셀 값이 있습니다.")
    number_type = ""
    effective_format = cell.get("effectiveFormat")
    if effective_format is not None:
        _need(isinstance(effective_format, dict), "셀 서식 모양이 올바르지 않습니다.")
        number_format = effective_format.get("numberFormat")
        if number_format is not None:
            _need(isinstance(number_format, dict), "숫자 서식 모양이 올바르지 않습니다.")
            number_type = number_format.get("type", "")
            _need(isinstance(number_type, str), "숫자 서식 종류가 올바르지 않습니다.")
    return value, number_type, value_kind


def _quote(title):
    return "'" + title.replace("'", "''") + "'"


def _grid(runner, workdir, gws, copy_id, metadata, minimum=2):
    reply = _call(
        runner, workdir, gws, ["sheets", "spreadsheets", "get"],
        {
            "spreadsheetId": copy_id,
            "includeGridData": True,
            "ranges": [
                f"{_quote(item['title'])}!A1:L{item['row_count']}"
                for item in metadata
            ],
            "fields": GRID_FIELDS,
        },
    )
    _need(
        isinstance(reply, dict) and isinstance(reply.get("sheets"), list),
        "월 탭 A:L 실제 값을 읽지 못했습니다.",
    )
    by_title = {}
    for sheet in reply["sheets"]:
        properties = sheet.get("properties") if isinstance(sheet, dict) else None
        _need(isinstance(properties, dict), "월 탭 실제 값 모양이 올바르지 않습니다.")
        by_title.setdefault(_text(properties.get("title")), []).append(sheet)
    result = {}
    for expected in metadata:
        title = expected["title"]
        matches = by_title.get(title, [])
        _need(len(matches) == 1, f"{title} 탭 A:L을 한 번에 확인하지 못했습니다.")
        sheet = matches[0]
        _need(
            sheet["properties"].get("sheetId") == expected["sheet_id"],
            f"{title} 탭 Google 번호가 바뀌었습니다.",
        )
        data = sheet.get("data")
        _need(isinstance(data, list) and len(data) == 1, f"{title} 탭 범위 모양이 올바르지 않습니다.")
        block = data[0]
        _need(
            isinstance(block, dict)
            and int(block.get("startRow", 0) or 0) == 0
            and int(block.get("startColumn", 0) or 0) == 0,
            f"{title} 탭을 A1부터 읽지 못했습니다.",
        )
        row_data = block.get("rowData", [])
        _need(isinstance(row_data, list), f"{title} 탭 행 목록이 올바르지 않습니다.")
        rows, formats, kinds = [], [], []
        for raw_row in row_data:
            cells = raw_row.get("values", []) if isinstance(raw_row, dict) else None
            _need(isinstance(cells, list) and len(cells) <= 12, f"{title} 탭 A:L 셀 수가 다릅니다.")
            parsed = [_cell(cell) for cell in cells]
            values = [item[0] for item in parsed] + [""] * (12 - len(parsed))
            number_types = [item[1] for item in parsed] + [""] * (12 - len(parsed))
            value_kinds = [item[2] for item in parsed] + ["blank"] * (12 - len(parsed))
            rows.append(values)
            formats.append(number_types)
            kinds.append(value_kinds)
        while (
            len(rows) > minimum
            and all(value == "" for value in rows[-1])
            and all(kind == "blank" for kind in kinds[-1])
        ):
            rows.pop()
            formats.pop()
            kinds.pop()
        while len(rows) < minimum:
            rows.append([""] * 12)
            formats.append([""] * 12)
            kinds.append(["blank"] * 12)
        result[title] = {"rows": rows, "formats": formats, "kinds": kinds}
    return result


def _hash(rows) -> str:
    return hashlib.sha256(_dump(rows).encode("utf-8")).hexdigest()


def _preflight(source_id, copy_id, metadata, grid):
    marker_sheets, raw_rows = [], {}
    for item in metadata:
        title = item["title"]
        rows = copy.deepcopy(grid[title]["rows"])
        _need(rows[0][:8] == list(INPUT_HEADERS), f"{title} 탭 A1:H1 제목이 다릅니다.")
        chat = rows[0][8:12]
        _need(
            chat in (list(CHAT_RESULT_HEADERS), [""] * 4),
            f"{title} 탭 I1:L1 Chat 제목이 일부 없거나 순서가 다릅니다.",
        )
        blank_chat_headers = chat == [""] * 4
        for row_index in range(1, len(rows)):
            for column_index in range(8, 12):
                kind = grid[title]["kinds"][row_index][column_index]
                if blank_chat_headers:
                    _need(
                        kind == "blank",
                        f"{title} 탭의 빈 Chat 제목 아래 기존 값이 남아 있습니다.",
                    )
                elif column_index == ATTEMPTED_AT_COLUMN and kind == "number":
                    # 시도시각은 프로그램이 글자로 쓰지만 구글이 날짜로 알아보고
                    # serial 숫자로 담는다. 날짜 서식이 입혀진 숫자만 정상으로 본다.
                    number_type = _text(
                        grid[title]["formats"][row_index][column_index]
                    ).upper()
                    _need(
                        number_type in DATE_NUMBER_FORMATS,
                        f"{title} 탭 Chat 시도시각 칸의 숫자가 날짜 서식이 아닙니다.",
                    )
                else:
                    _need(
                        kind in {"blank", "string"},
                        f"{title} 탭 Chat 결과 칸은 글자 또는 빈 셀이어야 합니다.",
                    )
        marker_rows = copy.deepcopy(rows)
        for index in range(1, len(marker_rows)):
            if marker_rows[index][0] != "":
                marker_rows[index][0] = normalize_google_sheet_date(
                    marker_rows[index][0], grid[title]["formats"][index][0]
                )
        marker_sheets.append({"sheet_name": title, "rows": marker_rows})
        raw_rows[title] = rows
    plan = plan_signature_migration(
        copy_id=copy_id,
        expected_copy_id=copy_id,
        source_id=source_id,
        monthly_sheets=marker_sheets,
    )
    expected = copy.deepcopy(raw_rows)
    by_title = {item["title"]: item for item in metadata}
    updates = []
    for change in plan["changes"]:
        title = change["sheet_name"]
        row = int(change["row_number"])
        column = int(change["column_number"])
        value = str(change["new_value"])
        expected[title][row - 1][column - 1] = value
        updates.append({
            "updateCells": {
                "range": {
                    "sheetId": by_title[title]["sheet_id"],
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": column - 1,
                    "endColumnIndex": column,
                },
                "rows": [{"values": [{"userEnteredValue": {"stringValue": value}}]}],
                "fields": "userEnteredValue",
            }
        })
    checks = []
    for item in metadata:
        title = item["title"]
        post = [[""] * 12] + expected[title]
        complete = copy.deepcopy(post)
        # 다 끝난 1행 모습이다 — A열 이름표, B열(합친 입력칸의 첫 칸) 안내 문구.
        complete[0][0] = AI_INPUT_LABEL
        complete[0][1] = AI_INPUT_HINT
        checks.append({
            "title": title,
            "sheet_id": item["sheet_id"],
            "row_count": item["row_count"],
            "pre_hash": _hash(raw_rows[title]),
            "post_hash": _hash(post),
            "complete_hash": _hash(complete),
        })
    return {"months": checks, "marker_change_count": len(updates)}, updates


def _inserts(metadata):
    return [
        {
            "insertDimension": {
                "range": {
                    "sheetId": item["sheet_id"],
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "inheritFromBefore": False,
            }
        }
        for item in metadata
    ]


def _input_row_cells():
    """1행 열여섯 칸을 통째로 만든다.

    1행은 제목 줄 위에 끼워 넣은 줄이라 아래 제목 줄의 진한 파랑을 그대로 물려받는다.
    앞 여덟 칸만 다시 칠하면 I열부터 P열까지 파란 채로 남으므로 P열까지 모두 덮어쓴다.
    """
    label = {
        "userEnteredValue": {"stringValue": AI_INPUT_LABEL},
        "userEnteredFormat": {
            "backgroundColor": {"red": 0.91, "green": 0.95, "blue": 1.0},
            "textFormat": {"bold": True},
            "horizontalAlignment": "LEFT",
        },
    }
    box = {
        "userEnteredValue": {"stringValue": AI_INPUT_HINT},
        "userEnteredFormat": {
            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
            "textFormat": {"bold": False, "foregroundColor": HINT_COLOR},
            "horizontalAlignment": "LEFT",
        },
    }
    blank = {
        "userEnteredValue": {"stringValue": ""},
        "userEnteredFormat": {
            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
            "textFormat": {"bold": False},
            "horizontalAlignment": "LEFT",
        },
    }
    return [label, box] + [copy.deepcopy(blank) for _ in range(ROW_ONE_COLUMN_COUNT - 2)]


def _ui(metadata):
    requests = []
    for item in metadata:
        row = {
            "sheetId": item["sheet_id"],
            "startRowIndex": 0,
            "endRowIndex": 1,
            "startColumnIndex": 0,
            "endColumnIndex": ROW_ONE_COLUMN_COUNT,
        }
        requests += [
            {
                "updateCells": {
                    "range": row,
                    "rows": [{"values": _input_row_cells()}],
                    "fields": (
                        "userEnteredValue,userEnteredFormat."
                        "(backgroundColor,textFormat,horizontalAlignment)"
                    ),
                }
            },
            {
                "mergeCells": {
                    "range": {
                        "sheetId": item["sheet_id"],
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": AI_INPUT_BOX_FIRST_COLUMN,
                        "endColumnIndex": AI_INPUT_BOX_LAST_COLUMN + 1,
                    },
                    "mergeType": "MERGE_ALL",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": item["sheet_id"],
                        "dimension": "ROWS",
                        "startIndex": 0,
                        "endIndex": 1,
                    },
                    "properties": {"pixelSize": 34},
                    "fields": "pixelSize",
                }
            },
        ]
    return requests


def _batch(runner, workdir, gws, copy_id, requests):
    return _call(
        runner, workdir, gws, ["sheets", "spreadsheets", "batchUpdate"],
        {"spreadsheetId": copy_id},
        {"requests": requests},
    )


def _batch_ok(reply, copy_id, request_count):
    replies = reply.get("replies") if isinstance(reply, dict) else None
    return (
        isinstance(reply, dict)
        and bool(reply)
        and _text(reply.get("spreadsheetId")) == copy_id
        and isinstance(replies, list)
        and len(replies) == request_count
        and all(isinstance(item, dict) for item in replies)
    )


def _stored_metadata(status):
    checks = status.get("checks")
    _need(isinstance(checks, dict) and isinstance(checks.get("months"), list), "행 삽입 전 확인 기록이 없습니다.")
    result, seen = [], set()
    for item in checks["months"]:
        _need(isinstance(item, dict), "월 탭 확인 기록 모양이 올바르지 않습니다.")
        title = _text(item.get("title"))
        sheet_id, row_count = item.get("sheet_id"), item.get("row_count")
        _need(
            title and title not in seen
            and isinstance(sheet_id, int) and not isinstance(sheet_id, bool)
            and isinstance(row_count, int) and not isinstance(row_count, bool)
            and row_count >= 2,
            "월 탭 확인 기록 값이 올바르지 않습니다.",
        )
        for key in ("pre_hash", "post_hash", "complete_hash"):
            _need(
                re.fullmatch(r"[0-9a-f]{64}", _text(item.get(key))),
                "월 탭 확인값이 올바르지 않습니다.",
            )
        seen.add(title)
        result.append({"title": title, "sheet_id": sheet_id, "row_count": row_count})
    _need(result, "확인할 월 탭이 없습니다.")
    return result


def _shape(status, grid):
    matched = {"pre": True, "post": True, "complete": True}
    for item in status["checks"]["months"]:
        current = _hash(grid[item["title"]]["rows"])
        for name in matched:
            matched[name] &= current == item[f"{name}_hash"]
    found = [name for name, value in matched.items() if value]
    return found[0] if len(found) == 1 else "ambiguous"


def _finish(status, writer, path):
    detail = (
        "비공개 사본의 표식·한 행·1행 입력칸 준비를 마쳤습니다. "
        "옛 Code.gs가 남아 있어 연결 변경과 일반 사용은 아직 보류합니다."
    )
    complete = _state_as(status, "complete", detail=detail)
    if not _save(writer, path, complete):
        return _result("ui_check_required", "완료 기록을 저장하지 못해 다시 쓰지 않습니다.", complete)
    return _result("complete", detail, complete)


def _resume(status, writer, path, runner, workdir, gws, account):
    copy_id = _text(status.get("copy_spreadsheet_id"))
    source_id = _text(status.get("source_spreadsheet_id"))
    if not copy_id or copy_id == source_id:
        return _result("stopped", "원본과 다른 사본 ID를 확인하지 못했습니다.", status)
    try:
        _current_copy(runner, workdir, gws, status, account)
        _private_copy(runner, workdir, gws, copy_id, account)
        stored = _stored_metadata(status)
        current = _metadata(runner, workdir, gws, copy_id, [item["title"] for item in stored])
        _need(
            [(item["title"], item["sheet_id"]) for item in current]
            == [(item["title"], item["sheet_id"]) for item in stored],
            "월 탭 Google 번호가 행 삽입 전과 다릅니다.",
        )
        row_deltas = [
            current_item["row_count"] - stored_item["row_count"]
            for current_item, stored_item in zip(current, stored)
        ]
        _need(
            len(set(row_deltas)) == 1 and row_deltas[0] in {0, 1},
            "월 탭 줄 수가 삽입 전 또는 삽입 후 모양과 다릅니다.",
        )
        row_delta = row_deltas[0]
        shape = _shape(
            status,
            _grid(
                runner,
                workdir,
                gws,
                copy_id,
                current,
                minimum=3 if row_delta == 1 else 2,
            ),
        )
        _need(
            (shape == "pre" and row_delta == 0)
            or (shape in {"post", "complete"} and row_delta == 1)
            or shape == "ambiguous",
            "월 탭 줄 수와 현재 내용 모양이 서로 맞지 않습니다.",
        )
    except Exception as exc:
        return _result("layout_check_required", f"현재 줄 모양을 확인하지 못해 더 쓰지 않습니다: {exc}", status)
    old_state = _text(status.get("state"))
    if shape == "complete":
        return _finish(status, writer, path)
    if old_state in {"ui_applying", "ui_check_required"}:
        stopped = _state_as(status, "ui_check_required")
        _save(writer, path, stopped)
        return _result("ui_check_required", "1행 입력칸 요청을 다시 보내지 않습니다.", stopped)
    if shape != "post":
        stopped = _state_as(status, "layout_check_required")
        _save(writer, path, stopped)
        return _result("layout_check_required", "행 삽입 요청을 다시 보내지 않고 직접 확인을 기다립니다.", stopped)
    ready = _state_as(status, "layout_ready")
    if not _save(writer, path, ready):
        return _result("stopped", "행 삽입 확인을 저장하지 못해 1행에는 쓰지 않았습니다.", ready)
    applying = _state_as(ready, "ui_applying")
    if not _save(writer, path, applying):
        return _result("stopped", "1행 쓰기 전 진행 기록을 저장하지 못했습니다.", applying)
    try:
        ui_requests = _ui(current)
        reply = _batch(runner, workdir, gws, copy_id, ui_requests)
        _need(
            _batch_ok(reply, copy_id, len(ui_requests)),
            "1행 입력칸 응답 모양이 올바르지 않습니다.",
        )
    except Exception:
        uncertain = _state_as(applying, "ui_check_required")
        _save(writer, path, uncertain)
        return _result("ui_check_required", "1행 입력칸 요청 결과가 불명확해 다시 쓰지 않습니다.", uncertain)
    return _finish(applying, writer, path)


def _prepare_once(
    config_dir,
    install_record,
    current_account,
    installed_account,
    runner,
    workdir,
    gws,
    now,
    writer,
):
    path = attendance_copy_status_path(config_dir)
    try:
        record = validate_attendance_install_record(dict(install_record))
    except Exception as exc:
        return _result("stopped", f"기존 출결 설치 기록을 엄격히 읽지 못했습니다: {exc}")
    source_id = _text(record.get("spreadsheet_id"))
    source_url = _text(record.get("spreadsheet_url"))
    account, installed = _text(current_account), _text(installed_account)
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]+", source_id)
        or not _sheet_url(source_url, source_id)
    ):
        return _result("stopped", "원본 Google Sheet ID와 주소가 맞지 않습니다.")
    if not account:
        return _result("stopped", "현재 Google 로그인 계정을 확인하지 못했습니다.")
    if installed and _email(installed) != _email(account):
        return _result("stopped", "설치 때 계정과 현재 Google 계정이 다릅니다.")
    try:
        status = _load_state(path)
    except AttendanceCopyError as exc:
        return _result("stopped", str(exc))
    if status:
        if (
            _text(status.get("source_spreadsheet_id")) != source_id
            or _email(status.get("account")) != _email(account)
        ):
            return _result("stopped", "진행 중인 사본의 원본 또는 계정이 현재 값과 다릅니다.", status)
        state = _text(status.get("state"))
        if state == "complete":
            return _result("complete", _text(status.get("detail")), status)
        if state in {"copying", "copy_check_required"}:
            return _result("copy_check_required", "사본 결과를 직접 확인할 때까지 다시 만들지 않습니다.", status)
        if state in {
            "layout_applying", "layout_check_required", "layout_ready",
            "ui_applying", "ui_check_required",
        }:
            return _resume(status, writer, path, runner, workdir, gws, account)
        if state != "copy_ready":
            return _result("stopped", "알 수 없는 사본 진행 상태라 계속하지 않습니다.", status)
    else:
        try:
            source = _call(
                runner, workdir, gws, ["drive", "files", "get"],
                {"fileId": source_id, "fields": SOURCE_FIELDS, "supportsAllDrives": True},
            )
            source_name = _source_name(source, source_id, account, installed)
            moment = now()
            _need(isinstance(moment, dt.datetime), "사본 이름에 넣을 현재 시각이 올바르지 않습니다.")
            copy_name = (
                f"{source_name} - AI 입력 준비 사본 "
                f"({moment.strftime('%Y-%m-%d %H%M%S')})"
            )
        except Exception as exc:
            return _result("stopped", f"원본을 복사하기 전 확인하지 못했습니다: {exc}")
        copying = _base_state("copying", account, source_id, copy_name)
        if not _save(writer, path, copying):
            return _result("stopped", "사본 요청 전 상태를 저장하지 못해 복사하지 않았습니다.", copying)
        try:
            reply = _call(
                runner, workdir, gws, ["drive", "files", "copy"],
                {
                    "fileId": source_id,
                    "ignoreDefaultVisibility": True,
                    "supportsAllDrives": True,
                    "fields": COPY_FIELDS,
                },
                {"name": copy_name, "parents": ["root"]},
            )
            copy_id, copy_url = _copy_values(reply, source_id, copy_name, account)
        except Exception as exc:
            uncertain = _state_as(copying, "copy_check_required", detail=str(exc))
            _save(writer, path, uncertain)
            return _result("copy_check_required", "사본 결과가 불명확해 다시 만들지 않습니다.", uncertain)
        status = _base_state(
            "copy_ready", account, source_id, copy_name, copy_id, copy_url
        )
        if not _save(writer, path, status):
            return _result("copy_check_required", "사본 응답 뒤 상태 저장에 실패해 다시 만들지 않습니다.", copying)
    copy_id = _text(status.get("copy_spreadsheet_id"))
    if not copy_id or copy_id == source_id:
        return _result("stopped", "원본과 다른 사본 ID를 확인하지 못했습니다.", status)
    try:
        _current_copy(runner, workdir, gws, status, account)
        _private_copy(runner, workdir, gws, copy_id, account)
        month_names = _months(runner, workdir, gws, copy_id)
        metadata = _metadata(runner, workdir, gws, copy_id, month_names)
        checks, marker_updates = _preflight(
            source_id, copy_id, metadata,
            _grid(runner, workdir, gws, copy_id, metadata),
        )
    except Exception as exc:
        return _result("stopped", f"사본 전체 확인 실패로 Sheet에는 쓰지 않았습니다: {exc}", status)
    applying = _state_as(status, "layout_applying", checks=checks)
    if not _save(writer, path, applying):
        return _result("stopped", "행 삽입 전 상태를 저장하지 못해 Sheet에는 쓰지 않았습니다.", applying)
    try:
        layout_requests = marker_updates + _inserts(metadata)
        reply = _batch(runner, workdir, gws, copy_id, layout_requests)
        _need(
            _batch_ok(reply, copy_id, len(layout_requests)),
            "행 삽입 응답 모양이 올바르지 않습니다.",
        )
    except Exception:
        uncertain = _state_as(applying, "layout_check_required")
        _save(writer, path, uncertain)
        return _result("layout_check_required", "행 삽입 결과가 불명확해 다시 넣지 않습니다.", uncertain)
    ready = _state_as(applying, "layout_ready")
    if not _save(writer, path, ready):
        return _result("layout_check_required", "행 삽입 응답 뒤 상태를 저장하지 못했습니다.", applying)
    return _resume(ready, writer, path, runner, workdir, gws, account)


def prepare_attendance_copy(
    config_dir: Path,
    install_record: Mapping[str, Any],
    *,
    current_account: str,
    installed_account: str = "",
    runner: Runner = default_runner,
    workdir: Path | None = None,
    gws: str = "gws",
    now: Callable[[], dt.datetime] | None = None,
    state_writer: Writer | None = None,
    lock_factory: Callable[[Path], Any] | None = None,
) -> AttendanceCopyResult:
    """호출자가 엄격히 읽은 설치기록과 현재 Google 계정으로 사본 하나만 준비한다."""
    config_dir = Path(config_dir)
    lock = lock_factory or _locked
    with lock(config_dir):
        return _prepare_once(
            config_dir,
            install_record,
            current_account,
            installed_account,
            runner,
            Path(workdir or SCRIPTS_DIR),
            _text(gws) or "gws",
            now or dt.datetime.now,
            state_writer or _atomic_state,
        )


__all__ = [
    "AI_INPUT_PLACEHOLDER",
    "AttendanceCopyError",
    "AttendanceCopyResult",
    "attendance_copy_status_path",
    "prepare_attendance_copy",
]
