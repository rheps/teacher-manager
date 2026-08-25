"""출석부가 시트 안에서 남긴 AI 연결 확인 표시를 읽기만 한다."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Callable, Sequence


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import attendance_script_update


CommandRunner = Callable[[Sequence[str], Path], str]
MARKER_SETTING_KEY = "ATTENDANCE_AI_SETUP_VERIFICATION"
MARKER_SCHEMA_VERSION = 1
MARKER_SETUP_VERSION = "1"
EXPECTED_HANDLER_NAME = "onAttendanceAiEdit"
MARKER_RANGE = "'설정'!A1:B200"


@dataclass(frozen=True)
class AttendanceAiSetupStatus:
    ok: bool
    account_matches: bool
    spreadsheet_matches: bool
    key_present: bool
    target_matches: bool
    trigger_count: int
    setup_done: bool
    detail: str = ""
    state: str = "ai-action-required"


_ACTION_REQUIRED = (
    "출석부 안의 확인 표시를 찾지 못했어요. 새 정본을 열고 "
    "출결 업무 자동화 메뉴에서 AI 출결 입력 연결 확인을 한 번 눌러 주세요."
)


def _result(
    *,
    verified: bool,
    spreadsheet_matches: bool = False,
    trigger_count: int = 0,
) -> AttendanceAiSetupStatus:
    return AttendanceAiSetupStatus(
        ok=verified,
        account_matches=False,
        spreadsheet_matches=spreadsheet_matches,
        key_present=False,
        target_matches=verified,
        trigger_count=trigger_count if type(trigger_count) is int else 0,
        setup_done=verified,
        detail="" if verified else _ACTION_REQUIRED,
        state="verified" if verified else "ai-action-required",
    )


def _compact(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _marker_from_rows(rows: Any) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    matches: list[Any] = []
    for row in rows:
        if (
            isinstance(row, list)
            and len(row) >= 2
            and str(row[0] or "").strip() == MARKER_SETTING_KEY
        ):
            matches.append(row[1])
    if len(matches) != 1 or not isinstance(matches[0], str):
        return None
    try:
        marker = json.loads(matches[0])
    except (TypeError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def inspect_attendance_ai_setup(
    *,
    runner: CommandRunner,
    workdir: Path,
    gws_executable: str,
    spreadsheet_id: str,
) -> AttendanceAiSetupStatus:
    """Sheets API 한 번으로, 시트 메뉴 동작이 남긴 표시만 검증한다.

    이 함수는 Apps Script를 실행하거나 감지기를 만들지 않는다. 표시가 말해 주는
    것도 해당 시트 안의 메뉴 동작이 기록한 당시의 ID·함수명·개수뿐이다.
    """

    expected_id = str(spreadsheet_id or "").strip()
    gws = str(gws_executable or "").strip()
    if not (expected_id and gws and callable(runner)):
        return _result(verified=False)
    try:
        reply = attendance_script_update._run_one_json(
            runner,
            [
                gws,
                "sheets",
                "spreadsheets",
                "values",
                "get",
                "--params",
                _compact(
                    {
                        "spreadsheetId": expected_id,
                        "range": MARKER_RANGE,
                        "majorDimension": "ROWS",
                        "valueRenderOption": "UNFORMATTED_VALUE",
                    }
                ),
                "--format",
                "json",
            ],
            Path(workdir),
        )
        if not (
            isinstance(reply, dict)
            and isinstance(reply.get("range"), str)
            and reply.get("majorDimension") == "ROWS"
        ):
            return _result(verified=False)
        marker = _marker_from_rows(reply.get("values", []))
        if marker is None:
            return _result(verified=False)
        trigger_count = marker.get("trigger_count")
        spreadsheet_matches = marker.get("spreadsheet_id") == expected_id
        verified = (
            set(marker)
            == {
                "schema_version",
                "setup_version",
                "spreadsheet_id",
                "handler_name",
                "trigger_count",
                "success",
            }
            and marker.get("schema_version") == MARKER_SCHEMA_VERSION
            and marker.get("setup_version") == MARKER_SETUP_VERSION
            and spreadsheet_matches
            and marker.get("handler_name") == EXPECTED_HANDLER_NAME
            and type(trigger_count) is int
            and trigger_count == 1
            and marker.get("success") is True
        )
        return _result(
            verified=verified,
            spreadsheet_matches=spreadsheet_matches,
            trigger_count=trigger_count if type(trigger_count) is int else 0,
        )
    except Exception:
        return _result(verified=False)


__all__ = [
    "AttendanceAiSetupStatus",
    "EXPECTED_HANDLER_NAME",
    "MARKER_SCHEMA_VERSION",
    "MARKER_SETUP_VERSION",
    "MARKER_SETTING_KEY",
    "inspect_attendance_ai_setup",
]
