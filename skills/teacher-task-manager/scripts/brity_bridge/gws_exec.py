from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from brity_bridge import gws_env, process_win, tool_runtime
from brity_bridge.google_account import (
    GOEDU_ACCOUNT_REQUIRED_MESSAGE,
    extract_email,
    require_goedu_email,
)
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


def resolve_gws_command(
    _legacy_command: list | None = None,
    *,
    gws_executable: str | None = None,
    runtime_run_command=None,
) -> list[str]:
    """예전 설정 문자열은 무시하고 검증된 실행 파일 하나만 돌려준다."""
    if gws_executable:
        executable = str(gws_executable)
    else:
        options = (
            {"run_command": runtime_run_command}
            if runtime_run_command is not None
            else {}
        )
        executable = str(tool_runtime.resolve_gws_executable(**options))
    if not executable or not Path(executable).is_absolute():
        raise ValueError("Google Workspace CLI 실행 파일은 전체 경로여야 합니다.")
    return [executable]


def _default_runner(
    args: list[str],
    *,
    base_environ=None,
) -> str:
    base = os.environ if base_environ is None else base_environ
    if gws_env.unsafe_account_storage_overrides(base):
        raise gws_env.GwsAccountStorageError(
            gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
        )
    child_env = gws_env.gws_environ(base)
    code, output = process_win.run_captured(args, env=child_env)
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


def _require_goedu_account(runner, gws_command: list[str]) -> str:
    """자료를 읽거나 쓰기 직전에 현재 로그인 계정을 한 번 확인한다."""

    output = runner(gws_command + ["auth", "status"])
    return require_goedu_email(extract_email(output))


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
    gws_command: list | None = None,
    runner=None,
    source_hash: str = "",
    *,
    gws_executable: str | None = None,
    notice_unavailable_message: str = "학생 안내 시트가 준비되지 않았어요 · 처음 설정 필요",
    expected_account: str = "",
    notice_preflight=None,
) -> ExecutionReport:
    runtime_run_command = None
    if runner is None:
        # 실행 파일을 고르는 --version 확인도 GWS 명령이다. 다른 계정 저장소를
        # 발견하면 resolver보다 먼저 끝내고, 정상일 때도 현재 계정용 환경만 준다.
        base = dict(os.environ)
        try:
            if gws_env.unsafe_account_storage_overrides(base):
                raise gws_env.GwsAccountStorageError(
                    gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
                )
            runtime_environment = gws_env.gws_environ(base)
        except gws_env.GwsAccountStorageError as error:
            detail = str(error) or gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
            return ExecutionReport(
                results=[
                    ActionResult(action, "failed", "", detail)
                    for action in actions
                ]
            )

        def runtime_run_command(args):
            return process_win.run_captured(args, env=runtime_environment)

    gws_command = resolve_gws_command(
        list(gws_command or []),
        gws_executable=gws_executable,
        runtime_run_command=runtime_run_command,
    )
    runner = runner or _default_runner
    known_keys = history.completed_keys(source_hash) if source_hash else set()
    results = []
    account_checked = False
    account_error = ""
    notice_preflight_checked = False
    notice_preflight_error = ""
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
                    str(notice_unavailable_message or "학생 안내 시트가 준비되지 않았어요 · 처음 설정 필요"),
                )
            )
            continue
        if not account_checked:
            account_checked = True
            try:
                current_account = _require_goedu_account(runner, gws_command)
            except gws_env.GwsAccountStorageError as error:
                account_error = str(error) or gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
            except Exception:  # noqa: BLE001 - 계정·명령 원문을 사용자 화면에 내보내지 않는다
                account_error = GOEDU_ACCOUNT_REQUIRED_MESSAGE
            else:
                owner = str(expected_account or "").strip()
                if owner and current_account.casefold() != owner.casefold():
                    account_error = "처음 준비하던 Google 계정으로 다시 로그인해 주세요."
        if account_error:
            results.append(ActionResult(action, "failed", "", account_error))
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
                # 화면을 연 뒤 Google 쪽 Apps Script가 바뀔 수도 있다. 실제 학생
                # 안내 줄을 쓰기 직전에 한 번만 다시 읽고, 모호하면 아무것도 쓰지
                # 않는다. 계정 확인보다 먼저 이 검사를 돌리면 다른 계정 자료를
                # 읽을 수 있으므로 반드시 위의 계정 확인 뒤에 둔다.
                if notice_preflight is not None and not notice_preflight_checked:
                    notice_preflight_checked = True
                    try:
                        ok, detail = notice_preflight(runner, gws_command[0])
                    except Exception:  # noqa: BLE001 - 모호하면 쓰지 않는 쪽으로 멈춘다
                        ok, detail = False, ""
                    if not ok:
                        notice_preflight_error = str(detail or notice_unavailable_message)
                if notice_preflight_error:
                    results.append(
                        ActionResult(action, "failed", "", notice_preflight_error)
                    )
                    continue
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
