from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import date

from brity_bridge import process_win
from brity_bridge.history import HistoryStore
from brity_bridge.proposal_check import CheckedAction

DUPLICATE_KEY_PROPERTY = "brityBridgeKey"
TASK_NOTE_MARK = "BRITY-BRIDGE:"
# 시트 탭 이름·열 구성은 Code.gs(MESSENGER_*_SHEET_NAME, *_MESSAGE_QUEUE_HEADERS)와 같아야 한다.
PERSONAL_QUEUE_SHEET = "메신저 개인톡 내용"
CLASS_QUEUE_SHEET = "메신저 단체톡 내용"
_QUEUE_DATE_RE = re.compile(r"^(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\.?$")


@dataclass
class ActionResult:
    action: CheckedAction
    status: str  # created | duplicate | failed
    google_id: str = ""
    detail: str = ""


@dataclass
class ExecutionReport:
    results: list

    def _by_status(self, status: str) -> list:
        return [result for result in self.results if result.status == status]

    @property
    def created(self) -> list:
        return self._by_status("created")

    @property
    def duplicates(self) -> list:
        return self._by_status("duplicate")

    @property
    def failures(self) -> list:
        return self._by_status("failed")

    @property
    def all_done(self) -> bool:
        return not self.failures


def resolve_gws_command(command: list, which=None) -> list:
    # npm 전역 설치본은 Windows에 gws.cmd만 만들어서 맨이름 "gws"는
    # CreateProcess가 못 찾는다(WinError 2). 대시보드 resolve_gws와 같은
    # 폴백을 실행 직전에 적용하되, 경로·확장자를 직접 지정한 설정은 존중한다.
    if not command:
        return command
    head = command[0]
    if not isinstance(head, str) or "\\" in head or "/" in head or "." in head:
        return command
    which = which or shutil.which
    found = which(head) or which(head + ".cmd")
    if not found:
        return command
    return [found, *command[1:]]


def _default_runner(args: list[str]) -> str:
    code, output = process_win.run_captured(args)
    if code != 0:
        raise RuntimeError(f"gws 종료 코드 {code}: {output.strip()[-300:]}")
    return output


def _run_json(runner, args: list[str]) -> dict:
    reply = runner(args)
    try:
        # keyring 안내 줄이 섞여도 응답을 삼키면 안 된다 — 삼키면 성공한 등록이
        # 실패로 기록되고 중복 방지 표식도 못 읽는다.
        parsed = process_win.parse_first_json(reply)
    except ValueError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _probe_calendar_duplicate(runner, gws_command, action) -> str:
    params = {
        "calendarId": action.google_id,
        "privateExtendedProperty": f"{DUPLICATE_KEY_PROPERTY}={action.action_key}",
        "maxResults": 1,
    }
    args = gws_command + [
        "calendar", "events", "list",
        "--params", json.dumps(params, ensure_ascii=False),
        "--format", "json",
    ]
    items = _run_json(runner, args).get("items") or []
    return items[0].get("id", "") if items else ""


def _probe_task_duplicate(runner, gws_command, action) -> str:
    params = {"tasklist": action.google_id, "showCompleted": True, "maxResults": 100}
    args = gws_command + [
        "tasks", "tasks", "list",
        "--params", json.dumps(params, ensure_ascii=False),
        "--format", "json",
    ]
    mark = f"{TASK_NOTE_MARK}{action.action_key}"
    for item in _run_json(runner, args).get("items") or []:
        if mark in (item.get("notes") or ""):
            return item.get("id", "")
    return ""


def _insert_calendar(runner, gws_command, action) -> str:
    body = dict(action.payload)
    body["extendedProperties"] = {"private": {DUPLICATE_KEY_PROPERTY: action.action_key}}
    args = gws_command + [
        "calendar", "events", "insert",
        "--params", json.dumps({"calendarId": action.google_id}, ensure_ascii=False),
        "--json", json.dumps(body, ensure_ascii=False),
    ]
    return _run_json(runner, args).get("id", "")


def _insert_task(runner, gws_command, action) -> str:
    body = dict(action.payload)
    body.pop("due", None)
    notes = body.get("notes", "").rstrip()
    body["notes"] = f"{notes}\n\n{TASK_NOTE_MARK}{action.action_key}" if notes else f"{TASK_NOTE_MARK}{action.action_key}"
    args = gws_command + [
        "tasks", "tasks", "insert",
        "--params", json.dumps({"tasklist": action.google_id}, ensure_ascii=False),
        "--json", json.dumps(body, ensure_ascii=False),
    ]
    return _run_json(runner, args).get("id", "")


def _normalize_message_line(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _queue_date_key(value) -> str:
    """시트에 표시된 날짜 문구를 yyyy-mm-dd로 정규화한다 ('2026. 7. 15.' 포함)."""
    text = str(value or "").strip()
    match = _QUEUE_DATE_RE.match(text)
    if not match:
        return text
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _today_key() -> str:
    return date.today().isoformat()


def _values_get(runner, gws_command, spreadsheet_id: str, a1_range: str) -> list:
    args = gws_command + [
        "sheets", "spreadsheets", "values", "get",
        "--params", json.dumps(
            {"spreadsheetId": spreadsheet_id, "range": a1_range}, ensure_ascii=False
        ),
        "--format", "json",
    ]
    values = _run_json(runner, args).get("values")
    return values if isinstance(values, list) else []


def _values_append(runner, gws_command, spreadsheet_id: str, sheet_name: str, rows: list) -> None:
    args = gws_command + [
        "sheets", "spreadsheets", "values", "append",
        "--params", json.dumps(
            {
                "spreadsheetId": spreadsheet_id,
                "range": f"'{sheet_name}'!A1",
                "valueInputOption": "RAW",
                "insertDataOption": "INSERT_ROWS",
            },
            ensure_ascii=False,
        ),
        "--json", json.dumps({"values": rows}, ensure_ascii=False),
        "--format", "json",
    ]
    _run_json(runner, args)


def _append_notice(runner, gws_command, action) -> str:
    """학생 안내를 Google Sheet에 직접 적는다.

    예전에는 Apps Script 실행(scripts.run)을 썼지만, 그 API는 호출한 프로그램과
    스크립트가 같은 클라우드 프로젝트를 공유해야 해서(사용자가 콘솔에서 수동
    전환해야만 충족) 일반 설치에서는 항상 403이 났다. Sheets API 직접 기록은
    이미 쓰는 스프레드시트 권한만으로 동작한다. 열 구성·기본값·단체톡 중복
    규칙은 Code.gs의 appendAnalyzedMessageQueueItemsForAutomation과 같다.
    """
    today = _today_key()
    content = _normalize_message_line(action.payload.get("content"))
    if action.target == "personal":
        row = [
            today, "", str(action.payload.get("name", "")).strip(), "기타",
            content, "자동분석", "확인필요", "", "", "",
        ]
        _values_append(runner, gws_command, action.google_id, PERSONAL_QUEUE_SHEET, [row])
        return "created"
    # 단체톡 — 같은 날짜·같은 내용이 '보냄' 아닌 상태로 이미 있으면 다시 넣지 않는다.
    existing = _values_get(
        runner, gws_command, action.google_id, f"'{CLASS_QUEUE_SHEET}'!A2:G"
    )
    for sheet_row in existing:
        row_date = _queue_date_key(sheet_row[0] if len(sheet_row) > 0 else "")
        row_text = _normalize_message_line(sheet_row[2] if len(sheet_row) > 2 else "")
        row_status = str(sheet_row[4] if len(sheet_row) > 4 else "").strip()
        if row_date == today and row_text == content and row_status != "보냄":
            return "duplicate"
    row = [today, "기타", content, "자동분석", "확인필요", "", ""]
    _values_append(runner, gws_command, action.google_id, CLASS_QUEUE_SHEET, [row])
    return "created"


def execute_actions(
    actions: list,
    history: HistoryStore,
    gws_command: list,
    runner=None,
    source_hash: str = "",
) -> ExecutionReport:
    if runner is None:
        # 실제 subprocess로 나가는 경우에만 실행 파일을 해석한다.
        # 시험용 러너가 주입되면 명령은 러너가 받은 그대로 둔다.
        gws_command = resolve_gws_command(list(gws_command))
    runner = runner or _default_runner
    known_keys = history.completed_keys(source_hash) if source_hash else set()
    results = []
    for action in actions:
        if action.action_key in known_keys:
            entry = history.entry(source_hash) or {}
            recorded = entry.get("actions", {}).get(action.action_key, {})
            results.append(
                ActionResult(action, "duplicate", recorded.get("google_id", ""), "이미 등록된 항목")
            )
            continue
        if action.kind == "notice" and not action.google_id:
            results.append(
                ActionResult(
                    action,
                    "failed",
                    "",
                    "학생 안내 시트가 준비되지 않았어요 · 처음 설정 필요",
                )
            )
            continue
        try:
            if action.kind == "calendar":
                existing = _probe_calendar_duplicate(runner, gws_command, action)
                if existing:
                    results.append(ActionResult(action, "duplicate", existing, "Google에 같은 항목이 있음"))
                    continue
                created_id = _insert_calendar(runner, gws_command, action)
            elif action.kind == "task":
                existing = _probe_task_duplicate(runner, gws_command, action)
                if existing:
                    results.append(ActionResult(action, "duplicate", existing, "Google에 같은 항목이 있음"))
                    continue
                created_id = _insert_task(runner, gws_command, action)
            else:  # notice — 시트 쪽 dedup(단체톡)과 history가 중복을 막는다
                status = _append_notice(runner, gws_command, action)
                if status == "duplicate":
                    results.append(ActionResult(action, "duplicate", "", "시트에 같은 안내가 있음"))
                    continue
                created_id = ""  # 시트 줄이라 Google ID 없음
        except Exception as error:  # noqa: BLE001 - 개별 항목 실패는 보고서로 넘긴다
            results.append(ActionResult(action, "failed", "", str(error)))
            continue
        results.append(ActionResult(action, "created", created_id))
        if source_hash:
            history.record_action(source_hash, action.action_key, action.kind, created_id)
            history.save()
    return ExecutionReport(results=results)
