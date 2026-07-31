# skills/teacher-task-manager/scripts/dashboard/engine.py
from __future__ import annotations

import csv
import hashlib
import json as _json
import os
import platform
import subprocess
import tempfile
import threading
import zipfile
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

import install_attendance_automation
import parse_settings
from attendance_install_record import (
    AttendanceInstallRecordError,
    load_attendance_install_record,
)
from brity_bridge import autostart_win, bundle_paths, hotkey_win, paths, process_win
from brity_bridge.doctor import CheckResult, DoctorDeps, _default_run_command, _extract_email, run_doctor_checks
from brity_bridge.gemini_analyze import check_gemini_key
from brity_bridge.hotkey import MODIFIER_ORDER, parse_hotkey
from brity_bridge.settings import ALLOWED_GEMINI_MODELS, load_settings, save_settings

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
    ("installer", "자동 설치 도구(winget)"),
    ("python", "Python"),
    ("node", "Node.js"),
    ("documents", "문서 읽기 도구"),
    ("screen", "화면 표시"),
]

# 출결 준비 상태를 홈 점검 한 건으로 바꾸는 규칙 — 로그인·개인 설정 문제는
# 설정/내 정보 카드가 이미 소유하므로 출결에서 다시 문제로 세지 않는다.
_ATTENDANCE_STATE_TO_OK = {
    "ready": True,
    "not-ready": False,
    "failed": False,
    "gws-required": None,
    "login-required": None,
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
    """Gemini 키를 넣을 시트를 고른다. AI 출결 입력을 실제로 쓰는 곳은 사본이다."""
    config_dir = Path(config_dir)

    def _sheet_id_in(path: Path, key: str) -> str:
        # 파일이 없거나 깨진 것만 넘어간다. 그 밖의 잘못은 감추지 않고 드러낸다.
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        try:
            saved = _json.loads(text)
        except ValueError:
            return ""
        if not isinstance(saved, dict):
            return ""
        return str(saved.get(key, "") or "").strip()

    import prepare_attendance_copy

    copy_id = _sheet_id_in(
        prepare_attendance_copy.attendance_copy_status_path(config_dir),
        "copy_spreadsheet_id",
    )
    if copy_id:
        return copy_id
    return _sheet_id_in(
        paths.attendance_install_record_path(config_dir), "spreadsheet_id"
    )


def _attendance_copy_url(config_dir: Path, record_spreadsheet_id: str = "") -> str:
    """사본 전환이 끝났으면 사본 시트 주소를 돌려준다. 아니면 빈 문자열.

    사본은 **지금 기록의 사본일 때만** 따라간다. 새 출석부로 바뀐 뒤에도 옛 사본
    상태 파일이 남아 있을 수 있는데, 그때 [열기]가 옛 사본을 열면 안 된다(2026-07-30).
    """
    import prepare_attendance_copy

    path = prepare_attendance_copy.attendance_copy_status_path(Path(config_dir))
    try:
        saved = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(saved, dict) or saved.get("state") != "complete":
        return ""  # 만들다 만 사본은 아직 쓰는 시트가 아니다
    source = str(saved.get("source_spreadsheet_id", "") or "").strip()
    if record_spreadsheet_id and source and source != str(record_spreadsheet_id):
        return ""  # 다른 출석부의 사본이다
    url = str(saved.get("copy_spreadsheet_url", "") or "").strip()
    return url if url.startswith("https://docs.google.com/spreadsheets/d/") else ""


def push_gemini_key_to_attendance_sheet(config_dir: Path, run_command=None) -> dict:
    """이 컴퓨터에 저장된 Gemini 키를 출결 시트 `설정` 탭에 적어 둔다.

    시트 안 Apps Script는 이 컴퓨터의 settings.json을 읽지 못한다. 여기서 넣어 두지 않으면
    선생님이 시트에서 같은 키를 또 붙여넣게 된다. 넣지 못해도 오류를 밖으로 던지지 않는다 —
    저장 버튼 하나가 인터넷 사정 때문에 실패한 것처럼 보이면 안 된다.
    """
    from dashboard import central_chat

    config_dir = Path(config_dir)
    key = str(load_settings(paths.settings_path(config_dir)).gemini_api_key or "").strip()
    if not key:
        return {"state": "skipped", "detail": "아직 Gemini API key를 넣지 않았어요."}
    spreadsheet_id = _attendance_sheet_for_gemini_key(config_dir)
    if not spreadsheet_id:
        return {"state": "skipped", "detail": "아직 출결 시트를 만들지 않았어요."}
    runner = run_command or central_chat._default_run_command
    try:
        rows = central_chat._read_settings_rows(spreadsheet_id, runner)
        central_chat._upsert_settings_value(
            spreadsheet_id, rows, "GEMINI_API_KEY", key, runner
        )
    except Exception as error:  # noqa: BLE001 - 화면에는 한국어 한 문장만 보인다
        return {"state": "failed", "detail": f"출결 시트에 넣지 못했어요: {error}"[:200]}
    return {"state": "ok", "detail": "출결 시트 설정 탭에도 넣었어요."}


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
            except Exception as error:  # noqa: BLE001 - 저장 자체는 이미 끝났다
                sheet_push = {"state": "failed", "detail": str(error)[:200]}
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
    state: str             # ready | not-ready | gws-required | login-required | profile-required | failed
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


ATTENDANCE_ERROR_MESSAGES = {
    "sheet": "Google Sheet를 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
    "docs": "Google Docs 양식을 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
    "tasks": "Google Tasks 목록을 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
    "setup": "출결 자료를 준비하지 못했어요. 설정에서 Google 연결을 다시 점검한 뒤 다시 시도해 주세요.",
}
ATTENDANCE_GWS_MESSAGE = "Google Workspace CLI가 아직 준비되지 않았어요. 설정에서 준비해 주세요."
ATTENDANCE_LOGIN_MESSAGE = "설정에서 학교 Google 계정으로 로그인해 주세요."
ATTENDANCE_PROFILE_MESSAGE = "내 정보와 하루 일과를 먼저 입력해 주세요."
ATTENDANCE_ACCOUNT_MESSAGE = "처음 준비하던 Google 계정으로 다시 로그인해 주세요."
ATTENDANCE_RECORD_BROKEN_MESSAGE = "출결 설치 기록을 읽지 못했어요. 이전에 만든 출결 시트를 확인해 주세요."
ATTENDANCE_NOT_READY_MESSAGE = "출결 준비 시작하기를 누르면 로그인한 계정에 자동으로 준비해요."
ATTENDANCE_DETAIL_LIMIT = 800  # 화면에 보여줄 실패 안내 길이 상한


def friendly_attendance_error(error) -> tuple[str, str]:
    """설치 실패를 (실패한 서비스, 화면에 보여줄 쉬운 문장)으로 바꾼다."""
    # 쓰던 시트 때문에 멈춘 경우는 이유가 시트마다 다르다. 뭉뚱그린 문장으로 바꾸면
    # 무엇을 고쳐야 할지 알 수 없으므로 적어 둔 문장을 그대로 보여준다.
    if isinstance(error, install_attendance_automation.ExistingAttendanceSheetError):
        return "sheet", str(error)
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
    try:
        saved = _json.loads((Path(config_dir) / ATTENDANCE_STATUS_CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and saved.get("state") else None


def _profile_school_year(config_dir: Path) -> str:
    """profile.generated.json의 school.year — 없거나 빈 값이면 지금 학년도로 본다."""
    profile = _read_json_dict(paths.profile_path(Path(config_dir))) or {}
    school = profile.get("school")
    year = str((school or {}).get("year", "") or "").strip() if isinstance(school, dict) else ""
    return year or install_attendance_automation.current_school_year()


def read_attendance_status(config_dir: Path, run_command=_default_run_command) -> AttendanceStatus:
    config_dir = Path(config_dir)
    setup_status = _read_setup_status(config_dir)
    account = str(setup_status.get("account", "") or "")
    gws = resolve_gws(run_command)
    if not gws:
        return AttendanceStatus(state="gws-required", account=account, detail=ATTENDANCE_GWS_MESSAGE)
    auth = gws_auth_status(run_command, gws)
    current_user = str(auth.get("user", "") or "")
    if not auth.get("logged_in"):
        return AttendanceStatus(state="login-required", account=account, detail=ATTENDANCE_LOGIN_MESSAGE)
    record_path = paths.attendance_install_record_path(config_dir)
    if record_path.exists():
        if account and current_user and account != current_user:
            return AttendanceStatus(
                state="failed", account=account, current_user=current_user,
                failed_service="setup", detail=ATTENDANCE_ACCOUNT_MESSAGE,
            )
        try:
            record = load_attendance_install_record(record_path)
        except AttendanceInstallRecordError:
            return AttendanceStatus(
                state="failed", account=account, current_user=current_user,
                failed_service="setup", detail=ATTENDANCE_RECORD_BROKEN_MESSAGE,
            )
        spreadsheet_url = str(record.get("spreadsheet_url", "") or "")
        # 사본 전환이 끝났으면 실제로 쓰는 시트는 사본이다. 원본 주소를 그대로 열면
        # 더 이상 쓰지 않는 옛 시트가 열린다(central_chat._spreadsheet_in_use와 같은 규칙).
        copy_url = _attendance_copy_url(config_dir, str(record.get("spreadsheet_id", "") or ""))
        if copy_url:
            spreadsheet_url = copy_url
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
        if valid_sheet_url:
            return AttendanceStatus(
                state="ready", account=account or current_user, current_user=current_user,
                spreadsheet_url=spreadsheet_url, template_doc_url=template_doc_url,
                school_year=record_year, workbook_name=workbook_name, year_mismatch=year_mismatch,
            )
        return AttendanceStatus(
            state="failed", account=account, current_user=current_user,
            spreadsheet_url=spreadsheet_url, template_doc_url=template_doc_url,
            failed_service="setup", detail=ATTENDANCE_RECORD_BROKEN_MESSAGE,
            school_year=record_year, workbook_name=workbook_name, year_mismatch=year_mismatch,
        )
    profile_error = _attendance_profile_error(config_dir)
    if profile_error:
        return AttendanceStatus(state="profile-required", current_user=current_user, detail=profile_error)
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
    record = _read_json_dict(record_path)
    if record is None:
        return False  # 깨진 기록은 여기서 고치지 않는다 — read_attendance_status가 이미 failed로 알린다
    if str(record.get("school_year", "") or "").strip():
        return False
    profile = _read_json_dict(paths.profile_path(config_dir)) or {}
    homeroom = profile.get("homeroom")
    homeroom = homeroom if isinstance(homeroom, dict) else {}
    record["school_year"] = install_attendance_automation.current_school_year()
    record["homeroom_grade"] = str(homeroom.get("grade", "") or "")
    record["homeroom_class"] = str(homeroom.get("class", "") or "")
    _atomic_write_json(record_path, record)
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
        output = parse_settings.parse_config_dir(Path(config_dir), require_links=require_links)
    except ValueError as error:
        return False, str(error)
    return True, str(output)


# SKILL.md의 전체 scope 그대로 — 축소 로그인이 Calendar/Tasks를 깨뜨린 전례가 있다.
GWS_LOGIN_SCOPES = (
    "email,profile,openid,"
    "https://www.googleapis.com/auth/calendar,"
    "https://www.googleapis.com/auth/drive,"
    "https://www.googleapis.com/auth/documents,"
    "https://www.googleapis.com/auth/spreadsheets,"
    "https://www.googleapis.com/auth/tasks,"
    "https://www.googleapis.com/auth/script.projects,"
    "https://www.googleapis.com/auth/script.deployments,"
    "https://www.googleapis.com/auth/script.container.ui"
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
    winget = check_version(run_command, "winget")
    node = check_version(run_command, "node")
    document_ok, document_detail = (document_probe or _document_reader_probe)()
    return {
        "installer": _readiness(bool(winget), winget or "자동 설치 기능을 찾지 못했어요"),
        "python": _readiness(True, platform.python_version()),
        "node": _readiness(bool(node), node or "설치 필요"),
        "documents": _readiness(document_ok, document_detail),
        "screen": _readiness(True, "이 화면을 정상 표시하고 있어요"),
    }


def resolve_gws(run_command=_default_run_command) -> str:
    for candidate in ("gws", "gws.cmd"):
        code, _output = run_command([candidate, "--help"])
        if code == 0:
            return candidate
    return ""


_WINGET_QUIET = ["--silent", "--accept-package-agreements", "--accept-source-agreements"]


def resolve_npm(run_command=_default_run_command) -> str:
    for candidate in ("npm", "npm.cmd"):
        code, _output = run_command([candidate, "--version"])
        if code == 0:
            return candidate
    return ""


def install_node(run_command=_default_run_command) -> tuple[bool, str]:
    code, output = run_command(
        ["winget", "install", "--id", "OpenJS.NodeJS.LTS", "-e", *_WINGET_QUIET]
    )
    return code == 0, output.strip()[-300:]


def install_gws(run_command=_default_run_command) -> tuple[bool, str]:
    npm = resolve_npm(run_command)
    if not npm:
        return False, "Node.js가 아직 없어요. 먼저 Node.js를 설치해 주세요"
    code, output = run_command([npm, "install", "-g", "@googleworkspace/cli"])
    return code == 0, output.strip()[-300:]


def gws_auth_status(run_command, gws: str) -> dict:
    code, output = run_command([gws, "auth", "status"])
    email = _extract_email(output)
    return {"logged_in": code == 0 and bool(email), "user": email, "raw": output}


def gws_logout(run_command, gws: str) -> tuple[bool, str]:
    if not gws:
        return False, "Google Workspace CLI가 아직 준비되지 않았어요"
    code, output = run_command([gws, "auth", "logout"])
    return code == 0, output.strip()[-300:]


_GWS_AUTH_URL_MARK = "https://accounts.google.com"


def login_command(gws: str) -> list[str]:
    return [gws, "auth", "login", "--scopes", GWS_LOGIN_SCOPES]


class LoginSession:
    """gws auth login을 백그라운드로 돌리며 URL·결과를 폴링용으로 내놓는다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._url = ""
        self._lines: list[str] = []
        self._ok: bool | None = None

    def start(self, args: list[str], popen=None) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return  # 이미 진행 중 — 멱등
            self._proc = process_win.popen_hidden(
                args, popen=popen or subprocess.Popen,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            self._url = ""
            self._lines = []
            self._ok = None
        threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            with self._lock:
                self._lines.append(line)
                if not self._url and _GWS_AUTH_URL_MARK in line:
                    for token in line.split():
                        if token.startswith(_GWS_AUTH_URL_MARK):
                            self._url = token
                            break
        code = proc.wait()
        with self._lock:
            self._ok = code == 0

    def snapshot(self) -> dict:
        with self._lock:
            running = self._proc is not None and self._ok is None
            return {
                "running": running,
                "url": self._url,
                "ok": self._ok,
                "detail": "".join(self._lines).strip()[-300:],
            }

    def cancel(self) -> bool:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        proc.kill()
        return True


_LOGIN_SETUP_GUIDE = (
    "이 컴퓨터에 Google 로그인 준비 파일(OAuth 클라이언트)이 아직 없는 것 같아요. "
    "배포자에게 gws-oauth-client.json 파일을 받아 내 폴더의 TeacherTaskManager "
    "폴더에 넣은 뒤 다시 로그인해 주세요."
)


def annotate_login_snapshot(snap: dict, environ=os.environ) -> dict:
    """로그인 URL도 못 낸 실패 = OAuth 클라이언트 부재 신호 — 한국어 준비 안내를 붙인다.

    URL이 나온 뒤의 실패(사용자가 창을 닫음 등)나 클라이언트 파일이 이미
    물려 있는 실패는 gws 원문이 더 유용하므로 그대로 둔다.
    """
    if snap.get("ok") is not False or snap.get("url"):
        return snap
    if environ.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"):
        return snap
    detail = str(snap.get("detail", "")).strip()
    tail = (" · " + detail[-120:]) if detail else ""
    return {**snap, "detail": _LOGIN_SETUP_GUIDE + tail}


# 배포 저장소(rheps/teacher-manager)의 최신 Release에 붙은 version.json을 본다.
UPDATE_INFO_URL = "https://github.com/rheps/teacher-manager/releases/latest/download/version.json"


def _fetch_update_json() -> dict:
    import urllib.request

    with urllib.request.urlopen(UPDATE_INFO_URL, timeout=3) as response:
        _require_https_response(response, UPDATE_INFO_URL)
        return _json.loads(response.read().decode("utf-8-sig"))  # BOM이 붙어 있어도 읽는다


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
        raise ValueError("unsafe final update URL")


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
    except ValueError:
        return _empty_update("failed", "배포 정보의 버전 모양이 올바르지 않아요.")
    except Exception:  # noqa: BLE001 - 실행은 계속하되 화면에는 확인 실패를 정확히 알린다
        return _empty_update("failed", "업데이트 확인을 하지 못했어요. 인터넷 연결을 확인해 주세요.")

    if not newer:
        return _empty_update("latest", "", latest)
    if not _valid_https_update_url(url):
        return _empty_update("failed", "업데이트 주소가 공식 배포 주소(GitHub https)가 아니에요.", latest)
    if not sha256:
        return _empty_update("failed", "설치 파일 확인값이 없어 업데이트할 수 없어요.", latest)
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


def _download_file(url: str, dest_dir, opener=None):
    import urllib.request

    opener = opener or (lambda target, timeout=None: urllib.request.urlopen(target, timeout=timeout))
    folder = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir()) / "TeacherManager-Update"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / _safe_download_name(url)
    try:
        with opener(url, timeout=30) as source:
            _require_https_response(source, url)
            with open(target, "wb") as sink:
                while True:
                    chunk = source.read(1024 * 256)
                    if not chunk:
                        break
                    sink.write(chunk)
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return target


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while True:
            chunk = source.read(1024 * 256)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _remove_download(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


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
    return os.path.normcase(str(Path(config_dir).resolve()))


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


def _try_lock_update_file(lock_file) -> bool:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
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
    base = Path(config_dir) if config_dir else Path(tempfile.gettempdir()) / "TeacherManager-Update"
    key = _update_lock_key(base)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
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
            lock_file = (base / ".update-run.lock").open("a+b")
        except OSError:
            yield lease
            return
        if not _try_lock_update_file(lock_file):
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


# 설치 파일에게 "마법사 없이 돌아라"라고 알리는 표시.
# /SILENT은 진행 창 하나만 남기고 마법사 화면을 전부 없앤다(/VERYSILENT은 그 창까지 감춘다).
# /CLOSEAPPLICATIONS은 켜져 있는 프로그램을 설치 프로그램이 닫게 하고,
# /NORESTART은 컴퓨터를 다시 켜라는 창이 뜨지 않게 한다.
SILENT_UPDATE_ARGS = ["/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", "/SUPPRESSMSGBOXES"]


def update_launch_command(path) -> list[str]:
    """업데이트용 설치 파일 실행 명령을 만든다."""
    return [str(path), *SILENT_UPDATE_ARGS]


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
            path = _download_file(target_url, dest_dir, opener=opener)
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

        try:
            # 설치 창은 눈에 보여야 하므로 숨김 실행을 쓰지 않는다.
            # 마법사만 없앤다 — 진행 창 하나는 그대로 뜬다.
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


def ai_skills_install(keys, run_command=None, enabled=None) -> dict:
    """npx skills add — 체크한 AI 도구에만 스킬을 연결한다."""
    allowed = ai_skill_install_enabled() if enabled is None else enabled is True
    if not allowed:
        return {"success": False, "detail": "AI 연결 기능은 공개 준비 중이에요."}
    selected = {str(key) for key in (keys or [])}
    agents = [agent for tool in AI_TOOLS if tool["key"] in selected for agent in tool["agents"]]
    if not agents:
        return {"success": False, "detail": "연결할 AI를 하나 이상 선택해 주세요."}
    args = ["--yes", "skills", "add", AI_SKILL_REPO, "-g", "-y", "--skill", "*"]
    for agent in agents:
        args += ["-a", agent]
    run = run_command or (lambda a, timeout=None: process_win.run_captured(a, timeout=timeout))
    code, output = run(["npx", *args], timeout=600)
    if code == 127:  # Windows에서 npx가 .cmd로만 잡히는 경우
        code, output = run(["npx.cmd", *args], timeout=600)
    tail = str(output or "").strip()[-200:]
    if code == 0:
        return {"success": True, "detail": tail}
    return {"success": False, "detail": tail or "연결 명령을 실행하지 못했어요."}


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
    reply = _run_gws_json(
        run_command,
        [gws, "calendar", "calendarList", "list", "--params", '{"maxResults":250}', "--format", "json"],
        LIST_CALENDARS_FAILURE,
    )
    return [{"id": item.get("id", ""), "name": item.get("summary", "")} for item in reply.get("items", [])]


def list_tasklists(run_command, gws: str) -> list[dict]:
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


@dataclass
class AttendanceDeps:
    run_command: object = _default_run_command
    attendance_runner: object = install_attendance_automation.default_runner
    attendance_installer: object = install_attendance_automation.install_attendance_automation
    write_record: object = install_attendance_automation.write_install_record


@dataclass
class ApplyDeps(AttendanceDeps):
    start_helper: object = start_helper
    stop_helper: object = stop_helper
    enable_autostart: object = autostart_win.enable_autostart


_ATTENDANCE_THREAD_LOCKS: dict[str, threading.Lock] = {}
_ATTENDANCE_THREAD_LOCKS_GUARD = threading.Lock()


def _attendance_thread_lock(config_dir: Path) -> threading.Lock:
    key = os.path.normcase(str(Path(config_dir).resolve()))
    with _ATTENDANCE_THREAD_LOCKS_GUARD:
        return _ATTENDANCE_THREAD_LOCKS.setdefault(key, threading.Lock())


def _lock_attendance_file(file) -> None:
    if sys.platform == "win32":
        import msvcrt

        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write(b"\0")
            file.flush()
        while True:
            try:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_EX)


def _unlock_attendance_file(file) -> None:
    if sys.platform == "win32":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def attendance_setup_lock(config_dir: Path):
    """같은 설정 폴더의 출결 준비는 프로그램 창이 여러 개여도 한 번씩 실행한다."""
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    local_lock = _attendance_thread_lock(config_dir)
    with local_lock:
        lock_path = config_dir / ".attendance-setup.lock"
        with lock_path.open("a+b") as lock_file:
            _lock_attendance_file(lock_file)
            try:
                yield
            finally:
                _unlock_attendance_file(lock_file)


def ensure_attendance(config_dir: Path, deps: AttendanceDeps | None = None) -> AttendanceStatus:
    """저장 버튼 한 번으로 출결 자료를 처음 한 번만 준비한다. 반복 호출해도 중복 생성하지 않는다."""
    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    with attendance_setup_lock(config_dir):
        return _ensure_attendance_once(config_dir, deps)


def _ensure_attendance_once(config_dir: Path, deps: AttendanceDeps) -> AttendanceStatus:
    status = read_attendance_status(config_dir, deps.run_command)
    if status.state in ("gws-required", "login-required", "profile-required", "ready"):
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
        )
    except Exception as error:  # noqa: BLE001 - 설치 실패는 쉬운 문장으로 바꿔 화면에 보여준다
        failed_service, message = friendly_attendance_error(error)
        # 쓰던 시트를 찾았을 때의 안내는 시트 주소와 비어 있는 값 이름까지 담기므로
        # 200자에서 자르면 정작 필요한 뒷부분이 잘려 나간다.
        detail = message[:ATTENDANCE_DETAIL_LIMIT]
        _write_setup_status(config_dir, {
            "state": "failed", "account": current_user, "failed_service": failed_service,
            "detail": detail, "progress": saved_progress,
        })
        return AttendanceStatus(
            state="failed", account=current_user, current_user=current_user,
            failed_service=failed_service, detail=detail,
        )
    deps.write_record(profile_json, result)
    _write_setup_status(config_dir, {"state": "ready", "account": current_user})
    return AttendanceStatus(
        state="ready", account=current_user, current_user=current_user,
        spreadsheet_url=result.spreadsheet_url, template_doc_url=result.template_doc_url,
        created=True,
        school_year=_profile_school_year(config_dir),
        workbook_name=getattr(result, "workbook_name", "") or "",
        year_mismatch=False,
    )


def start_new_attendance(config_dir: Path, deps: AttendanceDeps | None = None) -> AttendanceStatus:
    """새 학년도용 새 출석부 — 기존 기록은 지우지 않고 보관한 뒤 처음부터 다시 만든다.

    기존 출석부(시트·문서·폴더)는 Drive에 그대로 남는다. 로그인·프로필 준비가
    안 됐으면 아무것도 옮기지 않고 그 상태만 알려준다. 기록이 이미 없으면
    (직전 시도 실패 등) 보관 없이 이어서 설치한다 — 진행 기록으로 재개된다.
    """
    deps = deps or AttendanceDeps()
    config_dir = Path(config_dir)
    with attendance_setup_lock(config_dir):
        status = read_attendance_status(config_dir, deps.run_command)
        if status.state in ("gws-required", "login-required", "profile-required"):
            return status
        record_path = paths.attendance_install_record_path(config_dir)
        if record_path.exists():
            # 새로 만드는 출석부 이름에는 학년도가 새겨진다(attendance_workbook_name) —
            # 옛 기록의 이름을 바꿔 둘 필요 없이, 쓰던 기록만 보관하면 이름이 저절로 갈린다.
            _archive_attendance_files(
                config_dir,
                (record_path, paths.attendance_setup_status_path(config_dir)),
            )
        return _ensure_attendance_once(config_dir, deps)


def _archive_attendance_files(config_dir: Path, sources) -> None:
    archive_dir = Path(config_dir) / "attendance-archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for source in sources:
        if not source.exists():
            continue
        target = archive_dir / f"{source.stem}-{stamp}{source.suffix}"
        counter = 0
        while target.exists():  # 같은 초에 두 번 눌러도 보관본을 덮지 않는다
            counter += 1
            target = archive_dir / f"{source.stem}-{stamp}-{counter}{source.suffix}"
        source.rename(target)


def apply_all(config_dir: Path, profile_values: dict, grid: list, bridge_updates: dict,
              deps: ApplyDeps | None = None) -> list[StepResult]:
    """[모두 저장하고 적용] — 유일한 적용 지점. 모든 단계는 멱등이다."""
    deps = deps or ApplyDeps()
    config_dir = Path(config_dir)
    results: list[StepResult] = []

    write_profile_values(config_dir, profile_values)
    results.append(StepResult("csv", "선생님·학교 저장", "done", "teacher-profile.csv"))

    write_timetable_grid(config_dir, grid)
    results.append(StepResult("xlsx", "시간표 저장", "done", "weekly-timetable.xlsx"))

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
    results.append(StepResult("bridge", "도우미 설정 저장", "done", "brity-bridge/settings.json"))

    if deps.stop_helper() and deps.start_helper():
        results.append(StepResult("helper", "도우미 재시작", "done", "새 설정으로 실행 중"))
    else:
        results.append(StepResult("helper", "도우미 재시작", "failed", "doctor를 실행해 주세요"))
    return results
