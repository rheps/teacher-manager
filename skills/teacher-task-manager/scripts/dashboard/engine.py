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
from xml.sax.saxutils import escape
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from pathlib import Path
import urllib.error
from urllib.parse import unquote, urlsplit

import parse_settings
from brity_bridge import (
    atomic_io,
    autostart_win,
    bundle_paths,
    component_lock,
    hotkey_win,
    paths,
    process_win,
    recovery,
)
from brity_bridge.doctor import CheckResult, DoctorDeps, _default_run_command, run_doctor_checks, saved_target_ids
from brity_bridge.gemini_analyze import check_gemini_key
from brity_bridge.hotkey import MODIFIER_ORDER, parse_hotkey
from brity_bridge.settings import load_settings, normalize_gemini_model, save_settings
from dashboard import version

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

def home_checks(config_dir: Path, deps: HomeCheckDeps | None = None, *, identity: dict | None = None,
                target_statuses: dict | None = None) -> list[CheckResult]:
    """Setup checks only (SCREENS 'Home tile'). The attendance tile has its own read."""
    if deps is None:
        deps = HomeCheckDeps()
    elif isinstance(deps, DoctorDeps):
        deps = HomeCheckDeps(doctor_deps=deps)
    config_dir = Path(config_dir)
    doctor_deps = deps.doctor_deps or DoctorDeps()

    results = list(run_doctor_checks(config_dir, doctor_deps, identity=identity, target_statuses=target_statuses))
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

    readiness = computer_readiness(document_probe=deps.document_probe)
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

    return results


def verify_gemini_key(api_key: str, model: str, transport=None) -> tuple[str, str]:
    # AI-KEY-02: any saved or legacy model id is checked as the one Flash-Lite model.
    return check_gemini_key(api_key, normalize_gemini_model(model), transport=transport)


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
            if key == "gemini_model":
                value = normalize_gemini_model(value)
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


ATTENDANCE_SHEET_PUSH_FAILURE = (
    "이 컴퓨터 저장은 마쳤지만 출결표에는 반영하지 못했어요. "
    "Google 로그인과 인터넷 연결을 확인한 뒤 다시 저장해 주세요."
)


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
    stop=None,
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
        candidate.gemini_model = normalize_gemini_model(updates["gemini_model"])
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
        # Injectable so tests never send WM_CLOSE to the real helper (ST-05).
        stop=stop or stop_helper,
        start=restart or start_helper,
        exists=helper_exists or helper_window_exists,
        app_version=version.APP_VERSION,
    )
    sheet_push = {"state": "skipped", "detail": ""}
    if "gemini_api_key" in updates and push_key:
        pusher = push_key
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
    _log_helper_stop_request()
    user32.PostMessageW(ctypes.c_void_p(hwnd), _WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not helper_window_exists():
            return True
        time.sleep(0.1)
    return False


def _log_helper_stop_request() -> None:
    """대시보드가 도우미를 닫은 흐름(함수 이름)만 helper-*.log에 남긴다(PEND-43, R7-3)."""
    try:
        from brity_bridge import status_log

        callers = []
        frame = sys._getframe(2)
        while frame is not None and len(callers) < 3:
            callers.append(frame.f_code.co_name)
            frame = frame.f_back
        status_log.append_helper_event(
            paths.logs_dir(paths.default_config_dir()), "stop-requested",
            "by-dashboard " + "<".join(callers))
    except Exception:  # noqa: BLE001 - 기록 실패가 도우미 종료를 막으면 안 된다
        pass


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


ATTENDANCE_ACCOUNT_MESSAGE = "현재 출석부를 만든 Google 계정으로 로그인해 주세요. [Google 연결]에 표시된 계정과 브라우저에서 출석부를 열 때의 계정을 비교해 주세요."


def _read_json_dict(path: Path) -> dict | None:
    try:
        parsed = _json.loads(Path(path).read_text(encoding="utf-8"))
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


# 파서가 읽는 기존 항목명이 정본 — 대시보드가 새 이름을 만들지 않는다.
PROFILE_FIELD_ORDER = [
    "선생님이름", "학교명", "학교급", "담임여부", "담임학년", "담임반", "반이름",
    "출근시간", "퇴근시간", "조회시작", "1교시시작", "점심종료시간",
    "월요일마지막교시", "화요일마지막교시", "수요일마지막교시", "목요일마지막교시", "금요일마지막교시",
    "업무캘린더ID", "업무캘린더이름", "학사일정캘린더ID", "학사일정캘린더이름",
    "업무Tasks목록ID", "업무Tasks목록이름",
    "담임안내Tasks목록ID", "담임안내Tasks목록이름",
]

IDENTITY_FIELDS = frozenset({
    "선생님이름", "학교명", "학교급", "담임여부", "담임학년", "담임반", "반이름",
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
    merged.update({key: (value or "").strip() for key, value in updates.items() if key != "학년도"})
    merged.pop("학년도", None)
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
        raw = _json.loads(Path(path).read_text(encoding="utf-8"))
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
        candidate.gemini_model = normalize_gemini_model(checked["gemini_model"])
    save_and_verify_messenger(
        config_dir, candidate, checked, operation="gemini_save"
    )

    # The key stays on this PC (D11). There is no workbook to copy it to any more.
    sheet_push = {"state": "skipped", "detail": ""}
    if "gemini_api_key" in checked and push_key:
        try:
            sheet_push = push_key(Path(config_dir))
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


GOOGLE_PERMISSION_CHECK_MESSAGE = (
    "Google 로그인과 권한을 지금 확인하지 못했어요. 연결을 확인한 뒤 다시 점검해 주세요."
)


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


_COMPONENT_RECOVERY_COMPLETE_CODES = {
    "update_offer": frozenset({"UPDATE_AVAILABLE", "UPDATE_LATEST"}),
    "update_download": frozenset({"UPDATE_SETUP_READY"}),
    "update_helper_stop": frozenset({"UPDATE_HELPER_STOPPED"}),
    "update_setup_launch": frozenset({"UPDATE_SETUP_LAUNCHED"}),
}
_COMPONENT_RECOVERY_RETRYABLE_CODES = {
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


# 배포 저장소(rheps/teacher-manager)의 최신 Release에 붙은 version.json을 본다.
UPDATE_INFO_URL = "https://github.com/rheps/teacher-manager/releases/latest/download/version.json"
_UPDATE_INFO_MAX_BYTES = 64 * 1024
_UPDATE_INFO_DEADLINE_SECONDS = 25.0  # 느린 첫 이름 확인(약 11초)을 한 번 견딜 만큼
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


def _python_update_info_bytes() -> bytes:
    from brity_bridge import tls

    deadline = time.monotonic() + _UPDATE_INFO_DEADLINE_SECONDS
    contents = bytearray()
    with tls.open_https(UPDATE_INFO_URL, timeout=20) as response:
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
    return bytes(contents)


# UPD-01/UPD-04: on a school PC Python (and .NET) HTTPS to GitHub timed out every
# time while the built-in Windows curl.exe and the browser got 200 in under a second.
# UPD-04 (2026-09-30 user decision): curl.exe goes first and Python urllib is the
# fallback, once, inside the same attempt; the caller's three-attempt budget is
# unchanged and every host/size/SHA-256 guard still applies.
_UPDATE_CURL_RUNNER = None  # tests replace this; None runs the real curl.exe
_UPDATE_TRANSPORT_LABEL = re.compile(r"^[a-z0-9-]{1,40}$")


class _UpdateCurlFailed(OSError):
    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


def _system_curl_path() -> str:
    """Only %SystemRoot%\\System32\\curl.exe, by absolute path; never a PATH lookup."""
    if os.name != "nt":
        return ""
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or ""
    if not root or not os.path.isabs(root):
        return ""
    candidate = os.path.join(root, "System32", "curl.exe")
    return candidate if os.path.isfile(candidate) else ""


def _python_fallback_label(error: BaseException) -> str:
    """Name a timeout/connection failure; '' for HTTP replies and certificate errors."""
    if isinstance(error, urllib.error.HTTPError):
        return ""
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, (ssl.SSLCertVerificationError, ssl.CertificateError)):
        return ""
    if isinstance(cause, ssl.SSLError) and not isinstance(
        cause, (ssl.SSLEOFError, ssl.SSLZeroReturnError)
    ):
        return ""
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return "python-timeout"
    if isinstance(cause, OSError):
        return "python-connect"
    return ""


def _python_transport_label(error: BaseException) -> str:
    """Trail label for the Python fallback's final failure (UPD-04); diagnostics only."""
    if isinstance(error, urllib.error.HTTPError):
        code = getattr(error, "code", None)
        return f"python-http-{int(code)}" if isinstance(code, int) and 100 <= code <= 599 else "python-http"
    label = _python_fallback_label(error)
    if label:
        return label
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, (ssl.SSLError, ssl.CertificateError)):
        return "python-tls"
    return "python-error"


def _attach_update_transport(error: BaseException, trail: list[str]) -> None:
    try:
        error.update_transport = ">".join(trail)
    except Exception:  # noqa: BLE001 - diagnostics only
        pass


def _curl_fetch(url: str, target: Path, *, max_seconds: float, max_bytes: int) -> str:
    """Fetch one https URL to target with Windows curl.exe; return the checked final URL."""
    curl = _system_curl_path() if _UPDATE_CURL_RUNNER is None else "curl.exe"
    if not curl:
        raise _UpdateCurlFailed("curl-missing")
    marker = "TM_FINAL_URL="
    args = [
        curl, "--silent", "--fail", "--location", "--max-redirs", "5",
        "--proto", "=https", "--proto-redir", "=https",
        "--max-time", str(int(max_seconds)), "--max-filesize", str(int(max_bytes)),
        "--output", str(target), "--write-out", f"\\n{marker}%{{url_effective}}\\n", url,
    ]
    runner = _UPDATE_CURL_RUNNER or (lambda values, timeout: process_win.run_captured(values, timeout=timeout))
    try:
        code, output = runner(args, timeout=max_seconds + 10)
    except Exception:  # noqa: BLE001 - a curl.exe that cannot start is one failed transport (UPD-04)
        raise _UpdateCurlFailed("curl-error") from None
    if code != 0:
        raise _UpdateCurlFailed(f"curl-exit-{int(code)}")
    finals = [line[len(marker):].strip() for line in str(output or "").splitlines() if line.startswith(marker)]
    if not finals or not _valid_https_update_url(finals[-1]):
        raise _UpdateCurlFailed("host-not-allowed")
    size = target.stat().st_size if target.exists() else -1
    if size < 0 or size > max_bytes:
        raise _UpdateCurlFailed("curl-too-large" if size > max_bytes else "curl-no-file")
    return finals[-1]


def _curl_update_info_bytes() -> bytes:
    folder = Path(tempfile.mkdtemp(prefix="tm-update-info-"))
    target = folder / "version.json"
    try:
        _curl_fetch(UPDATE_INFO_URL, target, max_seconds=_UPDATE_INFO_DEADLINE_SECONDS,
                    max_bytes=_UPDATE_INFO_MAX_BYTES)
        return target.read_bytes()
    finally:
        try:
            target.unlink()
        except OSError:
            pass
        try:
            folder.rmdir()
        except OSError:
            pass


def _fetch_update_json() -> dict:
    # UPD-04: curl.exe first; Python urllib once only when curl did not deliver.
    trail: list[str] = []
    contents = None
    try:
        contents = _curl_update_info_bytes()
        trail.append("curl-ok")
    except _UpdateCurlFailed as curl_error:
        trail.append(curl_error.label)
        if curl_error.label == "curl-too-large":
            # The same 64 KB cap applies to Python; a second transport cannot help.
            too_large = _UpdateInfoTooLarge("UPDATE_INFO_TOO_LARGE")
            _attach_update_transport(too_large, trail)
            raise too_large from None
    if contents is None:
        try:
            contents = _python_update_info_bytes()
        except Exception as error:
            # Both transports failed: the Python failure decides the screen message.
            trail.append(_python_transport_label(error))
            _attach_update_transport(error, trail)
            raise
        trail.append("python-ok")
    # BOM이 붙어 있어도 읽는다.
    try:
        value = _json.loads(bytes(contents).decode("utf-8-sig"))
    except (UnicodeError, _json.JSONDecodeError) as error:
        raise _UpdateInfoMalformed("UPDATE_INFO_MALFORMED") from error
    if not isinstance(value, dict):
        raise _UpdateInfoMalformed("UPDATE_INFO_MALFORMED")
    value = dict(value)
    value["_transport"] = ">".join(trail)
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
    "배포 사이트에 연결하지 못해 업데이트를 확인하지 못했어요. "
    "인터넷 연결을 확인한 뒤 '업데이트 다시 확인'을 눌러 주세요."
)


# A timeout reached neither Python nor Windows curl.exe (UPD-01). The browser may
# still reach GitHub, so point to the download page first (C11).
_UPDATE_DOWNLOAD_UNREACHABLE = (
    "업데이트 서버(GitHub)에서 설치 파일을 받지 못했어요. "
    "먼저 아래 [다운로드 페이지 열기]로 최신 설치 파일을 직접 받을 수 있어요."
)
_UPDATE_SERVER_UNREACHABLE = (
    "업데이트 서버(GitHub)에 연결하지 못해 새 버전을 확인하지 못했어요. "
    "먼저 아래 [다운로드 페이지 열기]로 최신 설치 파일을 직접 받을 수 있어요."
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
        return _UPDATE_SERVER_UNREACHABLE
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return (
                "보안 인증서를 확인하지 못해 업데이트 확인을 멈췄어요. "
                "학교 보안 프로그램이나 컴퓨터의 날짜·시간을 확인해 주세요."
            )
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return _UPDATE_SERVER_UNREACHABLE
        return _UPDATE_CHECK_OFFLINE
    if isinstance(error, OSError):
        return _UPDATE_CHECK_OFFLINE
    return (
        "업데이트 확인을 하지 못했어요. "
        "실패 원인은 아직 확인하지 못했습니다."
    )


def _safe_update_transport(value) -> str:
    parts = str(value or "").split(">")
    if not value or len(parts) > 4 or not all(_UPDATE_TRANSPORT_LABEL.fullmatch(p) for p in parts):
        return ""
    return ">".join(parts)


def check_update(current: str, fetch=None) -> dict:
    """새 버전, 최신 상태, 확인 실패를 서로 다른 값으로 알려준다."""
    fetch = fetch or _fetch_update_json
    seen = {"transport": ""}

    def traced():
        # Which transport failed or succeeded goes to diagnostics only (UPD-01).
        try:
            data = fetch()
        except BaseException as error:
            seen["transport"] = getattr(error, "update_transport", "")
            raise
        if isinstance(data, dict):
            seen["transport"] = data.get("_transport", "")
        return data

    result = _check_update_once(current, traced)
    transport = _safe_update_transport(seen["transport"])
    return {**result, "transport": transport} if transport else result


def record_update_transport(config_dir, stage: str, transport: str) -> None:
    """Keep a local line unless curl.exe alone succeeded (UPD-02/UPD-04). Best effort only."""
    if config_dir is None or not transport or transport == "curl-ok":
        return
    try:
        log = paths.bridge_state_dir(Path(config_dir)) / "logs" / "update-transport.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        lines = log.read_text(encoding="utf-8").splitlines()[-199:] if log.exists() else []
        lines.append(_json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": str(stage),
                                  "transport": transport}, ensure_ascii=False))
        atomic_io.atomic_write_text(log, "".join(line + "\n" for line in lines))
    except Exception:  # noqa: BLE001 - diagnostics never block the update
        pass


def _check_update_once(current: str, fetch) -> dict:
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


def _curl_download_into(url: str, partial: Path, identity: tuple[int, int]) -> str:
    """UPD-04 first try: curl.exe overwrites the same owned partial; hash it back here."""
    _curl_fetch(url, partial, max_seconds=_UPDATE_SETUP_DEADLINE_SECONDS,
                max_bytes=_UPDATE_SETUP_MAX_BYTES)
    digest = hashlib.sha256()
    total = 0
    with partial.open("rb") as source:
        if component_lock.assert_open_file_is_direct(partial, source) != identity:
            raise component_lock.UnsafeLockPathError("받는 중인 설치 파일이 다른 파일로 바뀌었습니다.")
        while True:
            chunk = source.read(_UPDATE_READ_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > _UPDATE_SETUP_MAX_BYTES:
                raise _UpdateDownloadTooLarge("UPDATE_SETUP_TOO_LARGE")
            digest.update(chunk)
        if component_lock.assert_open_file_is_direct(partial, source) != identity:
            raise component_lock.UnsafeLockPathError("받는 중인 설치 파일이 다른 파일로 바뀌었습니다.")
    return digest.hexdigest()


def _download_file(url: str, dest_dir, expected_sha256: str, opener=None, transport_trail=None):
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
    digest = hashlib.sha256()
    total = 0
    final_digest = None
    try:
        trail = transport_trail if transport_trail is not None else []
        # UPD-04: curl.exe first into an owned partial; Python once only if curl did not deliver.
        with partial.open("xb") as sink:
            partial_identity = component_lock.assert_open_file_is_direct(partial, sink)
        try:
            final_digest = _curl_download_into(url, partial, partial_identity)
            trail.append("curl-ok")
        except _UpdateCurlFailed as curl_error:
            trail.append(curl_error.label)
            if curl_error.label == "curl-too-large":
                # Same 256 MB cap on the Python path; do not download it twice.
                too_large = _UpdateDownloadTooLarge("UPDATE_SETUP_TOO_LARGE")
                _attach_update_transport(too_large, trail)
                raise too_large from None
            # Drop curl's partial (only if it is still the file we made) and give
            # Python a fresh owned name, so no curl bytes mix into its digest.
            component_lock.remove_owned_file(partial, partial_identity)
            partial = folder / f".{name}.{os.getpid()}.{uuid.uuid4().hex}.partial"
            partial_identity = None
        if final_digest is None:
            deadline = time.monotonic() + _UPDATE_SETUP_DEADLINE_SECONDS
            try:
                try:
                    response = opener(url, timeout=min(30, _UPDATE_SETUP_DEADLINE_SECONDS))
                except (ssl.SSLError, urllib.error.URLError):
                    raise
                except OSError as error:
                    raise urllib.error.URLError(error) from error
                with response as source:
                    _require_https_response(source, url)
                    with partial.open("xb") as sink:
                        partial_identity = component_lock.assert_open_file_is_direct(partial, sink)
                        while True:
                            if time.monotonic() >= deadline:
                                raise _UpdateDownloadTimeout("UPDATE_SETUP_TIMEOUT")
                            remaining = _UPDATE_SETUP_MAX_BYTES - total
                            try:
                                chunk = source.read(min(_UPDATE_READ_SIZE, remaining + 1))
                            except OSError as error:
                                # A socket read failure is not a local folder permission problem.
                                raise urllib.error.URLError(error) from error
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
                final_digest = digest.hexdigest()
                trail.append("python-ok")
            except Exception as error:
                # Both transports failed: the Python failure decides the screen message.
                trail.append(_python_transport_label(error))
                _attach_update_transport(error, trail)
                raise

        if final_digest != expected:
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
                    "reason": "앞서 Teacher Manager 설치 창을 열었어요. 작업표시줄에서 설치 창을 찾아 계속해 주세요. 설치 완료 여부는 아직 확인하지 않았습니다.",
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
                    "reason": "설치 창이 열렸는지 확인하지 못해 새 창을 열지 않았어요. 작업표시줄에서 Teacher Manager 설치 창을 확인해 주세요.",
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
                    "reason": "앞서 Teacher Manager 설치 창을 열었어요. 작업표시줄에서 설치 창을 찾아 계속해 주세요. 설치 완료 여부는 아직 확인하지 않았습니다.",
                }
            return {
                "started": False,
                "code": "UPDATE_SETUP_LAUNCH_UNCERTAIN",
                "latest": target_latest,
                "reason": "설치 창이 열렸는지 확인하지 못해 새 창을 열지 않았어요. 작업표시줄에서 Teacher Manager 설치 창을 확인해 주세요.",
            }

        if path is None:
            download_trail: list[str] = []
            try:
                path = _download_file(
                    target_url,
                    dest_dir,
                    target_sha256,
                    opener=opener,
                    transport_trail=download_trail,
                )
            except _UpdateDownloadHashMismatch:
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_HASH_MISMATCH",
                    "latest": target_latest,
                    "reason": "받은 설치 파일이 공식 파일과 달라 실행하지 않았어요.",
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
            except ssl.SSLError:
                return {
                    "started": False, "code": "UPDATE_DOWNLOAD_TLS_UNSAFE", "latest": target_latest,
                    "reason": "배포 사이트의 보안 인증서를 확인하지 못해 설치 파일을 받지 않았어요. 컴퓨터의 날짜·시간을 확인해 주세요.",
                }
            except urllib.error.HTTPError:
                return {
                    "started": False, "code": "UPDATE_DOWNLOAD_SERVER_FAILED", "latest": target_latest,
                    "reason": "배포 사이트에서 설치 파일을 받지 못했어요.",
                }
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if isinstance(getattr(error, "reason", None), ssl.SSLError):
                    return {
                        "started": False, "code": "UPDATE_DOWNLOAD_TLS_UNSAFE", "latest": target_latest,
                        "reason": "배포 사이트의 보안 인증서를 확인하지 못해 설치 파일을 받지 않았어요. 컴퓨터의 날짜·시간을 확인해 주세요.",
                    }
                transport = _safe_update_transport(">".join(download_trail))
                record_update_transport(config_dir, "download", transport)
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_UNAVAILABLE",
                    "latest": target_latest,
                    # Neither Python nor Windows curl.exe reached GitHub (UPD-01, C11).
                    "reason": _UPDATE_DOWNLOAD_UNREACHABLE,
                    **({"transport": transport} if transport else {}),
                }
            except OSError:
                return {
                    "started": False, "code": "UPDATE_DOWNLOAD_SAVE_FAILED", "latest": target_latest,
                    "reason": f"설치 파일을 이 폴더에 저장하지 못했어요: {dest_dir or Path(tempfile.gettempdir()) / 'TeacherManager-Update'}. 저장 공간과 폴더의 쓰기 권한을 확인해 주세요.",
                }
            except ValueError:
                return {
                    "started": False, "code": "UPDATE_DOWNLOAD_UNSAFE", "latest": target_latest,
                    "reason": "설치 파일을 공식 보안 주소에서 받았는지 확인하지 못해 실행하지 않았어요.",
                }
            except Exception:
                return {
                    "started": False, "code": "UPDATE_DOWNLOAD_FAILED", "latest": target_latest,
                    "reason": "설치 파일을 준비하지 못했어요. 실패 원인은 아직 확인하지 못했습니다.",
                }
            record_update_transport(config_dir, "download", _safe_update_transport(">".join(download_trail)))
            try:
                actual_sha256 = _file_sha256(path)
            except OSError:
                _remove_download(path)
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_UNAVAILABLE",
                    "latest": target_latest,
                    "reason": "받은 설치 파일을 읽지 못해 실행하지 않았어요.",
                }
            if actual_sha256 != target_sha256:
                _remove_download(path)
                return {
                    "started": False,
                    "code": "UPDATE_DOWNLOAD_HASH_MISMATCH",
                    "latest": target_latest,
                    "reason": "받은 설치 파일이 공식 파일과 달라 실행하지 않았어요.",
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


def attendance_ui_enabled() -> bool:
    """설치본에 출결 시험 화면이 명시적으로 들어간 경우에만 화면을 연다."""
    try:
        data = _json.loads((bundle_paths.bundle_root() / "release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("attendanceUiEnabled") is True


GOOGLE_LISTS_FINAL_FAILURE = "Calendar와 할 일 목록을 불러오지 못했어요."
GOOGLE_UNCHANGED = "Google 자료는 바꾸지 않았습니다."


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


GOOGLE_SAVED_TARGET_KINDS = {
    "업무캘린더ID": "calendar", "학사일정캘린더ID": "calendar",
    "업무Tasks목록ID": "tasklist", "담임안내Tasks목록ID": "tasklist",
}
# Shown in the 연결 window and on setup step 8; both have the ↻ refresh at the top.
GOOGLE_TARGET_CHECK_FAILED = "저장된 연결을 확인하지 못했어요. 선택은 그대로 두었어요. 위쪽 [연결 상태 새로고침] 버튼(↻)을 눌러 주세요."


@dataclass
class StepResult:
    key: str
    label: str
    status: str  # done | skipped | failed
    detail: str = ""


_ATTENDANCE_REMOTE_LOCK_TIMEOUT_SECONDS = 5.0
_ATTENDANCE_REMOTE_BUSY_MESSAGE = (
    "다른 Teacher Manager 창에서 출석부를 준비하거나 연결을 바꾸고 있어요. "
    "그 작업이 끝난 뒤 다시 눌러 주세요."
)


class AttendanceRemoteWorkBusyError(RuntimeError):
    """같은 출결 자료의 다른 원격 작업이 제한 시간 안에 끝나지 않음."""


@dataclass
class ApplyDeps:
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


def apply_all(config_dir: Path, profile_values: dict, grid: list, bridge_updates: dict,
              deps: ApplyDeps | None = None) -> list[StepResult]:
    """[모두 저장] — 출결을 건드리지 않고 화면의 설정만 저장한다."""
    deps = deps or ApplyDeps()
    config_dir = Path(config_dir)
    results: list[StepResult] = []

    # The bridge's account token has already fenced this call to the signed-in account.
    write_profile_values(config_dir, profile_values)
    results.append(StepResult("csv", "선생님·학교 저장", "done", "이 컴퓨터에 저장함"))

    write_timetable_grid(config_dir, grid)
    results.append(StepResult("xlsx", "시간표 저장", "done", "이 컴퓨터에 저장함"))

    parse_ok, parse_detail = run_parser(config_dir)
    results.append(StepResult("parse", "시간표와 하루 일과 적용", "done" if parse_ok else "failed", parse_detail))

    bridge = {key: value for key, value in bridge_updates.items() if key != "autostart"}
    save_bridge_settings(config_dir, bridge)
    if bridge_updates.get("autostart"):
        deps.enable_autostart()
    results.append(StepResult("bridge", "메신저 단축키 설정 저장", "done", "이 컴퓨터에 저장함"))

    if deps.stop_helper() and deps.start_helper():
        results.append(StepResult("helper", "메신저 단축키 적용", "done", "새 설정으로 실행 중"))
    else:
        results.append(StepResult("helper", "메신저 단축키 적용", "failed", "저장한 설정을 적용하지 못했어요."))
    return results


# D36: full export reuses the existing inline-text XLSX machinery.
_EXPORT_COLUMNS = {
    "students": {"number": "번호", "name": "이름", "email": "이메일", "status": "학적", "deletedAt": "삭제한 때"},
    "attendance": {"date": "날짜", "studentLabel": "번호+이름", "category": "구분", "kind": "종류", "reason": "사유", "period": "교시",
                   "reportStatus": "신고서", "attachmentStatus": "첨부", "source": "입력 방법", "deletedAt": "삭제한 때"},
    "messages": {"date": "보낼 날짜", "channel": "개인톡·단체톡", "studentLabel": "받는 학생", "type": "종류", "text": "내용",
                 "status": "상태", "sentAt": "보낸 때", "result": "결과", "deletedAt": "삭제한 때"},
    "sendLog": {"at": "때", "kind": "종류", "targetLabel": "받는 사람", "spaceLabel": "단톡방", "preview": "내용", "result": "결과", "error": "오류"},
    "greetings": {"studentId": "학생", "state": "상태", "sentAt": "보낸 때", "result": "결과"},
    "holidays": {"id": "날짜", "name": "명칭", "type": "구분", "status": "상태", "note": "비고", "source": "출처", "deletedAt": "삭제한 때"},
    "settings": {"schoolName": "학교 이름", "grade": "학년", "classNumber": "반", "classLabel": "반 이름", "teacherName": "담임 이름",
                 "reportTemplateName": "결석신고서 양식", "taskListTitle": "할 일 목록"},
}


def write_attendance_export(path: Path, data: dict) -> None:
    labels = {"students": "학생명단", "attendance": "출결 기록", "messages": "메시지", "sendLog": "발송기록", "effects": "처리결과", "greetings": "환영 인사", "holidays": "휴일", "settings": "학급 설정"}
    sheets = []
    for year in data["years"]:
        for key, label in labels.items():
            values = [year[key]] if key == "settings" else year.get(key, [])
            fields = {field for value in values for field in value}
            # Teacher-facing columns first under Korean names; every other field follows, unchanged.
            known = [field for field in _EXPORT_COLUMNS.get(key, ()) if field in fields]
            columns = known + sorted(fields - set(known))
            rows = [[_EXPORT_COLUMNS[key][field] if field in known else field for field in columns]] + [[_json.dumps(value.get(field), ensure_ascii=False, sort_keys=True) if isinstance(value.get(field), (dict, list)) else ("" if value.get(field) is None else str(value.get(field))) for field in columns] for value in values]
            sheets.append((f"{year['year']} {label}", rows))
    if not sheets:
        sheets = [("출결 자료", [["저장된 학년도 자료가 없어요."]])]
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    content = parse_settings._xlsx_content_types().replace('<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>', ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1)))
    workbook = f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets)+1)) + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    # Replace only after the complete zip closes. Cancel/failure never leaves a partial workbook.
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as temporary:
        temp_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temp_path, 'w', compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr('[Content_Types].xml', content)
            target.writestr('_rels/.rels', parse_settings._xlsx_root_rels())
            target.writestr('xl/workbook.xml', workbook)
            target.writestr('xl/_rels/workbook.xml.rels', rels)
            target.writestr('xl/styles.xml', parse_settings._xlsx_styles())
            for i, (_, rows) in enumerate(sheets, 1):
                row_xml = []
                for row_no, values in enumerate(rows, 1):
                    cells = []
                    for column, value in enumerate(values, 1):
                        # Excel limits one cell to 32767 characters. Refuse, never silently truncate data.
                        if len(value) > 32767 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
                            raise ValueError("한 항목의 내용이 너무 길어 전체 자료를 저장하지 못했어요.")
                        cells.append(f'<c r="{parse_settings._column_name(column)}{row_no}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
                    row_xml.append(f'<row r="{row_no}">{"".join(cells)}</row>')
                target.writestr(f'xl/worksheets/sheet{i}.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(row_xml)}</sheetData></worksheet>')
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
