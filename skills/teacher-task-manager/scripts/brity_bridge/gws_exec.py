from __future__ import annotations

import hashlib
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
_LOCAL_ATTACHMENT_HEADER = "📎 첨부파일"
_LOCAL_ATTACHMENT_URL_PREFIX = "http://127.0.0.1:49271/a/"
TOOL_PREPARATION_FAILURE_DETAIL = (
    "Google 등록 도구를 준비하지 못했습니다. Teacher Manager를 다시 열고 다시 시도해 주세요."
)
LOGIN_FAILURE_DETAIL = "Google 로그인을 다시 확인해 주세요."
LOGIN_STORAGE_FAILURE_DETAIL = (
    "Google 로그인 정보를 안전하게 확인하지 못했습니다. Teacher Manager를 다시 열고 로그인해 주세요."
)
PERMISSION_FAILURE_DETAIL = "이 항목을 등록할 Google 권한을 확인해 주세요."
NETWORK_FAILURE_DETAIL = "인터넷 연결을 확인해 주세요. 잠시 뒤 다시 시도해 주세요."
GENERAL_FAILURE_DETAIL = "Google에서 이 항목을 처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요."
RESULT_RECORD_FAILURE_DETAIL = (
    "Google에 이미 만들었을 수 있으니 같은 메시지를 다시 누르지 말고 기록에서 확인해 주세요."
)
NOTICE_PREFLIGHT_FAILURE_DETAIL = (
    "학생 안내표의 출결 기능 확인이 필요합니다. 출결 탭 위쪽 안내를 확인해 주세요."
)
NOTICE_SETUP_FAILURE_DETAIL = "학생 안내표가 준비되지 않았어요 · 처음 설정 필요"


@dataclass
class ActionResult:
    action: CheckedAction
    status: str  # created | duplicate | failed
    google_id: str = ""
    detail: str = ""
    retry_allowed: bool = True


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


class GoogleListError(RuntimeError):
    """A Google list reply was unreadable, malformed, or incomplete."""


class AmbiguousGoogleResult(RuntimeError):
    """More than one remote item could represent the same operation."""


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


def _run_json_strict(runner, args: list[str]) -> dict:
    reply = runner(args)
    try:
        parsed = process_win.parse_first_json(reply)
    except (TypeError, ValueError) as error:
        raise GoogleListError("Google 목록 응답을 읽지 못했습니다") from error
    if not isinstance(parsed, dict):
        raise GoogleListError("Google 목록 응답 모양이 올바르지 않습니다")
    return parsed


def _complete_items(runner, command: list[str], params: dict) -> list[dict]:
    """Read every page and reject any reply that cannot prove completeness."""

    items: list[dict] = []
    seen_tokens: set[str] = set()
    page_token = ""
    for _page in range(100):
        page_params = dict(params)
        if page_token:
            page_params["pageToken"] = page_token
        args = command + [
            "--params", json.dumps(page_params, ensure_ascii=False),
            "--format", "json",
        ]
        payload = _run_json_strict(runner, args)
        page_items = payload.get("items")
        if not isinstance(page_items, list) or payload.get("incompleteSearch") is True:
            raise GoogleListError("Google 목록 전체를 확인하지 못했습니다")
        for item in page_items:
            if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                raise GoogleListError("Google 목록 항목을 확인하지 못했습니다")
            items.append(item)
        next_token = payload.get("nextPageToken")
        if next_token in (None, ""):
            return items
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise GoogleListError("Google 목록 다음 쪽을 확인하지 못했습니다")
        seen_tokens.add(next_token)
        page_token = next_token
    raise GoogleListError("Google 목록 쪽 수가 안전 한도를 넘었습니다")


def _safe_google_failure(error: BaseException) -> str:
    """Map external diagnostics to a fixed message that is safe for the UI."""

    text = str(error or "").casefold()
    if any(marker in text for marker in ("permission", "forbidden", "access denied", "403", "권한")):
        return PERMISSION_FAILURE_DETAIL
    if any(
        marker in text
        for marker in (
            "invalid_grant", "unauthenticated", "authentication", "oauth",
            "credential", "token", "login", "401", "로그인",
        )
    ):
        return LOGIN_FAILURE_DETAIL
    if any(
        marker in text
        for marker in (
            "timeout", "timed out", "network", "connection", "dns", "internet",
            "unreachable", "socket", "reset", "인터넷", "연결",
        )
    ):
        return NETWORK_FAILURE_DETAIL
    return GENERAL_FAILURE_DETAIL


def _is_google_user_action(detail: str) -> bool:
    return detail in {
        LOGIN_FAILURE_DETAIL,
        LOGIN_STORAGE_FAILURE_DETAIL,
        PERMISSION_FAILURE_DETAIL,
        GOEDU_ACCOUNT_REQUIRED_MESSAGE,
        "처음 준비하던 Google 계정으로 다시 로그인해 주세요.",
    }


def _known_write_not_sent(error: BaseException, detail: str) -> bool:
    """Retry a write only when its runner proves that execution never started."""

    del detail
    return getattr(error, "write_not_sent", False) is True


def _preparation_failures(
    actions: list, detail: str, *, retry_allowed: bool = True
) -> ExecutionReport:
    return ExecutionReport(
        results=[
            ActionResult(action, "failed", "", detail, retry_allowed=retry_allowed)
            for action in actions
        ]
    )


def _require_goedu_account(runner, gws_command: list[str]) -> str:
    """자료를 읽거나 쓰기 직전에 현재 로그인 계정을 한 번 확인한다."""

    output = runner(gws_command + ["auth", "status"])
    return require_goedu_email(extract_email(output))


def _probe_calendar_duplicate(runner, gws_command, action) -> str:
    params = {
        "calendarId": action.google_id,
        "privateExtendedProperty": f"{DUPLICATE_KEY_PROPERTY}={action.action_key}",
        "maxResults": 2500,
    }
    items = _complete_items(
        runner, gws_command + ["calendar", "events", "list"], params
    )
    if len(items) > 1:
        raise AmbiguousGoogleResult("같은 등록 표식의 일정이 둘 이상입니다")
    return str(items[0].get("id") or "") if items else ""


def _probe_calendar_three_cycles(runner, gws_command, action) -> tuple[bool, str, str]:
    last_detail = GENERAL_FAILURE_DETAIL
    for _attempt in range(3):
        try:
            return True, _probe_calendar_duplicate(runner, gws_command, action), ""
        except AmbiguousGoogleResult:
            return False, "", "같은 등록 표식의 일정이 둘 이상이라 자동 등록을 멈췄습니다."
        except Exception as error:  # noqa: BLE001 - 외부 원문은 사용자 화면에 내보내지 않는다
            detail = _safe_google_failure(error)
            if _is_google_user_action(detail):
                return False, "", detail
            if last_detail == GENERAL_FAILURE_DETAIL or detail != GENERAL_FAILURE_DETAIL:
                last_detail = detail
    return False, "", last_detail


def _get_calendar_event(runner, gws_command, action, event_id: str) -> dict:
    args = gws_command + [
        "calendar", "events", "get",
        "--params", json.dumps(
            {"calendarId": action.google_id, "eventId": event_id}, ensure_ascii=False
        ),
        "--format", "json",
    ]
    return _run_json(runner, args)


def _requires_local_link_verification(action) -> bool:
    description = str(action.payload.get("description", ""))
    return (
        _LOCAL_ATTACHMENT_HEADER in description
        and _LOCAL_ATTACHMENT_URL_PREFIX in description
    )


def _calendar_response_preserves_local_links(action, event: dict) -> bool:
    if not _requires_local_link_verification(action):
        return True
    expected = str(action.payload.get("description", "")).replace("\r\n", "\n")
    actual = str((event or {}).get("description", "")).replace("\r\n", "\n")
    return actual == expected


def _probe_task_duplicate(runner, gws_command, action) -> str:
    items = _list_task_items(runner, gws_command, action)
    mark = f"{TASK_NOTE_MARK}{action.action_key}"
    for item in items:
        if mark in (item.get("notes") or ""):
            return item.get("id", "")
    return ""


def _list_task_items(runner, gws_command, action) -> list[dict]:
    params = {
        "tasklist": action.google_id,
        "showCompleted": True,
        "showHidden": True,
        "maxResults": 100,
    }
    return _complete_items(
        runner, gws_command + ["tasks", "tasks", "list"], params
    )


def _insert_calendar(runner, gws_command, action) -> dict:
    body = dict(action.payload)
    body["extendedProperties"] = {"private": {DUPLICATE_KEY_PROPERTY: action.action_key}}
    params = {"calendarId": action.google_id}
    if body.get("attachments"):
        params["supportsAttachments"] = True
    args = gws_command + [
        "calendar", "events", "insert",
        "--params", json.dumps(params, ensure_ascii=False),
        "--json", json.dumps(body, ensure_ascii=False),
    ]
    return _run_json(runner, args)


def _calendar_intent_hash(action) -> str:
    body = dict(action.payload)
    body["extendedProperties"] = {
        "private": {DUPLICATE_KEY_PROPERTY: action.action_key}
    }
    canonical = json.dumps(
        {"calendarId": action.google_id, "body": body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _calendar_remote_result(
    runner,
    gws_command,
    action,
    history: HistoryStore,
    source_hash: str,
    event_id: str,
    *,
    created_here: bool,
    event: dict | None = None,
) -> ActionResult:
    if source_hash:
        try:
            history.record_action(source_hash, action.action_key, action.kind, event_id)
            history.save()
        except Exception:  # noqa: BLE001 - 이미 생긴 원격 결과를 다시 만들지 않는다
            return ActionResult(
                action,
                "failed",
                event_id,
                RESULT_RECORD_FAILURE_DETAIL,
                retry_allowed=False,
            )
    if _requires_local_link_verification(action):
        if event is None:
            try:
                event = _get_calendar_event(runner, gws_command, action, event_id)
            except Exception:  # noqa: BLE001 - 원격 원문은 화면에 내보내지 않는다
                return ActionResult(
                    action,
                    "failed",
                    event_id,
                    "기존 일정의 첨부 연결을 확인하지 못했습니다.",
                    retry_allowed=False if created_here else True,
                )
        if not _calendar_response_preserves_local_links(action, event):
            detail = (
                "일정은 만들었지만 첨부 연결이 확인되지 않았습니다."
                if created_here
                else "일정은 있지만 첨부 연결이 확인되지 않았습니다."
            )
            return ActionResult(action, "failed", event_id, detail)
    if created_here:
        return ActionResult(action, "created", event_id)
    return ActionResult(action, "duplicate", event_id, "Google에 같은 항목이 있음")


def _execute_calendar_action(
    runner,
    gws_command,
    action,
    history: HistoryStore,
    source_hash: str,
) -> ActionResult:
    """Use at most one insert and spend later cycles on private-key read-back."""

    expected_hash = _calendar_intent_hash(action)
    intent = history.write_intent(source_hash, action.action_key) if source_hash else None
    insert_sent = False
    if intent is not None:
        if (
            intent.get("kind") != "calendar"
            or intent.get("intent_hash") != expected_hash
            or intent.get("state") not in {"write_started", "confirmed"}
        ):
            return ActionResult(
                action,
                "failed",
                "",
                "저장된 일정 등록 상태가 현재 내용과 달라 자동 등록을 멈췄습니다.",
                retry_allowed=False,
            )
        if intent.get("state") == "confirmed" and intent.get("google_id"):
            return ActionResult(
                action,
                "duplicate",
                str(intent.get("google_id")),
                "이미 등록된 항목",
            )
        insert_sent = True
    last_detail = GENERAL_FAILURE_DETAIL
    for _attempt in range(3):
        try:
            existing = _probe_calendar_duplicate(runner, gws_command, action)
        except AmbiguousGoogleResult:
            return ActionResult(
                action,
                "failed",
                "",
                "같은 등록 표식의 일정이 둘 이상이라 자동 등록을 멈췄습니다.",
                retry_allowed=False,
            )
        except Exception as error:  # noqa: BLE001 - 외부 원문은 화면에 내보내지 않는다
            detail = _safe_google_failure(error)
            if _is_google_user_action(detail):
                return ActionResult(action, "failed", "", detail, retry_allowed=False)
            if last_detail == GENERAL_FAILURE_DETAIL or detail != GENERAL_FAILURE_DETAIL:
                last_detail = detail
            continue
        if existing:
            return _calendar_remote_result(
                runner,
                gws_command,
                action,
                history,
                source_hash,
                existing,
                created_here=insert_sent,
            )
        if insert_sent:
            continue
        if source_hash:
            try:
                history.record_write_intent(
                    source_hash,
                    action.action_key,
                    "calendar",
                    (),
                    expected_hash,
                )
                history.save()
            except Exception:  # noqa: BLE001 - 저장 전에는 Google에 쓰지 않는다
                history.clear_write_intent(source_hash, action.action_key)
                last_detail = "일정 등록 준비를 안전하게 저장하지 못했습니다."
                continue
        insert_sent = True
        try:
            created_event = _insert_calendar(runner, gws_command, action)
        except Exception as error:  # noqa: BLE001 - 응답 손실 뒤에는 조회만 한다
            detail = _safe_google_failure(error)
            if _is_google_user_action(detail):
                if source_hash:
                    history.clear_write_intent(source_hash, action.action_key)
                    try:
                        history.save()
                    except Exception:
                        pass
                return ActionResult(action, "failed", "", detail, retry_allowed=False)
            if _known_write_not_sent(error, detail):
                if source_hash:
                    history.clear_write_intent(source_hash, action.action_key)
                    try:
                        history.save()
                    except Exception:
                        pass
                    if history.write_intent(source_hash, action.action_key) is not None:
                        return ActionResult(
                            action,
                            "failed",
                            "",
                            RESULT_RECORD_FAILURE_DETAIL,
                            retry_allowed=False,
                        )
                insert_sent = False
                last_detail = detail
                continue
            if last_detail == GENERAL_FAILURE_DETAIL or detail != GENERAL_FAILURE_DETAIL:
                last_detail = detail
            continue
        created_id = str(created_event.get("id") or "")
        if not created_id:
            last_detail = RESULT_RECORD_FAILURE_DETAIL
            continue
        return _calendar_remote_result(
            runner,
            gws_command,
            action,
            history,
            source_hash,
            created_id,
            created_here=True,
            event=created_event,
        )
    return ActionResult(
        action,
        "failed",
        "",
        RESULT_RECORD_FAILURE_DETAIL if insert_sent else last_detail,
        retry_allowed=False,
    )


def _insert_task(runner, gws_command, action) -> str:
    body = dict(action.payload)
    body.pop("due", None)
    notes = body.get("notes", "").rstrip()
    body["notes"] = notes
    args = gws_command + [
        "tasks", "tasks", "insert",
        "--params", json.dumps({"tasklist": action.google_id}, ensure_ascii=False),
        "--json", json.dumps(body, ensure_ascii=False),
    ]
    return _run_json(runner, args).get("id", "")


def _task_intent_hash(action) -> str:
    body = dict(action.payload)
    body.pop("due", None)
    body["notes"] = str(body.get("notes") or "").rstrip()
    canonical = json.dumps(
        {"tasklist": action.google_id, "body": body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _task_matches_intent(item: dict, action) -> bool:
    expected_title = str(action.payload.get("title") or "")
    expected_notes = str(action.payload.get("notes") or "").rstrip()
    status = str(item.get("status") or "needsAction")
    return (
        str(item.get("title") or "") == expected_title
        and str(item.get("notes") or "").rstrip() == expected_notes
        and status == "needsAction"
        and not item.get("due")
        and item.get("deleted") is not True
    )


def _execute_task_action(
    runner,
    gws_command,
    action,
    history: HistoryStore,
    source_hash: str,
) -> ActionResult:
    """Persist a full ID snapshot, then use at most one task insert."""

    expected_hash = _task_intent_hash(action)
    intent = history.write_intent(source_hash, action.action_key) if source_hash else None
    write_sent = False
    pre_ids: set[str] = set()
    if intent is not None:
        if (
            intent.get("kind") != "task"
            or intent.get("intent_hash") != expected_hash
            or intent.get("state") not in {"write_started", "confirmed"}
        ):
            return ActionResult(
                action,
                "failed",
                "",
                "저장된 할 일 등록 상태가 현재 내용과 달라 자동 등록을 멈췄습니다.",
                retry_allowed=False,
            )
        if intent.get("state") == "confirmed" and intent.get("google_id"):
            return ActionResult(
                action,
                "duplicate",
                str(intent.get("google_id")),
                "이미 등록된 항목",
            )
        raw_pre_ids = intent.get("pre_ids")
        if not isinstance(raw_pre_ids, list) or any(not isinstance(item, str) for item in raw_pre_ids):
            return ActionResult(
                action,
                "failed",
                "",
                "저장된 할 일 목록 상태를 확인하지 못했습니다.",
                retry_allowed=False,
            )
        pre_ids = set(raw_pre_ids)
        write_sent = True

    last_detail = GENERAL_FAILURE_DETAIL
    ambiguous = False
    for _attempt in range(3):
        try:
            items = _list_task_items(runner, gws_command, action)
        except Exception as error:  # noqa: BLE001 - 외부 원문은 화면에 내보내지 않는다
            detail = _safe_google_failure(error)
            if _is_google_user_action(detail):
                return ActionResult(action, "failed", "", detail, retry_allowed=False)
            if last_detail == GENERAL_FAILURE_DETAIL or detail != GENERAL_FAILURE_DETAIL:
                last_detail = detail
            continue

        mark = f"{TASK_NOTE_MARK}{action.action_key}"
        marked = [item for item in items if mark in str(item.get("notes") or "")]
        if len(marked) == 1 and not write_sent:
            return ActionResult(
                action,
                "duplicate",
                str(marked[0].get("id") or ""),
                "Google에 같은 항목이 있음",
            )
        if len(marked) > 1:
            return ActionResult(
                action,
                "failed",
                "",
                "예전 등록 표식이 붙은 할 일이 둘 이상이라 자동 등록을 멈췄습니다.",
                retry_allowed=False,
            )

        if write_sent:
            candidates = [
                item
                for item in items
                if str(item.get("id") or "") not in pre_ids
                and _task_matches_intent(item, action)
            ]
            if len(candidates) == 1:
                created_id = str(candidates[0].get("id") or "")
                if source_hash:
                    try:
                        history.record_action(
                            source_hash, action.action_key, action.kind, created_id
                        )
                        history.save()
                    except Exception:  # noqa: BLE001 - 확인된 원격 결과는 다시 만들지 않는다
                        return ActionResult(
                            action,
                            "failed",
                            created_id,
                            RESULT_RECORD_FAILURE_DETAIL,
                            retry_allowed=False,
                        )
                return ActionResult(action, "created", created_id)
            if len(candidates) > 1:
                ambiguous = True
            continue

        pre_ids = {str(item.get("id") or "") for item in items}
        if source_hash:
            try:
                history.record_write_intent(
                    source_hash,
                    action.action_key,
                    "task",
                    pre_ids,
                    expected_hash,
                )
                history.save()
            except Exception:  # noqa: BLE001 - 저장 전에는 Google에 쓰지 않는다
                history.clear_write_intent(source_hash, action.action_key)
                last_detail = "할 일 등록 준비를 안전하게 저장하지 못했습니다."
                continue
        write_sent = True
        try:
            created_id = _insert_task(runner, gws_command, action)
        except Exception as error:  # noqa: BLE001 - 응답 손실 뒤에는 조회만 한다
            detail = _safe_google_failure(error)
            if _is_google_user_action(detail):
                if source_hash:
                    history.clear_write_intent(source_hash, action.action_key)
                    try:
                        history.save()
                    except Exception:
                        pass
                return ActionResult(action, "failed", "", detail, retry_allowed=False)
            if _known_write_not_sent(error, detail):
                if source_hash:
                    history.clear_write_intent(source_hash, action.action_key)
                    try:
                        history.save()
                    except Exception:
                        pass
                    if history.write_intent(source_hash, action.action_key) is not None:
                        return ActionResult(
                            action,
                            "failed",
                            "",
                            RESULT_RECORD_FAILURE_DETAIL,
                            retry_allowed=False,
                        )
                write_sent = False
                pre_ids = set()
                last_detail = detail
                continue
            if last_detail == GENERAL_FAILURE_DETAIL or detail != GENERAL_FAILURE_DETAIL:
                last_detail = detail
            continue
        created_id = str(created_id or "")
        if not created_id:
            last_detail = RESULT_RECORD_FAILURE_DETAIL
            continue
        if source_hash:
            try:
                history.record_action(
                    source_hash, action.action_key, action.kind, created_id
                )
                history.save()
            except Exception:  # noqa: BLE001 - 이미 생긴 항목을 다시 만들지 않는다
                return ActionResult(
                    action,
                    "failed",
                    created_id,
                    RESULT_RECORD_FAILURE_DETAIL,
                    retry_allowed=False,
                )
        return ActionResult(action, "created", created_id)

    detail = (
        "새로 생긴 할 일이 둘 이상이라 어느 항목인지 자동으로 확정하지 못했습니다."
        if ambiguous
        else (RESULT_RECORD_FAILURE_DETAIL if write_sent else last_detail)
    )
    return ActionResult(action, "failed", "", detail, retry_allowed=False)


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
            content, "자동분석", "대기", "", "", "",
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
    row = [today, "기타", content, "자동분석", "대기", "", ""]
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
    notice_unavailable_message: str = "학생 안내표가 준비되지 않았어요 · 처음 설정 필요",
    expected_account: str = "",
    notice_preflight=None,
) -> ExecutionReport:
    runtime_run_command = None
    try:
        if runner is None:
            # 실행 파일을 고르는 --version 확인도 GWS 명령이다. 다른 계정 저장소를
            # 발견하면 resolver보다 먼저 끝내고, 정상일 때도 현재 계정용 환경만 준다.
            base = dict(os.environ)
            if gws_env.unsafe_account_storage_overrides(base):
                raise gws_env.GwsAccountStorageError(
                    gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
                )
            runtime_environment = gws_env.gws_environ(base)

            def runtime_run_command(args):
                return process_win.run_captured(args, env=runtime_environment)

        gws_command = resolve_gws_command(
            list(gws_command or []),
            gws_executable=gws_executable,
            runtime_run_command=runtime_run_command,
        )
    except gws_env.GwsAccountStorageError:
        return _preparation_failures(
            actions, LOGIN_STORAGE_FAILURE_DETAIL, retry_allowed=False
        )
    except Exception:  # noqa: BLE001 - 실행 도구·경로 원문은 화면에 내보내지 않는다
        return _preparation_failures(actions, TOOL_PREPARATION_FAILURE_DETAIL)
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
            recorded_id = recorded.get("google_id", "")
            if action.kind == "calendar" and _requires_local_link_verification(action):
                try:
                    current_account = _require_goedu_account(runner, gws_command)
                    owner = str(expected_account or "").strip()
                    if owner and current_account.casefold() != owner.casefold():
                        raise RuntimeError("처음 준비하던 Google 계정으로 다시 로그인해 주세요.")
                    event = _get_calendar_event(runner, gws_command, action, recorded_id)
                except Exception:  # noqa: BLE001 - 원격 원문은 사용자 화면에 내보내지 않는다
                    results.append(
                        ActionResult(action, "failed", recorded_id, "기존 일정의 첨부 연결을 확인하지 못했습니다.")
                    )
                    continue
                if not _calendar_response_preserves_local_links(action, event):
                    results.append(
                        ActionResult(action, "failed", recorded_id, "일정은 있지만 첨부 연결이 확인되지 않았습니다.")
                    )
                    continue
            results.append(
                ActionResult(action, "duplicate", recorded_id, "이미 등록된 항목")
            )
            continue
        if action.kind == "notice" and not action.google_id:
            blocked_detail = (
                NOTICE_PREFLIGHT_FAILURE_DETAIL
                if "출결 기능" in str(notice_unavailable_message or "")
                else NOTICE_SETUP_FAILURE_DETAIL
            )
            results.append(
                ActionResult(
                    action,
                    "failed",
                    "",
                    blocked_detail,
                )
            )
            continue
        if not account_checked:
            account_checked = True
            try:
                current_account = _require_goedu_account(runner, gws_command)
            except gws_env.GwsAccountStorageError:
                account_error = LOGIN_STORAGE_FAILURE_DETAIL
            except Exception as error:  # noqa: BLE001 - 계정·명령 원문을 사용자 화면에 내보내지 않는다
                account_error = (
                    GOEDU_ACCOUNT_REQUIRED_MESSAGE
                    if str(error) == GOEDU_ACCOUNT_REQUIRED_MESSAGE
                    else _safe_google_failure(error)
                )
            else:
                owner = str(expected_account or "").strip()
                if owner and current_account.casefold() != owner.casefold():
                    account_error = "처음 준비하던 Google 계정으로 다시 로그인해 주세요."
        if account_error:
            results.append(
                ActionResult(
                    action,
                    "failed",
                    "",
                    account_error,
                    retry_allowed=False,
                )
            )
            continue
        try:
            if action.kind == "calendar":
                results.append(
                    _execute_calendar_action(
                        runner, gws_command, action, history, source_hash
                    )
                )
                continue
            elif action.kind == "task":
                results.append(
                    _execute_task_action(
                        runner, gws_command, action, history, source_hash
                    )
                )
                continue
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
                        notice_preflight_error = NOTICE_PREFLIGHT_FAILURE_DETAIL
                if notice_preflight_error:
                    results.append(
                        ActionResult(action, "failed", "", notice_preflight_error)
                    )
                    continue
                status = _append_notice(runner, gws_command, action)
                if status == "duplicate":
                    results.append(ActionResult(action, "duplicate", "", "학생 안내표에 같은 안내가 있음"))
                    continue
                created_id = ""  # 시트 줄이라 Google ID 없음
        except Exception as error:  # noqa: BLE001 - 외부 원문은 사용자 화면에 내보내지 않는다
            results.append(ActionResult(action, "failed", "", _safe_google_failure(error)))
            continue
        if action.kind in {"calendar", "task"} and not created_id:
            results.append(
                ActionResult(
                    action, "failed", "", RESULT_RECORD_FAILURE_DETAIL, retry_allowed=False
                )
            )
            continue
        if source_hash:
            try:
                history.record_action(source_hash, action.action_key, action.kind, created_id)
                history.save()
            except Exception:  # noqa: BLE001 - 저장 경로·원문은 사용자 화면에 내보내지 않는다
                results.append(
                    ActionResult(
                        action,
                        "failed",
                        created_id,
                        RESULT_RECORD_FAILURE_DETAIL,
                        retry_allowed=False,
                    )
                )
                continue
        if action.kind == "calendar" and not _calendar_response_preserves_local_links(action, created_event):
            results.append(
                ActionResult(
                    action,
                    "failed",
                    created_id,
                    "일정은 만들었지만 첨부 연결이 확인되지 않았습니다.",
                )
            )
            continue
        results.append(ActionResult(action, "created", created_id))
    return ExecutionReport(results=results)
