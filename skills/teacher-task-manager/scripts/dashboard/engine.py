# skills/teacher-task-manager/scripts/dashboard/engine.py
from __future__ import annotations

import csv
import errno
import hashlib
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
import attendance_canonical_rebuild
import attendance_script_update
import attendance_workbook_identity
import attendance_workbook_transition
import parse_settings
from attendance_install_record import (
    AttendanceInstallRecordError,
    attendance_script_is_attested,
    load_attendance_install_record,
    read_attendance_install_snapshot,
    replace_attendance_install_record,
)
from brity_bridge import (
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
)
from brity_bridge.doctor import CheckResult, DoctorDeps, _default_run_command, run_doctor_checks
from brity_bridge.gemini_analyze import check_gemini_key
from brity_bridge.hotkey import MODIFIER_ORDER, parse_hotkey
from brity_bridge.settings import ALLOWED_GEMINI_MODELS, load_settings, save_settings
from dashboard import external_url

HELPER_WINDOW_CLASS = "BrityBridgeTrayWindow"
_WM_CLOSE = 0x0010
_PROBE_HOTKEY_ID = 0xB111
DEFAULT_ATTACHMENT_FOLDER = r"C:\BrityWorks\BrityMessenger\download"


@dataclass
class HomeCheckDeps:
    doctor_deps: DoctorDeps = field(default_factory=DoctorDeps)
    document_probe: object = None


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

    attendance = read_attendance_status(config_dir, doctor_deps.run_command)
    attendance_ok = _ATTENDANCE_STATE_TO_OK.get(attendance.state)
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
    FIRST_TIME_SETUP_DONE 줄을 적는다(Code.gs). 마법사 출결 탭은 이 값으로
    완료를 자동 확인한다 — 네트워크·권한 실패는 오류가 아니라 '아직'이다.
    """
    from dashboard import central_chat

    record_path = paths.attendance_install_record_path(Path(config_dir))
    if not record_path.exists():
        return {"done": False, "value": ""}
    try:
        record = load_attendance_install_record(record_path)
    except AttendanceInstallRecordError:
        return {"done": False, "value": ""}
    spreadsheet_id = str(record.get("spreadsheet_id", "") or "")
    if not spreadsheet_id:
        return {"done": False, "value": ""}
    try:
        rows = central_chat._read_settings_rows(spreadsheet_id, run_command, gws_executable)
    except Exception:  # noqa: BLE001 - 네트워크·권한 실패는 '아직'으로만 보인다
        return {"done": False, "value": ""}
    value = ""
    for row in rows or []:
        if isinstance(row, list) and row and str(row[0]).strip() == "FIRST_TIME_SETUP_DONE":
            value = str(row[1]).strip() if len(row) > 1 else ""
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
) -> dict:
    """메신저 화면의 네 가지 선택을 한 번에 저장하고 실패하면 모두 되돌린다."""
    if not isinstance(updates, dict):
        raise ValueError("메신저 설정 모양이 올바르지 않아요")
    allowed = {
        "gemini_api_key", "gemini_model", "hotkey", "autostart", "brity_download_dir"
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
    old_autostart = checker() if "autostart" in updates else None
    new_autostart = updates.get("autostart", old_autostart)
    autostart_changed = old_autostart is not None and new_autostart != old_autostart

    save_settings(settings_path, candidate)
    try:
        if autostart_changed:
            (enable if new_autostart else disable)()
    except Exception:
        save_settings(settings_path, previous)
        try:
            (enable if old_autostart else disable)()
        except Exception:
            pass
        raise

    restarter = restart or restart_helper
    if restarter():
        # 저장이 확정된 뒤에만 시트로 보낸다. 되돌릴 저장이 남아 있으면 보내지 않는다.
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
            "restarted": True,
            "reason": "",
            "sheet_push": sheet_push,
        }

    save_settings(settings_path, previous)
    if autostart_changed:
        try:
            (enable if old_autostart else disable)()
        except Exception:
            pass
    restored = bool(restarter())
    return {
        "saved": False,
        "hotkey": previous.hotkey,
        "restarted": restored,
        "reason": "새 설정을 켜지 못해 이전 설정으로 되돌렸어요.",
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


def reset_attendance_record(config_dir: Path) -> bool:
    removed = False
    for path in (
        paths.attendance_install_record_path(Path(config_dir)),
        paths.attendance_setup_status_path(Path(config_dir)),
    ):
        if path.exists():
            path.unlink()
            removed = True
    return removed


@dataclass(frozen=True)
class AttendanceStatus:
    state: str             # ready | script-check-required | script-update-required | consolidation-required | not-ready | gws-required | login-required | account-required | auth-error | profile-required | failed
    account: str = ""      # 처음 준비할 때 사용한 계정
    current_user: str = ""  # 지금 로그인한 계정
    spreadsheet_url: str = ""
    template_doc_url: str = ""  # 결석 신고서 서식 — 화면 Docs 칸의 [서식 열기]가 쓴다
    detail: str = ""
    failed_service: str = ""  # sheet | docs | tasks | setup | 빈 문자열
    created: bool = False  # 이번 호출에서 새로 만들었는지
    school_year: str = ""  # 기록에 새긴 학년도 — 도장이 없으면 지금 학년도로 본다
    workbook_name: str = ""  # 화면에 보여줄 출석부 이름 — 없으면 옛 고정 이름
    year_mismatch: bool = False  # 프로필 학년도와 기록 학년도가 다르면 새 출석부 단추가 풀린다
    consolidation_required: bool = False  # 옛 두 갈래 기록이면 한 번의 정리 단추를 보인다
    canonical_workbook_name: str = ""  # 정리·새 학년도 확인창에 보여줄 새 정식 이름
    transition_action_token: str = ""  # 화면에 Google ID 대신 보내는 검증된 재개 지문


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
ATTENDANCE_DETAIL_LIMIT = 800  # 화면에 보여줄 실패 안내 길이 상한


def current_attendance_script_bundle_sha256() -> str:
    """지금 실행 중인 프로그램에 묶인 정식 출결 기능의 지문."""

    return attendance_script_update.target_bundle_sha256(
        bundle_paths.bundle_root() / "assets"
    )


def _existing_attendance_guidance(error) -> str:
    if isinstance(
        error, install_attendance_automation.LegacyAttendanceConsolidationRequired
    ):
        names = []
        for candidate in error.candidates:
            name = " ".join(str(candidate.get("name", "") or "").split())[:120]
            if name and name not in names:
                names.append(name)
        if len(error.candidates) > 1:
            shown = ", ".join(names) if names else "이름을 읽지 못한 출석부"
            return (
                "예전에 쓰던 출석부가 여러 개라 자동으로 고르지 않았어요. "
                f"파일 이름: {shown}. Google Drive에서 파일 이름으로 열어 확인한 뒤 "
                "출결 시트 하나로 정리를 다시 시작해 주세요."
            )
        shown = names[0] if names else "예전에 쓰던 출석부"
        return (
            f"예전에 쓰던 출석부를 찾았어요: {shown}. 기존 자료는 바꾸지 않았습니다. "
            "화면의 출석부 열기 버튼으로 확인한 뒤 출결 시트 하나로 정리를 시작해 주세요."
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


def _real_consolidation_required(
    config_dir: Path,
    run_command,
    *,
    gws_executable: str,
    account: str,
) -> bool | None:
    """Drive 읽기가 성공했을 때만 실제 원본 계약으로 화면 단추를 판정한다."""

    def runner(args, _cwd):
        code, output = run_command(list(args))
        if code != 0:
            raise subprocess.CalledProcessError(code, list(args), output=output)
        return output

    value = attendance_canonical_rebuild.inspect_consolidation_eligibility(
        config_dir=Path(config_dir),
        runner=runner,
        gws_executable=gws_executable,
        account=account,
    )
    if value.state == "current":
        return False
    if value.state == "ready":
        return True
    return None


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
    resumable = attendance_workbook_transition.read_resumable_transition_status(
        config_dir, expected_account=current_user
    )
    if resumable is not None:
        details = {
            "ai-action-required": (
                "새 정본을 열고 출결 업무 자동화 메뉴에서 AI 출결 입력 연결 확인을 "
                "한 번 누른 뒤 연결 확인하고 계속을 눌러 주세요."
            ),
            "record-switch-in-flight": "새 정본 연결 상태를 확인한 뒤 이어서 정리해 주세요.",
            "record-switched": "새 정본 연결은 끝났고 이전 파일 정리를 이어서 할 수 있습니다.",
            "cleanup-required": "새 정본 연결은 끝났고 이전 파일 정리만 남았습니다.",
            "recovery-required": "정리 진행 기록을 안전하게 확인하지 못해 자동 작업을 멈췄습니다.",
        }
        return AttendanceStatus(
            state=resumable.state,
            account=account or current_user,
            current_user=current_user,
            spreadsheet_url=resumable.spreadsheet_url,
            detail=details.get(resumable.state, details["recovery-required"]),
            consolidation_required=(
                resumable.state != "recovery-required"
                or bool(resumable.fingerprint)
            ),
            canonical_workbook_name=(
                attendance_workbook_identity.attendance_workbook_name(
                    _read_json_dict(paths.profile_path(config_dir)) or {}
                )
            ),
            transition_action_token=resumable.fingerprint,
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
        profile = _read_json_dict(paths.profile_path(config_dir)) or {}
        canonical_workbook_name = (
            attendance_workbook_identity.attendance_workbook_name(profile)
        )
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
        consolidation_required = (
            str(record.get("workbook_role", "") or "")
            != attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
            or workbook_name != recorded_workbook_name
        )
        try:
            real_required = _real_consolidation_required(
                config_dir,
                run_command,
                gws_executable=gws,
                account=current_user,
            )
        except Exception:
            real_required = None
        if real_required is not None:
            consolidation_required = real_required
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
                detail=script_detail,
                school_year=record_year, workbook_name=workbook_name, year_mismatch=year_mismatch,
                consolidation_required=consolidation_required,
                canonical_workbook_name=canonical_workbook_name,
            )
        return AttendanceStatus(
            state="failed", account=account, current_user=current_user,
            spreadsheet_url=spreadsheet_url, template_doc_url=template_doc_url,
            failed_service="setup", detail=ATTENDANCE_RECORD_BROKEN_MESSAGE,
            school_year=record_year, workbook_name=workbook_name, year_mismatch=year_mismatch,
            consolidation_required=consolidation_required,
            canonical_workbook_name=canonical_workbook_name,
        )
    profile_error = _attendance_profile_error(config_dir)
    if profile_error:
        return AttendanceStatus(state="profile-required", current_user=current_user, detail=profile_error)
    if setup_status.get("state") == "consolidation-required":
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
            state="reconnect-required",
            account=account or current_user,
            current_user=current_user,
            spreadsheet_url=str(
                setup_status.get("spreadsheet_url", "") or ""
            ),
            detail=(
                "이 컴퓨터에 현재 출석부 연결 기록이 없어 먼저 다시 연결해야 합니다. "
                + str(setup_status.get("detail", "") or "")
            )[:ATTENDANCE_DETAIL_LIMIT],
            school_year=_profile_school_year(config_dir),
            workbook_name=str(
                setup_status.get("workbook_name", "")
                or install_attendance_automation.ATTENDANCE_LEGACY_SHEET_NAME
            ),
            consolidation_required=False,
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


def read_profile_values(config_dir: Path) -> dict[str, str]:
    path = Path(config_dir) / "teacher-profile.csv"
    if not path.exists():
        return {}
    return parse_settings._read_profile_csv(path)


def write_profile_values(config_dir: Path, updates: dict[str, str]) -> Path:
    config_dir = Path(config_dir)
    merged = read_profile_values(config_dir)
    merged.update({key: (value or "").strip() for key, value in updates.items()})
    ordered = [key for key in PROFILE_FIELD_ORDER if key in merged]
    ordered += [key for key in merged if key not in PROFILE_FIELD_ORDER]
    path = config_dir / "teacher-profile.csv"
    config_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["항목", "값"])
        for key in ordered:
            writer.writerow([key, merged[key]])
    return path


def read_timetable_grid(config_dir: Path) -> list[list[str]]:
    path = Path(config_dir) / "weekly-timetable.xlsx"
    rows: dict[str, dict[str, str]] = {}
    if path.exists():
        try:
            rows = parse_settings._read_timetable_xlsx(path)
        except (ValueError, zipfile.BadZipFile, OSError):
            rows = {}  # 손상/판독불가 파일은 빈 격자로 — 대시보드가 안 열리는 것보다 낫다
    grid = []
    for period in range(1, 8):
        day_values = rows.get(str(period), {})
        grid.append([str(period)] + [day_values.get(day, "") for day in parse_settings.DAYS])
    return grid


def write_timetable_grid(config_dir: Path, grid: list[list[str]]) -> Path:
    path = Path(config_dir) / "weekly-timetable.xlsx"
    rows = [["교시", *parse_settings.DAYS]] + [[str(cell or "") for cell in row] for row in grid]
    parse_settings.write_timetable_xlsx(path, rows)
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
    logged_out_markers = (
        "not logged in", "not authenticated", "no credentials", "login required",
    )
    if logged_in:
        login_state = "logged_in"
        error_code = ""
    elif code != 0 and any(mark in lowered for mark in logged_out_markers):
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


def _empty_update(status: str = "latest", reason: str = "", latest: str = "") -> dict:
    return {
        "status": status,
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
        return _empty_update("failed", _update_check_failure_reason(error))
    except _UpdateInfoTooLarge:
        return _empty_update(
            "failed", "배포 정보가 너무 커서 안전하게 읽기를 중단했어요."
        )
    except (_UpdateInfoMalformed, _json.JSONDecodeError, UnicodeError):
        return _empty_update(
            "failed", "배포 정보를 읽을 수 없어 업데이트 확인을 중단했어요."
        )
    except _UpdateInfoUnsafeRedirect:
        return _empty_update(
            "failed", "배포 정보가 안전하지 않은 주소로 이동해 확인을 중단했어요."
        )
    except ValueError as error:
        marker = str(error or "")
        if marker == "UPDATE_INFO_TOO_LARGE":
            return _empty_update(
                "failed", "배포 정보가 너무 커서 안전하게 읽기를 중단했어요."
            )
        if marker in {"UPDATE_INFO_MALFORMED"}:
            return _empty_update(
                "failed", "배포 정보를 읽을 수 없어 업데이트 확인을 중단했어요."
            )
        if marker in {"UNSAFE_UPDATE_INFO_REDIRECT", "unsafe final update URL"}:
            return _empty_update(
                "failed", "배포 정보가 안전하지 않은 주소로 이동해 확인을 중단했어요."
            )
        return _empty_update("failed", "배포 정보의 버전 모양이 올바르지 않아요.")
    except Exception as error:  # noqa: BLE001 - 실행은 계속하되 화면에는 확인 실패를 정확히 알린다
        return _empty_update("failed", _update_check_failure_reason(error))

    if not newer:
        return _empty_update("latest", "", latest)
    if not _valid_https_update_url(url):
        return _empty_update("failed", "업데이트 주소가 공식 배포 주소가 아니어서 안전하게 중단했어요.", latest)
    if not sha256:
        return _empty_update("failed", "설치 파일의 안전 확인 정보가 없어 업데이트를 중단했어요.", latest)
    return {
        "status": "available",
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
_UPDATE_LAUNCHED: set[str] = set()
_HELD_UPDATE_FILE_LOCKS: dict[str, object] = {}


@dataclass
class _UpdateRunLease:
    acquired: bool
    hold_until_exit: bool = False

    def __bool__(self) -> bool:
        return self.acquired

    def keep_until_app_exits(self) -> None:
        self.hold_until_exit = True


def _update_lock_key(config_dir: Path) -> str:
    # 안전 확인 전 resolve()로 정션·링크를 따라가지 않는다.
    return os.path.normcase(os.path.abspath(str(config_dir)))


def _update_thread_lock(config_dir: Path) -> threading.Lock:
    key = _update_lock_key(config_dir)
    with _UPDATE_THREAD_LOCKS_GUARD:
        return _UPDATE_THREAD_LOCKS.setdefault(key, threading.Lock())


def _update_installer_was_launched(config_dir: Path) -> bool:
    key = _update_lock_key(config_dir)
    with _UPDATE_THREAD_LOCKS_GUARD:
        return key in _UPDATE_LAUNCHED


def _mark_update_installer_launched(config_dir: Path) -> None:
    key = _update_lock_key(config_dir)
    with _UPDATE_THREAD_LOCKS_GUARD:
        _UPDATE_LAUNCHED.add(key)


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
    key = _update_lock_key(base)

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
                if lease.hold_until_exit:
                    # 파일 객체를 앱이 끝날 때까지 붙잡아 둔다. 다른 프로그램 창은 같은
                    # 1바이트 잠금을 얻지 못하고, 앱이 끝나면 운영체제가 자동으로 풀어 준다.
                    with _UPDATE_THREAD_LOCKS_GUARD:
                        _HELD_UPDATE_FILE_LOCKS[key] = lock_file
                else:
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


def start_update(current: str, fetch=None, opener=None, launch=None, dest_dir=None,
                 url: str = "", latest: str = "", sha256: str = "",
                 stop_before_launch=None, config_dir=None,
                 resume_after_launch_failure=None, helper_is_running=None) -> dict:
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
                "latest": target_latest,
                "reason": "업데이트 파일을 안전하게 확인할 수 없어요. 다시 확인한 뒤 시도해 주세요.",
            }
    else:
        info = check_update(current, fetch=fetch)
        if not info["available"]:
            reason = info.get("reason") or "지금이 최신 버전이에요"
            return {"started": False, "latest": info["latest"], "reason": reason}
        target_url, target_latest, target_sha256 = info["url"], info["latest"], info["sha256"]

    if config_dir is not None and _update_installer_was_launched(Path(config_dir)):
        return {
            "started": False,
            "latest": target_latest,
            "reason": "다른 창에서 업데이트가 이미 진행 중이에요. 잠시 기다려 주세요.",
        }

    with update_run_lock(config_dir) as lease:
        if not lease:
            return {
                "started": False,
                "latest": target_latest,
                "reason": "다른 창에서 업데이트가 이미 진행 중이에요. 잠시 기다려 주세요.",
            }
        if config_dir is not None and _update_installer_was_launched(Path(config_dir)):
            return {
                "started": False,
                "latest": target_latest,
                "reason": "다른 창에서 업데이트가 이미 진행 중이에요. 잠시 기다려 주세요.",
            }
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
                "latest": target_latest,
                "reason": "받은 업데이트 파일이 배포 정보와 일치하지 않아요. 잠시 뒤 다시 시도해 주세요.",
            }
        except _UpdateDownloadTooLarge:
            return {
                "started": False,
                "latest": target_latest,
                "reason": "받으려는 설치 파일이 허용된 크기보다 커서 중단했어요.",
            }
        except _UpdateDownloadTimeout:
            return {
                "started": False,
                "latest": target_latest,
                "reason": "설치 파일을 받는 시간이 너무 길어 중단했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.",
            }
        except component_lock.UnsafeLockPathError:
            return {
                "started": False,
                "latest": target_latest,
                "reason": "업데이트를 저장할 폴더와 파일을 안전하게 확인하지 못했어요.",
            }
        except Exception:  # noqa: BLE001 - 통신 실패를 사람 말로
            return {"started": False, "latest": target_latest,
                    "reason": "새 버전 다운로드에 실패했어요. 인터넷 연결을 확인하고 다시 시도해 주세요."}
        try:
            actual_sha256 = _file_sha256(path)
        except OSError:
            _remove_download(path)
            return {
                "started": False,
                "latest": target_latest,
                "reason": "받은 업데이트 파일을 안전하게 확인하지 못했어요. 다시 시도해 주세요.",
            }
        if actual_sha256 != target_sha256:
            _remove_download(path)
            return {
                "started": False,
                "latest": target_latest,
                "reason": "받은 업데이트 파일이 배포 정보와 일치하지 않아요. 잠시 뒤 다시 시도해 주세요.",
            }

        checker = helper_is_running or helper_window_exists
        try:
            helper_was_running = bool(checker())
        except Exception:  # noqa: BLE001 - 상태를 못 읽으면 복구를 시도하는 쪽이 안전하다
            helper_was_running = True

        stopper = stop_before_launch or stop_helper
        try:
            stopped = bool(stopper())
        except Exception:  # noqa: BLE001 - 설치기를 열지 않는 쪽으로 안전하게 멈춘다
            stopped = False
        if not stopped:
            _remove_download(path)
            return {
                "started": False,
                "latest": target_latest,
                "reason": "도우미를 먼저 종료하지 못해 설치를 시작하지 않았어요. 앱을 닫고 다시 시도해 주세요.",
            }

        # 받기가 끝난 뒤 파일이 바뀔 수 있으므로, 도우미를 닫은 다음 설치 창을
        # 열기 바로 전에 같은 확인값을 다시 계산한다.
        try:
            launch_sha256 = _file_sha256(path)
        except OSError:
            launch_sha256 = ""
        if launch_sha256 != target_sha256:
            _remove_download(path)
            reason = "받은 업데이트 파일이 배포 정보와 일치하지 않아요. 잠시 뒤 다시 시도해 주세요."
            if helper_was_running:
                try:
                    restored = bool((resume_after_launch_failure or start_helper)())
                except Exception:  # noqa: BLE001
                    restored = False
                if not restored:
                    reason += " 도우미도 다시 켜지지 않았어요. 앱을 다시 실행해 주세요."
            return {"started": False, "latest": target_latest, "reason": reason}

        try:
            # 설치 진행과 오류를 사용자가 바로 볼 수 있게 마법사 전체를 보여 준다.
            run = launch or (
                lambda file: subprocess.Popen(update_launch_command(file), close_fds=True)
            )
            run(path)
        except Exception:  # noqa: BLE001
            # 설치기 창을 열지 못했다면, 원래 켜져 있던 도우미만 되살린다.
            reason = "설치 파일을 실행하지 못했어요. 다운로드 페이지에서 직접 받아 주세요."
            if helper_was_running:
                try:
                    restored = bool((resume_after_launch_failure or start_helper)())
                except Exception:  # noqa: BLE001
                    restored = False
                if not restored:
                    reason += " 도우미도 다시 켜지지 않았어요. 앱을 다시 실행해 주세요."
            return {"started": False, "latest": target_latest, "reason": reason}
        if config_dir is not None:
            _mark_update_installer_launched(Path(config_dir))
        if launch is None:
            lease.keep_until_app_exits()
        return {"started": True, "latest": target_latest, "reason": ""}


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


def ai_tools_status(home=None) -> list:
    home = Path(home) if home else Path.home()
    return [
        {"key": tool["key"], "name": tool["name"], "found": (home / tool["folder"]).exists()}
        for tool in AI_TOOLS
    ]


def _ai_node_result(runtime: tool_runtime.NodeRuntime) -> dict:
    return {
        "success": bool(runtime.ready),
        "code": str(runtime.code),
        "detail": str(runtime.detail),
        "version": str(runtime.version or ""),
    }


def ai_node_status(*, local_app_data=None, run_command=process_win.run_captured) -> dict:
    """시스템 Node를 보지 않고 Teacher Manager 전용 Node만 확인한다."""
    return _ai_node_result(tool_runtime.resolve_node(
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


def list_calendars(run_command, gws: str) -> list[dict]:
    require_goedu_gws_session(run_command, gws)
    reply = _run_gws_json(
        run_command,
        [gws, "calendar", "calendarList", "list", "--params", '{"maxResults":250}', "--format", "json"],
        LIST_CALENDARS_FAILURE,
    )
    return [{"id": item.get("id", ""), "name": item.get("summary", "")} for item in reply.get("items", [])]


def list_tasklists(run_command, gws: str) -> list[dict]:
    require_goedu_gws_session(run_command, gws)
    reply = _run_gws_json(
        run_command,
        [gws, "tasks", "tasklists", "list", "--format", "json"],
        LIST_TASKLISTS_FAILURE,
    )
    return [{"id": item.get("id", ""), "name": item.get("title", "")} for item in reply.get("items", [])]


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
    attendance_preview_builder: object = attendance_canonical_rebuild.build_consolidation_preview
    transition_deps_factory: object = attendance_workbook_transition.make_transition_deps
    workbook_consolidator: object = attendance_workbook_transition.consolidate_attendance_workbooks
    new_school_year_starter: object = attendance_workbook_transition.start_new_school_year_workbook
    gws_resolver: object = tool_runtime.resolve_gws_executable


@dataclass(frozen=True)
class AttendanceConsolidationFile:
    name: str
    created_time: str
    attendance_rows: int
    personal_chat_rows: int
    group_chat_rows: int
    send_rows: int
    total_rows: int


@dataclass(frozen=True)
class AttendanceConsolidationPreview:
    state: str
    fingerprint: str = ""
    files: tuple[AttendanceConsolidationFile, ...] = ()
    total_rows: int = 0
    conflict: bool = False
    detail: str = ""


@dataclass(frozen=True)
class AttendanceMovedFile:
    name: str
    moved_rows: int


@dataclass(frozen=True)
class AttendanceConsolidationResult:
    state: str
    spreadsheet_url: str = ""
    moved_files: tuple[AttendanceMovedFile, ...] = ()
    ai_connection_confirmed: bool = False
    trashed_count: int = 0
    remaining_cleanup_count: int = 0
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
        "reconnect-required", "ai-action-required", "record-switch-in-flight",
        "record-switched", "cleanup-required", "recovery-required",
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
        "reconnect-required", "ai-action-required", "record-switch-in-flight",
        "record-switched", "cleanup-required", "recovery-required",
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
            install_attendance_automation.LegacyAttendanceConsolidationRequired,
        ):
            candidates = list(error.candidates)
            single = candidates[0] if len(candidates) == 1 else {}
            spreadsheet_url = str(single.get("webViewLink", "") or "").strip()
            if not spreadsheet_url and str(single.get("id", "") or "").strip():
                spreadsheet_url = (
                    "https://docs.google.com/spreadsheets/d/"
                    + str(single["id"]).strip()
                    + "/edit"
                )
            workbook_name = str(
                single.get("name", "")
                or install_attendance_automation.ATTENDANCE_LEGACY_SHEET_NAME
            )
            profile = _read_json_dict(Path(profile_json)) or {}
            detail = (
                "이 컴퓨터에 현재 출석부 연결 기록이 없어 먼저 다시 연결해야 합니다. "
                + detail
            )[:ATTENDANCE_DETAIL_LIMIT]
            saved = {
                "state": "consolidation-required",
                "account": current_user,
                "detail": detail,
                "spreadsheet_url": spreadsheet_url,
                "workbook_name": workbook_name,
            }
            _write_setup_status(config_dir, saved)
            return AttendanceStatus(
                state="reconnect-required",
                account=current_user,
                current_user=current_user,
                spreadsheet_url=spreadsheet_url,
                detail=detail,
                school_year=_profile_school_year(config_dir),
                workbook_name=workbook_name,
                consolidation_required=False,
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
        detail=result_detail,
        created=True,
        school_year=_profile_school_year(config_dir),
        workbook_name=getattr(result, "workbook_name", "") or "",
        year_mismatch=False,
    )


_CONSOLIDATION_USABLE_STATES = frozenset({
    "ready",
    "script-check-required",
    "script-update-required",
    "consolidation-required",
    "ai-action-required",
    "record-switch-in-flight",
    "record-switched",
    "cleanup-required",
    "recovery-required",
})
_CONSOLIDATION_PERMISSION_MESSAGE = (
    "Google 권한을 한 번 허용한 뒤 정리할 자료를 다시 확인해 주세요."
)
_CONSOLIDATION_REFRESH_MESSAGE = (
    "확인한 뒤 자료가 바뀌었습니다. 정리할 자료를 다시 확인해 주세요."
)
_CONSOLIDATION_FAILED_MESSAGE = (
    "출결 시트를 하나로 정리하지 못했어요. 기존 출결 자료와 현재 연결은 바꾸지 않았습니다. "
    "다시 시도해 주세요."
)
_CONSOLIDATION_PREVIEW_MESSAGES = {
    "conflict": "학생 명단이나 기본 정보가 다른 파일이 있어 먼저 확인이 필요합니다.",
    "unprocessed-ai": "아직 처리하지 않은 AI 입력이 있어 먼저 출석부에서 확인해 주세요.",
    "key-conflict": "AI 입력 연결 정보가 서로 달라 자동으로 고르지 않았습니다.",
    "customized": "직접 바꾼 출결 기능이 있을 수 있어 자동 정리를 시작하지 않았습니다.",
    "script-update-required": "현재 출결 기능을 먼저 최신판으로 맞춰 주세요.",
    "current": "현재 출석부는 이미 정본 하나입니다.",
    "setup-required": "현재 출석부를 다시 연결한 뒤 정리를 시작해 주세요.",
    "failed": "정리할 출결 자료를 안전하게 끝까지 확인하지 못했어요.",
}


def _consolidation_preview_files(value) -> tuple[AttendanceConsolidationFile, ...]:
    safe_files: list[AttendanceConsolidationFile] = []
    rows = getattr(value, "counts_by_source", ())
    if not isinstance(rows, (list, tuple)):
        return ()
    for raw in rows:
        if not isinstance(raw, dict):
            return ()
        counts = raw.get("counts")
        if not isinstance(counts, dict):
            return ()

        def count(label: str) -> int:
            value = counts.get(label, 0)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        attendance_rows = sum(count(f"{month}월") for month in range(1, 13))
        personal_chat_rows = count("개인톡")
        group_chat_rows = count("단체톡")
        send_rows = count("발송기록")
        calculated_total = (
            attendance_rows + personal_chat_rows + group_chat_rows + send_rows
        )
        total_value = raw.get("total_rows", calculated_total)
        total_rows = (
            total_value
            if isinstance(total_value, int)
            and not isinstance(total_value, bool)
            and total_value >= 0
            else calculated_total
        )
        safe_files.append(AttendanceConsolidationFile(
            name=" ".join(str(raw.get("name", "") or "").split())[:160],
            created_time=str(raw.get("created_time", "") or "")[:40],
            attendance_rows=attendance_rows,
            personal_chat_rows=personal_chat_rows,
            group_chat_rows=group_chat_rows,
            send_rows=send_rows,
            total_rows=total_rows,
        ))
    return tuple(safe_files)


def _screen_consolidation_preview(value) -> AttendanceConsolidationPreview:
    state = str(getattr(value, "state", "failed") or "failed")
    if state not in {"ready", *_CONSOLIDATION_PREVIEW_MESSAGES}:
        state = "failed"
    fingerprint = str(getattr(value, "fingerprint", "") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        fingerprint = ""
    files = _consolidation_preview_files(value)
    total_value = getattr(value, "total_rows", 0)
    total_rows = (
        total_value
        if isinstance(total_value, int)
        and not isinstance(total_value, bool)
        and total_value >= 0
        else sum(item.total_rows for item in files)
    )
    if state == "ready" and (not fingerprint or not files):
        state = "failed"
    return AttendanceConsolidationPreview(
        state=state,
        fingerprint=fingerprint if state in {"ready", "conflict"} else "",
        files=files,
        total_rows=total_rows,
        conflict=state == "conflict",
        detail="" if state == "ready" else _CONSOLIDATION_PREVIEW_MESSAGES[state],
    )


def attendance_consolidation_preview(
    config_dir: Path, deps: AttendanceDeps | None = None
) -> AttendanceConsolidationPreview:
    """화면 확인용 요약만 돌려주며 후보·연결·Google 자료는 바꾸지 않는다."""

    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    try:
        gws = str(deps.gws_resolver())
        current = read_attendance_status(
            config_dir, deps.run_command, gws_executable=gws
        )
        if current.state == "reconnect-required":
            return AttendanceConsolidationPreview(
                state="setup-required",
                detail="현재 출석부를 다시 연결한 뒤 정리를 시작해 주세요.",
            )
        if (
            current.state not in _CONSOLIDATION_USABLE_STATES
            or not current.consolidation_required
        ):
            return AttendanceConsolidationPreview(
                state="unavailable",
                detail="현재 출석부는 하나로 정리할 대상이 아닙니다.",
            )
        account = current.current_user
        if not has_current_gws_scope_grant(config_dir, account):
            return AttendanceConsolidationPreview(
                state="permission-required",
                detail=_CONSOLIDATION_PERMISSION_MESSAGE,
            )
        with attendance_remote_work_lock(config_dir):
            current = read_attendance_status(
                config_dir, deps.run_command, gws_executable=gws
            )
            if (
                current.state not in _CONSOLIDATION_USABLE_STATES
                or not current.consolidation_required
                or not has_current_gws_scope_grant(config_dir, current.current_user)
            ):
                return AttendanceConsolidationPreview(
                    state="permission-required",
                    detail=_CONSOLIDATION_PERMISSION_MESSAGE,
                )
            value = deps.attendance_preview_builder(
                config_dir=config_dir,
                runner=deps.attendance_runner,
                gws_executable=gws,
                account=current.current_user,
            )
            return _screen_consolidation_preview(value)
    except Exception:  # noqa: BLE001 - 외부 오류 원문은 화면에 내보내지 않는다.
        return AttendanceConsolidationPreview(
            state="failed",
            detail=_CONSOLIDATION_PREVIEW_MESSAGES["failed"],
        )


def _cleanup_retry_checkpoint(
    config_dir: Path, fingerprint: str, account: str
):
    checkpoint = (
        attendance_workbook_transition.read_validated_consolidation_checkpoint(
            config_dir,
            expected_fingerprint=fingerprint,
            expected_account=account,
        )
    )
    return (
        checkpoint
        if checkpoint is not None
        and checkpoint.state in {"record-switched", "cleanup-required"}
        else None
    )


_CONSOLIDATION_RESUME_STATES = frozenset({
    "ai-action-required",
    "record-switch-in-flight",
    "record-switched",
    "cleanup-required",
    "recovery-required",
})
_CONSOLIDATION_AI_ACTION_MESSAGE = (
    "새 정본을 열고 출결 업무 자동화 메뉴에서 AI 출결 입력 연결 확인을 한 번 "
    "누른 뒤 연결 확인하고 계속을 눌러 주세요."
)
_CONSOLIDATION_INDETERMINATE_MESSAGE = (
    "정리가 시작된 뒤 응답을 끝까지 확인하지 못했어요. 저장된 진행 상태를 "
    "확인해 이어서 진행해 주세요."
)


def _resumable_for_action(
    config_dir: Path,
    *,
    fingerprint: str,
    account: str,
):
    value = attendance_workbook_transition.read_resumable_transition_status(
        config_dir, expected_account=account
    )
    if value is None:
        return None
    saved = str(getattr(value, "fingerprint", "") or "").strip().lower()
    if saved and saved != fingerprint:
        return attendance_workbook_transition.ResumableTransitionStatus(
            state="recovery-required"
        )
    return value


def _result_from_resumable_status(value) -> AttendanceConsolidationResult:
    state = str(getattr(value, "state", "recovery-required") or "recovery-required")
    url = str(getattr(value, "spreadsheet_url", "") or "")
    if state == "ai-action-required":
        detail = _CONSOLIDATION_AI_ACTION_MESSAGE
    elif state == "cleanup-required":
        detail = "새 정본 연결은 끝났고 이전 파일 정리만 남았습니다."
    elif state == "record-switched":
        detail = "새 정본 연결은 끝났고 이전 파일 정리를 이어서 할 수 있습니다."
    elif state == "record-switch-in-flight":
        detail = "새 정본 연결 상태를 다시 확인한 뒤 정리를 이어서 진행해 주세요."
    else:
        state = "recovery-required"
        detail = _CONSOLIDATION_INDETERMINATE_MESSAGE
    return AttendanceConsolidationResult(
        state=state,
        spreadsheet_url=url,
        detail=detail,
    )


def consolidate_attendance(
    config_dir: Path,
    preview_fingerprint: str,
    deps: AttendanceDeps | None = None,
) -> AttendanceConsolidationResult:
    """승인한 미리보기를 잠금 안에서 다시 읽고 Task 5 전환을 실행한다."""

    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    fingerprint = str(preview_fingerprint or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        return AttendanceConsolidationResult(
            state="refresh-required", detail=_CONSOLIDATION_REFRESH_MESSAGE
        )
    mutation_may_have_started = False
    account = ""
    try:
        gws = str(deps.gws_resolver())
        preflight = read_attendance_status(
            config_dir, deps.run_command, gws_executable=gws
        )
        if preflight.state == "reconnect-required":
            return AttendanceConsolidationResult(
                state="setup-required",
                detail="현재 출석부를 다시 연결한 뒤 정리를 시작해 주세요.",
            )
        account = preflight.current_user
        resumable = _resumable_for_action(
            config_dir, fingerprint=fingerprint, account=account
        )
        resume_retry = bool(
            resumable is not None
            and resumable.state in _CONSOLIDATION_RESUME_STATES
            and str(resumable.fingerprint or "").strip().lower() == fingerprint
        )
        if preflight.state not in _CONSOLIDATION_USABLE_STATES:
            return AttendanceConsolidationResult(
                state="failed", detail=_CONSOLIDATION_FAILED_MESSAGE
            )
        if not resume_retry and not preflight.consolidation_required:
            return AttendanceConsolidationResult(
                state="refresh-required", detail=_CONSOLIDATION_REFRESH_MESSAGE
            )
        if not has_current_gws_scope_grant(config_dir, account):
            return AttendanceConsolidationResult(
                state="permission-required", detail=_CONSOLIDATION_PERMISSION_MESSAGE
            )

        with attendance_remote_work_lock(config_dir):
            current = read_attendance_status(
                config_dir, deps.run_command, gws_executable=gws
            )
            account = current.current_user
            resumable = _resumable_for_action(
                config_dir, fingerprint=fingerprint, account=account
            )
            resume_retry = bool(
                resumable is not None
                and resumable.state in _CONSOLIDATION_RESUME_STATES
                and str(resumable.fingerprint or "").strip().lower() == fingerprint
            )
            if (
                current.state not in _CONSOLIDATION_USABLE_STATES
                or (not resume_retry and not current.consolidation_required)
            ):
                return AttendanceConsolidationResult(
                    state="refresh-required", detail=_CONSOLIDATION_REFRESH_MESSAGE
                )
            if not has_current_gws_scope_grant(config_dir, current.current_user):
                return AttendanceConsolidationResult(
                    state="permission-required", detail=_CONSOLIDATION_PERMISSION_MESSAGE
                )

            safe_preview = None
            if not resume_retry:
                fresh_value = deps.attendance_preview_builder(
                    config_dir=config_dir,
                    runner=deps.attendance_runner,
                    gws_executable=gws,
                    account=current.current_user,
                )
                safe_preview = _screen_consolidation_preview(fresh_value)
                if (
                    safe_preview.state != "ready"
                    or safe_preview.fingerprint != fingerprint
                ):
                    return AttendanceConsolidationResult(
                        state="refresh-required", detail=_CONSOLIDATION_REFRESH_MESSAGE
                    )

            transition_deps = deps.transition_deps_factory(
                runner=deps.attendance_runner,
                gws_executable=gws,
                account=current.current_user,
            )
            mutation_may_have_started = True
            result = deps.workbook_consolidator(
                config_dir,
                expected_fingerprint=fingerprint,
                deps=transition_deps,
            )
            state = str(getattr(result, "state", "failed") or "failed")
            resumable = _resumable_for_action(
                config_dir,
                fingerprint=fingerprint,
                account=current.current_user,
            )
            checkpoint = (
                attendance_workbook_transition
                .read_validated_consolidation_checkpoint(
                    config_dir,
                    expected_fingerprint=fingerprint,
                    expected_account=current.current_user,
                )
            )
            if state in {
                "ai-action-required",
                "record-switch-in-flight",
                "record-switched",
                "cleanup-required",
                "recovery-required",
            }:
                if resumable is not None:
                    response = _result_from_resumable_status(resumable)
                    if response.state == "cleanup-required" and checkpoint is not None:
                        return replace(
                            response,
                            remaining_cleanup_count=len(
                                checkpoint.remaining_cleanup_ids
                            ),
                        )
                    return response
                return AttendanceConsolidationResult(
                    state="recovery-required",
                    detail=_CONSOLIDATION_INDETERMINATE_MESSAGE,
                )
            if state == "refresh-required":
                return AttendanceConsolidationResult(
                    state="refresh-required", detail=_CONSOLIDATION_REFRESH_MESSAGE
                )
            if state != "complete":
                if resumable is not None:
                    return _result_from_resumable_status(resumable)
                return AttendanceConsolidationResult(
                    state="recovery-required",
                    detail=_CONSOLIDATION_INDETERMINATE_MESSAGE,
                )

            if checkpoint is None or checkpoint.state != "complete":
                return AttendanceConsolidationResult(
                    state="recovery-required",
                    detail=_CONSOLIDATION_INDETERMINATE_MESSAGE,
                )

            try:
                final = read_attendance_status(
                    config_dir, deps.run_command, gws_executable=gws
                )
                if (
                    final.state in _CONSOLIDATION_USABLE_STATES
                    and not final.consolidation_required
                    and final.spreadsheet_url == checkpoint.spreadsheet_url
                ):
                    save_attendance_status_cache(config_dir, asdict(final))
            except Exception:
                # 전환 기록과 현재 연결은 이미 한 묶음으로 검증됐다. 이후 계정 상태
                # 확인 실패가 성공 응답을 없던 일로 만들면 안 된다.
                pass
            counts = checkpoint.moved_row_counts
            files = safe_preview.files if safe_preview is not None else ()
            moved_files = tuple(
                AttendanceMovedFile(name=file.name, moved_rows=count)
                for file, count in zip(files, counts)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0
            )
            return AttendanceConsolidationResult(
                state="complete",
                spreadsheet_url=checkpoint.spreadsheet_url,
                moved_files=moved_files,
                ai_connection_confirmed=checkpoint.trigger_count == 1,
                trashed_count=(
                    checkpoint.total_cleanup_count
                    - len(checkpoint.remaining_cleanup_ids)
                ),
                remaining_cleanup_count=0,
            )
    except Exception:  # noqa: BLE001 - 외부 계정·명령·경로 원문은 숨긴다.
        if mutation_may_have_started:
            resumable = _resumable_for_action(
                config_dir, fingerprint=fingerprint, account=account
            )
            if resumable is not None:
                return _result_from_resumable_status(resumable)
            return AttendanceConsolidationResult(
                state="recovery-required",
                detail=_CONSOLIDATION_INDETERMINATE_MESSAGE,
            )
        return AttendanceConsolidationResult(
            state="failed", detail=_CONSOLIDATION_FAILED_MESSAGE
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
    if preflight.consolidation_required:
        return replace(
            preflight,
            state="failed",
            failed_service="sheet",
            detail="먼저 출결 시트 하나로 정리를 끝내 주세요.",
        )
    if not preflight.year_mismatch:
        return preflight

    with attendance_setup_lock(config_dir):
        current = read_attendance_status(
            config_dir, deps.run_command, gws_executable=gws
        )
        if current.state not in usable_states:
            return current
        if current.consolidation_required:
            return replace(
                current,
                state="failed",
                failed_service="sheet",
                detail="먼저 출결 시트 하나로 정리를 끝내 주세요.",
            )
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
            or final.consolidation_required
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
    """[모두 저장하고 적용] — 유일한 적용 지점. 모든 단계는 멱등이다."""
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

    attendance_status = ensure_attendance(config_dir, deps=deps)
    if attendance_status.created:
        results.append(StepResult("attendance", "출결 자동화", "done", attendance_status.spreadsheet_url))
    elif attendance_status.state == "ready":
        results.append(StepResult("attendance", "출결 자동화", "skipped", "이미 준비되어 있어요"))
    else:
        results.append(StepResult("attendance", "출결 자동화", "failed", attendance_status.detail))

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
