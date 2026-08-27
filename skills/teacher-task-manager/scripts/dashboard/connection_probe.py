"""Read-only connection checks for the dashboard's shared status table."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from brity_bridge import bundle_paths, doctor, gws_env, paths, process_win, tool_runtime
from brity_bridge.gemini_analyze import check_gemini_key
from brity_bridge.google_account import is_goedu_email
from brity_bridge.settings import load_settings
from dashboard import engine
from dashboard.connection_status import (
    ComparedValue,
    ConnectionAction,
    ConnectionReport,
    ConnectionSource,
    blocked_status,
    connected_status,
)


def _checked_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _source(checked_at: str, *, kind: str, account: str = "") -> ConnectionSource:
    return ConnectionSource(kind=kind, checked_at=checked_at, account=account)


@dataclass(frozen=True)
class GoogleContext:
    account: str
    blocker: object | None = None
    gws_executable: str = ""

    @property
    def ready(self) -> bool:
        return self.blocker is None


def _default_gws_base_environ() -> dict:
    return dict(os.environ)


def _default_bundled_oauth_client_path() -> Path | None:
    candidate = bundle_paths.bundle_root() / "assets" / gws_env.CLIENT_FILE_NAME
    return candidate if candidate.is_file() else None


def _saved_scope_grant_account(config_dir: Path) -> str:
    """Read the existing permission record without opening credentials or changing it."""

    try:
        saved = engine._read_json_dict(Path(config_dir) / engine.GWS_SCOPE_GRANT_FILE)
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(saved, dict):
        return ""
    account = str(saved.get("account") or "").strip()
    if (
        saved.get("schema_version") != 1
        or saved.get("scope_sha256") != engine.gws_scope_grant_sha256()
        or re.fullmatch(r"[^\s@]+@[^\s@]+", account) is None
    ):
        return ""
    return account


def _default_attachment_folder_checker(path_text: str) -> dict:
    path = Path(str(path_text or "").strip()).expanduser()
    if not path.exists():
        return {"ready": False, "code": "ATTACHMENT_FOLDER_MISSING"}
    if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK):
        return {"ready": False, "code": "ATTACHMENT_FOLDER_NOT_WRITABLE"}
    return {"ready": True}


def _default_ai_agent_checker(_config_dir: Path) -> tuple[bool, str]:
    # This intentionally only reads release/configuration state.  It never downloads
    # Node or starts an AI installer while someone is merely viewing status.
    from dashboard import engine

    if not engine.ai_skill_install_enabled():
        return False, "FEATURE_UNAVAILABLE"
    node = engine.ai_node_status()
    if not node.get("success"):
        return (
            False,
            "TOOL_NOT_INSTALLED"
            if node.get("code") == "NODE_NOT_INSTALLED"
            else "TOOL_RUNTIME_BROKEN",
        )
    try:
        from brity_bridge import ai_skill_install

        approval = ai_skill_install.load_approved_skill(
            engine.bundle_paths.bundle_root() / engine.AI_SKILL_APPROVAL_FILENAME
        )
        return (
            ai_skill_install.plan_is_already_approved(
                ai_skill_install.prepare_install_plan(["codex"]), approval
            ),
            "SKILL_CONFIG_MISMATCH",
        )
    except Exception:  # The status row must remain safe if optional AI files are absent.
        return False, "SKILL_CONFIG_MISSING"


@dataclass
class ConnectionProbeDeps:
    gws_resolver: Callable[[], str] = tool_runtime.resolve_gws_executable
    run_command: Callable[[list[str]], tuple[int, str]] = process_win.run_captured
    gws_base_environ: Callable[[], dict] = _default_gws_base_environ
    gws_config_dir_resolver: Callable[[dict], Path] = gws_env.default_gws_config_dir
    bundled_oauth_client_resolver: Callable[[], Path | None] = _default_bundled_oauth_client_path
    oauth_selector: Callable[[dict, Path, Path | None], gws_env.OAuthClientSelection] = gws_env.select_desktop_oauth_client
    account_storage_checker: Callable[[dict], tuple[str, ...]] = gws_env.unsafe_account_storage_overrides
    scope_grant_checker: Callable[[Path, str], bool] = engine.has_current_gws_scope_grant
    scope_grant_account_reader: Callable[[Path], str] = _saved_scope_grant_account
    gemini_checker: Callable[[str, str], tuple[str, str]] = check_gemini_key
    find_helper_window: Callable[[], bool] = doctor._default_find_helper_window
    attachment_folder_checker: Callable[[str], dict] = _default_attachment_folder_checker
    ai_agent_checker: Callable[[Path], tuple[bool, str]] = _default_ai_agent_checker
    attendance_deps: object | None = None


def _profile_values(config_dir: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with (Path(config_dir) / "teacher-profile.csv").open(
            "r", newline="", encoding="utf-8-sig"
        ) as handle:
            for row in csv.DictReader(handle):
                key = str(row.get("항목") or "").strip()
                if key:
                    values[key] = str(row.get("값") or "").strip()
    except OSError:
        pass
    return values


def _blocked(
    *, item_id: str, group: str, label: str, category: str, reason_code: str,
    reason_ko: str, source: ConnectionSource, action_id: str = "retry-connection-check",
    action_label: str = "다시 확인", expected: ComparedValue = ComparedValue(),
    actual: ComparedValue = ComparedValue(), level: str = "blocked",
):
    return blocked_status(
        id=item_id, group=group, label=label, category=category,
        reason_code=reason_code, reason_ko=reason_ko, expected=expected, actual=actual,
        action=ConnectionAction(action_id, action_label), source=source, level=level,
    )


def _google_context(
    config_dir: Path, deps: ConnectionProbeDeps, checked_at: str
) -> GoogleContext:
    try:
        gws = str(deps.gws_resolver()).strip()
        if not gws:
            raise ValueError("missing gws runtime")
    except Exception:
        return GoogleContext("", _blocked(
            item_id="google.account", group="google", label="학교 Google 계정",
            category="feature_unavailable", reason_code="GWS_RUNTIME_MISSING",
            reason_ko="Google 연결 도구를 찾지 못했어요.",
            source=_source(checked_at, kind="local-read"), action_id="goto-settings-google",
            action_label="Google 도구 고치기",
        ))
    try:
        base = dict(deps.gws_base_environ())
        unsafe_storage = tuple(deps.account_storage_checker(base))
    except Exception:
        unsafe_storage = ("unreadable",)
    if unsafe_storage:
        return GoogleContext("", _blocked(
            item_id="google.account", group="google", label="학교 Google 계정",
            category="feature_unavailable", reason_code="ACCOUNT_STORAGE_UNSAFE",
            reason_ko="Google 로그인 정보 저장 위치가 현재 사용자 폴더 밖을 가리키고 있어요.",
            source=_source(checked_at, kind="local-read"), action_id="goto-settings-google",
            action_label="설정 열기",
        ))
    try:
        gws_config_dir = Path(deps.gws_config_dir_resolver(base))
        selection = deps.oauth_selector(
            base, gws_config_dir, deps.bundled_oauth_client_resolver()
        )
    except Exception:
        selection = None
    if selection is None or not selection.ready:
        code = (
            "OAUTH_CLIENT_CONFLICT"
            if selection is not None and (
                selection.source == "conflict" or selection.error_code == "OAUTH_CLIENT_CONFLICT"
            )
            else "OAUTH_CLIENT_MISSING"
        )
        return GoogleContext("", _blocked(
            item_id="google.account", group="google", label="학교 Google 계정",
            category="feature_unavailable", reason_code=code,
            reason_ko=("Google 로그인 정보가 둘 이상 섞여 있어 안전하게 고르지 못했어요." if code == "OAUTH_CLIENT_CONFLICT" else "Google 로그인에 필요한 설치 정보를 찾지 못했어요."),
            source=_source(checked_at, kind="local-read"), action_id="goto-settings-google",
            action_label="설정 열기",
        ))
    try:
        auth = engine.gws_auth_status(deps.run_command, gws)
    except Exception:
        auth = {"logged_in": False, "login_state": "error", "user": ""}
    if not auth.get("logged_in"):
        is_logged_out = auth.get("login_state") == "logged_out"
        code = "LOGIN_REQUIRED" if is_logged_out else "EXTERNAL_UNREACHABLE"
        return GoogleContext("", _blocked(
            item_id="google.account", group="google", label="학교 Google 계정",
            category="login_account_mismatch" if is_logged_out else "external_unreachable",
            reason_code=code,
            reason_ko=("학교 Google 계정으로 로그인하지 않았어요." if is_logged_out else "Google 로그인 상태를 지금 확인하지 못했어요."),
            source=_source(checked_at, kind="external-live"), action_id="goto-settings-google" if is_logged_out else "retry-connection-check",
            action_label="Google 로그인 열기" if is_logged_out else "다시 확인",
            level="blocked" if is_logged_out else "unknown",
        ))
    account = str(auth.get("user") or "").strip()
    if not is_goedu_email(account):
        return GoogleContext(account, _blocked(
            item_id="google.account", group="google", label="학교 Google 계정",
            category="login_account_mismatch", reason_code="ACCOUNT_DOMAIN_NOT_ALLOWED",
            reason_ko="현재 계정은 @goedu.kr 계정이 아니에요.",
            source=_source(checked_at, kind="external-live", account=account),
            action_id="goto-settings-google", action_label="Google 로그인 열기",
        ))
    saved_account = str(deps.scope_grant_account_reader(Path(config_dir)) or "").strip()
    if saved_account and saved_account.casefold() != account.casefold():
        return GoogleContext(account, _blocked(
            item_id="google.account", group="google", label="학교 Google 계정",
            category="login_account_mismatch", reason_code="SAVED_ACCOUNT_MISMATCH",
            reason_ko="저장할 때 사용한 계정과 지금 로그인한 계정이 달라요.",
            source=_source(checked_at, kind="local-read", account=account),
            action_id="goto-settings-google", action_label="Google 로그인 열기",
            expected=ComparedValue("저장할 때 사용한 계정", saved_account),
            actual=ComparedValue("현재 로그인한 계정", account),
        ), gws)
    try:
        scope_current = bool(deps.scope_grant_checker(Path(config_dir), account))
    except Exception:
        scope_current = False
    if not scope_current:
        return GoogleContext(account, _blocked(
            item_id="google.account", group="google", label="학교 Google 계정",
            category="login_account_mismatch", reason_code="SCOPE_GRANT_STALE",
            reason_ko="현재 기능에 필요한 Google 권한 확인이 끝나지 않았어요.",
            source=_source(checked_at, kind="local-read", account=account),
            action_id="goto-settings-google", action_label="Google 로그인 열기",
        ), gws)
    return GoogleContext(account, gws_executable=gws)


def _copy_google_blocker(blocker, item_id: str, group: str, label: str):
    return _blocked(
        item_id=item_id, group=group, label=label, category=blocker.category,
        reason_code=blocker.reason_code, reason_ko=blocker.reason_ko,
        source=blocker.source, action_id=blocker.action.id, action_label=blocker.action.label,
        expected=blocker.expected, actual=blocker.actual, level=blocker.level,
    )


def _remote_failure(output: str, *, item: str) -> tuple[str, str, str, str]:
    text = str(output or "").casefold()
    if "403" in text or "access denied" in text or "permission" in text:
        return "saved_data_mismatch", f"{item}_ACCESS_DENIED", "현재 계정으로 저장된 대상을 열 수 없어요.", "blocked"
    if "404" in text or "not-found" in text or "not found" in text:
        return "saved_data_mismatch", f"{item}_NOT_FOUND", "현재 계정에서 저장된 대상을 찾지 못했어요.", "blocked"
    return "external_unreachable", "EXTERNAL_UNREACHABLE", "Google에서 현재 연결 상태를 확인하지 못했어요.", "unknown"


def _read_target(
    deps: ConnectionProbeDeps, context: GoogleContext, checked_at: str, *,
    item_id: str, group: str, label: str, saved_id: str, saved_name: str,
    service: str, resource: str, parameter: str, missing_code: str, not_found_code: str,
    access_code: str, not_configured_action: tuple[str, str],
):
    source = _source(checked_at, kind="external-live", account=context.account)
    if context.blocker is not None:
        return _copy_google_blocker(context.blocker, item_id, group, label)
    if not saved_id:
        return _blocked(
            item_id=item_id, group=group, label=label, category="not_configured",
            reason_code=missing_code, reason_ko="아직 고르지 않았어요.", source=source,
            action_id=not_configured_action[0], action_label=not_configured_action[1],
        )
    try:
        args = [
            context.gws_executable, service, resource, "get", "--params",
            json.dumps({parameter: saved_id}, ensure_ascii=False), "--format", "json",
        ]
        code, output = deps.run_command(args)
        if code != 0:
            category, generic_code, reason, level = _remote_failure(output, item="CALENDAR" if group == "calendar" else "TASK_LIST")
            reason_code = (
                access_code if generic_code.endswith("ACCESS_DENIED") else
                not_found_code if generic_code.endswith("NOT_FOUND") else generic_code
            )
            return _blocked(
                item_id=item_id, group=group, label=label, category=category,
                reason_code=reason_code, reason_ko=reason, source=source,
                action_id=not_configured_action[0], action_label=not_configured_action[1], level=level,
                expected=ComparedValue("저장된 ID", saved_id),
            )
        payload = process_win.parse_first_json(output)
        if not isinstance(payload, dict) or str(payload.get("id") or "") != saved_id:
            return _blocked(
                item_id=item_id, group=group, label=label, category="saved_data_mismatch",
                reason_code=not_found_code, reason_ko="현재 계정에서 저장된 대상을 찾지 못했어요.",
                source=source, action_id=not_configured_action[0], action_label=not_configured_action[1],
                expected=ComparedValue("저장된 ID", saved_id),
            )
    except (OSError, TypeError, ValueError, KeyError):
        return _blocked(
            item_id=item_id, group=group, label=label, category="external_unreachable",
            reason_code="EXTERNAL_UNREACHABLE", reason_ko="Google에서 현재 연결 상태를 확인하지 못했어요.",
            source=source, level="unknown",
        )
    current_name = str(payload.get("summary") or payload.get("title") or "").strip()
    if saved_name and current_name and saved_name != current_name:
        return connected_status(
            id=item_id, group=group, label=label, source=source, level="degraded",
            category="degraded", reason_code="", reason_ko="", notice_code="SAVED_LABEL_STALE",
            expected=ComparedValue("저장된 표시 이름", saved_name),
            actual=ComparedValue("Google의 현재 이름", current_name),
        )
    return connected_status(id=item_id, group=group, label=label, source=source)


def _gemini_status(config_dir: Path, deps: ConnectionProbeDeps, checked_at: str):
    settings = load_settings(paths.settings_path(config_dir))
    source = _source(checked_at, kind="external-live")
    status, _detail = deps.gemini_checker(settings.gemini_api_key, settings.gemini_model)
    if status == "ok":
        return connected_status(id="gemini", group="gemini", label="Gemini", source=source)
    if status == "rate-limited":
        return connected_status(
            id="gemini", group="gemini", label="Gemini", source=source, level="degraded",
            category="degraded", notice_code="RATE_LIMITED",
        )
    if status == "missing":
        return _blocked(
            item_id="gemini", group="gemini", label="Gemini", category="not_configured",
            reason_code="API_KEY_MISSING", reason_ko="Gemini 연결 키가 입력되지 않았어요.",
            source=source, action_id="goto-gemini", action_label="Gemini 설정 열기",
        )
    code = "API_KEY_REJECTED" if status == "invalid" else "EXTERNAL_UNREACHABLE"
    return _blocked(
        item_id="gemini", group="gemini", label="Gemini",
        category=("saved_data_mismatch" if code == "API_KEY_REJECTED" else "external_unreachable"),
        reason_code=code, reason_ko="Gemini 연결 상태를 지금 확인하지 못했어요.", source=source,
        action_id="goto-gemini" if code == "API_KEY_REJECTED" else "retry-connection-check",
        action_label="Gemini 설정 열기" if code == "API_KEY_REJECTED" else "다시 확인",
        level=("blocked" if code == "API_KEY_REJECTED" else "unknown"),
    )


def read_connection_statuses(
    config_dir: Path, item_ids=None, deps: ConnectionProbeDeps | None = None,
) -> ConnectionReport:
    """Read the requested connection targets without creating or changing anything."""

    deps = deps or ConnectionProbeDeps()
    config_dir = Path(config_dir)
    profile = _profile_values(config_dir)
    checked_at = _checked_at()
    requested = {str(item).strip() for item in (item_ids or ()) if str(item).strip()}
    wants_all = not requested

    google_needed = wants_all or bool(requested & {
        "google.account", "calendar.work", "calendar.school", "tasks.work", "tasks.homeroom",
    }) or any(item_id.startswith("attendance.") for item_id in requested)
    context = _google_context(config_dir, deps, checked_at) if google_needed else GoogleContext("")
    items = []

    def include(item_id: str) -> bool:
        return wants_all or item_id in requested

    if include("google.account"):
        if context.blocker is None:
            items.append(connected_status(
                id="google.account", group="google", label="학교 Google 계정",
                source=_source(checked_at, kind="external-live", account=context.account),
            ))
        else:
            items.append(context.blocker)

    targets = (
        ("calendar.work", "calendar", "업무 Calendar", "업무캘린더ID", "업무캘린더이름", "calendar", "calendarList", "calendarId", "CALENDAR_NOT_CONFIGURED", "CALENDAR_NOT_FOUND", "CALENDAR_ACCESS_DENIED", ("goto-work-calendar", "캘린더 고르기")),
        ("calendar.school", "calendar", "학사 일정 Calendar", "학사일정캘린더ID", "학사일정캘린더이름", "calendar", "calendarList", "calendarId", "CALENDAR_NOT_CONFIGURED", "CALENDAR_NOT_FOUND", "CALENDAR_ACCESS_DENIED", ("goto-school-calendar", "캘린더 고르기")),
        ("tasks.work", "tasks", "업무 Tasks", "업무Tasks목록ID", "업무Tasks목록이름", "tasks", "tasklists", "tasklist", "TASKS_NOT_CONFIGURED", "TASK_LIST_NOT_FOUND", "TASK_LIST_ACCESS_DENIED", ("goto-work-tasks", "할 일 목록 고르기")),
        ("tasks.homeroom", "tasks", "담임 Tasks", "담임안내Tasks목록ID", "담임안내Tasks목록이름", "tasks", "tasklists", "tasklist", "TASKS_NOT_CONFIGURED", "TASK_LIST_NOT_FOUND", "TASK_LIST_ACCESS_DENIED", ("goto-homeroom-tasks", "할 일 목록 고르기")),
    )
    for values in targets:
        item_id, group, label, id_key, name_key, service, resource, parameter, missing, not_found, denied, action = values
        if not include(item_id):
            continue
        if item_id == "tasks.homeroom" and profile.get("담임여부") not in {"예", "Y", "y", "yes", "YES", "담임"}:
            continue
        items.append(_read_target(
            deps, context, checked_at, item_id=item_id, group=group, label=label,
            saved_id=profile.get(id_key, ""), saved_name=profile.get(name_key, ""),
            service=service, resource=resource, parameter=parameter, missing_code=missing,
            not_found_code=not_found, access_code=denied, not_configured_action=action,
        ))

    attendance_requested = wants_all or {
        item_id for item_id in requested if item_id.startswith("attendance.")
    }
    if attendance_requested:
        from dashboard.attendance_connection_probe import (
            AttendanceProbeDeps,
            read_attendance_connection_statuses,
        )

        attendance_deps = deps.attendance_deps
        if attendance_deps is None:
            attendance_deps = AttendanceProbeDeps(run_command=deps.run_command)
        attendance_rows = read_attendance_connection_statuses(
            config_dir, context, attendance_deps
        )
        items.extend(
            row for row in attendance_rows
            if wants_all or row.id in requested
        )

    if include("gemini"):
        items.append(_gemini_status(config_dir, deps, checked_at))
    if include("brity.helper"):
        running = bool(deps.find_helper_window())
        source = _source(checked_at, kind="local-read")
        items.append(
            connected_status(id="brity.helper", group="brity", label="Brity 도우미", source=source)
            if running else _blocked(
                item_id="brity.helper", group="brity", label="Brity 도우미",
                category="feature_unavailable", reason_code="HELPER_NOT_RUNNING",
                reason_ko="Brity 도우미가 실행 중이 아니에요.", source=source,
            )
        )
    if include("brity.attachment-folder"):
        settings = load_settings(paths.settings_path(config_dir))
        checked = deps.attachment_folder_checker(settings.brity_download_dir)
        source = _source(checked_at, kind="local-read")
        if checked.get("ready"):
            items.append(connected_status(
                id="brity.attachment-folder", group="brity", label="Brity 첨부 폴더", source=source
            ))
        else:
            code = str(checked.get("code") or "ATTACHMENT_FOLDER_MISSING")
            items.append(_blocked(
                item_id="brity.attachment-folder", group="brity", label="Brity 첨부 폴더",
                category="feature_unavailable", reason_code=code,
                reason_ko="첨부 폴더를 현재 사용할 수 없어요.", source=source,
            ))
    if include("ai-agent"):
        ready, code = deps.ai_agent_checker(config_dir)
        if not ready:
            code = (
                "TOOL_NOT_INSTALLED"
                if str(code) == "NODE_NOT_INSTALLED"
                else "TOOL_RUNTIME_BROKEN"
                if str(code).startswith("NODE_")
                else str(code)
            )
        source = _source(checked_at, kind="local-read")
        items.append(
            connected_status(id="ai-agent", group="ai-agent", label="AI 도구", source=source)
            if ready else _blocked(
                item_id="ai-agent", group="ai-agent", label="AI 도구",
                category="feature_unavailable", reason_code=str(code),
                reason_ko="AI 도구를 아직 안전하게 사용할 수 없어요.", source=source,
            )
        )
    return ConnectionReport(checked_at=checked_at, items=tuple(items))
