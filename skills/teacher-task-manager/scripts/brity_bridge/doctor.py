# skills/teacher-task-manager/scripts/brity_bridge/doctor.py
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from brity_bridge import paths, process_win
from brity_bridge.gemini_analyze import cached_gemini_key_check
from brity_bridge.google_account import extract_email
from brity_bridge.hotkey import parse_hotkey
from brity_bridge.settings import load_settings

GEMINI_KEY_MISSING_FIX = "Gemini API key가 입력되지 않았어요. 발급받은 값을 붙여넣어 주세요."
GEMINI_KEY_INVALID_FIX = "Gemini API key가 맞지 않아요. AI Studio에서 다시 복사해 주세요."
GEMINI_NETWORK_FIX = "Gemini 응답을 받지 못했어요. 잠시 후 다시 확인해 주세요."
GEMINI_RATE_LIMIT_DETAIL = "Gemini 사용 한도에 도달했어요. 한도가 언제 다시 열리는지는 확인하지 못했습니다. AI Studio에서 사용량을 확인해 주세요."
HOTKEY_FIX = "설정에서 메신저 단축키를 다시 눌러 주세요."
GOOGLE_LOGIN_FIX = "설정에서 Google 계정으로 로그인해 주세요."
GRANT_MISSING_TEXT = "Google 권한을 다시 승인해 주세요."
# The settings window saves automatically; it has no [저장] button (C11, R5-3).
HELPER_FIX = "Teacher Manager의 [설정]에서 [메신저 단축키 다시 켜기]를 눌러 주세요."


@dataclass
class CheckResult:
    key: str
    label: str
    ok: bool | None  # None = 정보 항목(실패 아님)
    detail: str = ""
    fix: str = ""
    card: str = ""       # identity | timetable | connect | settings
    tab: str = ""        # messenger | attendance | 빈 문자열
    target: str = ""     # 실제 입력 name 또는 google-login 같은 화면 줄 이름
    unread: bool = False  # 확인 자체를 하지 못했다: 실패도 정상도 아니다 (회색, C6·C8)


def _default_run_command(args: list[str]) -> tuple[int, str]:
    return process_win.run_captured(args)


def _default_find_helper_window() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    find_window = ctypes.windll.user32.FindWindowW
    find_window.restype = ctypes.c_void_p
    return bool(find_window("BrityBridgeTrayWindow", None))


def _default_autostart_checker() -> bool:
    from brity_bridge import autostart_win

    return autostart_win.is_autostart_enabled()


@dataclass
class DoctorDeps:
    # No check runs a local Google tool any more.
    run_command: object = _default_run_command
    gemini_checker: object = cached_gemini_key_check
    find_helper_window: object = _default_find_helper_window
    autostart_checker: object = _default_autostart_checker
    python_version: tuple = field(default_factory=lambda: sys.version_info[:3])


def _extract_email(text: str) -> str:
    """옛 호출 자리도 공통 계정 판정과 같은 방식으로 이메일을 읽는다."""

    return extract_email(text)


def _read_profile_csv_values(config_dir: Path) -> dict[str, str]:
    """teacher-profile.csv의 현재 값을 읽는다. 없거나 깨졌으면 빈 값으로 취급한다."""
    path = Path(config_dir) / "teacher-profile.csv"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames or "항목" not in reader.fieldnames or "값" not in reader.fieldnames:
                return values
            for row in reader:
                key = (row.get("항목") or "").strip()
                if key:
                    values[key] = (row.get("값") or "").strip()
    except OSError:
        return values
    return values


def _is_time_text(value: str) -> bool:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        return False
    return True


_HOMEROOM_YES = {"예", "Y", "y", "yes", "YES", "담임"}
_HOMEROOM_NO = {"아니오", "아니요", "N", "n", "no", "NO", "비담임"}

_IDENTITY_TEXT_FIELDS = [
    ("identity.teacher-name", "선생님 이름", "선생님이름", "선생님 이름을 입력해 주세요."),
    ("identity.school-name", "학교 이름", "학교명", "학교 이름을 입력해 주세요."),
    ("identity.school-level", "학교급", "학교급", "학교급을 골라 주세요."),
]
_HOMEROOM_FIELDS = [
    ("identity.homeroom-grade", "담임 학년", "담임학년", "담임 학년을 입력해 주세요."),
    ("identity.homeroom-class", "담임 반", "담임반", "담임 반을 입력해 주세요."),
]
_TIME_FIELDS = ["출근시간", "퇴근시간", "조회시작", "1교시시작", "점심종료시간"]
_LAST_PERIOD_FIELDS = [
    ("월", "월요일마지막교시"), ("화", "화요일마지막교시"), ("수", "수요일마지막교시"),
    ("목", "목요일마지막교시"), ("금", "금요일마지막교시"),
]
_CONNECT_LINK_FIELDS = [
    ("connect.work-calendar", "개인 업무 Calendar", "업무캘린더ID", "개인 업무 일정을 등록할 Calendar를 골라 주세요."),
    ("connect.school-calendar", "학사 일정 Calendar", "학사일정캘린더ID", "학사 일정을 등록할 Calendar를 골라 주세요."),
    ("connect.work-tasks", "개인 업무 Tasks 목록", "업무Tasks목록ID", "개인 업무를 등록할 Tasks 목록을 골라 주세요."),
]
_HOMEROOM_TASKS_FIELD = (
    "connect.homeroom-tasks", "조종례 전달 Tasks 목록", "담임안내Tasks목록ID",
    "조종례 전달사항을 등록할 Tasks 목록을 골라 주세요.",
)


def _identity_checks(profile: dict[str, str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for key, label, target, fix in _IDENTITY_TEXT_FIELDS:
        value = profile.get(target, "")
        results.append(CheckResult(
            key, label, bool(value), value or "아직 입력하지 않았어요",
            "" if value else fix, card="identity", target=target,
        ))

    homeroom_value = profile.get("담임여부", "")
    homeroom_valid = homeroom_value in _HOMEROOM_YES or homeroom_value in _HOMEROOM_NO
    results.append(CheckResult(
        "identity.homeroom", "담임 여부", homeroom_valid,
        homeroom_value or "아직 고르지 않았어요",
        "" if homeroom_valid else "담임 여부를 골라 주세요.",
        card="identity", target="담임여부",
    ))
    if homeroom_value in _HOMEROOM_YES:
        for key, label, target, fix in _HOMEROOM_FIELDS:
            value = profile.get(target, "")
            results.append(CheckResult(
                key, label, bool(value), value or "아직 입력하지 않았어요",
                "" if value else fix, card="identity", target=target,
            ))

    for target in _TIME_FIELDS:
        value = profile.get(target, "")
        ok = bool(value) and _is_time_text(value)
        results.append(CheckResult(
            f"identity.time.{target}", target, ok, value or "아직 입력하지 않았어요",
            "" if ok else f"{target}을(를) 시:분으로 입력해 주세요.",
            card="identity", target=target,
        ))
    for day, target in _LAST_PERIOD_FIELDS:
        value = profile.get(target, "")
        ok = value.isdigit() and 1 <= int(value) <= 7
        results.append(CheckResult(
            f"identity.last-period.{day}", f"{day}요일 마지막 교시", ok,
            value or "아직 고르지 않았어요",
            "" if ok else f"{day}요일 마지막 교시를 1에서 7 사이로 골라 주세요.",
            card="identity", target=target,
        ))
    return results


def _connect_checks(profile: dict[str, str], statuses: dict | None = None) -> list[CheckResult]:
    results: list[CheckResult] = []
    fields = list(_CONNECT_LINK_FIELDS)
    if profile.get("담임여부", "") in _HOMEROOM_YES:
        fields.append(_HOMEROOM_TASKS_FIELD)
    for key, label, target, fix in fields:
        value = profile.get(target, "")
        status = (statuses or {}).get(target, {})
        state = status.get("state") if status.get("id") == value else None
        # Only an answer from the server says ready or unavailable; a failed check says neither (C6, C8).
        ok = False if not value or state == "unavailable" else True if state == "ready" else None
        detail = status.get("detail", "저장된 연결을 아직 확인하지 못했어요.") if value else "아직 고르지 않았어요"
        results.append(CheckResult(
            key, label, ok, detail,
            ("" if ok is not False else detail) if value else fix, card="connect", tab="messenger", target=target,
            unread=ok is None,
        ))
    return results


def saved_target_ids(config_dir: Path) -> dict[str, str]:
    """The Google targets this profile uses; the homeroom list only for a homeroom teacher."""
    profile = _read_profile_csv_values(Path(config_dir))
    fields = list(_CONNECT_LINK_FIELDS)
    if profile.get("담임여부", "") in _HOMEROOM_YES:
        fields.append(_HOMEROOM_TASKS_FIELD)
    return {target: profile[target] for _key, _label, target, _fix in fields if profile.get(target)}


def _signin_checks(identity: dict | None) -> list[CheckResult]:
    """The rows of GET /v1/me. A check that did not finish is not a logout (LOGIN-17, LOGIN-32)."""
    common = {"card": "settings", "target": "google-login"}
    if not identity or identity.get("login_state") == "error":
        detail = "서버에 연결하지 못했어요" if (identity or {}).get("server_unreachable") else "확인하지 못했어요"
        return [CheckResult("settings.google-login", "Google 로그인과 권한", None, detail, unread=True, **common)]
    if not identity.get("logged_in"):
        return [CheckResult(
            "settings.google-login", "Google 로그인과 권한", False,
            identity.get("authorization_detail") or GOOGLE_LOGIN_FIX, GOOGLE_LOGIN_FIX, **common,
        )]
    rows = [CheckResult("settings.google-login", "Google 로그인과 권한", True, str(identity.get("user") or ""), **common)]
    grant = (identity.get("grant") or {}).get("state")
    if grant in {"partial", "refresh-required"}:
        rows.append(CheckResult("settings.grant", "Google 권한", False, GRANT_MISSING_TEXT, GRANT_MISSING_TEXT, **common))
    elif grant == "check-failed":
        rows.append(CheckResult("settings.grant", "Google 권한", None, "확인하지 못했어요", unread=True, **common))
    return rows


def _gemini_check(deps: DoctorDeps, bridge_settings) -> CheckResult:
    key_status, key_detail = deps.gemini_checker(
        bridge_settings.gemini_api_key, bridge_settings.gemini_model
    )
    common = {"card": "connect", "tab": "messenger", "target": "gemini_api_key"}
    if key_status == "unchecked":
        return CheckResult("connect.gemini-key", "Gemini API key", None, key_detail, **common)
    if key_status == "ok":
        return CheckResult("connect.gemini-key", "Gemini API key", True, "key가 정상이에요", **common)
    if key_status == "rate-limited":
        return CheckResult("connect.gemini-key", "Gemini API key", True, GEMINI_RATE_LIMIT_DETAIL, **common)
    fix = {
        "missing": GEMINI_KEY_MISSING_FIX,
        "invalid": GEMINI_KEY_INVALID_FIX,
        "network": GEMINI_NETWORK_FIX,
        "service-unavailable": "Google Gemini가 일시적으로 응답하지 못했어요. 키를 바꾸지 말고 잠시 후 다시 확인해 주세요.",
        "model-unavailable": "Gemini 3.5 Flash-Lite 모델을 사용할 수 없어요. 키를 발급한 계정에서 이 모델을 쓸 수 있는지 확인해 주세요.",
        "forbidden": "Google AI Studio에서 키의 사용 권한을 확인해 주세요.",
    }.get(key_status, "Google이 키 확인 요청을 처리하지 못했어요. 키 오류로 확인된 것은 아닙니다.")
    detail = "아직 입력하지 않았어요" if key_status == "missing" else (key_detail or key_status)
    return CheckResult("connect.gemini-key", "Gemini API key", False, detail, fix, **common)


def run_doctor_checks(
    config_dir: Path,
    deps: DoctorDeps | None = None,
    *,
    identity: dict | None = None,
    target_statuses: dict | None = None,
) -> list[CheckResult]:
    """`identity` is this PC's reading of GET /v1/me and `target_statuses` its check of the saved
    targets. Without them the sign-in rows say 확인하지 못했어요: nothing here calls a local tool."""
    deps = deps or DoctorDeps()
    config_dir = Path(config_dir)
    profile = _read_profile_csv_values(config_dir)

    results: list[CheckResult] = []
    results.extend(_identity_checks(profile))

    bridge_settings = load_settings(paths.settings_path(config_dir))
    results.append(_gemini_check(deps, bridge_settings))
    results.extend(_connect_checks(profile, target_statuses))
    results.extend(_signin_checks(identity))

    try:
        parse_hotkey(bridge_settings.hotkey)
        results.append(CheckResult(
            "settings.hotkey", "메신저 단축키", True, f"단축키 {bridge_settings.hotkey}",
            card="settings", target="hotkey",
        ))
    except ValueError:
        results.append(CheckResult(
            "settings.hotkey", "메신저 단축키", False, "저장된 단축키를 읽지 못했어요",
            HOTKEY_FIX, card="settings", target="hotkey",
        ))

    running = bool(deps.find_helper_window())
    results.append(CheckResult(
        "settings.helper", "메신저 단축키", running,
        "실행 중" if running else "꺼져 있음 — 단축키가 동작하지 않아요",
        "" if running else HELPER_FIX,
        card="settings", target="helper",
    ))
    results.append(CheckResult(
        "settings.autostart", "Windows 시작 시 자동 실행", None,
        "켜짐" if deps.autostart_checker() else "꺼짐",
        card="settings", target="autostart",
    ))
    return results


def format_report(results: list[CheckResult]) -> str:
    lines = []
    for result in results:
        mark = "INFO" if result.ok is None else ("OK  " if result.ok else "FAIL")
        line = f"[{mark}] {result.label}: {result.detail}"
        if result.fix and result.ok is False:
            line += f" → {result.fix}"
        lines.append(line)
    return "\n".join(lines)


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(result.ok is False for result in results) else 0
