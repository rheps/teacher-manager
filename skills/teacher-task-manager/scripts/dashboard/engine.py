# skills/teacher-task-manager/scripts/dashboard/engine.py
from __future__ import annotations

import csv
import errno
import hashlib
import io
import json as _json
import os
import platform
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import zipfile
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
import urllib.error
from urllib.parse import unquote, urlsplit

import install_attendance_automation
import attendance_connection_handover
import attendance_script_update
import attendance_workbook_identity
import attendance_workbook_transition
import parse_settings
from attendance_install_record import (
    AttendanceInstallRecordError,
    CONNECTION_FIELDS,
    SCRIPT_ATTESTATION_FIELD,
    SETUP_ACCOUNT_FIELD,
    SCRIPT_UPDATE_REQUIRED_FIELD,
    attendance_script_is_attested,
    build_script_attestation,
    ensure_create_only_install_backup,
    load_attendance_install_record,
    read_verified_canonical_record,
    read_attendance_install_snapshot,
    replace_attendance_install_record,
    validate_verified_canonical_record,
    write_attendance_install_record,
)
from brity_bridge import (
    atomic_io,
    ai_skill_install,
    autostart_win,
    bundle_paths,
    component_lock,
    component_update,
    gws_env,
    google_account,
    hotkey_win,
    managed_node,
    paths,
    process_supervision,
    process_win,
    tool_runtime,
    recovery,
)
from brity_bridge.doctor import CheckResult, DoctorDeps, _default_run_command, run_doctor_checks
from brity_bridge.gemini_analyze import check_gemini_key
from brity_bridge.hotkey import MODIFIER_ORDER, parse_hotkey
from brity_bridge.settings import ALLOWED_GEMINI_MODELS, load_settings, save_settings
from dashboard import central_chat, external_url, version

HELPER_WINDOW_CLASS = "BrityBridgeTrayWindow"
_WM_CLOSE = 0x0010
_PROBE_HOTKEY_ID = 0xB111
DEFAULT_ATTACHMENT_FOLDER = r"C:\BrityWorks\BrityMessenger\download"


@dataclass
class HomeCheckDeps:
    doctor_deps: DoctorDeps = field(default_factory=DoctorDeps)
    document_probe: object = None
    attendance_ui_probe: object = None


def _timetable_check(config_dir: Path) -> CheckResult:
    """빈 시간표 칸은 정상 — 파일이 없거나 읽히지 않을 때만 문제다."""
    xlsx_path = Path(config_dir) / "weekly-timetable.xlsx"
    csv_path = Path(config_dir) / "weekly-timetable.csv"
    ok = True
    detail = "weekly-timetable.xlsx"
    if xlsx_path.exists():
        try:
            parse_settings._read_timetable_xlsx(xlsx_path)
        except (ValueError, zipfile.BadZipFile, OSError):
            ok = False
            detail = "시간표 파일을 읽지 못했어요"
    elif csv_path.exists():
        detail = "weekly-timetable.csv"
    else:
        ok = False
        detail = "시간표 파일이 없어요"
    return CheckResult(
        "timetable.file", "시간표 파일", ok, detail,
        "" if ok else "시간표 카드에서 시간표를 저장해 주세요.",
        card="timetable", target="timetable",
    )


_READINESS_ROWS = [
    ("python", "프로그램 실행 기능"),
    ("documents", "문서 읽기 기능"),
    ("screen", "화면 표시 기능"),
]

# 출결 준비 상태를 홈 점검 한 건으로 바꾸는 규칙 — 로그인·개인 설정 문제는
# 설정/내 정보 카드가 이미 소유하므로 출결에서 다시 문제로 세지 않는다.
_ATTENDANCE_STATE_TO_OK = {
    "ready": True,
    "script-check-required": False,
    "script-update-required": False,
    "connection-repair-required": False,
    "not-ready": False,
    "failed": False,
    "gws-required": None,
    "login-required": None,
    "account-required": None,
    "auth-error": None,
    "profile-required": None,
}


def home_checks(config_dir: Path, deps: HomeCheckDeps | None = None) -> list[CheckResult]:
    if deps is None:
        deps = HomeCheckDeps()
    elif isinstance(deps, DoctorDeps):
        deps = HomeCheckDeps(doctor_deps=deps)
    config_dir = Path(config_dir)
    doctor_deps = deps.doctor_deps or DoctorDeps()

    results = list(run_doctor_checks(config_dir, doctor_deps))
    login_problem = next(
        (
            row
            for row in results
            if row.key in {"settings.google-login", "settings.goedu-account"}
            and row.ok is False
        ),
        None,
    )
    if login_problem is not None:
        # 같은 로그인 하나가 설정과 연결을 함께 막는다. 설정이 왼쪽 카드이므로
        # 선생님은 먼저 설정에서 고치고, 다음 홈 점검에서 두 경고가 함께 사라진다.
        results.append(CheckResult(
            "connect.google-login", "Google 로그인", False,
            login_problem.detail,
            login_problem.fix,
            card="connect", tab="messenger", target="google-login",
        ))
    results.append(_timetable_check(config_dir))

    readiness = computer_readiness(doctor_deps.run_command, deps.document_probe)
    for key, label in _READINESS_ROWS:
        row = readiness[key]
        results.append(CheckResult(
            f"settings.{key}", label, bool(row["ready"]), str(row["detail"]),
            "" if row["ready"] else "설정에서 컴퓨터 준비 항목을 설치해 주세요.",
            card="settings", target=key,
        ))

    saved = load_settings(paths.settings_path(config_dir))
    folder = attachment_folder_status(saved.brity_download_dir)
    results.append(CheckResult(
        "settings.attachment-folder", "첨부파일 다운로드 폴더", bool(folder["ready"]), folder["detail"],
        "" if folder["ready"] else "설정에서 첨부파일 다운로드 폴더를 다시 골라 주세요.",
        card="settings", target="brity_download_dir",
    ))

    attendance_ui_visible = (
        bool(deps.attendance_ui_probe())
        if callable(deps.attendance_ui_probe)
        else attendance_ui_enabled()
    )
    if attendance_ui_visible:
        attendance = read_attendance_status(config_dir, doctor_deps.run_command)
        # 로그인처럼 출결 밖에서 해결하는 상태만 위 목록에서 안내용(None)으로 둔다.
        # 새 안전 정지 상태가 추가돼도 홈이 실수로 정상 취급하지 않게 나머지는 문제로 본다.
        attendance_ok = _ATTENDANCE_STATE_TO_OK.get(attendance.state, False)
        results.append(CheckResult(
            "connect.attendance", "출결 시트", attendance_ok,
            attendance.detail or "출결 업무 준비가 끝났어요",
            "" if attendance_ok is not False else (attendance.detail or "연결의 출결 탭에서 출결 준비 시작하기를 눌러 주세요."),
            card="connect", tab="attendance", target="attendance-setup",
        ))
    return results


def verify_gemini_key(api_key: str, model: str, transport=None) -> tuple[str, str]:
    if model not in ALLOWED_GEMINI_MODELS:
        raise ValueError("목록에 있는 Gemini 모델을 골라 주세요")
    return check_gemini_key(api_key, model, transport=transport)


def _default_register(modifiers: int, key: int) -> bool:
    import ctypes

    return bool(ctypes.windll.user32.RegisterHotKey(None, _PROBE_HOTKEY_ID, modifiers, key))


def _default_unregister() -> None:
    import ctypes

    ctypes.windll.user32.UnregisterHotKey(None, _PROBE_HOTKEY_ID)


def probe_hotkey(text: str, register=None, unregister=None, modifier_probe=None) -> str:
    """단축키를 시험 등록해 즉석에서 사용 가능 여부를 판정한다.

    주의: 도우미가 같은 단축키로 이미 실행 중이면 taken으로 나온다 — GUI는 그 경우
    '도우미가 이미 이 단축키로 실행 중이면 정상입니다'를 함께 보여준다.
    """
    try:
        spec = parse_hotkey(text)
    except ValueError:
        return "invalid"
    if spec.modifier_only:
        probe = modifier_probe or hotkey_win.probe_modifier_hotkey
        return "available" if probe(spec) else "taken"
    register = register or _default_register
    unregister = unregister or _default_unregister
    if not register(spec.modifiers, spec.key_code):
        return "taken"
    unregister()
    return "available"


def build_hotkey(modifiers, key) -> str:
    """옛 체크박스 화면도 새 단축키 규칙을 쓰도록 문자열을 조립한다."""
    mods = [str(m).strip().lower() for m in (modifiers or [])]
    for name in mods:
        if name not in MODIFIER_ORDER:
            raise ValueError(f"지원하지 않는 수식키예요: {name}")
    ordered = [name for name in MODIFIER_ORDER if name in mods]
    ordinary = str(key or "").strip().lower()
    return parse_hotkey("+".join(ordered + ([ordinary] if ordinary else []))).text


def save_hotkey(
    config_dir: Path, text: str, register=None, unregister=None,
    modifier_probe=None, restart=None,
) -> dict:
    """조립 → (바뀐 조합만) 검증 → 저장 → 도우미 재시작.

    현재 저장된 단축키와 같은 조합이면 probe를 건너뛴다 — 도우미가 이미 그 키를
    등록하고 있어 probe가 항상 실패하기 때문이다.
    """
    text = parse_hotkey(text).text
    saved = load_settings(paths.settings_path(Path(config_dir)))
    if text != saved.hotkey:
        status = probe_hotkey(
            text, register=register, unregister=unregister, modifier_probe=modifier_probe
        )
        if status != "available":
            reason = (
                "다른 프로그램이 이 조합을 쓰고 있어요. 다른 조합을 골라 주세요."
                if status == "taken"
                else "단축키 형식이 잘못됐어요. 다른 조합을 눌러 주세요."
            )
            return {"saved": False, "hotkey": text, "restarted": False, "reason": reason}
    save_bridge_settings(config_dir, {"hotkey": text})
    restarter = restart or restart_helper
    if restarter():
        return {"saved": True, "hotkey": text, "restarted": True, "reason": ""}

    save_bridge_settings(config_dir, {"hotkey": saved.hotkey})
    restored = bool(restarter())
    return {
        "saved": False,
        "hotkey": saved.hotkey,
        "restarted": restored,
        "reason": "새 단축키를 켜지 못해 이전 단축키로 되돌렸어요.",
    }


def _send_helper_hotkey_message(message: int, value: int, sender=None) -> bool:
    if sender is not None:
        return bool(sender(message, value))
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(HELPER_WINDOW_CLASS, None)
    if not hwnd:
        return False
    return bool(user32.PostMessageW(ctypes.c_void_p(hwnd), message, int(value), 0))


def pause_helper_hotkey(seconds: int = 15, sender=None) -> bool:
    return _send_helper_hotkey_message(
        hotkey_win.WM_HOTKEY_PAUSE, min(30, max(1, int(seconds))), sender=sender
    )


def resume_helper_hotkey(sender=None) -> bool:
    return _send_helper_hotkey_message(hotkey_win.WM_HOTKEY_RESUME, 0, sender=sender)


def save_bridge_settings(config_dir: Path, updates: dict) -> None:
    settings_path = paths.settings_path(Path(config_dir))
    bridge_settings = load_settings(settings_path)
    known = asdict(bridge_settings)
    for key, value in updates.items():
        if key in known:
            if key == "gemini_model" and value not in ALLOWED_GEMINI_MODELS:
                raise ValueError("목록에 있는 Gemini 모델을 골라 주세요")
            setattr(bridge_settings, key, value)
    save_settings(settings_path, bridge_settings)


def autostart_enabled() -> bool:
    try:
        return bool(autostart_win.is_autostart_enabled())
    except (ImportError, OSError):
        return False


def attachment_folder_status(path_text: str) -> dict:
    value = str(path_text or "").strip() or DEFAULT_ATTACHMENT_FOLDER
    path = Path(value).expanduser()
    if not path.exists():
        return {"ready": False, "detail": "폴더를 찾지 못했어요", "path": str(path)}
    if not path.is_dir():
        return {"ready": False, "detail": "이 폴더를 열 수 없어요", "path": str(path)}
    try:
        next(path.iterdir(), None)
    except OSError:
        return {"ready": False, "detail": "이 폴더를 열 수 없어요", "path": str(path)}
    return {"ready": True, "detail": "폴더를 확인했어요", "path": str(path)}


def choose_attachment_folder(current_path: str = "") -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(
            initialdir=str(current_path or DEFAULT_ATTACHMENT_FOLDER), mustexist=True
        )
        return str(selected or "")
    finally:
        root.destroy()


def _attendance_sheet_for_gemini_key(config_dir: Path) -> str:
    """Gemini 키를 넣을 현재 출결 시트 번호를 설치 기록에서만 읽는다."""

    return attendance_workbook_identity.current_attendance_spreadsheet_id(config_dir)


ATTENDANCE_SHEET_PUSH_FAILURE = (
    "이 컴퓨터 저장은 마쳤지만 출결표에는 반영하지 못했어요. "
    "Google 로그인과 인터넷 연결을 확인한 뒤 다시 저장해 주세요."
)


def push_gemini_key_to_attendance_sheet(
    config_dir: Path,
    run_command=None,
    *,
    gws_executable: str | None = None,
) -> dict:
    """이 컴퓨터에 저장된 Gemini 키를 출결 시트 `설정` 탭에 적어 둔다.

    시트 안 Apps Script는 이 컴퓨터의 settings.json을 읽지 못한다. 여기서 넣어 두지 않으면
    선생님이 시트에서 같은 키를 또 붙여넣게 된다. 넣지 못해도 오류를 밖으로 던지지 않는다 —
    저장 버튼 하나가 인터넷 사정 때문에 실패한 것처럼 보이면 안 된다.
    """
    from dashboard import central_chat

    config_dir = Path(config_dir)
    key = str(load_settings(paths.settings_path(config_dir)).gemini_api_key or "").strip()
    if not key:
        return {"state": "skipped", "detail": "아직 인공지능 연결 키를 넣지 않았어요."}
    spreadsheet_id = _attendance_sheet_for_gemini_key(config_dir)
    if not spreadsheet_id:
        return {"state": "skipped", "detail": "아직 출석부를 만들지 않았어요."}
    runner = run_command or central_chat._default_run_command
    try:
        gws = str(gws_executable or resolve_gws(runner))
        require_goedu_gws_session(runner, gws)
        rows = central_chat._read_settings_rows(spreadsheet_id, runner, gws)
        central_chat._upsert_settings_value(
            spreadsheet_id, rows, "GEMINI_API_KEY", key, runner, gws
        )
    except Exception:  # noqa: BLE001 - 외부 원문은 화면에 보내지 않는다
        return {"state": "failed", "detail": ATTENDANCE_SHEET_PUSH_FAILURE}
    return {"state": "ok", "detail": "출석부의 설정 화면에도 반영했어요."}


def read_first_time_setup_done(config_dir: Path, run_command, gws_executable: str) -> dict:
    """시트의 [처음 설정 한 번에 끝내기] 완료 표시를 읽는다. 못 읽으면 미완료로 본다.

    시트 안 Apps Script가 네 단계를 모두 마치면 `설정` 탭에
    FIRST_TIME_SETUP_DONE과 ATTENDANCE_CONNECTION_CODE 줄을 적는다(Code.gs).
    현재 정식 출석부의 전체 Google 번호에서 계산한 확인번호와 처음 연결한 학교
    계정이 모두 정확히 같을 때만 완료로 인정한다 — 네트워크·권한 실패는 오류가
    아니라 '아직'이다.
    """
    from dashboard import central_chat

    record_path = paths.attendance_install_record_path(Path(config_dir))
    if not record_path.exists():
        return {"done": False, "value": ""}
    try:
        record = read_verified_canonical_record(record_path)
    except (OSError, AttendanceInstallRecordError):
        return {"done": False, "value": ""}
    spreadsheet_id = str(record.get("spreadsheet_id", "") or "").strip()
    expected_connection_code = attendance_workbook_identity.attendance_connection_code(
        spreadsheet_id
    )
    record_account = str(record.get(SETUP_ACCOUNT_FIELD, "") or "").strip()
    status_account = str(
        _read_setup_status(config_dir).get("account", "") or ""
    ).strip()
    if (
        record_account
        and status_account
        and record_account.casefold() != status_account.casefold()
    ):
        return {"done": False, "value": ""}
    expected_account = record_account or status_account
    if not spreadsheet_id or not expected_connection_code or not expected_account:
        return {"done": False, "value": ""}
    try:
        rows = central_chat._read_settings_rows(spreadsheet_id, run_command, gws_executable)
    except Exception as error:  # noqa: BLE001 - 외부 원문은 숨기고 공통 세 차례 확인으로 넘긴다
        raise recovery.RetryableOperationError(
            "ATTENDANCE_FIRST_SETUP_READ",
            "출석부의 처음 설정 상태를 다시 읽고 있어요.",
        ) from error
    done_values = []
    connection_codes = []
    for row in rows or []:
        if not isinstance(row, list) or not row:
            continue
        key = str(row[0]).strip()
        value = str(row[1]).strip() if len(row) > 1 else ""
        if key == "FIRST_TIME_SETUP_DONE":
            done_values.append(value)
        elif key == "ATTENDANCE_CONNECTION_CODE":
            connection_codes.append(value)
    if connection_codes != [expected_connection_code]:
        return {"done": False, "value": ""}
    value = ""
    for item in done_values:
        parts = item.split()
        if (
            len(parts) >= 3
            and parts[0] == expected_connection_code
            and parts[1].casefold() == expected_account.casefold()
        ):
            value = item
            break
    return {"done": bool(value), "value": value}


def save_messenger_settings(
    config_dir: Path,
    updates: dict,
    *,
    register=None,
    unregister=None,
    modifier_probe=None,
    restart=None,
    autostart_checker=None,
    autostart_enable=None,
    autostart_disable=None,
    push_key=None,
    helper_exists=None,
) -> dict:
    """메신저 선택을 확인해 저장한 뒤, 도우미 시작은 별도로 회복한다."""
    if not isinstance(updates, dict):
        raise ValueError("메신저 설정 모양이 올바르지 않아요")
    allowed = {
        "gemini_api_key", "gemini_model", "hotkey", "autostart", "brity_download_dir",
        "error_reports_enabled",
    }
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError("메신저 설정에 알 수 없는 항목이 있어요")

    settings_path = paths.settings_path(Path(config_dir))
    previous = load_settings(settings_path)
    candidate = load_settings(settings_path)

    if "gemini_api_key" in updates:
        if not isinstance(updates["gemini_api_key"], str):
            raise ValueError("Gemini API 키 모양이 올바르지 않아요")
        candidate.gemini_api_key = updates["gemini_api_key"].strip()
    if "gemini_model" in updates:
        model = updates["gemini_model"]
        if model not in ALLOWED_GEMINI_MODELS:
            raise ValueError("목록에 있는 Gemini 모델을 골라 주세요")
        candidate.gemini_model = model
    if "hotkey" in updates:
        if not isinstance(updates["hotkey"], str):
            raise ValueError("단축키 모양이 올바르지 않아요")
        candidate.hotkey = parse_hotkey(updates["hotkey"]).text
    if "brity_download_dir" in updates:
        folder = attachment_folder_status(updates["brity_download_dir"])
        if not folder["ready"]:
            raise ValueError(folder["detail"])
        candidate.brity_download_dir = folder["path"]
    if "autostart" in updates and not isinstance(updates["autostart"], bool):
        raise ValueError("자동 시작 선택이 올바르지 않아요")
    if "error_reports_enabled" in updates:
        if not isinstance(updates["error_reports_enabled"], bool):
            raise ValueError("오류 자동 보고 선택이 올바르지 않아요")
        candidate.error_reports_enabled = updates["error_reports_enabled"]

    if candidate.hotkey != previous.hotkey:
        status = probe_hotkey(
            candidate.hotkey,
            register=register,
            unregister=unregister,
            modifier_probe=modifier_probe,
        )
        if status != "available":
            reason = (
                "다른 프로그램이 이 조합을 쓰고 있어요. 다른 조합을 골라 주세요."
                if status == "taken"
                else "단축키 형식이 잘못됐어요. 다른 조합을 눌러 주세요."
            )
            return {
                "saved": False,
                "hotkey": previous.hotkey,
                "restarted": False,
                "reason": reason,
            }

    checker = autostart_checker or autostart_enabled
    enable = autostart_enable or autostart_win.enable_autostart
    disable = autostart_disable or autostart_win.disable_autostart
    autostart_requested = "autostart" in updates
    new_autostart = bool(updates.get("autostart")) if autostart_requested else None
    old_autostart = {"value": None}

    def original_autostart() -> bool:
        if old_autostart["value"] is None:
            try:
                old_autostart["value"] = bool(checker())
            except Exception as error:  # noqa: BLE001 - local shell check can be briefly unavailable
                raise recovery.RetryableOperationError(
                    "AUTOSTART_READ", "자동 시작 선택을 다시 확인하고 있어요."
                ) from error
        return old_autostart["value"]

    settings_updates = {key: value for key, value in updates.items() if key != "autostart"}
    if settings_updates or autostart_requested:
        expected = {key: getattr(candidate, key) for key in settings_updates}

        def verified() -> tuple[bool, dict]:
            try:
                saved = _read_settings_scope(settings_path, expected)
                autostart_ok = (not autostart_requested) or bool(checker()) == new_autostart
            except recovery.RetryableOperationError:
                raise
            except Exception as error:  # noqa: BLE001 - local checker failures are recoverable
                raise recovery.RetryableOperationError(
                    "AUTOSTART_READ", "자동 시작 선택을 다시 확인하고 있어요."
                ) from error
            return saved == expected and autostart_ok, saved

        def write_and_verify() -> dict:
            if settings_updates:
                _write_settings_scope(settings_path, candidate, expected)
            if autostart_requested and original_autostart() != new_autostart:
                try:
                    (enable if new_autostart else disable)()
                except Exception as error:  # noqa: BLE001 - restore the observed old choice when possible
                    try:
                        (enable if original_autostart() else disable)()
                    except Exception:
                        pass
                    raise recovery.RetryableOperationError(
                        "AUTOSTART_WRITE", "자동 시작 선택을 적용하지 못했어요."
                    ) from error
            complete, saved = verified()
            if not complete:
                raise recovery.RetryableOperationError(
                    "AUTOSTART_VERIFY", "자동 시작 선택을 다시 확인하고 있어요."
                )
            return saved

        recovery.run_operation(
            "messenger_save", "설정을 저장하지 못했어요.", write_and_verify,
            verify=verified, delays=recovery.LOCAL_DELAYS,
            change_status="기존 설정은 그대로입니다.", app_version=version.APP_VERSION,
        )

    # 설정을 다시 쓰지 않는다. 이미 읽어 확인한 값은 그대로 두고 도우미 시작만 다시 한다.
    restart_result = restart_helper_verified(
        stop=stop_helper,
        start=restart or start_helper,
        exists=helper_exists or helper_window_exists,
        app_version=version.APP_VERSION,
    )
    sheet_push = {"state": "skipped", "detail": ""}
    if "gemini_api_key" in updates:
        pusher = push_key or push_gemini_key_to_attendance_sheet
        try:
            sheet_push = pusher(Path(config_dir))
            if not isinstance(sheet_push, dict):
                sheet_push = {"state": "failed", "detail": ATTENDANCE_SHEET_PUSH_FAILURE}
            elif sheet_push.get("state") == "failed":
                sheet_push = {**sheet_push, "detail": ATTENDANCE_SHEET_PUSH_FAILURE}
        except Exception:  # noqa: BLE001 - 저장 자체는 이미 끝났다
            sheet_push = {"state": "failed", "detail": ATTENDANCE_SHEET_PUSH_FAILURE}
    return {
        "saved": True,
        "hotkey": candidate.hotkey,
        "restarted": bool(restart_result["restarted"]),
        "reason": "",
        "sheet_push": sheet_push,
    }


def helper_window_exists() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    find_window = ctypes.windll.user32.FindWindowW
    find_window.restype = ctypes.c_void_p
    return bool(find_window(HELPER_WINDOW_CLASS, None))


def stop_helper(timeout_seconds: float = 5.0) -> bool:
    """실행 중인 도우미 창에 WM_CLOSE를 보내 정상 종료를 기다린다."""
    if sys.platform != "win32":
        return True
    import ctypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(HELPER_WINDOW_CLASS, None)
    if not hwnd:
        return True
    user32.PostMessageW(ctypes.c_void_p(hwnd), _WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not helper_window_exists():
            return True
        time.sleep(0.1)
    return False


def start_helper(timeout_seconds: float = 5.0) -> bool:
    if sys.platform != "win32":
        return False
    from brity_bridge import bundle_paths

    if bundle_paths.is_frozen():
        process_win.popen_hidden(autostart_win.autostart_command())
    else:
        launcher = autostart_win.autostart_command()[0]
        main_py = Path(autostart_win.__file__).resolve().parent / "__main__.py"
        process_win.popen_hidden([launcher, str(main_py), "run"])
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if helper_window_exists():
            return True
        time.sleep(0.2)
    return False


def restart_helper() -> bool:
    if not stop_helper():
        return False
    return start_helper()


def restart_helper_verified(
    *,
    stop=None,
    start=None,
    exists=None,
    app_version: str = version.APP_VERSION,
) -> dict:
    """Restart once, then retry only the start while checking for a live helper."""

    stopper = stop or stop_helper
    starter = start or start_helper
    checker = exists or helper_window_exists

    def stop_once() -> dict:
        if stopper():
            return {"stopped": True}
        try:
            # 종료 요청의 반환값이 늦어도 창이 이미 사라졌다면 안전하게 다음 단계로 간다.
            if not checker():
                return {"stopped": True}
        except Exception as error:  # noqa: BLE001 - Windows readiness can be briefly unavailable
            raise recovery.RetryableOperationError(
                "HELPER_STOP_READ", "Brity 도우미 종료 상태를 다시 확인하고 있어요."
            ) from error
        raise recovery.RetryableOperationError(
            "HELPER_STOP", "기존 Brity 도우미가 아직 종료되지 않았어요."
        )

    recovery.run_operation(
        "helper_start",
        "설정은 저장했지만 Brity 도우미를 시작하지 못했어요.",
        stop_once,
        delays=recovery.LOCAL_DELAYS,
        change_status="저장한 설정은 그대로입니다.",
        app_version=app_version,
    )

    def start_once() -> dict:
        if starter():
            return {"restarted": True}
        raise recovery.RetryableOperationError("HELPER_START", "Brity 도우미를 아직 시작하지 못했어요.")

    return recovery.run_operation(
        "helper_start",
        "설정은 저장했지만 Brity 도우미를 시작하지 못했어요.",
        start_once,
        verify=lambda: (bool(checker()), {"restarted": True}),
        delays=recovery.LOCAL_DELAYS,
        change_status="저장한 설정은 그대로입니다.",
        app_version=app_version,
    )


def ensure_helper_running(exists=None, start=None) -> None:
    """감시 도우미가 안 떠 있으면 시작한다. 운영체제 판단은 하부 함수가 맡는다."""
    exists = exists or helper_window_exists
    start = start or start_helper
    if not exists():
        start()


def build_shortcut_command(target_script, icon_path, working_dir, link_name="Teacher Manager") -> list[str]:
    """PowerShell로 바탕화면 바로가기를 만드는 명령을 조립한다. 옛 한국어 이름 바로가기는 정리한다."""
    def q(value: str) -> str:
        return str(value).replace("'", "''")

    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "Remove-Item -LiteralPath (Join-Path $desktop '티처 매니저.lnk') -ErrorAction SilentlyContinue; "
        f"$lnk = $ws.CreateShortcut((Join-Path $desktop '{q(link_name)}.lnk')); "
        "$lnk.TargetPath = 'wscript.exe'; "
        f"$lnk.Arguments = '\"{q(target_script)}\"'; "
        f"$lnk.IconLocation = '{q(icon_path)}'; "
        f"$lnk.WorkingDirectory = '{q(working_dir)}'; "
        "$lnk.WindowStyle = 7; "
        "$lnk.Save()"
    )
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def ensure_desktop_shortcut(config_dir, skill_root, run_command=None) -> bool:
    """바탕화면 바로가기를 한 번만 만든다. 실패해도 프로그램 실행은 계속된다."""
    from brity_bridge import bundle_paths

    if bundle_paths.is_frozen():
        return False  # 설치본에서는 설치 마법사가 바로가기를 만든다
    # v3: 바로가기 이름을 Teacher Manager로 바꾸면서 기존 설치도 한 번 다시 만든다.
    marker = Path(config_dir) / "desktop-shortcut-v3.done"
    try:
        if marker.exists():
            return False
        run_command = run_command or _default_run_command
        skill_root = Path(skill_root)
        launcher = skill_root / "시작하기.vbs"
        icon = skill_root / "assets" / "teacher-manager.ico"
        code, _output = run_command(
            build_shortcut_command(str(launcher), str(icon), str(skill_root))
        )
        if code != 0:
            return False
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("done", encoding="utf-8")
    except Exception:  # noqa: BLE001 - 바로가기 실패가 실행을 막으면 안 된다
        return False
    return True


@dataclass(frozen=True)
class AttendanceStatus:
    state: str             # ready | script-check-required | script-update-required | connection-repair-required | not-ready | gws-required | login-required | account-required | auth-error | profile-required | failed
    account: str = ""      # 처음 준비할 때 사용한 계정
    current_user: str = ""  # 지금 로그인한 계정
    spreadsheet_url: str = ""
    connection_code: str = ""  # 시트·대시보드에서 같은 출석부인지 대조하는 확인번호
    template_doc_url: str = ""  # 결석 신고서 서식 — 화면 Docs 칸의 [서식 열기]가 쓴다
    detail: str = ""
    failed_service: str = ""  # sheet | docs | tasks | setup | 빈 문자열
    created: bool = False  # 이번 호출에서 새로 만들었는지
    school_year: str = ""  # 기록에 새긴 학년도 — 도장이 없으면 지금 학년도로 본다
    workbook_name: str = ""  # 화면에 보여줄 출석부 이름 — 없으면 옛 고정 이름
    year_mismatch: bool = False  # 프로필 학년도와 기록 학년도가 다르면 새 출석부 단추가 풀린다
    canonical_workbook_name: str = ""  # 연결 선택·새 학년도 확인창에 보여줄 정식 이름


ATTENDANCE_ERROR_MESSAGES = {
    "sheet": "Google Sheet를 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
    "docs": "Google Docs 양식을 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
    "tasks": "Google Tasks 목록을 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
    "setup": "출결 자료를 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
}
ATTENDANCE_GWS_MESSAGE = "Google 연결 기능이 아직 준비되지 않았어요. Teacher Manager 설치 파일을 다시 실행해 주세요."
ATTENDANCE_LOGIN_MESSAGE = "설정에서 학교 Google 계정으로 로그인해 주세요."
ATTENDANCE_ACCOUNT_REQUIRED_MESSAGE = google_account.GOEDU_ACCOUNT_REQUIRED_MESSAGE
ATTENDANCE_AUTH_STATUS_MESSAGE = (
    "Google 로그인 상태를 확인하지 못했어요. 설정에서 다시 점검하고 "
    "인터넷 연결이나 학교 보안 정책을 확인해 주세요."
)
ATTENDANCE_PROFILE_MESSAGE = "내 정보와 하루 일과를 먼저 입력해 주세요."
ATTENDANCE_ACCOUNT_MESSAGE = "처음 준비하던 Google 계정으로 다시 로그인해 주세요."
ATTENDANCE_RECORD_BROKEN_MESSAGE = "출결 설치 기록을 읽지 못했어요. 이전에 만든 출결 시트를 확인해 주세요."
ATTENDANCE_NOT_READY_MESSAGE = "출결 준비 시작하기를 누르면 로그인한 계정에 자동으로 준비해요."
ATTENDANCE_SCRIPT_UPDATE_REQUIRED_MESSAGE = (
    "예전에 설치한 공식 출결 자료를 찾았어요. 출결 기능을 최신판으로 올린 뒤 사용할 수 있어요."
)
ATTENDANCE_SCRIPT_CHECK_REQUIRED_MESSAGE = (
    "기존 출결 자료는 그대로 두고, 현재 출결 기능이 안전한 최신판인지 먼저 확인해 주세요."
)
ATTENDANCE_CONNECTION_REPAIR_MESSAGE = (
    "현재 연결이 옛 출석부를 가리키고 있어요. 사용할 정식 출석부를 한 번 골라 주세요."
)
ATTENDANCE_DETAIL_LIMIT = 800  # 화면에 보여줄 실패 안내 길이 상한


def current_attendance_script_bundle_sha256() -> str:
    """지금 실행 중인 프로그램에 묶인 정식 출결 기능의 지문."""

    return attendance_script_update.target_bundle_sha256(
        bundle_paths.bundle_root() / "assets"
    )


def _existing_attendance_guidance(error) -> str:
    if isinstance(
        error, install_attendance_automation.AttendanceConnectionChoiceRequired
    ):
        names = []
        for candidate in error.candidates:
            name = " ".join(str(candidate.get("name", "") or "").split())[:120]
            if name and name not in names:
                names.append(name)
        if len(error.candidates) > 1:
            shown = ", ".join(names) if names else "이름을 읽지 못한 출석부"
            return (
                "정식 출석부가 여러 개라 자동으로 고르지 않았어요. "
                f"파일 이름: {shown}. 화면의 [사용할 출석부 고르기]에서 "
                "연결 확인번호를 비교한 뒤 하나를 골라 주세요."
            )
        shown = names[0] if names else "예전에 쓰던 출석부"
        return (
            f"정식 출석부를 찾았어요: {shown}. 기존 자료는 바꾸지 않았습니다. "
            "화면의 [사용할 출석부 고르기]에서 연결 확인번호를 비교한 뒤 골라 주세요."
        )

    raw = str(error or "")
    labels = []
    for marker, label in (
        ("SCRIPT_ID", "자동 기능 연결"),
        ("DEPLOYMENT_ID", "자동 기능 배포 연결"),
        ("TEMPLATE_DOC_ID", "결석 신고서 양식"),
        ("FOLDER_ID", "출결 파일 보관 폴더"),
        ("TASK_LIST_ID", "할 일 목록"),
    ):
        if marker in raw and label not in labels:
            labels.append(label)
    if "여러 개" in raw:
        return (
            "같은 이름의 기존 출석부가 여러 개라 자동으로 고르지 않았어요. "
            "Google Drive에서 파일 이름으로 열어 확인한 뒤 사용할 파일 하나만 남겨 주세요."
        )
    checks = ", ".join(labels) if labels else "비어 있는 연결 항목"
    return (
        "쓰시던 출석부를 찾았지만 연결 준비가 비어 있어 이어서 사용하지 않았어요. "
        "기존 출석부는 바꾸지 않았습니다. 출석부의 설정 화면에서 확인할 항목: "
        f"{checks}. 확인한 뒤 다시 시도해 주세요."
    )


_APPS_SCRIPT_API_DISABLED_MARKERS = (
    "script.google.com/home/usersettings",
    "has not enabled the apps script api",
)
ATTENDANCE_APPS_SCRIPT_API_MESSAGE = (
    "이 Google 계정에서 Google Apps Script API가 아직 꺼져 있어요. "
    "브라우저에서 https://script.google.com/home/usersettings 를 열고 같은 @goedu.kr 계정으로 "
    "로그인한 뒤 [Google Apps Script API] 스위치를 켜 주세요. "
    "몇 분 뒤 Teacher Manager로 돌아와 출결 준비를 다시 시작해 주세요."
)


def friendly_attendance_error(error) -> tuple[str, str]:
    """설치 실패를 (실패한 서비스, 화면에 보여줄 쉬운 문장)으로 바꾼다."""
    if isinstance(error, install_attendance_automation.ExistingAttendanceSheetError):
        return "sheet", _existing_attendance_guidance(error)
    service = "setup"
    cmd = getattr(error, "cmd", None) or []
    tokens = [str(part) for part in cmd]
    joined = " ".join(tokens)
    if "tasks" in tokens:
        service = "tasks"
    elif "sheets" in tokens or "google-apps.spreadsheet" in joined:
        service = "sheet"
    elif "google-apps.document" in joined:
        service = "docs"
    # 로그인이 풀렸거나 권한이 부족하면 어느 단계든 같은 처방 — 원인 그대로 알려준다.
    evidence = (str(getattr(error, "output", "") or "") + " " + str(error)).lower()
    # Apps Script API 사용자 설정을 한 번도 켜지 않은 계정은 403이 나지만 로그인 문제가
    # 아니다. 다시 로그인해도 풀리지 않으므로 Google의 설정 화면을 그대로 안내한다
    # (2026-09-03 조사 6번).
    if any(marker in evidence for marker in _APPS_SCRIPT_API_DISABLED_MARKERS):
        return service, ATTENDANCE_APPS_SCRIPT_API_MESSAGE
    auth_markers = ("invalid_grant", "unauthorized", "credential", "401", "403",
                    "insufficient", "login required", "sign in", "please login")
    if any(marker in evidence for marker in auth_markers):
        return service, ("Google 로그인이 풀렸거나 권한이 부족해요. "
                         "설정에서 로그아웃 후 다시 로그인하고 다시 시도해 주세요.")
    return service, ATTENDANCE_ERROR_MESSAGES[service]


def _read_json_dict(path: Path) -> dict | None:
    try:
        parsed = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _read_setup_status(config_dir: Path) -> dict:
    return _read_json_dict(paths.attendance_setup_status_path(Path(config_dir))) or {}


def _atomic_write_json(path: Path, data: dict) -> None:
    """JSON 기록을 원자 교체로 쓴다 — 임시 이름은 쓸 때마다 새로 만든다.

    brity_bridge.atomic_io는 파일 이름 뒤에 .tmp-write를 붙인 고정 이름을 쓴다.
    작성자가 하나뿐인 파일에는 그걸로 충분하지만, 여기서 쓰는 기록들은 도우미와
    대시보드가 몇 초 사이에 둘 다 쓴다(update-state.json은 로그인 직후 양쪽이 쓴다).
    고정 이름이면 두 프로세스가 같은 임시 파일을 열어 내용을 섞어 쓰고, 먼저 끝낸
    쪽이 그 파일을 옮겨 버려 나중 쪽의 교체가 '파일 없음'으로 터진다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(_json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_setup_status(config_dir: Path, data: dict) -> None:
    _atomic_write_json(paths.attendance_setup_status_path(Path(config_dir)), data)


def _attendance_profile_error(config_dir: Path) -> str:
    """출결 준비에 필요한 개인 설정이 채워졌는지 파일을 만들지 않고 확인한다."""
    try:
        profile = parse_settings._read_profile_csv(Path(config_dir) / "teacher-profile.csv")
        if not profile.get("점심종료시간") and profile.get("5교시시작"):
            profile["점심종료시간"] = profile["5교시시작"]
        parse_settings._require_profile_values(profile, require_links=False)
    except ValueError:
        return ATTENDANCE_PROFILE_MESSAGE
    return ""


ATTENDANCE_STATUS_CACHE_NAME = "attendance-status-cache.generated.json"


def save_attendance_status_cache(config_dir: Path, status: dict) -> None:
    """마지막으로 확인한 출결 상태를 저장한다 — 켠 직후 화면이 이것부터 보여준다.

    "확인하는 중이에요…"를 프로그램을 켤 때마다 보여주지 않기 위한 것(2026-07-30).
    저장이 실패해도 확인 자체를 막지 않는다.
    """
    try:
        _atomic_write_json(Path(config_dir) / ATTENDANCE_STATUS_CACHE_NAME, dict(status))
    except OSError:
        pass


def load_attendance_status_cache(config_dir: Path) -> dict | None:
    config_dir = Path(config_dir)
    try:
        saved = _json.loads((config_dir / ATTENDANCE_STATUS_CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or not saved.get("state"):
        return None
    if saved.get("state") in {
        "ready",
        "script-check-required",
        "script-update-required",
        "ai-action-required",
    }:
        try:
            record = read_verified_canonical_record(
                paths.attendance_install_record_path(config_dir)
            )
        except (OSError, AttendanceInstallRecordError):
            return None
        cached_url = str(saved.get("spreadsheet_url", "") or "")
        if cached_url and cached_url != str(record.get("spreadsheet_url", "") or ""):
            return None
        connection_code = attendance_workbook_identity.attendance_connection_code(
            record.get("spreadsheet_id")
        )
        if saved.get("connection_code") != connection_code:
            saved = dict(saved)
            saved["connection_code"] = connection_code
    if saved.get("state") == "ready":
        record_path = paths.attendance_install_record_path(config_dir)
        try:
            record = load_attendance_install_record(record_path)
            attested = attendance_script_is_attested(
                record, current_attendance_script_bundle_sha256()
            ) and record.get("script_update_required") is not True
        except (OSError, ValueError, AttendanceInstallRecordError):
            attested = False
        if not attested:
            saved = dict(saved)
            saved["state"] = "script-check-required"
            saved["detail"] = ATTENDANCE_SCRIPT_CHECK_REQUIRED_MESSAGE
    return saved


def _profile_school_year(config_dir: Path) -> str:
    """profile.generated.json의 school.year — 없거나 빈 값이면 지금 학년도로 본다."""
    profile = _read_json_dict(paths.profile_path(Path(config_dir))) or {}
    school = profile.get("school")
    year = str((school or {}).get("year", "") or "").strip() if isinstance(school, dict) else ""
    return year or install_attendance_automation.current_school_year()


def read_attendance_status(
    config_dir: Path,
    run_command=_default_run_command,
    *,
    gws_executable: str | None = None,
) -> AttendanceStatus:
    config_dir = Path(config_dir)
    setup_status = _read_setup_status(config_dir)
    account = str(setup_status.get("account", "") or "")
    gws = str(gws_executable or resolve_gws(run_command))
    if not gws:
        return AttendanceStatus(state="gws-required", account=account, detail=ATTENDANCE_GWS_MESSAGE)
    auth = gws_auth_status(run_command, gws)
    current_user = str(auth.get("user", "") or "")
    if auth.get("login_state") == "error":
        return AttendanceStatus(
            state="auth-error",
            account=account,
            detail=ATTENDANCE_AUTH_STATUS_MESSAGE,
        )
    if not auth.get("logged_in"):
        return AttendanceStatus(state="login-required", account=account, detail=ATTENDANCE_LOGIN_MESSAGE)
    if not auth.get("account_allowed"):
        return AttendanceStatus(
            state="account-required",
            account=account,
            current_user=current_user,
            detail=ATTENDANCE_ACCOUNT_REQUIRED_MESSAGE,
        )
    if account and current_user and account != current_user:
        return AttendanceStatus(
            state="failed", account=account, current_user=current_user,
            failed_service="setup", detail=ATTENDANCE_ACCOUNT_MESSAGE,
        )
    record_path = paths.attendance_install_record_path(config_dir)
    if record_path.exists():
        try:
            record = load_attendance_install_record(record_path)
        except AttendanceInstallRecordError:
            return AttendanceStatus(
                state="failed", account=account, current_user=current_user,
                failed_service="setup", detail=ATTENDANCE_RECORD_BROKEN_MESSAGE,
            )
        canonical_workbook_name = (
            attendance_workbook_identity.attendance_workbook_name_from_record(record)
        )
        try:
            record = read_verified_canonical_record(record_path)
        except AttendanceInstallRecordError:
            return AttendanceStatus(
                state="connection-repair-required",
                account=account or current_user,
                current_user=current_user,
                detail=ATTENDANCE_CONNECTION_REPAIR_MESSAGE,
                school_year=str(record.get("school_year", "") or ""),
                workbook_name=str(record.get("workbook_name", "") or ""),
                canonical_workbook_name=canonical_workbook_name,
            )
        record_account = str(record.get(SETUP_ACCOUNT_FIELD, "") or "").strip()
        if (
            account
            and record_account
            and account.casefold() != record_account.casefold()
        ):
            return AttendanceStatus(
                state="failed",
                account=account,
                current_user=current_user,
                failed_service="setup",
                detail=ATTENDANCE_ACCOUNT_MESSAGE,
            )
        expected_account = account or record_account
        if not expected_account:
            return AttendanceStatus(
                state="connection-repair-required",
                account=current_user,
                current_user=current_user,
                detail=(
                    "이전 설치의 Google 계정 확인 기록이 없어 사용할 출석부를 "
                    "한 번 다시 골라 주세요. 새 파일은 만들지 않습니다."
                ),
                school_year=str(record.get("school_year", "") or ""),
                workbook_name=str(record.get("workbook_name", "") or ""),
                canonical_workbook_name=canonical_workbook_name,
            )
        if (
            current_user
            and expected_account.casefold() != current_user.casefold()
        ):
            return AttendanceStatus(
                state="failed",
                account=expected_account,
                current_user=current_user,
                failed_service="setup",
                detail=ATTENDANCE_ACCOUNT_MESSAGE,
            )
        account = expected_account
        spreadsheet_url = str(record.get("spreadsheet_url", "") or "")
        template_doc_url = str(record.get("template_doc_url", "") or "")
        valid_sheet_url = spreadsheet_url.startswith(
            "https://docs.google.com/spreadsheets/d/"
        )
        # 학년도 판정 — 기록에 도장이 없으면(옛 기록) 지금 학년도로 본다. bridge가 그 도장을
        # 한 번 채워 적지만, 여기서는 파일을 고치지 않고 비교값만 판정한다.
        profile_year = _profile_school_year(config_dir)
        record_year = (
            str(record.get("school_year", "") or "")
            or install_attendance_automation.current_school_year()
        )
        workbook_name = (
            str(record.get("workbook_name", "") or "")
            or install_attendance_automation.ATTENDANCE_LEGACY_SHEET_NAME
        )
        year_mismatch = profile_year != record_year
        record_grade = str(record.get("homeroom_grade", "") or "").strip()
        record_class = str(record.get("homeroom_class", "") or "").strip()
        recorded_workbook_name = (
            attendance_workbook_identity.attendance_workbook_name(
                {
                    "school": {"year": record_year},
                    "homeroom": {
                        "enabled": bool(record_grade and record_class),
                        "grade": record_grade,
                        "class": record_class,
                    },
                }
            )
        )
        # 평상시 상태 확인은 다른 파일을 이름으로 찾지 않는다. 사용자가 한 번 고른
        # 검증 정본 번호만 열기·업데이트의 기준으로 쓴다.
        if valid_sheet_url:
            script_update_required = record.get("script_update_required") is True
            if script_update_required:
                script_state = "script-update-required"
                script_detail = ATTENDANCE_SCRIPT_UPDATE_REQUIRED_MESSAGE
            else:
                try:
                    current_bundle_sha256 = current_attendance_script_bundle_sha256()
                except (OSError, UnicodeError, ValueError):
                    current_bundle_sha256 = ""
                script_attested = (
                    bool(current_bundle_sha256)
                    and attendance_script_is_attested(
                        record, current_bundle_sha256
                    )
                )
                script_state = "ready" if script_attested else "script-check-required"
                script_detail = (
                    "" if script_attested else ATTENDANCE_SCRIPT_CHECK_REQUIRED_MESSAGE
                )
            return AttendanceStatus(
                state=script_state,
                account=account or current_user, current_user=current_user,
                spreadsheet_url=spreadsheet_url, template_doc_url=template_doc_url,
                connection_code=(
                    attendance_workbook_identity.attendance_connection_code(
                        record.get("spreadsheet_id")
                    )
                ),
                detail=script_detail,
                school_year=record_year, workbook_name=workbook_name, year_mismatch=year_mismatch,
                canonical_workbook_name=canonical_workbook_name,
            )
        return AttendanceStatus(
            state="failed", account=account, current_user=current_user,
            spreadsheet_url=spreadsheet_url, template_doc_url=template_doc_url,
            failed_service="setup", detail=ATTENDANCE_RECORD_BROKEN_MESSAGE,
            school_year=record_year, workbook_name=workbook_name, year_mismatch=year_mismatch,
            canonical_workbook_name=canonical_workbook_name,
        )
    profile_error = _attendance_profile_error(config_dir)
    if profile_error:
        return AttendanceStatus(state="profile-required", current_user=current_user, detail=profile_error)
    if setup_status.get("state") == "connection-choice-required":
        if account and current_user and account != current_user:
            return AttendanceStatus(
                state="failed",
                account=account,
                current_user=current_user,
                failed_service="setup",
                detail=ATTENDANCE_ACCOUNT_MESSAGE,
            )
        profile = _read_json_dict(paths.profile_path(config_dir)) or {}
        return AttendanceStatus(
            state="connection-repair-required",
            account=account or current_user,
            current_user=current_user,
            spreadsheet_url=str(
                setup_status.get("spreadsheet_url", "") or ""
            ),
            detail=(
                "현재 출석부 연결을 고르기 전에는 새 출석부를 만들지 않습니다. "
                + str(setup_status.get("detail", "") or "")
            )[:ATTENDANCE_DETAIL_LIMIT],
            school_year=_profile_school_year(config_dir),
            workbook_name=str(
                setup_status.get("workbook_name", "")
                or install_attendance_automation.ATTENDANCE_LEGACY_SHEET_NAME
            ),
            canonical_workbook_name=(
                attendance_workbook_identity.attendance_workbook_name(profile)
            ),
        )
    if setup_status.get("state") == "failed":
        return AttendanceStatus(
            state="failed", account=account, current_user=current_user,
            failed_service=str(setup_status.get("failed_service", "") or ""),
            detail=str(setup_status.get("detail", "") or ""),
        )
    return AttendanceStatus(state="not-ready", current_user=current_user, detail=ATTENDANCE_NOT_READY_MESSAGE)


def backfill_attendance_record_stamp(config_dir: Path) -> bool:
    """옛 출결 기록에 학년도 도장이 없으면 한 번만 채워 적는다.

    read_attendance_status는 판정만 하고 파일을 고치지 않는다 — 기록을 실제로
    고치는 자리는 여기 하나뿐이다(bridge.attendance_status가 부른다).
    이미 school_year가 있으면 아무것도 하지 않는다(덮어쓰지 않는다).
    """
    config_dir = Path(config_dir)
    record_path = paths.attendance_install_record_path(config_dir)
    if not record_path.exists():
        return False
    try:
        snapshot = read_attendance_install_snapshot(record_path)
    except AttendanceInstallRecordError:
        return False  # 깨진 기록은 여기서 고치지 않는다 — read_attendance_status가 이미 failed로 알린다
    record = dict(snapshot.record)
    if str(record.get("school_year", "") or "").strip():
        return False
    profile = _read_json_dict(paths.profile_path(config_dir)) or {}
    homeroom = profile.get("homeroom")
    homeroom = homeroom if isinstance(homeroom, dict) else {}
    record["school_year"] = install_attendance_automation.current_school_year()
    record["homeroom_grade"] = str(homeroom.get("grade", "") or "")
    record["homeroom_class"] = str(homeroom.get("class", "") or "")
    try:
        replace_attendance_install_record(record_path, record, snapshot)
    except AttendanceInstallRecordError:
        # 다른 창이 확인 증명이나 새 출결 연결을 방금 썼다면 그 기록이 우선이다.
        return False
    return True


# 파서가 읽는 기존 항목명이 정본 — 대시보드가 새 이름을 만들지 않는다.
PROFILE_FIELD_ORDER = [
    "선생님이름", "학년도", "학교명", "학교급", "담임여부", "담임학년", "담임반",
    "출근시간", "퇴근시간", "조회시작", "1교시시작", "점심종료시간",
    "월요일마지막교시", "화요일마지막교시", "수요일마지막교시", "목요일마지막교시", "금요일마지막교시",
    "업무캘린더ID", "업무캘린더이름", "학사일정캘린더ID", "학사일정캘린더이름",
    "업무Tasks목록ID", "업무Tasks목록이름",
    "담임안내Tasks목록ID", "담임안내Tasks목록이름",
]

IDENTITY_FIELDS = frozenset({
    "선생님이름", "학년도", "학교명", "학교급", "담임여부", "담임학년", "담임반",
    "출근시간", "퇴근시간", "조회시작", "1교시시작", "점심종료시간",
    "월요일마지막교시", "화요일마지막교시", "수요일마지막교시", "목요일마지막교시", "금요일마지막교시",
})
CALENDAR_FIELDS = frozenset({
    "업무캘린더ID", "업무캘린더이름", "학사일정캘린더ID", "학사일정캘린더이름",
})
TASK_FIELDS = frozenset({
    "업무Tasks목록ID", "업무Tasks목록이름", "담임안내Tasks목록ID", "담임안내Tasks목록이름",
})
GEMINI_FIELDS = frozenset({"gemini_api_key", "gemini_model"})


def read_profile_values(config_dir: Path, *, strict: bool = False) -> dict[str, str]:
    path = Path(config_dir) / "teacher-profile.csv"
    try:
        if not path.exists():
            return {}
        return parse_settings._read_profile_csv(path)
    except (OSError, ValueError, csv.Error) as error:
        if strict:
            raise recovery.RetryableOperationError(
                "LOCAL_PROFILE_READ", "내 정보를 다시 읽고 있어요."
            ) from error
        raise


def write_profile_values(config_dir: Path, updates: dict[str, str]) -> Path:
    config_dir = Path(config_dir)
    merged = read_profile_values(config_dir)
    merged.update({key: (value or "").strip() for key, value in updates.items()})
    ordered = [key for key in PROFILE_FIELD_ORDER if key in merged]
    ordered += [key for key in merged if key not in PROFILE_FIELD_ORDER]
    path = config_dir / "teacher-profile.csv"
    config_dir.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["항목", "값"])
    for key in ordered:
        writer.writerow([key, merged[key]])
    atomic_io.atomic_write_text(path, "\ufeff" + buffer.getvalue(), encoding="utf-8")
    return path


def read_timetable_grid(config_dir: Path, *, strict: bool = False) -> list[list[str]]:
    path = Path(config_dir) / "weekly-timetable.xlsx"
    rows: dict[str, dict[str, str]] = {}
    if path.exists():
        try:
            rows = parse_settings._read_timetable_xlsx(path)
        except (ValueError, zipfile.BadZipFile, OSError) as error:
            if strict:
                raise recovery.RetryableOperationError(
                    "LOCAL_TIMETABLE_READ", "시간표를 다시 읽고 있어요."
                ) from error
            rows = {}  # 손상/판독불가 파일은 빈 격자로 — 대시보드가 안 열리는 것보다 낫다
    grid = []
    for period in range(1, 8):
        day_values = rows.get(str(period), {})
        grid.append([str(period)] + [day_values.get(day, "") for day in parse_settings.DAYS])
    return grid


def write_timetable_grid(config_dir: Path, grid: list[list[str]]) -> Path:
    path = Path(config_dir) / "weekly-timetable.xlsx"
    rows = [["교시", *parse_settings.DAYS]] + [[str(cell or "") for cell in row] for row in grid]
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        parse_settings.write_timetable_xlsx(temporary, rows)
        with zipfile.ZipFile(temporary) as workbook:
            if workbook.testzip() is not None:
                raise OSError("시간표 파일을 확인하지 못했어요")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return path


def run_parser(config_dir: Path, *, require_links: bool = True) -> tuple[bool, str]:
    try:
        parse_settings.parse_config_dir(Path(config_dir), require_links=require_links)
    except ValueError as error:
        raw = str(error or "")
        missing = [name for name in PROFILE_FIELD_ORDER if name in raw]
        if missing:
            return False, "입력하지 않은 항목을 확인해 주세요: " + ", ".join(missing)
        return False, "저장한 내용을 확인하지 못했어요. 내 정보와 시간표를 다시 확인해 주세요."
    return True, "입력한 설정을 확인했어요."


def _checked_updates(updates: dict, allowed: frozenset[str], label: str) -> dict:
    if not isinstance(updates, dict):
        raise ValueError(f"{label} 저장 내용의 모양이 올바르지 않아요")
    if set(updates) - allowed:
        raise ValueError(f"{label} 저장 내용에 다른 화면 항목이 섞여 있어요")
    return dict(updates)


def _retryable_local_error(error: Exception) -> None:
    if isinstance(error, recovery.RetryableOperationError):
        raise error
    if isinstance(error, (OSError, zipfile.BadZipFile)):
        raise recovery.RetryableOperationError("LOCAL_IO", "이 컴퓨터의 설정을 다시 확인하고 있어요.") from error
    raise error


def _saved_scope(reader, updates: dict) -> dict:
    values = reader()
    if not isinstance(values, dict):
        raise recovery.RetryableOperationError("LOCAL_READ", "저장한 내용을 다시 읽지 못했어요.")
    return {key: values.get(key) for key in updates}


def _verify_saved_fields(updates: dict, reader) -> tuple[bool, dict]:
    try:
        saved = _saved_scope(reader, updates)
    except Exception as error:  # noqa: BLE001 - known local read failures are retryable
        _retryable_local_error(error)
    return saved == updates, saved


def _write_then_read(updates: dict, writer, reader) -> dict:
    try:
        writer()
    except Exception as error:  # noqa: BLE001 - preserve a classified filesystem failure
        _retryable_local_error(error)
    complete, saved = _verify_saved_fields(updates, reader)
    if not complete:
        raise recovery.RetryableOperationError("LOCAL_VERIFY", "저장한 내용을 다시 확인하고 있어요.")
    return saved


def _save_scope_with_recovery(
    config_dir: Path,
    updates: dict,
    writer,
    reader,
    *,
    operation: str,
    app_version: str,
) -> dict:
    """Write one local scope and return only after its requested fields read back."""

    del config_dir  # writers and readers retain their one approved local path.
    return recovery.run_operation(
        operation,
        "설정을 저장하지 못했어요.",
        lambda: _write_then_read(updates, writer, reader),
        verify=lambda: _verify_saved_fields(updates, reader),
        delays=recovery.LOCAL_DELAYS,
        change_status="기존 설정은 그대로입니다.",
        app_version=app_version,
    )


def save_and_verify_profile_scope(
    config_dir: Path,
    updates: dict,
    allowed: frozenset[str],
    label: str,
    *,
    operation: str,
    app_version: str = version.APP_VERSION,
) -> dict:
    checked = _checked_updates(updates, allowed, label)
    expected = {key: (value or "").strip() for key, value in checked.items()}
    config_dir = Path(config_dir)
    _save_scope_with_recovery(
        config_dir,
        expected,
        lambda: write_profile_values(config_dir, expected),
        lambda: {key: read_profile_values(config_dir).get(key, "") for key in expected},
        operation=operation,
        app_version=app_version,
    )
    parsed, detail = run_parser(config_dir, require_links=False)
    return {"parsed": parsed, "detail": detail}


def _save_profile_scope(
    config_dir: Path,
    updates: dict,
    allowed: frozenset[str],
    label: str,
) -> dict:
    return save_and_verify_profile_scope(
        config_dir, updates, allowed, label, operation="profile_save"
    )


def save_identity(config_dir: Path, updates: dict) -> dict:
    return _save_profile_scope(config_dir, updates, IDENTITY_FIELDS, "내 정보")


def save_calendars(config_dir: Path, updates: dict) -> dict:
    return _save_profile_scope(config_dir, updates, CALENDAR_FIELDS, "Calendar")


def save_tasks(config_dir: Path, updates: dict) -> dict:
    return _save_profile_scope(config_dir, updates, TASK_FIELDS, "Tasks")


def save_timetable(config_dir: Path, grid: list) -> dict:
    if not isinstance(grid, list):
        raise ValueError("시간표 저장 내용의 모양이 올바르지 않아요")
    config_dir = Path(config_dir)
    expected = [[str(cell or "") for cell in row] for row in grid]
    _save_scope_with_recovery(
        config_dir,
        {"grid": expected},
        lambda: write_timetable_grid(config_dir, expected),
        lambda: {"grid": read_timetable_grid(config_dir)},
        operation="timetable_save",
        app_version=version.APP_VERSION,
    )
    parsed, detail = run_parser(config_dir, require_links=False)
    return {"parsed": parsed, "detail": detail}


def _settings_json(path: Path) -> dict | None:
    """Keep unknown existing values; only a truly absent file may start empty."""
    path = Path(path)
    try:
        if not path.exists():
            return None
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise recovery.RetryableOperationError(
            "LOCAL_SETTINGS_READ", "기존 설정을 다시 읽고 있어요."
        ) from error
    if not isinstance(raw, dict):
        raise recovery.RetryableOperationError(
            "LOCAL_SETTINGS_READ", "기존 설정의 모양을 다시 확인하고 있어요."
        )
    return dict(raw)


def _write_settings_scope(path: Path, candidate, updates: dict) -> None:
    """Atomically change only requested setting fields, retaining future JSON values."""

    raw = _settings_json(path)
    if raw is None:
        raw = asdict(load_settings(path))
    for key in updates:
        raw[key] = getattr(candidate, key)
    atomic_io.atomic_write_text(
        Path(path), _json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    )


def _read_settings_scope(path: Path, updates: dict) -> dict:
    _settings_json(path)
    saved = load_settings(path)
    return {key: getattr(saved, key) for key in updates}


def read_messenger_settings(config_dir: Path):
    settings_path = paths.settings_path(Path(config_dir))
    _settings_json(settings_path)
    return load_settings(settings_path)


def save_and_verify_messenger(
    config_dir: Path,
    candidate,
    updates: dict,
    *,
    operation: str,
    app_version: str = version.APP_VERSION,
) -> dict:
    settings_path = paths.settings_path(Path(config_dir))
    expected = {key: getattr(candidate, key) for key in updates}
    return _save_scope_with_recovery(
        Path(config_dir),
        expected,
        lambda: _write_settings_scope(settings_path, candidate, expected),
        lambda: _read_settings_scope(settings_path, expected),
        operation=operation,
        app_version=app_version,
    )


def save_gemini(config_dir: Path, updates: dict, *, push_key=None) -> dict:
    checked = _checked_updates(updates, GEMINI_FIELDS, "Gemini")
    settings_path = paths.settings_path(Path(config_dir))
    candidate = load_settings(settings_path)
    if "gemini_api_key" in checked:
        value = checked["gemini_api_key"]
        if not isinstance(value, str):
            raise ValueError("Gemini API 키 모양이 올바르지 않아요")
        candidate.gemini_api_key = value.strip()
    if "gemini_model" in checked:
        model = checked["gemini_model"]
        if model not in ALLOWED_GEMINI_MODELS:
            raise ValueError("목록에 있는 Gemini 모델을 골라 주세요")
        candidate.gemini_model = model
    save_and_verify_messenger(
        config_dir, candidate, checked, operation="gemini_save"
    )

    sheet_push = {"state": "skipped", "detail": ""}
    if "gemini_api_key" in checked:
        pusher = push_key or push_gemini_key_to_attendance_sheet
        try:
            sheet_push = pusher(Path(config_dir))
            if not isinstance(sheet_push, dict):
                sheet_push = {
                    "state": "failed",
                    "detail": ATTENDANCE_SHEET_PUSH_FAILURE,
                }
            elif sheet_push.get("state") == "failed":
                sheet_push = {
                    **sheet_push,
                    "detail": ATTENDANCE_SHEET_PUSH_FAILURE,
                }
        except Exception:  # noqa: BLE001 - 로컬 저장은 이미 끝났다
            sheet_push = {
                "state": "failed",
                "detail": ATTENDANCE_SHEET_PUSH_FAILURE,
            }
    return {"saved": True, "sheet_push": sheet_push}


# 데스크톱 Google 로그인 권한은 이 한 목록만 정본으로 쓴다. 시트 안에서 교사가
# 직접 실행하는 감지기 연결 권한은 Apps Script 묶음의 manifest가 따로 요청한다.
GOOGLE_LOGIN_SCOPE_LIST = (
    "email",
    "profile",
    "openid",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/script.projects",
    "https://www.googleapis.com/auth/script.deployments",
    "https://www.googleapis.com/auth/script.container.ui",
)
GWS_LOGIN_SCOPES = ",".join(GOOGLE_LOGIN_SCOPE_LIST)
REQUIRED_ATTENDANCE_SCOPES = frozenset(GOOGLE_LOGIN_SCOPE_LIST)
GWS_SCOPE_GRANT_FILE = "google-scope-grant.generated.json"


def gws_scope_grant_sha256(scopes: str = GWS_LOGIN_SCOPES) -> str:
    return hashlib.sha256(str(scopes).encode("utf-8")).hexdigest()


def record_gws_scope_grant(config_dir: Path, account: str) -> Path:
    path = Path(config_dir) / GWS_SCOPE_GRANT_FILE
    _atomic_write_json(path, {
        "schema_version": 1,
        "account": str(account or "").strip().casefold(),
        "scope_sha256": gws_scope_grant_sha256(GWS_LOGIN_SCOPES),
    })
    return path


def has_current_gws_scope_grant(config_dir: Path, account: str) -> bool:
    saved = _read_json_dict(Path(config_dir) / GWS_SCOPE_GRANT_FILE)
    if not saved:
        return False
    return (
        saved.get("schema_version") == 1
        and saved.get("account") == str(account or "").strip().casefold()
        and saved.get("scope_sha256") == gws_scope_grant_sha256(GWS_LOGIN_SCOPES)
    )


def check_version(run_command, executable: str) -> str:
    code, output = run_command([executable, "--version"])
    return output.strip().splitlines()[0] if code == 0 and output.strip() else ""


def _readiness(ready: bool, detail: str) -> dict:
    return {"ready": bool(ready), "detail": str(detail or "")}


def _document_reader_probe() -> tuple[bool, str]:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False, "설치 필요"
    return True, "PDF와 문서를 읽을 수 있어요"


def computer_readiness(run_command=_default_run_command, document_probe=None) -> dict:
    del run_command  # 설치형 앱의 Python은 제품 안에 있으며 시스템 명령은 찾지 않는다.
    document_ok, document_detail = (document_probe or _document_reader_probe)()
    return {
        "python": _readiness(True, platform.python_version()),
        "documents": _readiness(document_ok, document_detail),
        "screen": _readiness(True, "이 화면을 정상 표시하고 있어요"),
    }


def resolve_gws(run_command=_default_run_command) -> str:
    """PATH와 npm 설치본을 보지 않고 검증된 동봉/승인 GWS 전체 경로를 돌려준다."""
    del run_command  # 예전 호출 모양은 유지하되, 외부 실행 파일 탐색에는 쓰지 않는다.
    return tool_runtime.resolve_gws_executable()


def install_gws(run_command=_default_run_command) -> tuple[bool, str]:
    del run_command  # GWS는 npm으로 설치하지 않고 제품 안의 검증된 파일만 쓴다.
    try:
        executable = resolve_gws()
    except tool_runtime.GwsRuntimeError:
        return False, "Google 연결 기능을 준비하지 못했어요. Teacher Manager 설치 파일을 다시 실행해 주세요."
    return True, executable


_COMPONENT_RECOVERY_COMPLETE_CODES = {
    "gws_check": frozenset({
        "UP_TO_DATE", "UPDATE_AVAILABLE", "APPROVAL_NOT_PUBLISHED",
        "COMPONENT_VERSION_REJECTED",
    }),
    "gws_install": frozenset({
        "COMPONENT_UPDATE_INSTALLED", "COMPONENT_UPDATE_ALREADY_INSTALLED",
    }),
    "node_status": frozenset({"NODE_READY", "NODE_NOT_INSTALLED"}),
    "node_prepare": frozenset({"NODE_READY"}),
    "ai_skill_install": frozenset({"AI_SKILLS_READY"}),
    "update_offer": frozenset({"UPDATE_AVAILABLE", "UPDATE_LATEST"}),
    "update_download": frozenset({"UPDATE_SETUP_READY"}),
    "update_helper_stop": frozenset({"UPDATE_HELPER_STOPPED"}),
    "update_setup_launch": frozenset({"UPDATE_SETUP_LAUNCHED"}),
}
_COMPONENT_RECOVERY_RETRYABLE_CODES = {
    "gws_check": frozenset({
        "NETWORK_OFFLINE", "NETWORK_TIMEOUT", "APPROVAL_SERVER_UNAVAILABLE",
        "COMPONENT_CHECK_BUSY",
    }),
    "gws_install": frozenset({
        "NETWORK_OFFLINE", "NETWORK_TIMEOUT", "COMPONENT_FILE_LOCKED",
        "COMPONENT_UPDATE_BUSY", "GWS_DOWNLOAD_NOT_FOUND",
        "GWS_DOWNLOAD_SERVER_UNAVAILABLE",
    }),
    "node_prepare": frozenset({
        "NETWORK_OFFLINE", "NETWORK_TIMEOUT", "COMPONENT_FILE_LOCKED",
        "COMPONENT_UPDATE_BUSY", "NODE_DOWNLOAD_NOT_FOUND",
        "NODE_DOWNLOAD_SERVER_UNAVAILABLE",
    }),
    "ai_skill_install": frozenset({
        "NETWORK_TIMEOUT", "AI_SKILLS_ARCHIVE_DOWNLOAD_FAILED",
        "AI_SKILLS_INSTALL_BUSY", "AI_SKILLS_APPLY_FAILED",
    }),
    "update_offer": frozenset({
        "NETWORK_OFFLINE", "NETWORK_TIMEOUT", "UPDATE_INFO_UNAVAILABLE",
    }),
    "update_download": frozenset({
        "NETWORK_OFFLINE", "NETWORK_TIMEOUT", "UPDATE_DOWNLOAD_UNAVAILABLE",
        "COMPONENT_FILE_LOCKED", "UPDATE_RUN_BUSY",
    }),
    "update_helper_stop": frozenset({"UPDATE_HELPER_BUSY"}),
    # Setup launch is deliberately absent: an uncertain launch is read back,
    # never issued a second time.
    "update_setup_launch": frozenset(),
}


class ComponentRecoveryStop(RuntimeError):
    """A safe component read proves that another automatic write must not run."""

    def __init__(self, code: str, detail: str):
        self.code = str(code or "COMPONENT_RECOVERY_STOP")
        self.detail = str(detail or "이 작업을 안전하게 계속할 수 없습니다.")
        super().__init__(self.detail)


def component_recovery_disposition(stage: str, code: str) -> str:
    """Tell the bridge whether a component stage is done, retryable, or unsafe.

    Lower-level installers keep ownership of validation and rollback.  This
    function only turns their stable result code into the next recovery step;
    an unknown code therefore stops instead of being guessed retryable.
    """

    safe_stage = str(stage or "").strip()
    safe_code = str(code or "").strip().upper()
    if safe_code in _COMPONENT_RECOVERY_COMPLETE_CODES.get(safe_stage, frozenset()):
        return "complete"
    if safe_code in _COMPONENT_RECOVERY_RETRYABLE_CODES.get(safe_stage, frozenset()):
        return "retry"
    return "stop"


def verify_gws_update_completion(
    offer: component_update.GwsUpdateOffer,
    run_command=_default_run_command,
    *,
    component_root: Path | None = None,
    resolver=None,
) -> tuple[bool, dict]:
    """Read the active executable again and accept only the exact approved GWS."""

    if not isinstance(offer, component_update.GwsUpdateOffer):
        raise ComponentRecoveryStop(
            "UPDATE_OFFER_NOT_APPROVED",
            "확인했던 Google 연결 기능 갱신 정보를 다시 확인할 수 없습니다.",
        )
    resolver = resolver or tool_runtime.resolve_gws
    try:
        resolution = resolver(
            component_root=component_root,
            run_command=run_command,
            force_refresh=True,
        )
    except tool_runtime.GwsRuntimeError as error:
        raise ComponentRecoveryStop(
            error.code,
            "현재 Google 연결 기능을 안전하게 확인하지 못했습니다.",
        ) from error
    except OSError as error:
        raise recovery.RetryableOperationError(
            "GWS_RUNTIME_CHECK_FAILED",
            "현재 Google 연결 기능을 다시 확인하고 있어요.",
        ) from error
    complete = bool(
        resolution.version == offer.manifest.version
        and resolution.source == "approved-update"
    )
    code = "COMPONENT_UPDATE_ALREADY_INSTALLED" if complete else "COMPONENT_UPDATE_NOT_ACTIVE"
    detail = (
        "승인된 Google 연결 기능이 이미 준비돼 있습니다."
        if complete
        else "승인된 Google 연결 기능이 아직 적용되지 않았습니다."
    )
    return complete, {
        "success": complete,
        "code": code,
        "detail": detail,
        "runtime_ready": True,
        "can_continue": True,
        "repair_required": False,
        "current_version": str(resolution.version),
        "current_source": str(resolution.source),
        "runtime_error_code": "",
    }


def verify_managed_node_completion(
    *,
    local_app_data=None,
    run_command=process_win.run_captured,
    resolver=None,
) -> tuple[bool, dict]:
    """Read the managed Node again; only its full runtime check proves completion."""

    resolver = resolver or tool_runtime.resolve_node
    try:
        runtime = resolver(
            local_app_data=local_app_data,
            run_command=run_command,
        )
    except OSError as error:
        raise recovery.RetryableOperationError(
            "NODE_RUNTIME_CHECK_FAILED",
            "AI 연결 도구의 준비 상태를 다시 확인하고 있어요.",
        ) from error
    if not isinstance(runtime, tool_runtime.NodeRuntime):
        raise ComponentRecoveryStop(
            "NODE_RUNTIME_CHECK_INVALID",
            "AI 연결 도구의 준비 상태를 안전하게 확인하지 못했습니다.",
        )
    payload = _ai_node_result(runtime)
    if runtime.ready:
        return True, payload
    if runtime.code == "NODE_NOT_INSTALLED":
        return False, payload
    raise ComponentRecoveryStop(
        runtime.code,
        "AI 연결 도구의 현재 파일을 안전하게 확인하지 못했습니다.",
    )


def _gws_offer_for_screen(offer: component_update.GwsUpdateOffer) -> dict:
    """화면이 다시 돌려줄 수 있는 공개 승인값만 담는다.

    실제 설치에는 이 사본을 쓰지 않는다. bridge가 메모리에 보관한 승인 원문과
    이 값이 정확히 같은지 확인한 뒤, 승인 원문만 설치 함수에 넘긴다.
    """
    manifest = offer.manifest
    return {
        "version": manifest.version,
        "notes": manifest.notes,
        "verified_on": manifest.verified_on,
        "checked_on": offer.checked_on,
        "archive_url": manifest.archive_url,
        "archive_sha256": manifest.archive_sha256,
        "executable_sha256": manifest.executable_sha256,
        "approval_sha256": offer.approval_sha256,
    }


def _gws_runtime_status(
    run_command=_default_run_command,
    *,
    component_root: Path | None = None,
    resolver=None,
    force_refresh: bool = False,
) -> dict:
    resolver = resolver or tool_runtime.resolve_gws
    try:
        resolution = resolver(
            component_root=component_root,
            run_command=run_command,
            force_refresh=force_refresh,
        )
    except tool_runtime.GwsRuntimeError as error:
        return {
            "runtime_ready": False,
            "can_continue": False,
            "repair_required": True,
            "current_version": "",
            "current_source": "",
            "runtime_error_code": error.code,
        }
    except OSError:
        return {
            "runtime_ready": False,
            "can_continue": False,
            "repair_required": True,
            "current_version": "",
            "current_source": "",
            "runtime_error_code": "GWS_RUNTIME_CHECK_FAILED",
        }
    return {
        "runtime_ready": True,
        "can_continue": True,
        "repair_required": False,
        "current_version": str(resolution.version),
        "current_source": str(resolution.source),
        "runtime_error_code": "",
    }


def read_gws_update_status(
    current_app_version: str,
    run_command=_default_run_command,
    *,
    component_root: Path | None = None,
    checker=None,
    resolver=None,
) -> tuple[dict, component_update.GwsUpdateOffer | None]:
    """승인된 새 판과 현재 실제 실행 판을 한 화면 상태로 묶는다."""
    checker = checker or component_update.check_gws_update
    check = checker(
        current_app_version,
        component_root=component_root,
    )
    runtime = _gws_runtime_status(
        run_command,
        component_root=component_root,
        resolver=resolver,
    )
    exact_offer = check.offer if check.success and check.offer is not None else None
    status_success = bool(check.success)
    status_code = str(check.code)
    status_detail = (
        str(check.detail)
        if check.success
        else component_update.user_update_failure_detail(check.code)
    )
    # 오늘 받은 승인 제안이 cache에 남아 있어도 이미 그 판(또는 더 최신 판)을
    # 실제로 쓰고 있으면 다시 갱신 단추를 보여 주지 않는다.
    if (
        exact_offer is not None
        and runtime["runtime_ready"]
        and tool_runtime._compare_versions(
            runtime["current_version"], exact_offer.manifest.version
        ) >= 0
    ):
        exact_offer = None
        status_code = "UP_TO_DATE"
        status_detail = "현재 사용 중인 Google 도구가 승인 목록과 같거나 더 최신입니다."
    elif (
        exact_offer is not None
        and component_update.gws_version_permanently_rejected(
            exact_offer.manifest.version,
            component_root=component_root,
        )
    ):
        # 안전검사에서 영구 거부된 판은 설치 함수도 다시 받지 않는다. 눌러도
        # 끝없이 거절되는 갱신 단추를 다시 보여 주지 않고 기본판 사용을 알린다.
        exact_offer = None
        status_success = False
        status_code = "COMPONENT_VERSION_REJECTED"
        status_detail = "앞서 안전 확인에 실패한 같은 Google 도구 판은 다시 받지 않고 현재 기본판을 사용합니다."
    payload = {
        "success": status_success,
        "code": status_code,
        "detail": status_detail,
        "checked_on": str(check.checked_on),
        "offer": _gws_offer_for_screen(exact_offer) if exact_offer is not None else None,
        **runtime,
    }
    return payload, exact_offer


def apply_gws_update(
    offer: component_update.GwsUpdateOffer,
    run_command=_default_run_command,
    *,
    component_root: Path | None = None,
    installer=None,
    resolver=None,
) -> dict:
    """승인 원문 한 판을 적용하고, cache를 버린 실제 선택 결과로 답한다."""
    installer = installer or component_update.install_gws_update
    result = installer(
        offer,
        component_root=component_root,
        run_command=run_command,
    )
    runtime = _gws_runtime_status(
        run_command,
        component_root=component_root,
        resolver=resolver,
        force_refresh=True,
    )
    selected_new_version = bool(
        result.success
        and runtime["runtime_ready"]
        and runtime["current_version"] == offer.manifest.version
        and runtime["current_source"] == "approved-update"
    )
    code = str(result.code)
    detail = (
        str(result.detail or "Google 연결 기능 갱신을 끝내지 못했습니다.")
        if result.success
        else component_update.user_update_failure_detail(result.code)
    )
    if result.success and not selected_new_version:
        code = "COMPONENT_RESOLUTION_FAILED"
        detail = "새 Google 도구를 적용한 뒤 실제 실행 판을 확인하지 못했습니다."
    if not selected_new_version:
        if runtime["runtime_ready"]:
            fallback = (
                "현재 기본판으로 계속 쓸 수 있어요."
                if runtime["current_source"] == "bundled"
                else "현재 사용 중인 Google 도구로 계속 쓸 수 있어요."
            )
            if fallback not in detail:
                detail = f"{detail} {fallback}".strip()
        else:
            repair = "설치 파일이 손상됐어요. Teacher Manager 설치 파일을 다시 실행해 주세요."
            if "설치 파일을 다시 실행" not in detail:
                detail = f"{detail} {repair}".strip()
    return {
        "success": selected_new_version,
        "code": code,
        "detail": detail,
        **runtime,
    }


def gws_auth_status(run_command, gws: str) -> dict:
    raw = run_command([gws, "auth", "status"])
    if isinstance(raw, tuple) and len(raw) == 2:
        code, output = raw
    else:
        code, output = 0, raw
    email = google_account.extract_email(output)
    logged_in = code == 0 and bool(email)
    account_allowed = logged_in and google_account.is_goedu_email(email)
    lowered = str(output or "").lower()
    status_document = None
    if code == 0 and not logged_in:
        try:
            parsed = process_win.parse_first_json(output)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            status_document = parsed
    credentials_absent = bool(
        status_document is not None
        and (
            str(status_document.get("storage") or "").casefold() == "none"
            or (
                status_document.get("encrypted_credentials_exists") is False
                and status_document.get("plain_credentials_exists") is False
            )
        )
    )
    # gws 0.22.5는 `auth status`마다 refresh token을 새 access token으로 바꿔 본다
    # (auth_commands.rs handle_status). Google이 access_token 없는 JSON으로 거절하면
    # `token_valid: false`를 적으므로 저장된 로그인은 더 쓸 수 없고 다시 로그인해야
    # 한다. 통신 자체가 막히면 `token_valid`도 `user`도 없이 돌아오는데, 이는 확인
    # 실패일 뿐 로그아웃이 아니라서 종전대로 `error`에 둔다(2026-09-03 실측).
    token_rejected = bool(
        status_document is not None and status_document.get("token_valid") is False
    )
    logged_out_markers = (
        "not logged in", "not authenticated", "no credentials", "login required",
    )
    if logged_in:
        login_state = "logged_in"
        error_code = ""
    elif (
        credentials_absent
        or token_rejected
        or any(mark in lowered for mark in logged_out_markers)
    ):
        login_state = "logged_out"
        error_code = ""
    else:
        login_state = "error"
        error_code = "GWS_AUTH_STATUS_FAILED"
    return {
        "logged_in": logged_in,
        "account_allowed": account_allowed,
        "user": email if logged_in else "",
        "login_state": login_state,
        "error_code": error_code,
    }


def require_goedu_gws_session(run_command, gws: str) -> str:
    """실제 Google 자료를 읽거나 쓰기 직전에 학교 계정을 다시 확인한다."""

    if not gws:
        raise RuntimeError("Google 연결 기능이 아직 준비되지 않았어요. Teacher Manager 설치 파일을 다시 실행해 주세요.")
    auth = gws_auth_status(run_command, gws)
    if not auth.get("logged_in"):
        if auth.get("login_state") == "error":
            raise RuntimeError(
                "Google 로그인 상태를 확인하지 못했어요. 설정에서 다시 점검해 주세요."
            )
        raise RuntimeError("Google Workspace에 @goedu.kr 계정으로 먼저 로그인해 주세요.")
    return google_account.require_goedu_email(auth.get("user", ""))


def gws_logout(run_command, gws: str) -> tuple[bool, str]:
    if not gws:
        return False, "Google 연결 기능이 아직 준비되지 않았어요. Teacher Manager 설치 파일을 다시 실행해 주세요."
    code, output = run_command([gws, "auth", "logout"])
    return code == 0, output.strip()[-300:]


_GWS_AUTH_HOST = "accounts.google.com"


def login_command(gws: str) -> list[str]:
    return [gws, "auth", "login", "--scopes", GWS_LOGIN_SCOPES]


def _gws_auth_url_from_line(line: str) -> str:
    """GWS가 한 줄 전체로 내놓은 Google HTTPS 주소만 잠깐 돌려준다."""
    candidate = str(line or "").strip()
    if not candidate or any(character.isspace() for character in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or str(host or "").casefold() != _GWS_AUTH_HOST
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return candidate


class LoginSession:
    """gws auth login을 돌리며 검증한 주소를 진행 중에만 메모리로 내놓는다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._auth_url_opener = external_url.open_external_url
        self._auth_url_attempted = False
        self._auth_url = ""
        self._browser_opened = False
        self._error_code = ""
        self._ok: bool | None = None
        self._on_complete = None

    def start(
        self,
        args: list[str],
        popen=None,
        env=None,
        auth_url_opener=None,
        on_complete=None,
    ) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False  # 이미 진행 중 — 멱등
            self._proc = process_win.popen_hidden(
                args, popen=popen or subprocess.Popen,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env=dict(env) if env is not None else None,
            )
            self._auth_url_opener = auth_url_opener or external_url.open_external_url
            self._auth_url_attempted = False
            self._auth_url = ""
            self._browser_opened = False
            self._error_code = ""
            self._ok = None
            self._on_complete = on_complete if callable(on_complete) else None
        threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()
        return True

    def _read(self, proc) -> None:
        code = 1
        try:
            for line in proc.stdout:
                auth_url = _gws_auth_url_from_line(line)
                if not auth_url:
                    continue
                with self._lock:
                    if proc is not self._proc or self._auth_url_attempted:
                        continue
                    self._auth_url_attempted = True
                    opener = self._auth_url_opener
                try:
                    opened_result = opener(auth_url)
                    opened = (
                        bool(opened_result.get("opened"))
                        if isinstance(opened_result, dict)
                        else opened_result is True
                    )
                except Exception:  # noqa: BLE001 - 주소와 운영체제 원문은 상태에 남기지 않는다.
                    opened = False
                with self._lock:
                    if proc is self._proc:
                        # 주소는 파일이나 로그에 쓰지 않고, 이 로그인 프로세스가
                        # 기다리는 동안 화면의 직접 열기·복사에만 잠깐 쓴다.
                        self._auth_url = auth_url
                        self._browser_opened = opened
            code = proc.wait()
        except Exception:  # noqa: BLE001 - 로그인 원문은 상태에 남기지 않는다.
            code = 1
        callback = None
        final_ok = False
        with self._lock:
            if proc is not self._proc:
                return
            self._auth_url = ""
            if self._error_code:
                final_ok = False
            else:
                final_ok = code == 0
            callback = self._on_complete
            self._on_complete = None
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
        with self._lock:
            if proc is self._proc and self._ok is None:
                # 로그인으로 바뀔 수 있는 자료와 공용 잠금 정리가 모두 끝난 뒤에만
                # 화면에 완료를 알린다.
                self._ok = final_ok

    def snapshot(self) -> dict:
        with self._lock:
            running = self._proc is not None and self._ok is None
            return {
                "running": running,
                "url": self._auth_url if running else "",
                "browser_opened": self._browser_opened,
                "ok": self._ok,
                "error_code": self._error_code if self._ok is False else "",
                "detail": "" if self._ok is not False else (self._error_code or "GWS_LOGIN_FAILED"),
            }

    def cancel(self) -> bool:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        proc.kill()
        return True


_LOGIN_SETUP_GUIDE = "Google 로그인 준비 파일을 확인한 뒤 다시 시도해 주세요."
_LOGIN_BROWSER_GUIDE = (
    "웹 브라우저를 열지 못했어요. 기본 브라우저 또는 Microsoft Edge를 준비한 뒤 다시 눌러 주세요."
)


def annotate_login_snapshot(snap: dict, environ=os.environ) -> dict:
    """OAuth 값·주소 질문값·원문 오류를 숨긴 짧은 로그인 상태를 돌려준다."""
    del environ
    if snap.get("ok") is not False:
        return snap
    if snap.get("error_code") == external_url.NO_EXTERNAL_BROWSER:
        return {
            **snap,
            "url": "",
            "error_code": external_url.NO_EXTERNAL_BROWSER,
            "detail": _LOGIN_BROWSER_GUIDE,
        }
    return {**snap, "url": "", "error_code": "GWS_LOGIN_FAILED", "detail": _LOGIN_SETUP_GUIDE}


# 배포 저장소(rheps/teacher-manager)의 최신 Release에 붙은 version.json을 본다.
UPDATE_INFO_URL = "https://github.com/rheps/teacher-manager/releases/latest/download/version.json"
_UPDATE_INFO_MAX_BYTES = 64 * 1024
_UPDATE_INFO_DEADLINE_SECONDS = 5.0
_UPDATE_SETUP_MAX_BYTES = 256 * 1024 * 1024
_UPDATE_SETUP_DEADLINE_SECONDS = 300.0
_UPDATE_READ_SIZE = 256 * 1024


class _UpdateDownloadHashMismatch(ValueError):
    pass


class _UpdateDownloadTooLarge(ValueError):
    pass


class _UpdateDownloadTimeout(TimeoutError):
    pass


class _UpdateInfoTooLarge(ValueError):
    pass


class _UpdateInfoMalformed(ValueError):
    pass


class _UpdateInfoUnsafeRedirect(ValueError):
    pass


def _fetch_update_json() -> dict:
    import urllib.request

    deadline = time.monotonic() + _UPDATE_INFO_DEADLINE_SECONDS
    contents = bytearray()
    with urllib.request.urlopen(UPDATE_INFO_URL, timeout=3) as response:
        _require_https_response(response, UPDATE_INFO_URL)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("UPDATE_INFO_TIMEOUT")
            block = response.read(min(16 * 1024, _UPDATE_INFO_MAX_BYTES + 1 - len(contents)))
            if time.monotonic() >= deadline:
                raise TimeoutError("UPDATE_INFO_TIMEOUT")
            if not block:
                break
            contents.extend(block)
            if len(contents) > _UPDATE_INFO_MAX_BYTES:
                raise _UpdateInfoTooLarge("UPDATE_INFO_TOO_LARGE")
    # BOM이 붙어 있어도 읽는다.
    try:
        value = _json.loads(bytes(contents).decode("utf-8-sig"))
    except (UnicodeError, _json.JSONDecodeError) as error:
        raise _UpdateInfoMalformed("UPDATE_INFO_MALFORMED") from error
    if not isinstance(value, dict):
        raise _UpdateInfoMalformed("UPDATE_INFO_MALFORMED")
    return value


def _empty_update(
    status: str = "latest",
    reason: str = "",
    latest: str = "",
    code: str = "",
) -> dict:
    return {
        "status": status,
        "code": str(code or ("UPDATE_LATEST" if status == "latest" else "UPDATE_INFO_UNAVAILABLE")),
        "available": False,
        "latest": latest,
        "url": "",
        "notes": "",
        "sha256": "",
        "reason": reason,
    }


def _version_parts(text: str) -> list[int]:
    # 시험판 꼬리표는 비교에서 빼되, 숫자 자리 자체가 깨진 버전은 받지 않는다.
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty")
    core = raw.split("-", 1)[0]
    parts: list[int] = []
    for segment in core.split("."):
        if segment == "" or not segment.isdigit():
            raise ValueError(raw)
        parts.append(int(segment))
    return parts


def _is_newer(latest: str, current: str) -> bool:
    # 길이가 달라도 0으로 채워 비교한다("1.7.4" vs "1.7.4.0"은 같은 버전 → 새 버전 아님).
    new, old = _version_parts(latest), _version_parts(current)
    width = max(len(new), len(old))
    new += [0] * (width - len(new))
    old += [0] * (width - len(old))
    return new > old


# url·sha256을 같은 version.json이 함께 정하므로, 임의 exe 실행을 막는
# 심층 방어는 배포 호스트 정확일치 allowlist다. (2026-07-20 감사 H11)
_UPDATE_HOST_ALLOWLIST = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})


def _valid_https_update_url(value) -> bool:
    raw = str(value or "").strip()
    if not raw or any(ch.isspace() or ord(ch) < 32 for ch in raw) or "\\" in raw:
        return False
    try:
        parsed = urlsplit(raw)
        _ = parsed.port  # 잘못된 포트나 대괄호도 여기서 거른다.
        return (
            parsed.scheme.lower() == "https"
            and bool(parsed.netloc)
            and (parsed.hostname or "").lower() in _UPDATE_HOST_ALLOWLIST
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _require_https_response(response, requested_url: str) -> None:
    """자동 이동 뒤의 실제 주소도 안전한 HTTPS인지 확인한다."""
    get_final_url = getattr(response, "geturl", None)
    final_url = get_final_url() if callable(get_final_url) else requested_url
    if not _valid_https_update_url(final_url):
        raise _UpdateInfoUnsafeRedirect("UNSAFE_UPDATE_INFO_REDIRECT")


# 원인을 뭉뚱그려 "인터넷 연결을 확인하세요"라고만 쓰면, 인터넷이 멀쩡한
# 컴퓨터에서 선생님이 엉뚱한 곳을 들여다보게 된다. component_update와 같은
# 갈래로 나눠 실제 이유를 그대로 알린다.
_UPDATE_CHECK_RETRY = "잠시 뒤 '업데이트 다시 확인'을 눌러 주세요."
_UPDATE_CHECK_OFFLINE = (
    "인터넷에 연결되지 않아 업데이트를 확인하지 못했어요. "
    "인터넷 연결을 확인한 뒤 '업데이트 다시 확인'을 눌러 주세요."
)


def _update_check_failure_reason(error: BaseException) -> str:
    if isinstance(error, (ssl.SSLCertVerificationError, ssl.CertificateError)):
        return (
            "보안 인증서를 확인하지 못해 업데이트 확인을 멈췄어요. "
            "학교 보안 프로그램이나 컴퓨터의 날짜·시간을 확인해 주세요."
        )
    if isinstance(error, urllib.error.HTTPError) and error.code == 407:
        return f"학교나 기관의 인터넷 인증이 필요해 업데이트를 확인하지 못했어요. {_UPDATE_CHECK_RETRY}"
    if isinstance(error, urllib.error.HTTPError):
        # 서버가 HTTP 응답을 돌려줬으니 인터넷이 없는 상황은 아니다.
        return f"배포 서버가 응답했지만 업데이트 확인을 마치지 못했어요. {_UPDATE_CHECK_RETRY}"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return f"인터넷 응답을 기다리는 시간이 지났어요. {_UPDATE_CHECK_RETRY}"
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return (
                "보안 인증서를 확인하지 못해 업데이트 확인을 멈췄어요. "
                "학교 보안 프로그램이나 컴퓨터의 날짜·시간을 확인해 주세요."
            )
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return f"인터넷 응답을 기다리는 시간이 지났어요. {_UPDATE_CHECK_RETRY}"
        return _UPDATE_CHECK_OFFLINE
    if isinstance(error, OSError):
        return _UPDATE_CHECK_OFFLINE
    return (
        "업데이트 확인을 하지 못했어요. "
        "인터넷 연결을 확인한 뒤 '업데이트 다시 확인'을 눌러 주세요."
    )


def check_update(current: str, fetch=None) -> dict:
    """새 버전, 최신 상태, 확인 실패를 서로 다른 값으로 알려준다."""
    fetch = fetch or _fetch_update_json
    try:
        data = fetch() or {}
        latest = str(data.get("version", "") or "")
        url = str(data.get("url", "") or "").strip()
        notes = str(data.get("notes", "") or "")
        sha256 = _valid_update_sha256(data.get("sha256", ""))
        newer = _is_newer(latest, current)
    except (ssl.SSLError, ssl.CertificateError) as error:
        # SSLCertVerificationError는 ValueError를 물려받는다. 아래 ValueError보다
        # 먼저 잡지 않으면 인증서 문제가 `버전 모양이 올바르지 않아요`로 나온다.
        return _empty_update(
            "failed", _update_check_failure_reason(error), code="UPDATE_INFO_TLS_UNSAFE"
        )
    except _UpdateInfoTooLarge:
        return _empty_update(
            "failed", "배포 정보가 너무 커서 안전하게 읽기를 중단했어요.",
            code="UPDATE_INFO_TOO_LARGE",
        )
    except (_UpdateInfoMalformed, _json.JSONDecodeError, UnicodeError):
        return _empty_update(
            "failed", "배포 정보를 읽을 수 없어 업데이트 확인을 중단했어요.",
            code="UPDATE_INFO_MALFORMED",
        )
    except _UpdateInfoUnsafeRedirect:
        return _empty_update(
            "failed", "배포 정보가 안전하지 않은 주소로 이동해 확인을 중단했어요.",
            code="UPDATE_INFO_REDIRECT_UNSAFE",
        )
    except ValueError as error:
        marker = str(error or "")
        if marker == "UPDATE_INFO_TOO_LARGE":
            return _empty_update(
                "failed", "배포 정보가 너무 커서 안전하게 읽기를 중단했어요.",
                code="UPDATE_INFO_TOO_LARGE",
            )
        if marker in {"UPDATE_INFO_MALFORMED"}:
            return _empty_update(
                "failed", "배포 정보를 읽을 수 없어 업데이트 확인을 중단했어요.",
                code="UPDATE_INFO_MALFORMED",
            )
        if marker in {"UNSAFE_UPDATE_INFO_REDIRECT", "unsafe final update URL"}:
            return _empty_update(
                "failed", "배포 정보가 안전하지 않은 주소로 이동해 확인을 중단했어요.",
                code="UPDATE_INFO_REDIRECT_UNSAFE",
            )
        return _empty_update(
            "failed", "배포 정보의 버전 모양이 올바르지 않아요.",
            code="UPDATE_INFO_VERSION_INVALID",
        )
    except Exception as error:  # noqa: BLE001 - 실행은 계속하되 화면에는 확인 실패를 정확히 알린다
        code = (
            "UPDATE_INFO_TLS_UNSAFE"
            if isinstance(
                getattr(error, "reason", error),
                (ssl.SSLCertVerificationError, ssl.CertificateError),
            )
            else "UPDATE_INFO_UNAVAILABLE"
        )
        return _empty_update("failed", _update_check_failure_reason(error), code=code)

    if not newer:
        return _empty_update("latest", "", latest, code="UPDATE_LATEST")
    if not _valid_https_update_url(url):
        return _empty_update(
            "failed", "업데이트 주소가 공식 배포 주소가 아니어서 안전하게 중단했어요.",
            latest, code="UPDATE_URL_UNSAFE",
        )
    if not sha256:
        return _empty_update(
            "failed", "설치 파일의 안전 확인 정보가 없어 업데이트를 중단했어요.",
            latest, code="UPDATE_SHA256_REQUIRED",
        )
    return {
        "status": "available",
        "code": "UPDATE_AVAILABLE",
        "available": True,
        "latest": latest,
        "url": url,
        "notes": notes,
        "sha256": sha256,
        "reason": "",
    }


def _valid_update_sha256(value) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return ""
    return digest


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_download_name(url: str) -> str:
    # 질문표·표시 조각에 든 슬래시를 파일 경로로 착각하지 않도록 URL의 path만 쓴다.
    try:
        path = urlsplit(str(url or "")).path
    except ValueError:
        path = ""
    name = unquote(path.rsplit("/", 1)[-1]).strip().rstrip(" .")
    invalid = set('<>:"/\\|?*')
    name = "".join("_" if ch in invalid or ord(ch) < 32 else ch for ch in name)
    name = name.strip().rstrip(" .")
    if not name:
        return "TeacherManager-Setup.exe"
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        return f"TeacherManager-{name}"
    return name


_OWNED_UPDATE_DOWNLOADS: dict[str, tuple[int, int]] = {}
_OWNED_UPDATE_DOWNLOADS_GUARD = threading.Lock()


def _download_path_key(path: Path) -> str:
    # resolve()는 링크를 따라간다. 안전 확인 전에는 글자 그대로의 절대 경로만 쓴다.
    return os.path.normcase(os.path.abspath(str(path)))


def _remember_owned_download(path: Path, identity: tuple[int, int]) -> None:
    with _OWNED_UPDATE_DOWNLOADS_GUARD:
        _OWNED_UPDATE_DOWNLOADS[_download_path_key(path)] = tuple(identity)


def _owned_download_identity(path: Path) -> tuple[int, int] | None:
    with _OWNED_UPDATE_DOWNLOADS_GUARD:
        return _OWNED_UPDATE_DOWNLOADS.get(_download_path_key(path))


def _forget_owned_download(path: Path) -> None:
    with _OWNED_UPDATE_DOWNLOADS_GUARD:
        _OWNED_UPDATE_DOWNLOADS.pop(_download_path_key(path), None)


def _unique_download_target(folder: Path, requested_name: str) -> Path:
    """기존 파일·링크를 건드리지 않고 쓸 수 있는 새 이름을 고른다."""
    requested = folder / requested_name
    if not os.path.lexists(requested):
        return requested
    suffix = Path(requested_name).suffix
    stem = requested_name[:-len(suffix)] if suffix else requested_name
    while True:
        candidate = folder / f"{stem}-{uuid.uuid4().hex}{suffix}"
        if not os.path.lexists(candidate):
            return candidate


def _publish_download_no_overwrite(partial: Path, folder: Path, name: str,
                                   identity: tuple[int, int]) -> Path:
    """완성된 임시 파일을 기존 파일을 덮지 않는 한 번의 이름 변경으로 공개한다."""
    while True:
        component_lock.prepare_direct_directory(folder)
        if component_lock.direct_file_identity(partial) != identity:
            raise component_lock.UnsafeLockPathError(
                "받는 중인 설치 파일이 다른 파일로 바뀌었습니다."
            )
        target = _unique_download_target(folder, name)
        try:
            if os.name == "nt":
                # Windows의 rename은 대상이 생겼으면 덮어쓰지 않고 실패한다.
                os.rename(partial, target)
            else:
                # POSIX rename은 덮어쓰므로, 단일 파일 시스템 안에서 link를
                # 독점 생성한 뒤 임시 이름을 떼어 같은 효과를 낸다.
                os.link(partial, target, follow_symlinks=False)
                partial.unlink()
        except OSError as error:
            if error.errno == errno.EEXIST or getattr(error, "winerror", None) == 183:
                continue
            raise
        try:
            if component_lock.direct_file_identity(target) != identity:
                raise component_lock.UnsafeLockPathError(
                    "완성된 설치 파일이 다른 파일로 바뀌었습니다."
                )
        except Exception:
            # 이름 변경 뒤 검사에서 실패해도, 우리가 만든 바로 그 단일 파일로
            # 확인되는 경우에만 치운다. 연결 수가 늘었으면 건드리지 않는다.
            component_lock.remove_owned_file(target, identity)
            raise
        return target


def _download_file(url: str, dest_dir, expected_sha256: str, opener=None):
    """Setup을 고유 임시 파일에 받고 같은 읽기에서 크기·시간·확인값을 검사한다."""
    import urllib.request

    expected = _valid_update_sha256(expected_sha256)
    if not expected:
        raise ValueError("UPDATE_SETUP_HASH_MISSING")
    if not _valid_https_update_url(url):
        raise ValueError("unsafe update URL")

    opener = opener or (lambda target, timeout=None: urllib.request.urlopen(target, timeout=timeout))
    requested_folder = (
        Path(dest_dir)
        if dest_dir
        else Path(tempfile.gettempdir()) / "TeacherManager-Update"
    )
    folder = component_lock.prepare_direct_directory(requested_folder)
    name = _safe_download_name(url)
    partial = folder / f".{name}.{os.getpid()}.{uuid.uuid4().hex}.partial"
    partial_identity: tuple[int, int] | None = None
    published: Path | None = None
    deadline = time.monotonic() + _UPDATE_SETUP_DEADLINE_SECONDS
    digest = hashlib.sha256()
    total = 0
    try:
        with opener(url, timeout=min(30, _UPDATE_SETUP_DEADLINE_SECONDS)) as source:
            _require_https_response(source, url)
            with partial.open("xb") as sink:
                partial_identity = component_lock.assert_open_file_is_direct(partial, sink)
                while True:
                    if time.monotonic() >= deadline:
                        raise _UpdateDownloadTimeout("UPDATE_SETUP_TIMEOUT")
                    remaining = _UPDATE_SETUP_MAX_BYTES - total
                    chunk = source.read(min(_UPDATE_READ_SIZE, remaining + 1))
                    if time.monotonic() >= deadline:
                        raise _UpdateDownloadTimeout("UPDATE_SETUP_TIMEOUT")
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _UPDATE_SETUP_MAX_BYTES:
                        raise _UpdateDownloadTooLarge("UPDATE_SETUP_TOO_LARGE")
                    # 네트워크를 읽는 동안 다른 이름이 붙거나 경로가 바뀌었는지,
                    # 실제 쓰기 바로 전에 열린 handle과 경로를 다시 맞춰 본다.
                    current = component_lock.assert_open_file_is_direct(partial, sink)
                    if current != partial_identity:
                        raise component_lock.UnsafeLockPathError(
                            "받는 중인 설치 파일이 다른 파일로 바뀌었습니다."
                        )
                    sink.write(chunk)
                    digest.update(chunk)
                sink.flush()
                os.fsync(sink.fileno())
                if time.monotonic() >= deadline:
                    raise _UpdateDownloadTimeout("UPDATE_SETUP_TIMEOUT")
                current = component_lock.assert_open_file_is_direct(partial, sink)
                if current != partial_identity:
                    raise component_lock.UnsafeLockPathError(
                        "받는 중인 설치 파일이 다른 파일로 바뀌었습니다."
                    )

        if digest.hexdigest() != expected:
            raise _UpdateDownloadHashMismatch("UPDATE_SETUP_HASH_MISMATCH")
        if partial_identity is None:
            raise component_lock.UnsafeLockPathError("받은 설치 파일을 확인하지 못했습니다.")
        published = _publish_download_no_overwrite(partial, folder, name, partial_identity)
        _remember_owned_download(published, partial_identity)
        return published
    except Exception:
        if published is not None and partial_identity is not None:
            component_lock.remove_owned_file(published, partial_identity)
        raise
    finally:
        # 우리가 만든 그 단일 파일이라는 확인이 될 때만 치운다. 공격자가 다른
        # 이름을 붙인 파일은 지워서 바깥 자료에 영향을 줄 수 있으므로 남긴다.
        if partial_identity is not None:
            component_lock.remove_owned_file(partial, partial_identity)


def _file_sha256(path: Path) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        identity = component_lock.assert_open_file_is_direct(path, source)
        owned = _owned_download_identity(path)
        if owned is not None and identity != owned:
            raise component_lock.UnsafeLockPathError(
                "받은 설치 파일이 다른 파일로 바뀌었습니다."
            )
        while True:
            chunk = source.read(_UPDATE_READ_SIZE)
            if not chunk:
                break
            digest.update(chunk)
        current = component_lock.assert_open_file_is_direct(path, source)
        if current != identity:
            raise component_lock.UnsafeLockPathError(
                "받은 설치 파일이 읽는 동안 바뀌었습니다."
            )
    return digest.hexdigest()


def _remove_download(path: Path) -> None:
    path = Path(path)
    identity = _owned_download_identity(path)
    if identity is None:
        return
    if component_lock.remove_owned_file(path, identity):
        _forget_owned_download(path)


_UPDATE_THREAD_LOCKS: dict[str, threading.Lock] = {}
_UPDATE_THREAD_LOCKS_GUARD = threading.Lock()
_UPDATE_RUN_RECORD_NAME = "update-run.generated.json"
_UPDATE_RUN_RECORD_SCHEMA = 1
_UPDATE_RUN_STAGE_ORDER = {
    "offer_verified": 1,
    "setup_verified": 2,
    "helper_stopped": 3,
    "setup_opened": 4,
}
_UPDATE_RUN_STAGE_CODES = {
    "offer_verified": "UPDATE_AVAILABLE",
    "setup_verified": "UPDATE_SETUP_READY",
    "helper_stopped": "UPDATE_HELPER_STOPPED",
    "setup_opened": "UPDATE_SETUP_LAUNCHED",
}


@dataclass
class _UpdateRunLease:
    acquired: bool

    def __bool__(self) -> bool:
        return self.acquired


def _update_lock_key(config_dir: Path) -> str:
    # 안전 확인 전 resolve()로 정션·링크를 따라가지 않는다.
    return os.path.normcase(os.path.abspath(str(config_dir)))


def _update_thread_lock(config_dir: Path) -> threading.Lock:
    key = _update_lock_key(config_dir)
    with _UPDATE_THREAD_LOCKS_GUARD:
        return _UPDATE_THREAD_LOCKS.setdefault(key, threading.Lock())


def _update_offer_token(*, latest: str, url: str, sha256: str) -> str:
    safe_latest = str(latest or "").strip()
    safe_url = str(url or "").strip()
    safe_sha256 = _valid_update_sha256(sha256)
    try:
        _version_parts(safe_latest)
    except ValueError as error:
        raise ComponentRecoveryStop(
            "UPDATE_OFFER_CHANGED",
            "확인했던 앱 업데이트 판을 다시 확인할 수 없습니다.",
        ) from error
    if not _valid_https_update_url(safe_url) or not safe_sha256:
        raise ComponentRecoveryStop(
            "UPDATE_OFFER_CHANGED",
            "확인했던 앱 업데이트 정보를 다시 확인할 수 없습니다.",
        )
    raw = _json.dumps(
        [safe_latest, safe_url, safe_sha256],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _public_update_run_status(record: dict | None) -> dict:
    if not record:
        return {
            "complete": False,
            "code": "UPDATE_RUN_NOT_STARTED",
            "stage": "",
            "latest": "",
        }
    stage = str(record["stage"])
    return {
        "complete": stage == "setup_opened",
        "code": _UPDATE_RUN_STAGE_CODES[stage],
        "stage": stage,
        "latest": str(record["latest"]),
    }


def _update_run_state_unclear(detail: str = "앱 업데이트 진행 기록을 안전하게 확인할 수 없습니다.") -> ComponentRecoveryStop:
    return ComponentRecoveryStop("UPDATE_RUN_STATE_UNCLEAR", detail)


def _validated_update_run_record(value) -> dict:
    allowed = {
        "schema_version", "offer_token", "latest", "stage", "setup_name",
        "setup_identity", "helper_stopped_by_flow", "launch_attempted",
    }
    if not isinstance(value, dict) or not set(value).issubset(allowed):
        raise _update_run_state_unclear()
    stage = value.get("stage")
    token = value.get("offer_token")
    latest = value.get("latest")
    if (
        value.get("schema_version") != _UPDATE_RUN_RECORD_SCHEMA
        or not isinstance(stage, str)
        or stage not in _UPDATE_RUN_STAGE_ORDER
        or not isinstance(token, str)
        or re.fullmatch(r"[0-9a-f]{64}", token) is None
        or not isinstance(latest, str)
        or not latest.strip()
        or len(latest) > 64
        or not isinstance(value.get("launch_attempted"), bool)
    ):
        raise _update_run_state_unclear()
    try:
        _version_parts(latest)
    except ValueError as error:
        raise _update_run_state_unclear() from error

    normalized = {
        "schema_version": _UPDATE_RUN_RECORD_SCHEMA,
        "offer_token": token,
        "latest": latest.strip(),
        "stage": stage,
        "launch_attempted": bool(value["launch_attempted"]),
    }
    if _UPDATE_RUN_STAGE_ORDER[stage] >= _UPDATE_RUN_STAGE_ORDER["setup_verified"]:
        setup_name = value.get("setup_name")
        identity = value.get("setup_identity")
        if (
            not isinstance(setup_name, str)
            or not setup_name
            or len(setup_name) > 260
            or Path(setup_name).name != setup_name
            or not isinstance(identity, list)
            or len(identity) != 2
            or any(not isinstance(part, int) or part < 0 for part in identity)
        ):
            raise _update_run_state_unclear()
        normalized["setup_name"] = setup_name
        normalized["setup_identity"] = list(identity)
    if _UPDATE_RUN_STAGE_ORDER[stage] >= _UPDATE_RUN_STAGE_ORDER["helper_stopped"]:
        if not isinstance(value.get("helper_stopped_by_flow"), bool):
            raise _update_run_state_unclear()
        normalized["helper_stopped_by_flow"] = bool(value["helper_stopped_by_flow"])
    if (
        normalized["launch_attempted"]
        and _UPDATE_RUN_STAGE_ORDER[stage] < _UPDATE_RUN_STAGE_ORDER["helper_stopped"]
    ):
        raise _update_run_state_unclear()
    if stage == "setup_opened" and not normalized["launch_attempted"]:
        raise _update_run_state_unclear()
    return normalized


def _load_update_run_record(config_dir: Path) -> dict | None:
    try:
        base = component_lock.prepare_direct_directory(Path(config_dir))
        path = base / _UPDATE_RUN_RECORD_NAME
        if not os.path.lexists(path):
            return None
        path = component_lock.prepare_direct_file_path(path)
        with path.open("r", encoding="utf-8") as source:
            component_lock.assert_open_file_is_direct(path, source)
            value = _json.load(source)
            component_lock.assert_open_file_is_direct(path, source)
    except ComponentRecoveryStop:
        raise
    except (OSError, ValueError, TypeError, component_lock.UnsafeLockPathError) as error:
        raise _update_run_state_unclear() from error
    return _validated_update_run_record(value)


def _write_update_run_record(config_dir: Path, record: dict) -> None:
    safe = _validated_update_run_record(record)
    try:
        base = component_lock.prepare_direct_directory(Path(config_dir))
        path = component_lock.prepare_direct_file_path(base / _UPDATE_RUN_RECORD_NAME)
        component_lock.atomic_write_text_unique(
            path,
            _json.dumps(safe, ensure_ascii=True, indent=2) + "\n",
        )
        if _load_update_run_record(base) != safe:
            raise OSError("update run record read-back mismatch")
    except ComponentRecoveryStop:
        raise
    except (OSError, ValueError, component_lock.UnsafeLockPathError) as error:
        raise _update_run_state_unclear() from error


def record_update_run_stage(
    config_dir: Path,
    *,
    latest: str,
    url: str,
    sha256: str,
    stage: str,
    setup_path: Path | None = None,
    helper_stopped_by_flow: bool | None = None,
    launch_attempted: bool | None = None,
) -> dict:
    """Atomically record a non-personal stage for the exact approved offer."""

    safe_stage = str(stage or "")
    if safe_stage not in _UPDATE_RUN_STAGE_ORDER:
        raise ValueError("unknown update run stage")
    token = _update_offer_token(latest=latest, url=url, sha256=sha256)
    current = _load_update_run_record(Path(config_dir))
    if current is not None and current["offer_token"] != token:
        raise ComponentRecoveryStop(
            "UPDATE_OFFER_CHANGED",
            "화면에서 확인한 앱 업데이트 정보가 달라졌습니다.",
        )
    if current is None:
        current = {
            "schema_version": _UPDATE_RUN_RECORD_SCHEMA,
            "offer_token": token,
            "latest": str(latest).strip(),
            "stage": safe_stage,
            "launch_attempted": False,
        }
    elif _UPDATE_RUN_STAGE_ORDER[safe_stage] > _UPDATE_RUN_STAGE_ORDER[current["stage"]]:
        current = {**current, "stage": safe_stage}
    if setup_path is not None:
        path = Path(setup_path)
        try:
            identity = component_lock.direct_file_identity(path)
        except (OSError, component_lock.UnsafeLockPathError) as error:
            raise _update_run_state_unclear() from error
        current["setup_name"] = path.name
        current["setup_identity"] = [int(identity[0]), int(identity[1])]
    if helper_stopped_by_flow is not None:
        current["helper_stopped_by_flow"] = bool(helper_stopped_by_flow)
    if launch_attempted is not None:
        current["launch_attempted"] = bool(launch_attempted)
    _write_update_run_record(Path(config_dir), current)
    return _public_update_run_status(current)


def verify_update_run_stage(
    config_dir: Path,
    *,
    latest: str,
    url: str,
    sha256: str,
    expected_stage: str,
) -> tuple[bool, dict]:
    """Read an update stage without returning its URL, hash, or local file path."""

    if expected_stage not in _UPDATE_RUN_STAGE_ORDER:
        raise ValueError("unknown update run stage")
    token = _update_offer_token(latest=latest, url=url, sha256=sha256)
    current = _load_update_run_record(Path(config_dir))
    if current is not None and current["offer_token"] != token:
        raise ComponentRecoveryStop(
            "UPDATE_OFFER_CHANGED",
            "화면에서 확인한 앱 업데이트 정보가 달라졌습니다.",
        )
    status = _public_update_run_status(current)
    complete = bool(
        current is not None
        and _UPDATE_RUN_STAGE_ORDER[current["stage"]]
        >= _UPDATE_RUN_STAGE_ORDER[expected_stage]
    )
    return complete, status


def _read_update_run_record(
    config_dir: Path,
    *,
    latest: str,
    url: str,
    sha256: str,
) -> dict | None:
    """Read private cross-process resume fields without returning them to a screen."""

    token = _update_offer_token(latest=latest, url=url, sha256=sha256)
    current = _load_update_run_record(Path(config_dir))
    if current is not None and current["offer_token"] != token:
        raise ComponentRecoveryStop(
            "UPDATE_OFFER_CHANGED",
            "화면에서 확인한 앱 업데이트 정보가 달라졌습니다.",
        )
    return dict(current) if current is not None else None


def _try_lock_update_file(lock_path: Path, lock_file) -> bool:
    # 첫 바이트를 쓰기 바로 전에 경로와 열린 handle이 같은 단일 파일인지 확인한다.
    component_lock.assert_open_file_is_direct(lock_path, lock_file)
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        component_lock.assert_open_file_is_direct(lock_path, lock_file)
        lock_file.write(b"\0")
        lock_file.flush()
    lock_file.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_update_file(lock_file) -> None:
    lock_file.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def update_run_lock(config_dir: Path | None = None):
    """여러 창이나 프로세스가 동시에 누르면 먼저 시작한 한 요청만 들여보낸다."""
    requested_base = (
        Path(config_dir)
        if config_dir
        else Path(tempfile.gettempdir()) / "TeacherManager-Update"
    )
    try:
        base = component_lock.prepare_direct_directory(requested_base)
        lock_path = component_lock.prepare_direct_file_path(base / ".update-run.lock")
    except (OSError, component_lock.UnsafeLockPathError):
        yield _UpdateRunLease(False)
        return
    local_lock = _update_thread_lock(base)
    if not local_lock.acquire(blocking=False):
        yield _UpdateRunLease(False)
        return

    lock_file = None
    lease = _UpdateRunLease(False)
    try:
        try:
            try:
                lock_file = lock_path.open("xb")
            except FileExistsError:
                # 기존 파일을 바꾸지 않는 방식으로 연 뒤 identity를 확인한다.
                lock_file = lock_path.open("r+b")
            component_lock.assert_open_file_is_direct(lock_path, lock_file)
        except (OSError, component_lock.UnsafeLockPathError):
            if lock_file is not None:
                lock_file.close()
                lock_file = None
            yield lease
            return
        try:
            locked = _try_lock_update_file(lock_path, lock_file)
        except (OSError, component_lock.UnsafeLockPathError):
            locked = False
        if not locked:
            lock_file.close()
            lock_file = None
            yield lease
            return
        lease = _UpdateRunLease(True)
        yield lease
    finally:
        try:
            if lock_file is not None:
                try:
                    _unlock_update_file(lock_file)
                finally:
                    lock_file.close()
        finally:
            local_lock.release()


# 업데이트도 설치 진행 상황과 오류를 사용자가 볼 수 있게 설치 마법사를 연다.
# 실행 중인 앱은 안전하게 닫되 컴퓨터를 자동 재시작하지 않는다.
UPDATE_SETUP_ARGS = ["/NORESTART", "/CLOSEAPPLICATIONS"]


def update_launch_command(path) -> list[str]:
    """진행 상황을 볼 수 있는 업데이트용 설치 파일 실행 명령을 만든다."""
    return [str(path), *UPDATE_SETUP_ARGS]


def read_update_state(config_dir) -> dict:
    """업데이트 확인 기록을 읽는다. 없거나 망가졌으면 빈 기록으로 본다."""
    return _read_json_dict(paths.update_state_path(Path(config_dir))) or {}


def _write_update_state(config_dir, changes: dict) -> None:
    # 이 기록은 작성자가 둘이다 — 도우미(tray_win)와 대시보드(bridge)가 로그인 직후
    # 몇 초 안에 둘 다 쓴다. 그래서 고정 임시 이름을 쓰는 atomic_io 대신 쓸 때마다
    # 새 임시 이름을 만드는 _atomic_write_json을 쓴다.
    state = read_update_state(config_dir)
    state.update(changes)
    _atomic_write_json(paths.update_state_path(Path(config_dir)), state)


def remember_update_checked(config_dir, today: str) -> None:
    _write_update_state(config_dir, {"last_checked": str(today)})


def remember_update_declined(config_dir, latest: str, today: str) -> None:
    """오늘은 그만 물으라는 뜻이다. 어느 버전을 언제 취소했는지 함께 적는다."""
    _write_update_state(
        config_dir, {"declined_version": str(latest), "declined_on": str(today)}
    )


def should_check_update(config_dir, today: str) -> bool:
    """하루에 한 번만 확인하러 나간다."""
    return read_update_state(config_dir).get("last_checked") != str(today)


def should_ask_update(config_dir, latest: str, today: str) -> bool:
    """오늘 취소한 그 버전만 안 묻는다. 날짜가 바뀌거나 더 새 버전이면 다시 묻는다."""
    state = read_update_state(config_dir)
    if state.get("declined_on") != str(today):
        return True
    declined = str(state.get("declined_version") or "")
    if declined == str(latest):
        return False
    try:
        return _is_newer(str(latest), declined)
    except ValueError:
        # declined_version이 없거나 숫자 모양이 아니면 기록을 못 믿는다는 뜻이다.
        # read_update_state가 망가진 파일을 빈 기록으로 보는 것과 같은 철학 —
        # 못 믿을 땐 "다시 묻는다" 쪽으로 기운다.
        return True


def _windows_process_image_paths(executable_name: str) -> tuple[str, ...]:
    """Read full image paths for Windows processes with the same executable name."""

    if sys.platform != "win32":
        raise OSError("Windows process scan is unavailable")
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "process snapshot failed")
    wanted = str(executable_name or "").casefold()
    paths_found: list[str] = []
    uncertain_match = False
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        ctypes.set_last_error(0)
        has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        if not has_entry:
            scan_error = ctypes.get_last_error()
            if scan_error != 18:
                raise OSError(scan_error, "process snapshot read failed")
        while has_entry:
            if str(entry.szExeFile).casefold() == wanted:
                handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                if not handle:
                    uncertain_match = True
                else:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(buffer))
                        if kernel32.QueryFullProcessImageNameW(
                            handle, 0, buffer, ctypes.byref(size)
                        ):
                            paths_found.append(buffer.value)
                        else:
                            uncertain_match = True
                    finally:
                        kernel32.CloseHandle(handle)
            ctypes.set_last_error(0)
            has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
            if not has_entry:
                scan_error = ctypes.get_last_error()
    finally:
        kernel32.CloseHandle(snapshot)
    if scan_error != 18:
        raise OSError(scan_error, "process snapshot read failed")
    if uncertain_match:
        raise OSError("matching process path could not be read")
    return tuple(paths_found)


def _normalized_process_image_path(value) -> str:
    text = os.path.abspath(os.fspath(value))
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(text))


def setup_process_is_open(setup_path: Path, *, process_paths=None) -> bool | None:
    """Return True/False only after full image paths can be compared exactly."""

    try:
        safe_path = component_lock.prepare_direct_file_path(Path(setup_path))
        component_lock.direct_file_identity(safe_path)
        reader = process_paths or _windows_process_image_paths
        running_paths = reader(safe_path.name)
        if running_paths is None:
            return None
        expected = _normalized_process_image_path(safe_path)
        return any(
            _normalized_process_image_path(candidate) == expected
            for candidate in running_paths
        )
    except (OSError, TypeError, ValueError, component_lock.UnsafeLockPathError):
        return None


def _recorded_update_setup(record: dict, dest_dir, expected_sha256: str) -> Path | None:
    """Re-open only the exact direct Setup named and identified by the work record."""
    if _UPDATE_RUN_STAGE_ORDER[record["stage"]] < _UPDATE_RUN_STAGE_ORDER["setup_verified"]:
        return None
    requested_folder = (
        Path(dest_dir)
        if dest_dir
        else Path(tempfile.gettempdir()) / "TeacherManager-Update"
    )
    try:
        folder = component_lock.prepare_direct_directory(requested_folder)
        candidate = component_lock.prepare_direct_file_path(folder / record["setup_name"])
        identity = component_lock.direct_file_identity(candidate)
    except (OSError, component_lock.UnsafeLockPathError) as error:
        raise _update_run_state_unclear(
            "앞서 확인한 Setup 파일을 같은 자리에서 다시 확인할 수 없습니다."
        ) from error
    saved_identity = tuple(record["setup_identity"])
    if identity != saved_identity:
        raise _update_run_state_unclear(
            "앞서 확인한 Setup 파일이 다른 파일로 바뀌어 자동 실행하지 않았습니다."
        )
    _remember_owned_download(candidate, identity)
    try:
        digest = _file_sha256(candidate)
    except (OSError, component_lock.UnsafeLockPathError) as error:
        raise _update_run_state_unclear(
            "앞서 확인한 Setup 파일을 안전하게 다시 읽지 못했습니다."
        ) from error
    if digest != expected_sha256:
        _remove_download(candidate)
        raise _UpdateDownloadHashMismatch("UPDATE_SETUP_HASH_MISMATCH")
    return candidate


def _update_stop_result(error: ComponentRecoveryStop, latest: str) -> dict:
    reason = (
        "화면에서 확인한 업데이트 정보가 달라 안전하게 중단했어요."
        if error.code == "UPDATE_OFFER_CHANGED"
        else error.detail
    )
    return {
        "started": False,
        "code": error.code,
        "latest": latest,
        "reason": reason,
    }


def start_update(current: str, fetch=None, opener=None, launch=None, dest_dir=None,
                 url: str = "", latest: str = "", sha256: str = "",
                 stop_before_launch=None, config_dir=None,
                 resume_after_launch_failure=None, helper_is_running=None,
                 setup_is_open=None) -> dict:
    """새 설치 파일을 받아 설치 창을 연다 — 기존 위에 덮어써서 삭제·재설치가 필요 없다.

    화면이 이미 확인한 url을 넘겨주면(url) 다시 조회하지 않는다 — 재조회 중 통신이
    깜빡여 '지금이 최신'으로 잘못 안내하는 일을 막는다.
    """
    if url:
        target_url, target_latest = str(url).strip(), latest or ""
        target_sha256 = _valid_update_sha256(sha256)
        if not (_valid_https_update_url(target_url) and target_sha256):
            return {
                "started": False,
                "code": "UPDATE_OFFER_CHANGED",
                "latest": target_latest,
                "reason": "업데이트 파일을 안전하게 확인할 수 없어요. 다시 확인한 뒤 시도해 주세요.",
            }
    else:
        info = check_update(current, fetch=fetch)
        if not info["available"]:
            reason = info.get("reason") or "지금이 최신 버전이에요"
            return {
                "started": False,
                "code": str(info.get("code") or "UPDATE_INFO_UNAVAILABLE"),
                "latest": info["latest"],
                "reason": reason,
            }
        target_url, target_latest, target_sha256 = info["url"], info["latest"], info["sha256"]

    with update_run_lock(config_dir) as lease:
        if not lease:
            return {
                "started": False,
                "code": "UPDATE_RUN_BUSY",
                "latest": target_latest,
                "reason": "다른 창에서 업데이트가 이미 진행 중이에요. 잠시 기다려 주세요.",
            }
        progress = None
        if config_dir is not None:
            try:
                progress = _load_update_run_record(Path(config_dir))
                target_token = _update_offer_token(
                    latest=target_latest,
                    url=target_url,
                    sha256=target_sha256,
                )
                if progress is not None and progress["offer_token"] != target_token:
                    try:
                        prior_offer_is_newer = _is_newer(progress["latest"], str(current))
                    except ValueError as error:
                        raise _update_run_state_unclear(
                            "현재 앱 판 번호를 확인할 수 없어 새 업데이트를 시작하지 않았습니다."
                        ) from error
                    if prior_offer_is_newer:
                        raise ComponentRecoveryStop(
                            "UPDATE_OFFER_CHANGED",
                            "화면에서 확인한 앱 업데이트 정보가 달라졌습니다.",
                        )
                    # 이전 Setup 판 이상으로 앱이 실제 올라온 뒤에만 다음 제안 기록을 시작한다.
                    progress = {
                        "schema_version": _UPDATE_RUN_RECORD_SCHEMA,
                        "offer_token": target_token,
                        "latest": str(target_latest).strip(),
                        "stage": "offer_verified",
                        "launch_attempted": False,
                    }
                    _write_update_run_record(Path(config_dir), progress)
                if progress is None:
                    record_update_run_stage(
                        Path(config_dir),
                        latest=target_latest,
                        url=target_url,
                        sha256=target_sha256,
                        stage="offer_verified",
                    )
                    progress = _read_update_run_record(
                        Path(config_dir),
                        latest=target_latest,
                        url=target_url,
                        sha256=target_sha256,
                    )
            except ComponentRecoveryStop as error:
                return _update_stop_result(error, target_latest)
            if progress and progress["stage"] == "setup_opened":
                return {
                    "started": False,
                    "code": "UPDATE_SETUP_LAUNCHED",
                    "stage": "setup_opened",
                    "latest": target_latest,
                    "reason": "업데이트가 이미 진행 중이며 Setup 창을 열었습니다. 같은 창에서 계속해 주세요.",
                }

        path = None
        if progress is not None:
            try:
                path = _recorded_update_setup(progress, dest_dir, target_sha256)
            except _UpdateDownloadHashMismatch:
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_HASH_MISMATCH",
                    "latest": target_latest,
                    "reason": "받은 업데이트 파일이 배포 정보와 일치하지 않아요. 다시 실행하지 않았습니다.",
                }
            except ComponentRecoveryStop as error:
                return _update_stop_result(error, target_latest)

        if progress and progress.get("launch_attempted"):
            if setup_is_open is None:
                return {
                    "started": False,
                    "code": "UPDATE_SETUP_LAUNCH_UNCERTAIN",
                    "latest": target_latest,
                    "reason": "Setup 창을 앞서 열었는지 확실히 확인할 수 없어 다시 열지 않았어요.",
                }
            try:
                opened = setup_is_open(path)
            except Exception:  # noqa: BLE001 - 확인 불능이면 절대로 두 번째 창을 열지 않는다
                opened = None
            if opened is True:
                try:
                    record_update_run_stage(
                        Path(config_dir),
                        latest=target_latest,
                        url=target_url,
                        sha256=target_sha256,
                        stage="setup_opened",
                        setup_path=path,
                        helper_stopped_by_flow=bool(progress.get("helper_stopped_by_flow")),
                        launch_attempted=True,
                    )
                except ComponentRecoveryStop as error:
                    return _update_stop_result(error, target_latest)
                return {
                    "started": False,
                    "code": "UPDATE_SETUP_LAUNCHED",
                    "stage": "setup_opened",
                    "latest": target_latest,
                    "reason": "업데이트가 이미 진행 중이며 Setup 창을 열었습니다. 같은 창에서 계속해 주세요.",
                }
            return {
                "started": False,
                "code": "UPDATE_SETUP_LAUNCH_UNCERTAIN",
                "latest": target_latest,
                "reason": "Setup 창을 앞서 열었는지 확실히 확인할 수 없어 다시 열지 않았어요.",
            }

        if path is None:
            try:
                path = _download_file(
                    target_url,
                    dest_dir,
                    target_sha256,
                    opener=opener,
                )
            except _UpdateDownloadHashMismatch:
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_HASH_MISMATCH",
                    "latest": target_latest,
                    "reason": "받은 업데이트 파일이 배포 정보와 일치하지 않아요. 잠시 뒤 다시 시도해 주세요.",
                }
            except _UpdateDownloadTooLarge:
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_TOO_LARGE",
                    "latest": target_latest,
                    "reason": "받으려는 설치 파일이 허용된 크기보다 커서 중단했어요.",
                }
            except _UpdateDownloadTimeout:
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_UNAVAILABLE",
                    "latest": target_latest,
                    "reason": "설치 파일을 받는 시간이 너무 길어 중단했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.",
                }
            except component_lock.UnsafeLockPathError:
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_PATH_UNSAFE",
                    "latest": target_latest,
                    "reason": "업데이트를 저장할 폴더와 파일을 안전하게 확인하지 못했어요.",
                }
            except Exception:  # noqa: BLE001 - 통신 실패를 사람 말로
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_UNAVAILABLE",
                    "latest": target_latest,
                    "reason": "새 버전 다운로드에 실패했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.",
                }
            try:
                actual_sha256 = _file_sha256(path)
            except OSError:
                _remove_download(path)
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_UNAVAILABLE",
                    "latest": target_latest,
                    "reason": "받은 업데이트 파일을 안전하게 확인하지 못했어요. 다시 시도해 주세요.",
                }
            if actual_sha256 != target_sha256:
                _remove_download(path)
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_HASH_MISMATCH",
                    "latest": target_latest,
                    "reason": "받은 업데이트 파일이 배포 정보와 일치하지 않아요. 잠시 뒤 다시 시도해 주세요.",
                }

        if config_dir is not None:
            try:
                record_update_run_stage(
                    Path(config_dir),
                    latest=target_latest,
                    url=target_url,
                    sha256=target_sha256,
                    stage="setup_verified",
                    setup_path=path,
                )
                progress = _read_update_run_record(
                    Path(config_dir),
                    latest=target_latest,
                    url=target_url,
                    sha256=target_sha256,
                )
            except ComponentRecoveryStop as error:
                return _update_stop_result(error, target_latest)

        checker = helper_is_running or helper_window_exists
        helper_stage_done = bool(
            progress
            and _UPDATE_RUN_STAGE_ORDER[progress["stage"]]
            >= _UPDATE_RUN_STAGE_ORDER["helper_stopped"]
        )
        helper_stopped_by_flow = bool((progress or {}).get("helper_stopped_by_flow"))
        try:
            helper_running_now = bool(checker())
        except Exception:  # noqa: BLE001 - 알 수 없는 상태를 "켜짐"으로 짐작하지 않는다
            return {
                "started": False,
                "code": "UPDATE_HELPER_BUSY",
                "latest": target_latest,
                "reason": "도우미 상태를 확인하지 못해 설치를 시작하지 않았어요. 앱을 닫고 다시 시도해 주세요.",
            }

        if not helper_stage_done or helper_running_now:
            stopper = stop_before_launch or stop_helper
            try:
                stopped = bool(stopper())
            except Exception:  # noqa: BLE001 - 응답 손실이면 실제 종료 상태를 바로 다시 읽는다
                stopped = False
            if not stopped and helper_running_now:
                try:
                    stopped = not bool(checker())
                except Exception:  # noqa: BLE001 - 확인할 수 없으면 설치기를 열지 않는다
                    stopped = False
            if not stopped:
                return {
                    "started": False,
                    "code": "UPDATE_HELPER_BUSY",
                    "latest": target_latest,
                    "reason": "도우미를 먼저 종료하지 못해 설치를 시작하지 않았어요. 앱을 닫고 다시 시도해 주세요.",
                }
            if helper_running_now:
                helper_stopped_by_flow = True

            if config_dir is not None:
                try:
                    record_update_run_stage(
                        Path(config_dir),
                        latest=target_latest,
                        url=target_url,
                        sha256=target_sha256,
                        stage="helper_stopped",
                        setup_path=path,
                        helper_stopped_by_flow=helper_stopped_by_flow,
                    )
                    progress = _read_update_run_record(
                        Path(config_dir),
                        latest=target_latest,
                        url=target_url,
                        sha256=target_sha256,
                    )
                except ComponentRecoveryStop as error:
                    return _update_stop_result(error, target_latest)

        # 받기가 끝난 뒤 파일이 바뀔 수 있으므로, 도우미를 닫은 다음 설치 창을
        # 열기 바로 전에 같은 확인값을 다시 계산한다.
        try:
            launch_sha256 = _file_sha256(path)
        except OSError:
            launch_sha256 = ""
        if launch_sha256 != target_sha256:
            _remove_download(path)
            return {
                "started": False,
                "code": "UPDATE_DOWNLOAD_HASH_MISMATCH",
                "latest": target_latest,
                "reason": "받은 업데이트 파일이 배포 정보와 일치하지 않아요. 다시 실행하지 않았습니다.",
            }

        if config_dir is not None:
            try:
                record_update_run_stage(
                    Path(config_dir),
                    latest=target_latest,
                    url=target_url,
                    sha256=target_sha256,
                    stage="helper_stopped",
                    setup_path=path,
                    helper_stopped_by_flow=helper_stopped_by_flow,
                    launch_attempted=True,
                )
            except ComponentRecoveryStop as error:
                return _update_stop_result(error, target_latest)

        try:
            # 설치 진행과 오류를 사용자가 바로 볼 수 있게 마법사 전체를 보여 준다.
            run = launch or (
                lambda file: subprocess.Popen(update_launch_command(file), close_fds=True)
            )
            run(path)
        except Exception:  # noqa: BLE001
            opened = False
            if setup_is_open is None:
                return {
                    "started": False,
                    "code": "UPDATE_SETUP_LAUNCH_UNCERTAIN",
                    "latest": target_latest,
                    "reason": "Setup 창을 열었는지 확인할 수 없어 다시 열지 않았어요.",
                }
            try:
                observed = setup_is_open(path)
            except Exception:  # noqa: BLE001
                observed = None
            if observed is True:
                opened = True
            elif observed is not False:
                return {
                    "started": False,
                    "code": "UPDATE_SETUP_LAUNCH_UNCERTAIN",
                    "latest": target_latest,
                    "reason": "Setup 창을 열었는지 확인할 수 없어 다시 열지 않았어요.",
                }
            if opened:
                if config_dir is not None:
                    try:
                        record_update_run_stage(
                            Path(config_dir),
                            latest=target_latest,
                            url=target_url,
                            sha256=target_sha256,
                            stage="setup_opened",
                            setup_path=path,
                            helper_stopped_by_flow=helper_stopped_by_flow,
                            launch_attempted=True,
                        )
                    except ComponentRecoveryStop as error:
                        return _update_stop_result(error, target_latest)
                return {
                    "started": True,
                    "code": "UPDATE_SETUP_LAUNCHED",
                    "stage": "setup_opened",
                    "latest": target_latest,
                    "reason": "",
                }
            # Setup을 열지 못한 것이 확인됐을 때만, 이 흐름이 실제로 끈 도우미를 되살린다.
            reason = "설치 파일을 실행하지 못했어요. 다운로드 페이지에서 직접 받아 주세요."
            if helper_stopped_by_flow:
                try:
                    restored = bool((resume_after_launch_failure or start_helper)())
                except Exception:  # noqa: BLE001
                    restored = False
                if not restored:
                    reason += " 도우미도 다시 켜지지 않았어요. 앱을 다시 실행해 주세요."
            return {
                "started": False,
                "code": "UPDATE_SETUP_LAUNCH_FAILED",
                "latest": target_latest,
                "reason": reason,
            }
        if config_dir is not None:
            try:
                record_update_run_stage(
                    Path(config_dir),
                    latest=target_latest,
                    url=target_url,
                    sha256=target_sha256,
                    stage="setup_opened",
                    setup_path=path,
                    helper_stopped_by_flow=helper_stopped_by_flow,
                    launch_attempted=True,
                )
            except ComponentRecoveryStop as error:
                return _update_stop_result(error, target_latest)
        return {
            "started": True,
            "code": "UPDATE_SETUP_LAUNCHED",
            "stage": "setup_opened",
            "latest": target_latest,
            "reason": "",
        }


AI_TOOLS = [
    {"key": "claude", "name": "클로드 코드", "folder": ".claude", "agents": ["claude-code"]},
    {"key": "codex", "name": "GPT Codex", "folder": ".codex", "agents": ["codex"]},
    {"key": "gemini", "name": "제미나이 (Gemini CLI·Antigravity)", "folder": ".gemini",
     "agents": ["gemini-cli", "antigravity"]},
]
# 공개 저장소 — 선생님 컴퓨터에서 로그인 없이 받을 수 있어야 한다.
# 작업용 저장소(비공개)에는 점검 보고서·서버 소스가 함께 있어 공개하지 않는다.
AI_SKILL_REPO = "rheps/teacher-manager"
AI_SKILL_APPROVAL_FILENAME = "ai-skill-approval.json"


def ai_skill_install_enabled() -> bool:
    """공개 배포 정보에서 명시적으로 켠 경우에만 AI 연결 명령을 허용한다."""
    try:
        data = _json.loads((bundle_paths.bundle_root() / "release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("aiSkillInstallEnabled") is True


def attendance_ui_enabled() -> bool:
    """설치본에 출결 시험 화면이 명시적으로 들어간 경우에만 화면을 연다."""
    try:
        data = _json.loads((bundle_paths.bundle_root() / "release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("attendanceUiEnabled") is True


def ai_tools_status(home=None) -> list:
    home = Path(home) if home else Path.home()
    return [
        {"key": tool["key"], "name": tool["name"], "found": (home / tool["folder"]).exists()}
        for tool in AI_TOOLS
    ]


def verify_ai_skills_completion(
    keys,
    *,
    approval,
    environ=None,
    plan_builder=None,
    approved_checker=None,
) -> tuple[bool, dict]:
    """Check only the selected AI destinations and their exact approved receipt."""

    if isinstance(keys, (str, bytes)):
        raw_keys = {str(keys)}
    else:
        raw_keys = {str(key) for key in (keys or [])}
    known_keys = {tool["key"] for tool in AI_TOOLS}
    if not raw_keys or not raw_keys.issubset(known_keys):
        raise ComponentRecoveryStop(
            "AI_SELECTION_REQUIRED",
            "연결할 AI 선택을 안전하게 이어갈 수 없습니다.",
        )
    selected_tools = [tool for tool in AI_TOOLS if tool["key"] in raw_keys]
    selected = [tool["key"] for tool in selected_tools]
    agents = [agent for tool in selected_tools for agent in tool["agents"]]
    try:
        approved = ai_skill_install.validate_approved_skill(approval)
        plan = (plan_builder or ai_skill_install.prepare_install_plan)(
            agents,
            environ=environ,
        )
        complete = bool(
            (approved_checker or ai_skill_install.plan_is_already_approved)(
                plan,
                approved,
            )
        )
    except ai_skill_install.AiSkillInstallError as error:
        raise ComponentRecoveryStop(error.code, error.detail) from error
    except OSError as error:
        raise recovery.RetryableOperationError(
            "AI_SKILLS_READ_FAILED",
            "선택한 AI 연결 상태를 다시 확인하고 있어요.",
        ) from error
    return complete, {
        "success": complete,
        "code": "AI_SKILLS_READY" if complete else "AI_SKILLS_NOT_READY",
        "detail": (
            "선택한 AI에 검토한 연결이 이미 준비돼 있습니다."
            if complete
            else "선택한 AI에 검토한 연결이 아직 준비되지 않았습니다."
        ),
        "selected": selected,
        "receipt": approved.commit,
    }


def _ai_node_result(runtime: tool_runtime.NodeRuntime) -> dict:
    return {
        "success": bool(runtime.ready),
        "code": str(runtime.code),
        "detail": str(runtime.detail),
        "version": str(runtime.version or ""),
    }


def ai_node_status(
    *, local_app_data=None, run_command=process_win.run_captured, resolver=None,
) -> dict:
    """시스템 Node를 보지 않고 Teacher Manager 전용 Node만 확인한다."""
    read_runtime = resolver or tool_runtime.resolve_node
    return _ai_node_result(read_runtime(
        local_app_data=local_app_data,
        run_command=run_command,
    ))


def ai_node_prepare(
    *, local_app_data=None, opener=None, run_command=process_win.run_captured, enabled=None,
) -> dict:
    """사용자가 AI 연결을 누른 뒤에만 전용 Node를 내려받는다."""
    allowed = ai_skill_install_enabled() if enabled is None else enabled is True
    if not allowed:
        return {
            "success": False,
            "code": "AI_SKILLS_DISABLED",
            "detail": "AI 공개판 동기화와 안전 확인이 끝나지 않아 연결 기능을 준비 중이에요.",
            "version": "",
        }
    kwargs = {
        "local_app_data": local_app_data,
        "run_command": run_command,
    }
    if opener is not None:
        kwargs["opener"] = opener
    result = managed_node.prepare_managed_node(**kwargs)
    return {
        "success": bool(result.success),
        "code": str(result.code),
        "detail": str(result.detail),
        "version": str(result.runtime.version or ""),
    }


def _ai_command_error(code: int, output: str) -> tuple[str, str]:
    lowered = str(output or "").casefold()
    if code == 407 or "407" in lowered or "proxy authentication required" in lowered:
        return "NETWORK_PROXY_AUTH_REQUIRED", "학교나 기관의 인터넷 중계 서버 로그인이 필요합니다."
    if "certificate" in lowered or "self signed" in lowered or "unable to verify" in lowered:
        return "NETWORK_TLS_INSPECTION_BLOCKED", "학교 보안 인증서 때문에 AI 연결 파일을 확인하지 못했습니다."
    if code == 124 or "timed out" in lowered or "timeout" in lowered:
        return "NETWORK_TIMEOUT", "AI 연결 시간이 너무 오래 걸렸습니다."
    if code == 127 or "access is denied" in lowered or "blocked" in lowered:
        return "NODE_SECURITY_BLOCKED", "보안 프로그램이 AI 연결 도구 실행을 막았습니다."
    return "AI_SKILLS_INSTALL_FAILED", _ai_safe_output(output) or "AI 연결 명령을 실행하지 못했습니다."


def _ai_safe_output(output: str) -> str:
    text = re.sub(
        r"(?i)(https?://)[^/@\s]+@",
        r"\1[로그인 숨김]@",
        str(output or ""),
    )
    safe = process_win.safe_log_text([], text)
    detail = safe.partition("결과: ")[2] or safe
    return detail.strip()[-200:]


def _ai_skills_install_locked(
    current, executable, agents, package, environment, run_command, approval, archive_opener,
) -> dict:
    """두 창이 함께 들어오지 못하는 상태에서 격리 설치와 적용을 끝낸다."""
    try:
        install_plan = ai_skill_install.prepare_install_plan(agents)
    except ai_skill_install.AiSkillInstallError as error:
        return {"success": False, "code": error.code, "detail": error.detail, "version": str(current.version or "")}
    if ai_skill_install.plan_is_already_approved(install_plan, approval):
        return {
            "success": True,
            "code": "AI_SKILLS_READY",
            "detail": "검토한 AI 연결이 이미 준비돼 있어 파일을 다시 받거나 바꾸지 않았습니다.",
            "version": str(current.version or ""),
        }
    try:
        stage_root = ai_skill_install.make_stage_root(current.root)
    except ai_skill_install.AiSkillInstallError as error:
        return {"success": False, "code": error.code, "detail": error.detail, "version": str(current.version or "")}
    cleanup_allowed = True

    if run_command is None:
        def run(arguments, **options):
            nonlocal cleanup_allowed
            supervised = ai_skill_install.run_supervised_command(
                arguments,
                timeout=options.get("timeout"),
                env=options.get("env"),
                cwd=options.get("cwd"),
            )
            cleanup_allowed = cleanup_allowed and supervised.tree_stopped
            return supervised.code, supervised.output
    else:
        run = run_command
    try:
        environment = ai_skill_install.isolated_environment(environment, stage_root)
        source_kwargs = {}
        if archive_opener is not None:
            source_kwargs["opener"] = archive_opener
        try:
            approved_source = ai_skill_install.prepare_approved_source(
                stage_root, approval, **source_kwargs,
            )
        except ai_skill_install.AiSkillInstallError as error:
            return {
                "success": False,
                "code": error.code,
                "detail": error.detail,
                "version": str(current.version or ""),
            }
        args = [
            "--yes", package, "add", str(approved_source), "-g", "-y", "--copy",
            "--skill", "teacher-task-manager",
        ]
        for agent in agents:
            args += ["-a", agent]
        code, output = run(
            [executable, "--yes", package, "--help"],
            timeout=60,
            env=environment,
        )
        if not cleanup_allowed:
            return {
                "success": False,
                "code": "AI_SKILLS_PROCESS_TREE_NOT_STOPPED",
                "detail": "AI 연결 작업이 모두 끝났는지 확인하지 못해 실제 사용자 파일은 바꾸지 않았습니다.",
                "version": str(current.version or ""),
            }
        if code != 0:
            error_code, detail = _ai_command_error(code, output)
            return {
                "success": False,
                "code": "AI_SKILLS_CLI_UNAPPROVED" if error_code == "AI_SKILLS_INSTALL_FAILED" else error_code,
                "detail": detail,
                "version": str(current.version or ""),
            }
        code, output = run([executable, *args], timeout=600, env=environment)
        if not cleanup_allowed:
            return {
                "success": False,
                "code": "AI_SKILLS_PROCESS_TREE_NOT_STOPPED",
                "detail": "AI 연결 작업이 모두 끝났는지 확인하지 못해 실제 사용자 파일은 바꾸지 않았습니다.",
                "version": str(current.version or ""),
            }
        tail = _ai_safe_output(output)
        if code == 0:
            try:
                ai_skill_install.write_staged_lock(stage_root, approval)
                ai_skill_install.apply_staged_install(stage_root, install_plan, approval)
            except ai_skill_install.AiSkillInstallError as error:
                return {"success": False, "code": error.code, "detail": error.detail, "version": str(current.version or "")}
            return {"success": True, "code": "AI_SKILLS_READY", "detail": tail, "version": str(current.version or "")}
        error_code, detail = _ai_command_error(code, output)
        return {"success": False, "code": error_code, "detail": detail, "version": str(current.version or "")}
    finally:
        if cleanup_allowed:
            ai_skill_install.cleanup_stage(stage_root)


def ai_skills_install(
    keys,
    *,
    runtime=None,
    run_command=None,
    enabled=None,
    permission_ack=False,
    approval=None,
    archive_opener=None,
) -> dict:
    """별도 동의와 검토한 정확한 공개판이 있을 때만 선택한 AI에 연결한다."""
    allowed = ai_skill_install_enabled() if enabled is None else enabled is True
    if not allowed:
        return {
            "success": False,
            "code": "AI_SKILLS_DISABLED",
            "detail": "AI 공개판 동기화와 안전 확인이 끝나지 않아 연결 기능을 준비 중이에요.",
            "version": "",
        }
    selected = {str(key) for key in (keys or [])}
    agents = [agent for tool in AI_TOOLS if tool["key"] in selected for agent in tool["agents"]]
    if not agents:
        return {"success": False, "code": "AI_SELECTION_REQUIRED", "detail": "연결할 AI를 하나 이상 선택해 주세요.", "version": ""}
    if permission_ack is not True:
        return {
            "success": False,
            "code": "AI_SKILLS_PERMISSION_REQUIRED",
            "detail": "AI 연결 권한 안내를 읽고 동의해야 연결할 수 있어요.",
            "version": "",
        }
    try:
        approved = (
            ai_skill_install.load_approved_skill(
                bundle_paths.bundle_root() / AI_SKILL_APPROVAL_FILENAME
            )
            if approval is None
            else ai_skill_install.validate_approved_skill(approval)
        )
    except ai_skill_install.AiSkillInstallError as error:
        return {
            "success": False,
            "code": error.code,
            "detail": error.detail,
            "version": "",
        }
    current = runtime if isinstance(runtime, tool_runtime.NodeRuntime) else tool_runtime.resolve_node()
    if not current.ready or current.npx_cmd is None:
        return {
            "success": False,
            "code": "AI_NODE_NOT_READY",
            "detail": current.detail or "AI 연결에 필요한 도구가 아직 준비되지 않았습니다.",
            "version": str(current.version or ""),
        }
    spec, manifest_error = tool_runtime._required_node_spec(None)
    if spec is None:
        return {
            "success": False,
            "code": manifest_error or "NODE_MANIFEST_MISSING",
            "detail": "AI 연결 도구의 승인 목록을 읽지 못했습니다.",
            "version": str(current.version or ""),
        }
    package = f"{spec.skills_cli_package}@{spec.skills_cli_version}"
    try:
        environment = tool_runtime.node_subprocess_env(current)
    except ValueError as error:
        return {"success": False, "code": "AI_NODE_NOT_READY", "detail": str(error), "version": str(current.version or "")}
    try:
        executable = str(ai_skill_install.resolve_managed_npx(current.root, current.npx_cmd))
    except ai_skill_install.AiSkillInstallError as error:
        return {"success": False, "code": error.code, "detail": error.detail, "version": str(current.version or "")}
    try:
        with ai_skill_install.exclusive_install_lock():
            return _ai_skills_install_locked(
                current, executable, agents, package, environment, run_command, approved,
                archive_opener,
            )
    except TimeoutError:
        return {
            "success": False,
            "code": "AI_SKILLS_INSTALL_BUSY",
            "detail": "다른 Teacher Manager 창에서 AI 연결을 진행 중입니다. 잠시 뒤 다시 눌러 주세요.",
            "version": str(current.version or ""),
        }


LIST_CALENDARS_FAILURE = "Calendar 목록을 가져오지 못했어요. 설정에서 다시 점검해 주세요."
LIST_TASKLISTS_FAILURE = "Tasks 목록을 가져오지 못했어요. 설정에서 다시 점검해 주세요."
CREATE_CALENDAR_FAILURE = "Calendar를 만들지 못했어요. 잠시 뒤 다시 시도해 주세요."
CREATE_TASKLIST_FAILURE = "Tasks 목록을 만들지 못했어요. 잠시 뒤 다시 시도해 주세요."
GOOGLE_LISTS_FINAL_FAILURE = "Calendar와 할 일 목록을 불러오지 못했어요."
GOOGLE_UNCHANGED = "Google 자료는 바꾸지 않았습니다."


def _run_gws_json(run_command, args: list[str], failure_message: str) -> dict:
    """명령 실패나 깨진 응답을 빈 목록으로 숨기지 않고 쉬운 문장으로 알린다."""
    code, output = run_command(args)
    if code != 0:
        raise RuntimeError(failure_message)
    try:
        parsed = process_win.parse_first_json(output)
    except ValueError:
        raise RuntimeError(failure_message) from None
    if not isinstance(parsed, dict):
        raise RuntimeError(failure_message)
    return parsed


def _run_gws_json_pages(run_command, args: list[str], failure_message: str) -> list[dict]:
    code, output = run_command(args)
    if code != 0:
        raise RuntimeError(failure_message)
    text = str(output or "").strip()
    if not text:
        raise RuntimeError(failure_message)
    decoder = _json.JSONDecoder()
    pages: list[dict] = []
    offset = 0
    while offset < len(text):
        starts = [
            index
            for index in (text.find("{", offset), text.find("[", offset))
            if index != -1
        ]
        if not starts:
            break
        offset = min(starts)
        try:
            page, end = decoder.raw_decode(text, offset)
        except (ValueError, TypeError) as error:
            raise RuntimeError(failure_message) from error
        if not isinstance(page, dict):
            raise RuntimeError(failure_message)
        pages.append(page)
        offset = end
        while offset < len(text) and text[offset].isspace():
            offset += 1
    if not pages:
        raise RuntimeError(failure_message)
    return pages


def _google_login_issue(*, personal: bool = False) -> recovery.UserActionRequired:
    message = (
        "현재 개인 Google 계정으로 로그인되어 있어요. 학교 @goedu.kr 계정으로 바꿔 주세요."
        if personal
        else "학교 @goedu.kr Google 계정으로 로그인해 주세요."
    )
    return recovery.UserActionRequired(
        recovery.UserIssue.needs_user(
            operation="google_login",
            title="학교 Google 로그인이 필요해요.",
            message=message,
            change_status=GOOGLE_UNCHANGED,
            actions=(recovery.IssueAction("google-login", "학교 계정으로 로그인"),),
            resume="google-login",
        )
    )


def _require_google_target_account(run_command, gws: str, expected_account: str = "") -> str:
    if not gws:
        raise recovery.RetryableOperationError(
            "GWS_NOT_READY", "Google 연결 도구를 확인하지 못했습니다."
        )
    auth = gws_auth_status(run_command, gws)
    if auth.get("login_state") == "error":
        raise recovery.RetryableOperationError(
            "GOOGLE_AUTH_STATUS", "Google 로그인 상태를 확인하지 못했습니다."
        )
    if not auth.get("logged_in"):
        raise _google_login_issue()
    account = str(auth.get("user") or "").strip()
    if not auth.get("account_allowed"):
        raise _google_login_issue(personal=True)
    expected = str(expected_account or "").strip()
    if expected and account.casefold() != expected.casefold():
        raise _google_login_issue()
    return account


def _google_targets_once(
    run_command, gws: str, kind: str, *, expected_account: str = ""
) -> list[dict]:
    _require_google_target_account(run_command, gws, expected_account)
    if kind == "calendar":
        args = [
            gws, "calendar", "calendarList", "list", "--params",
            '{"maxResults":250}', "--format", "json",
            "--page-all", "--page-limit", "1000",
        ]
        title_key = "summary"
    elif kind == "tasklist":
        args = [
            gws, "tasks", "tasklists", "list", "--format", "json",
            "--page-all", "--page-limit", "1000",
        ]
        title_key = "title"
    else:
        raise ValueError("kind must be calendar or tasklist")
    try:
        pages = _run_gws_json_pages(run_command, args, GOOGLE_LISTS_FINAL_FAILURE)
    except RuntimeError as error:
        raise recovery.RetryableOperationError(
            "GOOGLE_LISTS", "Google 목록 응답을 받지 못했습니다."
        ) from error
    rows = []
    seen_ids: set[str] = set()
    for reply in pages:
        for item in reply.get("items", []):
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("id") or "").strip()
            name = str(item.get(title_key) or "").strip()
            if not target_id or target_id in seen_ids:
                continue
            seen_ids.add(target_id)
            owned = kind == "tasklist" or str(item.get("accessRole", "")) == "owner"
            row = {"id": target_id, "name": name, "owned": owned}
            if kind == "calendar":
                color = str(item.get("backgroundColor") or "").strip().lower()
                row["primary"] = item.get("primary") is True
                row["color"] = color if re.fullmatch(r"#[0-9a-f]{6}", color) else ""
            else:
                updated = str(item.get("updated") or "").strip()
                row["updated"] = updated[:10] if re.match(r"^\d{4}-\d{2}-\d{2}T", updated) else ""
            rows.append(row)
    return rows


def list_google_targets_with_recovery(
    run_command, gws: str, kind: str, *, sleeper=time.sleep
) -> list[dict]:
    """Read Calendar or Tasks targets over three network cycles."""

    rows = recovery.run_operation(
        "google_lists",
        GOOGLE_LISTS_FINAL_FAILURE,
        lambda: _google_targets_once(run_command, gws, kind),
        delays=recovery.NETWORK_DELAYS,
        change_status=GOOGLE_UNCHANGED,
        app_version=version.APP_VERSION,
        sleeper=sleeper,
    )
    return [
        {key: value for key, value in row.items() if key != "owned"}
        for row in rows
    ]


def _target_selection_issue(kind: str, matches: list[dict]) -> recovery.UserActionRequired:
    noun = "Calendar" if kind == "calendar" else "할 일 목록"
    descriptors = []
    for row in matches:
        if kind == "calendar" and row.get("primary"):
            descriptors.append("기본")
        elif kind == "calendar" and row.get("color"):
            descriptors.append(f"색상 {row['color'].upper()}")
        elif kind == "tasklist" and row.get("updated"):
            descriptors.append(f"마지막 변경 {row['updated']}")
        else:
            descriptors.append("")
    fingerprints = [
        hashlib.sha256(str(row["id"]).encode("utf-8")).hexdigest().upper()
        for row in matches
    ]
    code_length = 6
    while code_length < 64 and len({value[:code_length] for value in fingerprints}) < len(fingerprints):
        code_length += 2
    codes = [value[:code_length] for value in fingerprints]
    actions = []
    for row, descriptor, code in zip(matches, descriptors, codes):
        detail = f" · {descriptor}" if descriptor else ""
        actions.extend((
            recovery.IssueAction(
                f"select-{kind}:{row['id']}",
                f"확인코드 {code}{detail} · {noun} 선택",
            ),
            recovery.IssueAction(
                f"inspect-{kind}:{row['id']}",
                f"확인코드 {code} · 이 {noun} 확인",
            ),
        ))
    return recovery.UserActionRequired(
        recovery.UserIssue.needs_user(
            operation=f"{kind}_selection",
            title=f"같은 이름의 {noun}이 여러 개 있어요.",
            message=f"연결할 {noun}을 직접 골라 주세요.",
            change_status=f"{noun}을 새로 만들지 않았습니다.",
            actions=tuple(actions),
        )
    )


def _exact_owned_targets(rows: list[dict], name: str) -> list[dict]:
    return [row for row in rows if row["owned"] and row["name"] == name]


def verify_google_target_candidate(
    run_command,
    gws: str,
    account: str,
    kind: str,
    candidate_id: str,
    name: str,
    *,
    sleeper=time.sleep,
) -> bool:
    expected_account = str(account or "").strip()

    def verify_once() -> bool:
        current_account = _require_google_target_account(run_command, gws)
        if not expected_account or current_account.casefold() != expected_account.casefold():
            return False
        rows = _google_targets_once(
            run_command, gws, kind, expected_account=expected_account
        )
        return any(
            row["owned"] and row["id"] == candidate_id and row["name"] == name
            for row in rows
        )

    return recovery.run_operation(
        f"{kind}_candidate_check",
        "Google 연결 후보를 다시 확인하지 못했어요.",
        verify_once,
        delays=recovery.NETWORK_DELAYS,
        change_status=GOOGLE_UNCHANGED,
        app_version=version.APP_VERSION,
        sleeper=sleeper,
    )


def _ensure_google_target_verified(
    run_command,
    gws: str,
    account: str,
    name: str,
    kind: str,
    *,
    sleeper=time.sleep,
) -> str:
    operation = "calendar_create" if kind == "calendar" else "tasklist_create"
    title = "Calendar를 만들지 못했어요." if kind == "calendar" else "할 일 목록을 만들지 못했어요."
    change_status = (
        "확인되지 않은 Calendar는 추가로 만들지 않았습니다."
        if kind == "calendar"
        else "확인되지 않은 할 일 목록은 추가로 만들지 않았습니다."
    )
    first_cycle = {"pending": True}

    def inspect() -> tuple[bool, str | None]:
        rows = _google_targets_once(
            run_command, gws, kind, expected_account=account
        )
        matches = _exact_owned_targets(rows, name)
        if len(matches) > 1:
            raise _target_selection_issue(kind, matches)
        if len(matches) == 1:
            return True, matches[0]["id"]
        return False, None

    def create_once() -> str:
        if first_cycle["pending"]:
            first_cycle["pending"] = False
            complete, found = inspect()
            if complete:
                return str(found)
        payload_key = "summary" if kind == "calendar" else "title"
        args = (
            [gws, "calendar", "calendars", "insert"]
            if kind == "calendar"
            else [gws, "tasks", "tasklists", "insert"]
        )
        try:
            reply = _run_gws_json(
                run_command,
                [
                    *args, "--json", _json.dumps({payload_key: name}, ensure_ascii=False),
                    "--format", "json",
                ],
                title,
            )
        except RuntimeError as error:
            safe_reason = (
                "Calendar 만들기 응답을 받지 못했습니다."
                if kind == "calendar"
                else "할 일 목록 만들기 응답을 받지 못했습니다."
            )
            raise recovery.RetryableOperationError(
                "GOOGLE_CREATE", safe_reason
            ) from error
        target_id = str(reply.get("id") or "").strip()
        if not target_id:
            raise recovery.RetryableOperationError(
                "GOOGLE_CREATE_RESULT", "만든 Google 자료의 확인번호를 받지 못했습니다."
            )
        return target_id

    return recovery.run_operation(
        operation,
        title,
        create_once,
        verify=inspect,
        delays=recovery.NETWORK_DELAYS,
        change_status=change_status,
        app_version=version.APP_VERSION,
        sleeper=sleeper,
    )


def ensure_calendar_verified(
    run_command, gws: str, account: str, name: str, *, sleeper=time.sleep
) -> str:
    return _ensure_google_target_verified(
        run_command, gws, account, name, "calendar", sleeper=sleeper
    )


def ensure_tasklist_verified(
    run_command, gws: str, account: str, name: str, *, sleeper=time.sleep
) -> str:
    return _ensure_google_target_verified(
        run_command, gws, account, name, "tasklist", sleeper=sleeper
    )


def list_calendars(run_command, gws: str) -> list[dict]:
    rows = _google_targets_once(run_command, gws, "calendar")
    return [{key: value for key, value in row.items() if key != "owned"} for row in rows]


def list_tasklists(run_command, gws: str) -> list[dict]:
    rows = _google_targets_once(run_command, gws, "tasklist")
    return [{key: value for key, value in row.items() if key != "owned"} for row in rows]


def ensure_calendar(run_command, gws: str, name: str) -> str:
    for item in list_calendars(run_command, gws):
        if item["name"] == name:
            return item["id"]  # 같은 이름이 있으면 새로 만들지 않는다 (표준 공간 규칙)
    reply = _run_gws_json(
        run_command,
        [gws, "calendar", "calendars", "insert", "--json", _json.dumps({"summary": name}, ensure_ascii=False), "--format", "json"],
        CREATE_CALENDAR_FAILURE,
    )
    return reply.get("id", "")


def ensure_tasklist(run_command, gws: str, name: str) -> str:
    for item in list_tasklists(run_command, gws):
        if item["name"] == name:
            return item["id"]
    reply = _run_gws_json(
        run_command,
        [gws, "tasks", "tasklists", "insert", "--json", _json.dumps({"title": name}, ensure_ascii=False), "--format", "json"],
        CREATE_TASKLIST_FAILURE,
    )
    return reply.get("id", "")


@dataclass
class StepResult:
    key: str
    label: str
    status: str  # done | skipped | failed
    detail: str = ""


_ATTENDANCE_REMOTE_COMMAND_TIMEOUT_SECONDS = 120.0
_ATTENDANCE_REMOTE_LOCK_TIMEOUT_SECONDS = 5.0
_ATTENDANCE_REMOTE_TIMEOUT_MESSAGE = (
    "Google 출결 작업이 너무 오래 걸려 중단했어요. "
    "인터넷 연결을 확인한 뒤 다시 눌러 주세요."
)
_ATTENDANCE_REMOTE_TREE_MESSAGE = (
    "Google 출결 작업을 안전하게 끝내지 못했어요. "
    "Teacher Manager를 껐다 켠 뒤 다시 눌러 주세요."
)
_ATTENDANCE_REMOTE_BUSY_MESSAGE = (
    "다른 창에서 출결 원격 작업을 진행하고 있어요. "
    "그 작업이 끝난 뒤 다시 눌러 주세요."
)


class AttendanceRemoteWorkBusyError(RuntimeError):
    """같은 출결 자료의 다른 원격 작업이 제한 시간 안에 끝나지 않음."""


def attendance_remote_command(
    args,
    *,
    cwd=None,
    timeout_seconds: float = _ATTENDANCE_REMOTE_COMMAND_TIMEOUT_SECONDS,
    environment=None,
    supervisor=None,
) -> tuple[int, str]:
    """Google 명령과 그 명령이 만든 자식 작업까지 제한 시간 안에서 실행한다."""

    supervisor = supervisor or process_supervision.run_supervised_command
    child_environment = (
        gws_env.gws_environ() if environment is None else dict(environment)
    )
    result = supervisor(
        list(args),
        timeout=max(0.01, float(timeout_seconds)),
        env=child_environment,
        cwd=cwd,
    )
    if not bool(getattr(result, "tree_stopped", False)):
        raise RuntimeError(_ATTENDANCE_REMOTE_TREE_MESSAGE)
    code = int(getattr(result, "code", 127))
    output = str(getattr(result, "output", "") or "")
    if code == 124:
        raise RuntimeError(_ATTENDANCE_REMOTE_TIMEOUT_MESSAGE)
    return code, output


def attendance_remote_runner(
    args,
    cwd,
    *,
    timeout_seconds: float = _ATTENDANCE_REMOTE_COMMAND_TIMEOUT_SECONDS,
    environment=None,
    supervisor=None,
) -> str:
    """출결 설치·Apps Script 확인기가 쓰는 폴더 인자형 실행기."""

    code, output = attendance_remote_command(
        args,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        environment=environment,
        supervisor=supervisor,
    )
    if code != 0:
        raise subprocess.CalledProcessError(
            code, list(args), output=output, stderr=output
        )
    return output


@dataclass
class AttendanceDeps:
    run_command: object = attendance_remote_command
    attendance_runner: object = attendance_remote_runner
    attendance_installer: object = install_attendance_automation.install_attendance_automation
    write_record: object = install_attendance_automation.write_install_record
    transition_deps_factory: object = attendance_workbook_transition.make_transition_deps
    new_school_year_starter: object = attendance_workbook_transition.start_new_school_year_workbook
    connection_record_handover: object = attendance_connection_handover.handover_sheet_records
    chat_connection_mover: object = central_chat.move_sheet_connection
    chat_connection_rollback: object = central_chat.rollback_sheet_connection
    chat_connection_complete: object = central_chat.complete_sheet_connection
    gws_resolver: object = tool_runtime.resolve_gws_executable


@dataclass(frozen=True)
class AttendanceConnectionCandidate:
    spreadsheet_id: str
    connection_code: str
    name: str
    modified_time: str
    attendance_rows: int


@dataclass(frozen=True)
class AttendanceConnectionCandidates:
    state: str
    expected_name: str = ""
    candidates: tuple[AttendanceConnectionCandidate, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class AttendanceConnectionSelection:
    state: str
    spreadsheet_url: str = ""
    detail: str = ""


@dataclass
class ApplyDeps(AttendanceDeps):
    start_helper: object = start_helper
    stop_helper: object = stop_helper
    enable_autostart: object = autostart_win.enable_autostart


_ATTENDANCE_THREAD_LOCKS: dict[str, threading.Lock] = {}
_ATTENDANCE_THREAD_LOCKS_GUARD = threading.Lock()


def _attendance_thread_lock(config_dir: Path, lock_name: str) -> threading.Lock:
    key = os.path.normcase(
        str((Path(config_dir).resolve() / str(lock_name)).resolve())
    )
    with _ATTENDANCE_THREAD_LOCKS_GUARD:
        return _ATTENDANCE_THREAD_LOCKS.setdefault(key, threading.Lock())


def _lock_attendance_file(file, deadline: float) -> None:
    file.seek(0, os.SEEK_END)
    if file.tell() == 0:
        file.write(b"\0")
        file.flush()
    if sys.platform == "win32":
        import msvcrt

        while True:
            try:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise AttendanceRemoteWorkBusyError(
                        _ATTENDANCE_REMOTE_BUSY_MESSAGE
                    )
                time.sleep(0.02)
    else:
        import fcntl

        while True:
            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise AttendanceRemoteWorkBusyError(
                        _ATTENDANCE_REMOTE_BUSY_MESSAGE
                    )
                time.sleep(0.02)


def _unlock_attendance_file(file) -> None:
    if sys.platform == "win32":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def attendance_remote_work_lock(
    config_dir: Path,
    *,
    timeout_seconds: float = _ATTENDANCE_REMOTE_LOCK_TIMEOUT_SECONDS,
):
    """같은 출결 자료의 긴 Google 작업은 하나씩, 기다림은 제한 시간까지만."""

    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    lock_name = ".attendance-setup.lock"
    local_lock = _attendance_thread_lock(config_dir, lock_name)
    timeout = max(0.01, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    if not local_lock.acquire(timeout=timeout):
        raise AttendanceRemoteWorkBusyError(_ATTENDANCE_REMOTE_BUSY_MESSAGE)
    try:
        lock_path = config_dir / lock_name
        with lock_path.open("a+b") as lock_file:
            _lock_attendance_file(lock_file, deadline)
            try:
                yield
            finally:
                _unlock_attendance_file(lock_file)
    finally:
        local_lock.release()


def _attendance_repair_account(
    config_dir: Path, deps: AttendanceDeps, gws: str
) -> tuple[str, str]:
    auth = gws_auth_status(deps.run_command, gws)
    if auth.get("login_state") == "error":
        return "auth-error", ""
    if not auth.get("logged_in"):
        return "login-required", ""
    if not auth.get("account_allowed"):
        return "account-required", ""
    current_user = str(auth.get("user", "") or "").strip()
    expected = str(_read_setup_status(config_dir).get("account", "") or "").strip()
    if expected and current_user and expected.casefold() != current_user.casefold():
        return "account-required", current_user
    return "ready", current_user


def _attendance_candidate_row_count(
    runner, workdir: Path, gws_executable: str, spreadsheet_id: str
) -> int:
    """후보의 개인정보를 돌려주지 않고 월별 출결 기록 줄 수만 센다."""

    total = 0
    for month in range(1, 13):
        reply = install_attendance_automation.run_json(
            runner,
            [
                gws_executable,
                "sheets",
                "spreadsheets",
                "values",
                "get",
                "--params",
                _json.dumps(
                    {
                        "spreadsheetId": spreadsheet_id,
                        "range": f"'{month}월'!A3:M",
                        "majorDimension": "ROWS",
                        "valueRenderOption": "UNFORMATTED_VALUE",
                        "dateTimeRenderOption": "SERIAL_NUMBER",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "--format",
                "json",
            ],
            Path(workdir),
        )
        values = reply.get("values", []) if isinstance(reply, dict) else None
        if not isinstance(values, list):
            raise ValueError("출석부 월별 기록을 읽지 못했어요.")
        for row in values:
            if (
                not isinstance(row, list)
                or len(row) > 13
                or any(isinstance(cell, (dict, list)) for cell in row)
            ):
                raise ValueError("출석부 월별 기록 모양을 확인하지 못했어요.")
            if any(cell not in (None, "") for cell in row):
                total += 1
    return total


def _verified_attendance_candidate(
    item: object, *, expected_name: str, school_year: str
) -> dict | None:
    if not isinstance(item, dict):
        return None
    properties = item.get("appProperties")
    spreadsheet_id = str(item.get("id", "") or "").strip()
    if not (
        spreadsheet_id
        and item.get("name") == expected_name
        and item.get("mimeType")
        == install_attendance_automation.SPREADSHEET_MIME
        and item.get("ownedByMe") is True
        and item.get("trashed") is not True
        and isinstance(properties, dict)
        and properties.get(
            attendance_workbook_identity.ATTENDANCE_ROLE_PROPERTY
        )
        == attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
        and properties.get(
            attendance_workbook_identity.ATTENDANCE_SCHOOL_YEAR_PROPERTY
        )
        == school_year
    ):
        return None
    return dict(item)


def _attendance_connection_identity(config_dir: Path):
    """옛 기록이 있으면 그 학급을, 기록이 없으면 현재 내 정보를 연결 기준으로 쓴다."""

    config_dir = Path(config_dir)
    record_path = paths.attendance_install_record_path(config_dir)
    if record_path.exists():
        snapshot = read_attendance_install_snapshot(record_path)
        record = snapshot.record
        school_year = str(record.get("school_year", "") or "").strip()
        grade = str(record.get("homeroom_grade", "") or "").strip()
        klass = str(record.get("homeroom_class", "") or "").strip()
        expected_name = (
            attendance_workbook_identity.attendance_workbook_name_from_record(
                record
            )
        )
    else:
        snapshot = None
        profile = _read_json_dict(paths.profile_path(config_dir)) or {}
        school = profile.get("school") or {}
        homeroom = profile.get("homeroom") or {}
        school_year = str(school.get("year", "") or "").strip()
        grade = str(homeroom.get("grade", "") or "").strip()
        klass = str(homeroom.get("class", "") or "").strip()
        expected_name = (
            attendance_workbook_identity.attendance_workbook_name(profile)
            if school_year
            else ""
        )
    if not school_year or not expected_name:
        raise AttendanceInstallRecordError(
            "출결 학년도·학년·반 확인값이 비어 있어요."
        )
    return snapshot, school_year, grade, klass, expected_name


def attendance_connection_candidates(
    config_dir: Path,
    deps: AttendanceDeps | None = None,
    *,
    include_row_counts: bool = True,
) -> AttendanceConnectionCandidates:
    """명시적인 연결 화면에서만 정식 후보를 읽어 개인정보 없는 요약을 만든다."""

    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    record_path = paths.attendance_install_record_path(config_dir)
    try:
        _snapshot, school_year, _grade, _klass, expected_name = (
            _attendance_connection_identity(config_dir)
        )
        gws = str(deps.gws_resolver())
        state, account = _attendance_repair_account(config_dir, deps, gws)
        if state != "ready":
            return AttendanceConnectionCandidates(
                state=state,
                expected_name=expected_name,
                detail="설정에서 같은 학교 Google 계정으로 로그인해 주세요.",
            )
        del account  # 계정 일치는 위에서 확인했고 후보는 ownedByMe 표식까지 다시 본다.
        found = install_attendance_automation.find_canonical_attendance_sheets(
            deps.attendance_runner,
            config_dir,
            False,
            school_year,
            gws,
        )
        candidates: list[AttendanceConnectionCandidate] = []
        for item in found:
            checked = _verified_attendance_candidate(
                item, expected_name=expected_name, school_year=school_year
            )
            if checked is None:
                continue
            spreadsheet_id = str(checked["id"])
            attendance_rows = 0
            if include_row_counts:
                try:
                    attendance_rows = _attendance_candidate_row_count(
                        deps.attendance_runner,
                        config_dir,
                        gws,
                        spreadsheet_id,
                    )
                except Exception:  # noqa: BLE001 - 한 후보의 줄 수는 선택 보조 정보일 뿐이다.
                    attendance_rows = 0
            candidates.append(
                AttendanceConnectionCandidate(
                    spreadsheet_id=spreadsheet_id,
                    connection_code=(
                        attendance_workbook_identity.attendance_connection_code(
                            spreadsheet_id
                        )
                    ),
                    name=expected_name,
                    modified_time=str(checked.get("modifiedTime", "") or ""),
                    attendance_rows=attendance_rows,
                )
            )
        candidates.sort(
            key=lambda value: (value.modified_time, value.spreadsheet_id),
            reverse=True,
        )
        if not candidates:
            return AttendanceConnectionCandidates(
                state="not-found",
                expected_name=expected_name,
                detail=(
                    "정식 표식이 있는 기존 출석부를 찾지 못했어요. "
                    "새 파일은 만들지 않았습니다."
                ),
            )
        return AttendanceConnectionCandidates(
            state="ready" if len(candidates) == 1 else "choose",
            expected_name=expected_name,
            candidates=tuple(candidates),
        )
    except Exception:  # noqa: BLE001 - 외부 원문과 Google 번호를 화면 문장에 섞지 않는다.
        return AttendanceConnectionCandidates(
            state="failed",
            detail=(
                "기존 출석부 목록을 끝까지 확인하지 못했어요. "
                "현재 연결과 Google 파일은 그대로입니다."
            ),
        )


def select_attendance_connection_by_code(
    config_dir: Path,
    connection_code: str,
    deps: AttendanceDeps | None = None,
) -> AttendanceConnectionSelection:
    """사람이 붙여 넣은 확인번호와 정식 후보 하나를 대조한 뒤 전체 ID로 연결한다.

    ``TM-…`` 확인번호는 Sheet를 여는 실제 키가 아니다. 같은 계정·학년도·정식
    이름·Drive 표식을 모두 통과한 후보 중 정확히 하나와 일치할 때만, 아래의 기존
    선택 함수에 잘리지 않은 ``spreadsheet_id`` 전체를 넘겨 다시 확인한다.
    """

    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    checked_code = str(connection_code or "").strip().upper()
    if re.fullmatch(r"TM-[0-9A-F]{6}-[0-9A-F]{6}", checked_code) is None:
        return AttendanceConnectionSelection(
            state="failed",
            detail="연결 확인번호는 TM-XXXXXX-XXXXXX 모양으로 붙여 넣어 주세요.",
        )
    listed = attendance_connection_candidates(
        config_dir, deps=deps, include_row_counts=False
    )
    if listed.state not in {"ready", "choose"}:
        return AttendanceConnectionSelection(
            state=listed.state if listed.state in {
                "login-required", "account-required", "auth-error"
            } else "failed",
            detail=(
                listed.detail
                or "확인번호와 대조할 정식 출석부를 찾지 못했어요. 현재 연결은 그대로입니다."
            ),
        )
    matched = [
        candidate
        for candidate in listed.candidates
        if candidate.connection_code == checked_code
    ]
    if len(matched) != 1:
        detail = (
            "같은 연결 확인번호의 정식 출석부가 둘 이상이라 연결하지 않았어요. "
            "파일을 직접 확인해 주세요."
            if len(matched) > 1
            else (
                "그 연결 확인번호와 같은 정식 출석부를 찾지 못했어요. "
                "시트의 연결 상태 확인 창에 나온 번호를 다시 확인해 주세요."
            )
        )
        return AttendanceConnectionSelection(
            state="choose" if len(matched) > 1 else "not-found",
            detail=detail,
        )
    return select_attendance_connection(
        config_dir, matched[0].spreadsheet_id, deps=deps
    )


def select_attendance_connection(
    config_dir: Path,
    spreadsheet_id: str,
    deps: AttendanceDeps | None = None,
) -> AttendanceConnectionSelection:
    """고른 정식 Sheet로 업무 자료와 Chat 연결을 인계한 뒤 로컬 연결을 교체한다."""

    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    candidate_id = str(spreadsheet_id or "").strip()
    record_path = paths.attendance_install_record_path(config_dir)
    google_handover_started = False
    chat_rollback_failed = False
    try:
        with attendance_remote_work_lock(config_dir):
            (
                snapshot,
                school_year,
                homeroom_grade,
                homeroom_class,
                expected_name,
            ) = _attendance_connection_identity(config_dir)
            if not candidate_id:
                raise AttendanceInstallRecordError(
                    "고를 정식 출석부 확인값이 비어 있어요."
                )
            gws = str(deps.gws_resolver())
            state, selected_account = _attendance_repair_account(config_dir, deps, gws)
            if state != "ready":
                return AttendanceConnectionSelection(
                    state=state,
                    detail="설정에서 같은 학교 Google 계정으로 로그인해 주세요.",
                )
            fresh = install_attendance_automation.find_canonical_attendance_sheets(
                deps.attendance_runner,
                config_dir,
                False,
                school_year,
                gws,
            )
            chosen = next(
                (
                    checked
                    for item in fresh
                    if (
                        checked := _verified_attendance_candidate(
                            item,
                            expected_name=expected_name,
                            school_year=school_year,
                        )
                    )
                    is not None
                    and checked.get("id") == candidate_id
                ),
                None,
            )
            if chosen is None:
                return AttendanceConnectionSelection(
                    state="failed",
                    detail=(
                        "고른 출석부를 정식 파일로 다시 확인하지 못했어요. "
                        "현재 연결은 그대로입니다."
                    ),
                )
            recovered = install_attendance_automation.reuse_existing_attendance_sheet(
                deps.attendance_runner,
                config_dir,
                chosen,
                expected_name,
                gws,
            )
            wanted = dict(snapshot.record) if snapshot is not None else {}
            for key in CONNECTION_FIELDS:
                wanted[key] = str(getattr(recovered, key))
            wanted["school_year"] = school_year
            wanted["homeroom_grade"] = homeroom_grade
            wanted["homeroom_class"] = homeroom_class
            wanted["workbook_name"] = expected_name
            wanted["workbook_role"] = (
                attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
            )
            wanted[SETUP_ACCOUNT_FIELD] = selected_account.strip().lower()
            wanted.pop(SCRIPT_ATTESTATION_FIELD, None)
            wanted.pop(SCRIPT_UPDATE_REQUIRED_FIELD, None)
            if recovered.script_update_required:
                wanted[SCRIPT_UPDATE_REQUIRED_FIELD] = True
            bundle_sha256 = str(recovered.script_bundle_sha256 or "").strip()
            if bundle_sha256:
                wanted[SCRIPT_ATTESTATION_FIELD] = build_script_attestation(
                    wanted, bundle_sha256
                )
            # 활성 기록을 바꾸기 전에 후보 ID와 열기 주소까지 완성된 새 기록을
            # 먼저 검증한다. 교체한 뒤 처음 오류를 발견하면 화면은 실패인데 실제
            # 연결은 이미 바뀐 반쪽 상태가 된다.
            verified_wanted = validate_verified_canonical_record(wanted)
            if verified_wanted["spreadsheet_id"] != candidate_id:
                raise AttendanceInstallRecordError(
                    "고른 정식 출석부 번호와 복구할 연결번호가 달라요."
                )
            if snapshot is not None and snapshot.record == verified_wanted:
                return AttendanceConnectionSelection(
                    state="selected",
                    spreadsheet_url=str(verified_wanted["spreadsheet_url"]),
                )
            chat_move_result = None
            source_id = (
                str(snapshot.record.get("spreadsheet_id", "") or "").strip()
                if snapshot is not None
                else ""
            )
            if source_id and source_id != candidate_id:
                google_handover_started = True
                handed_over = deps.connection_record_handover(
                    config_dir=config_dir,
                    source_spreadsheet_id=source_id,
                    target_spreadsheet_id=candidate_id,
                    runner=deps.attendance_runner,
                    gws_executable=gws,
                )
                if getattr(handed_over, "state", "") != "complete":
                    raise AttendanceInstallRecordError(
                        "기존 출석부 자료를 새 연결로 옮긴 결과를 확인하지 못했어요."
                    )
                chat_move_result = deps.chat_connection_mover(
                    config_dir,
                    candidate_id,
                    deps.run_command,
                    gws_executable=gws,
                    attendance_record=dict(snapshot.record),
                )
                if (
                    not isinstance(chat_move_result, dict)
                    or chat_move_result.get("outcome")
                    not in {"moved", "not_registered", "same"}
                ):
                    raise AttendanceInstallRecordError(
                        "Google Chat 연결을 새 출석부로 옮긴 결과를 확인하지 못했어요."
                    )
            if snapshot is None:
                # 후보를 확인하는 동안 다른 창이 연결 기록을 만들었다면 그 결과를
                # 덮지 않는다. 아직 없을 때만 검증된 로컬 연결 기록 하나를 만든다.
                if record_path.exists():
                    raise AttendanceInstallRecordError(
                        "다른 창에서 출석부 연결 기록을 먼저 만들었어요."
                    )
                written = write_attendance_install_record(record_path, wanted)
                verified = validate_verified_canonical_record(written)
            else:
                try:
                    backup_path = config_dir / (
                        "attendance-install.before-connection-repair."
                        + snapshot.sha256[:12]
                        + ".json"
                    )
                    ensure_create_only_install_backup(backup_path, snapshot)
                    final = replace_attendance_install_record(
                        record_path, wanted, snapshot
                    )
                except Exception:
                    if (
                        isinstance(chat_move_result, dict)
                        and chat_move_result.get("moved") is True
                    ):
                        try:
                            rolled_back = deps.chat_connection_rollback(
                                config_dir,
                                chat_move_result,
                                deps.run_command,
                                gws_executable=gws,
                            )
                            if rolled_back is not True:
                                chat_rollback_failed = True
                        except Exception:  # noqa: BLE001 - 복구 불명확 상태를 별도로 알린다.
                            chat_rollback_failed = True
                    raise
                # replace 함수가 교체 뒤 바이트를 다시 읽어 돌려주므로 같은 파일을 한 번
                # 더 열지 않는다. 두 번째 읽기만 실패해 선택 실패로 보이는 일을 막는다.
                verified = validate_verified_canonical_record(final.record)
                if final.record != verified:
                    raise AttendanceInstallRecordError(
                        "교체한 정식 출석부 기록이 다시 읽은 값과 달라요."
                    )
                if (
                    isinstance(chat_move_result, dict)
                    and chat_move_result.get("moved") is True
                ):
                    try:
                        deps.chat_connection_complete(config_dir, chat_move_result)
                    except Exception:
                        pass  # 보호 기록을 못 지워도 이미 확인한 연결 교체는 되돌리지 않는다.
            if verified["spreadsheet_id"] != candidate_id:
                raise AttendanceInstallRecordError("교체한 정식 출석부 번호가 달라요.")
            return AttendanceConnectionSelection(
                state="selected",
                spreadsheet_url=str(verified["spreadsheet_url"]),
            )
    except Exception:  # noqa: BLE001 - 내부값과 외부 원문은 화면에 섞지 않는다.
        if chat_rollback_failed:
            detail = (
                "컴퓨터의 출석부 연결은 바꾸지 않았지만 Google Chat 연결을 "
                "원래대로 되돌렸는지 확인하지 못했어요. 다시 누르지 말고 설정의 "
                "출결 문제 해결에서 연결 상태를 확인해 주세요."
            )
        elif google_handover_started:
            detail = (
                "컴퓨터의 출석부 연결은 바꾸지 않았어요. 대상 출석부에 이미 확인된 "
                "자료는 그대로 두었으며, 다시 고르면 중복 없이 이어서 확인합니다."
            )
        else:
            detail = (
                "고른 출석부 연결을 끝까지 확인하지 못했어요. "
                "현재 연결과 Google 파일은 그대로입니다."
            )
        return AttendanceConnectionSelection(
            state="failed",
            detail=detail,
        )


@contextmanager
def attendance_setup_lock(
    config_dir: Path,
    *,
    timeout_seconds: float = _ATTENDANCE_REMOTE_LOCK_TIMEOUT_SECONDS,
):
    """옛 호출 이름도 출결 원격 작업 공용 잠금과 같은 문을 사용한다."""

    with attendance_remote_work_lock(
        config_dir, timeout_seconds=timeout_seconds
    ):
        yield


def ensure_attendance(config_dir: Path, deps: AttendanceDeps | None = None) -> AttendanceStatus:
    """저장 버튼 한 번으로 출결 자료를 처음 한 번만 준비한다. 반복 호출해도 중복 생성하지 않는다."""
    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    # 계정 확인은 폴더·잠금 파일을 만들기 전에 한다. 개인 Gmail에서 눌렀다는
    # 이유만으로 사용자 폴더에 흔적을 남기지 않는다. 실제 준비 직전에는 잠금 안에서
    # 다시 읽어 다른 창이 먼저 끝낸 결과도 그대로 사용한다.
    gws = str(deps.gws_resolver())
    preflight = read_attendance_status(
        config_dir, deps.run_command, gws_executable=gws
    )
    if preflight.state in (
        "gws-required", "login-required", "account-required", "auth-error",
        "profile-required", "ready", "script-check-required", "script-update-required",
        "connection-repair-required", "ai-action-required",
    ):
        return preflight
    with attendance_setup_lock(config_dir):
        return _ensure_attendance_once(config_dir, deps, gws)


def _ensure_attendance_once(
    config_dir: Path,
    deps: AttendanceDeps,
    gws_executable: str | None = None,
) -> AttendanceStatus:
    gws = str(gws_executable or deps.gws_resolver())
    status = read_attendance_status(
        config_dir, deps.run_command, gws_executable=gws
    )
    if status.state in (
        "gws-required", "login-required", "account-required", "auth-error",
        "profile-required", "ready", "script-check-required", "script-update-required",
        "connection-repair-required", "ai-action-required",
    ):
        return status
    if paths.attendance_install_record_path(config_dir).exists():
        return status  # 깨진 설치 기록 — 자동으로 새 자료를 만들지 않는다

    try:
        profile_json = parse_settings.parse_config_dir(config_dir, require_links=False)
    except ValueError:
        return AttendanceStatus(
            state="profile-required", current_user=status.current_user, detail=ATTENDANCE_PROFILE_MESSAGE
        )

    setup_status = _read_setup_status(config_dir)
    progress = setup_status.get("progress")
    progress = dict(progress) if isinstance(progress, dict) and progress else None
    account = str(setup_status.get("account", "") or "")
    current_user = status.current_user
    if progress and account and current_user and account != current_user:
        return AttendanceStatus(
            state="failed", account=account, current_user=current_user,
            failed_service="setup", detail=ATTENDANCE_ACCOUNT_MESSAGE,
            school_year=_profile_school_year(config_dir),
        )

    saved_progress = dict(progress or {})

    def record_progress(ids: dict) -> None:
        saved_progress.clear()
        saved_progress.update(ids)
        _write_setup_status(config_dir, {
            "state": "installing", "account": current_user, "progress": dict(ids),
        })

    # 미제출 할 일은 별도 목록을 만들지 않고 조종례 목록으로 통합한다.
    try:
        profile_data = _json.loads(Path(profile_json).read_text(encoding="utf-8"))
    except ValueError:
        profile_data = {}
    homeroom_tasks_id = str((profile_data.get("calendars") or {}).get("homeroom_tasks_id", "") or "")
    try:
        result = deps.attendance_installer(
            profile_json, runner=deps.attendance_runner, resume=progress, progress=record_progress,
            attendance_task_list_id=homeroom_tasks_id,
            attendance_task_list_title="조종례시 담임학급 안내사항",
            gemini_api_key=install_attendance_automation.local_gemini_api_key(config_dir),
            gws_executable=gws,
        )
    except Exception as error:  # noqa: BLE001 - 설치 실패는 쉬운 문장으로 바꿔 화면에 보여준다
        failed_service, message = friendly_attendance_error(error)
        # 쓰던 시트를 찾았을 때의 안내는 시트 주소와 비어 있는 값 이름까지 담기므로
        # 200자에서 자르면 정작 필요한 뒷부분이 잘려 나간다.
        detail = message[:ATTENDANCE_DETAIL_LIMIT]
        if isinstance(
            error,
            install_attendance_automation.AttendanceConnectionChoiceRequired,
        ):
            candidates = list(error.candidates)
            workbook_name = str(
                (candidates[0] if len(candidates) == 1 else {}).get("name", "")
                or install_attendance_automation.ATTENDANCE_LEGACY_SHEET_NAME
            )
            profile = _read_json_dict(Path(profile_json)) or {}
            detail = (
                "현재 출석부 연결을 고르기 전에는 새 출석부를 만들지 않습니다. "
                + detail
            )[:ATTENDANCE_DETAIL_LIMIT]
            saved = {
                "state": "connection-choice-required",
                "account": current_user,
                "detail": detail,
                "workbook_name": workbook_name,
            }
            _write_setup_status(config_dir, saved)
            return AttendanceStatus(
                state="connection-repair-required",
                account=current_user,
                current_user=current_user,
                detail=detail,
                school_year=_profile_school_year(config_dir),
                workbook_name=workbook_name,
                canonical_workbook_name=(
                    attendance_workbook_identity.attendance_workbook_name(profile)
                ),
            )
        _write_setup_status(config_dir, {
            "state": "failed", "account": current_user, "failed_service": failed_service,
            "detail": detail, "progress": saved_progress,
        })
        return AttendanceStatus(
            state="failed", account=current_user, current_user=current_user,
            failed_service=failed_service, detail=detail,
        )
    if isinstance(result, install_attendance_automation.AttendanceInstallResult):
        result = replace(result, setup_account=current_user.strip().lower())
    deps.write_record(profile_json, result)
    script_update_required = bool(
        getattr(result, "script_update_required", False)
    )
    result_bundle_sha256 = str(
        getattr(result, "script_bundle_sha256", "") or ""
    )
    if script_update_required:
        result_state = "script-update-required"
        result_detail = ATTENDANCE_SCRIPT_UPDATE_REQUIRED_MESSAGE
    elif result_bundle_sha256 == current_attendance_script_bundle_sha256():
        result_state = "ready"
        result_detail = ""
    else:
        result_state = "script-check-required"
        result_detail = ATTENDANCE_SCRIPT_CHECK_REQUIRED_MESSAGE
    _write_setup_status(
        config_dir, {"state": result_state, "account": current_user}
    )
    return AttendanceStatus(
        state=result_state, account=current_user, current_user=current_user,
        spreadsheet_url=result.spreadsheet_url, template_doc_url=result.template_doc_url,
        connection_code=(
            attendance_workbook_identity.attendance_connection_code(
                getattr(result, "spreadsheet_id", "")
            )
        ),
        detail=result_detail,
        created=True,
        school_year=_profile_school_year(config_dir),
        workbook_name=getattr(result, "workbook_name", "") or "",
        year_mismatch=False,
    )


def start_new_attendance(config_dir: Path, deps: AttendanceDeps | None = None) -> AttendanceStatus:
    """학년도가 달라졌을 때 후보를 완성한 뒤 현재 Sheet 번호를 마지막에 바꾼다."""

    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    gws = str(deps.gws_resolver())
    usable_states = {"ready", "script-check-required", "script-update-required"}
    preflight = read_attendance_status(
        config_dir, deps.run_command, gws_executable=gws
    )
    if preflight.state not in usable_states:
        return preflight
    if not preflight.year_mismatch:
        return preflight

    with attendance_setup_lock(config_dir):
        current = read_attendance_status(
            config_dir, deps.run_command, gws_executable=gws
        )
        if current.state not in usable_states:
            return current
        if not current.year_mismatch:
            return current

        transition_deps = deps.transition_deps_factory(
            runner=deps.attendance_runner,
            gws_executable=gws,
            account=current.current_user,
        )
        result = deps.new_school_year_starter(
            config_dir, deps=transition_deps
        )
        if str(getattr(result, "state", "") or "") != "complete":
            detail = (
                str(result.detail or "")
                if isinstance(result, attendance_workbook_transition.TransitionResult)
                else attendance_workbook_transition.NEW_SCHOOL_YEAR_FAILURE
            )
            return replace(
                current,
                state="failed",
                detail=detail or "새 학년도 출석부를 시작하지 못했어요.",
                failed_service="sheet",
                created=False,
            )

        final = read_attendance_status(
            config_dir, deps.run_command, gws_executable=gws
        )
        expected_url = str(getattr(result, "spreadsheet_url", "") or "")
        if (
            final.state not in usable_states
            or final.year_mismatch
            or (expected_url and final.spreadsheet_url != expected_url)
        ):
            return replace(
                final,
                state="failed",
                detail="새 학년도 출석부로 연결된 결과를 다시 확인하지 못했어요.",
                failed_service="sheet",
                created=False,
            )
        return replace(final, created=True)


def apply_all(config_dir: Path, profile_values: dict, grid: list, bridge_updates: dict,
              deps: ApplyDeps | None = None) -> list[StepResult]:
    """[모두 저장] — 출결을 건드리지 않고 화면의 설정만 저장한다."""
    deps = deps or ApplyDeps()
    config_dir = Path(config_dir)
    results: list[StepResult] = []

    # 사용자 파일을 쓰기 전에 계정을 먼저 확인한다. 잘못된 계정에서 멈춘 뒤
    # profile·시간표 일부만 저장되는 반쪽 적용을 만들지 않는다.
    gws = str(deps.gws_resolver())
    require_goedu_gws_session(deps.run_command, gws)

    write_profile_values(config_dir, profile_values)
    results.append(StepResult("csv", "선생님·학교 저장", "done", "이 컴퓨터에 저장함"))

    write_timetable_grid(config_dir, grid)
    results.append(StepResult("xlsx", "시간표 저장", "done", "이 컴퓨터에 저장함"))

    parse_ok, parse_detail = run_parser(config_dir)
    results.append(StepResult("parse", "설정 파서", "done" if parse_ok else "failed", parse_detail))

    bridge = {key: value for key, value in bridge_updates.items() if key != "autostart"}
    save_bridge_settings(config_dir, bridge)
    if bridge_updates.get("autostart"):
        deps.enable_autostart()
    results.append(StepResult("bridge", "도우미 설정 저장", "done", "이 컴퓨터에 저장함"))

    if deps.stop_helper() and deps.start_helper():
        results.append(StepResult("helper", "도우미 재시작", "done", "새 설정으로 실행 중"))
    else:
        results.append(StepResult("helper", "도우미 재시작", "failed", "Teacher Manager를 다시 시작해 주세요"))
    return results


def save_wizard_inputs(config_dir: Path, profile_values: dict, grid: list, bridge_updates: dict,
                       deps: ApplyDeps | None = None) -> tuple[bool, str]:
    """메신저 탭 [다음] 순간의 저장 — apply_all 앞부분과 같고 멱등이다."""
    deps = deps or ApplyDeps()
    config_dir = Path(config_dir)
    gws = str(deps.gws_resolver())
    require_goedu_gws_session(deps.run_command, gws)
    write_profile_values(config_dir, profile_values)
    write_timetable_grid(config_dir, grid)
    bridge = {key: value for key, value in bridge_updates.items() if key != "autostart"}
    save_bridge_settings(config_dir, bridge)
    parse_ok, parse_detail = run_parser(config_dir)
    if not parse_ok:
        return False, parse_detail
    return True, ""
