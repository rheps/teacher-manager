"""기존 출결 Chat 발송 표식을 행 번호 없는 표식으로 바꿀 계획을 만든다."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


CHAT_RESULT_HEADERS = (
    "Google Chat\n발송상태",
    "Google Chat\n발송시각",
    "Google Chat\n결과",
    "Google Chat\n내용기준",
)
LEGACY_CHAT_RESULT_HEADERS = (
    "Google Chat 발송상태",
    "Google Chat 시도시각",
    "Google Chat 결과",
    "Google Chat 내용기준",
)


class AttendanceChatMarkerError(ValueError):
    """기존 발송 기록을 안전하게 해석할 수 없을 때 발생한다."""


def normalize_google_sheet_date(
    value: Any,
    number_format_type: Any,
) -> Any:
    """날짜 형식이 확인된 Google serial만 날짜로 바꾼다."""

    format_type = _text(number_format_type).strip().upper()
    if format_type not in {"DATE", "DATE_TIME"}:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AttendanceChatMarkerError(
            "날짜 형식인 A열에서 정상 Google 날짜 숫자를 읽지 못했습니다."
        )
    try:
        serial = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise AttendanceChatMarkerError(
            "날짜 형식인 A열에서 정상 Google 날짜 숫자를 읽지 못했습니다."
        ) from exc
    if not math.isfinite(serial):
        raise AttendanceChatMarkerError(
            "날짜 형식인 A열에서 정상 Google 날짜 숫자를 읽지 못했습니다."
        )
    try:
        return (
            dt.datetime(1899, 12, 30)
            + dt.timedelta(days=serial)
        ).date()
    except (OverflowError, TypeError, ValueError) as exc:
        raise AttendanceChatMarkerError(
            "Google 날짜 숫자를 실제 날짜로 바꾸지 못했습니다."
        ) from exc


def _text(value: Any) -> str:
    if value is None or value is False:
        return ""
    if value is True:
        return "true"
    if isinstance(value, (int, float)):
        if value == 0 or (isinstance(value, float) and math.isnan(value)):
            return ""
        if isinstance(value, float):
            if math.isinf(value):
                return "Infinity" if value > 0 else "-Infinity"
            if value.is_integer():
                return str(int(value))
    return str(value)


def _row_value(row: Sequence[Any], index: int) -> Any:
    return row[index] if index < len(row) else ""


def _date_key(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = _text(value).strip()
    match = re.fullmatch(
        r"(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})",
        text,
    )
    if not match:
        return text
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _request_id(parts: Sequence[Any]) -> str:
    source = "|".join(_text(value) for value in parts)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"req-{digest[:48]}"


def _require_row(row: Sequence[Any]) -> Sequence[Any]:
    if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
        raise AttendanceChatMarkerError("출결 행은 칸 목록이어야 합니다.")
    return row


def legacy_signature_v1(
    sheet_name: Any,
    row_number: Any,
    row: Sequence[Any],
) -> str:
    """현재 사본의 위치에서 옛 행 번호 포함 표식을 다시 계산한다."""

    values = _require_row(row)
    return _request_id(
        [
            sheet_name,
            row_number,
            _date_key(_row_value(values, 0)),
            _row_value(values, 1),
            _row_value(values, 6),
            _row_value(values, 7),
        ]
    )


def signature_v2(row: Sequence[Any]) -> str:
    """행 번호와 탭 이름 없이 날짜·학생·G·H 칸으로 새 표식을 만든다."""

    values = _require_row(row)
    payload = json.dumps(
        [
            "attendance-chat-v2",
            _date_key(_row_value(values, 0)),
            _text(_row_value(values, 1)).strip(),
            _text(_row_value(values, 6)).strip(),
            _text(_row_value(values, 7)).strip(),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _request_id([payload])


def _result_columns(header: Sequence[Any]) -> dict[str, int] | None:
    normalized = [" ".join(_text(value).split()) for value in header]
    accepted = [
        {
            " ".join(CHAT_RESULT_HEADERS[index].split()),
            " ".join(LEGACY_CHAT_RESULT_HEADERS[index].split()),
        }
        for index in range(len(CHAT_RESULT_HEADERS))
    ]
    positions = [
        [index + 1 for index, value in enumerate(normalized) if value in names]
        for names in accepted
    ]
    if sum(len(found) for found in positions) == 0:
        return None
    if any(len(found) != 1 for found in positions):
        raise AttendanceChatMarkerError(
            "Google Chat 발송 결과 제목이 일부 없거나 중복되어 있습니다."
        )
    start = positions[0][0]
    if any(found[0] != start + index for index, found in enumerate(positions)):
        raise AttendanceChatMarkerError(
            "Google Chat 발송 결과 제목의 순서가 기존과 다릅니다."
        )
    return {
        "status": start,
        "attempted_at": start + 1,
        "result": start + 2,
        "signature": start + 3,
    }


def _sheet_plan(sheet: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(sheet, Mapping):
        raise AttendanceChatMarkerError("월 탭 읽기 결과의 모양이 맞지 않습니다.")
    sheet_name = _text(sheet.get("sheet_name")).strip()
    rows = sheet.get("rows")
    if not sheet_name:
        raise AttendanceChatMarkerError("이름이 없는 월 탭은 확인할 수 없습니다.")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise AttendanceChatMarkerError(f"{sheet_name} 탭의 행 목록이 없습니다.")

    if not rows:
        return {
            "sheet_name": sheet_name,
            "rows_checked": 0,
            "already_v2": 0,
            "changes": [],
        }
    header = _require_row(rows[0])
    columns = _result_columns(header)
    result = {
        "sheet_name": sheet_name,
        "rows_checked": max(len(rows) - 1, 0),
        "already_v2": 0,
        "changes": [],
    }
    if columns is None:
        return result

    status_index = columns["status"] - 1
    signature_index = columns["signature"] - 1
    for row_number, raw_row in enumerate(rows[1:], start=2):
        row = _require_row(raw_row)
        for column_index, column_name in (
            (1, "B열 학생"),
            (6, "G열 신고서"),
            (7, "H열 첨부"),
        ):
            value = _row_value(row, column_index)
            if value is not None and not isinstance(value, str):
                raise AttendanceChatMarkerError(
                    f"{sheet_name} {row_number}행의 {column_name} 값이 글자가 아닙니다."
                )
        status = _text(_row_value(row, status_index)).strip()
        saved = _text(_row_value(row, signature_index)).strip()
        if status == "보냄":
            next_signature = signature_v2(row)
            if saved == next_signature:
                result["already_v2"] += 1
                continue
            # 표식 칸이 완전히 비어 있는 보낸 줄이 있다. 그 줄을 보낼 당시에는
            # 프로그램이 내용기준을 적지 않았다. 비어 있으면 재발송을 막지 못하므로
            # 그 줄의 값으로 계산한 새 표식을 채운다. 값이 들어 있는데 다르면
            # 누군가 고친 것이므로 그때는 그대로 멈춘다.
            if saved:
                legacy = legacy_signature_v1(sheet_name, row_number, row)
            else:
                legacy = ""
            if saved != legacy:
                raise AttendanceChatMarkerError(
                    f"{sheet_name} {row_number}행의 기존 발송 표식이 맞지 않습니다."
                )
            result["changes"].append(
                {
                    "sheet_name": sheet_name,
                    "row_number": row_number,
                    "column_number": columns["signature"],
                    "old_value": saved,
                    "new_value": next_signature,
                }
            )
            continue
        if status == "발송중":
            raise AttendanceChatMarkerError(
                f"{sheet_name} {row_number}행이 발송중이라 멈춥니다."
            )
        if status in {"", "실패", "연결필요"}:
            if saved:
                raise AttendanceChatMarkerError(
                    f"{sheet_name} {row_number}행의 상태와 발송 표식이 맞지 않습니다."
                )
            continue
        raise AttendanceChatMarkerError(
            f"{sheet_name} {row_number}행의 발송 상태를 알 수 없습니다: {status}"
        )
    return result


def plan_signature_migration(
    *,
    copy_id,
    expected_copy_id,
    source_id,
    monthly_sheets,
):
    """모든 월 탭을 먼저 확인하고 표식 셀 변경 목록만 돌려준다.

    Task 3은 A열의 raw 값을 ``effectiveFormat.numberFormat.type``과 함께
    읽어야 한다.
    그 형식이 DATE/DATE_TIME이면 ``normalize_google_sheet_date``로 바꾼 뒤
    이 함수에 rows를 넘긴다. 지역별 표시 문자열은 추측해서 날짜로 바꾸지 않는다.
    """

    actual = _text(copy_id).strip()
    expected = _text(expected_copy_id).strip()
    source = _text(source_id).strip()
    if not actual or not expected or not source:
        raise AttendanceChatMarkerError("원본과 사본 Sheet ID를 모두 확인해야 합니다.")
    if actual != expected or expected == source:
        raise AttendanceChatMarkerError(
            "지정한 사본이 아니거나 원본과 같은 Sheet라서 멈춥니다."
        )
    if isinstance(monthly_sheets, (str, bytes)) or not isinstance(
        monthly_sheets, Iterable
    ):
        raise AttendanceChatMarkerError("월 탭 읽기 결과가 없습니다.")

    sheet_plans = [_sheet_plan(item) for item in monthly_sheets]
    return {
        "sheets_checked": len(sheet_plans),
        "rows_checked": sum(item["rows_checked"] for item in sheet_plans),
        "already_v2": sum(item["already_v2"] for item in sheet_plans),
        "changes": [
            change
            for item in sheet_plans
            for change in item["changes"]
        ],
    }
