"""동봉 자료(assets·templates·release.json)의 기준점 — 개발/설치본 공용.

설치본(PyInstaller onedir)에서는 sys._MEIPASS(=_internal)가 스킬 루트 레이아웃을
그대로 담는다. 개발에서는 skills/teacher-task-manager가 그 루트다.
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent / "_internal"))
    return Path(__file__).resolve().parent.parent.parent  # …/skills/teacher-task-manager


def helper_executable() -> Path:
    """설치본에서 도우미 실행파일 — TeacherManager.exe 옆에 있다."""
    return Path(sys.executable).with_name("TeacherManagerHelper.exe")


def dashboard_executable() -> Path:
    """설치본에서 대시보드 실행파일 — TeacherManagerHelper.exe 옆에 있다."""
    return Path(sys.executable).with_name("TeacherManager.exe")


def _package_name_status() -> int:
    import ctypes

    length = ctypes.c_uint32(0)
    return int(ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None))


def is_packaged(query=None) -> bool:
    """MS 스토어(MSIX) 패키지로 실행 중인지 — 15700(패키지 없음)이면 일반 실행이다."""
    if query is None:
        if sys.platform != "win32":
            return False
        query = _package_name_status
    try:
        return query() != 15700  # APPMODEL_ERROR_NO_PACKAGE
    except Exception:  # noqa: BLE001 - 감지 실패는 일반 실행으로 취급
        return False
