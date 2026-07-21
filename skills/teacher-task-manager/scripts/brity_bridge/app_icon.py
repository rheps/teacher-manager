"""창 제목줄·작업표시줄·트레이에 쓰는 앱 아이콘 — 개발/설치본/스토어판 공용.

pywebview는 Windows에서 창 아이콘을 지정해 주지 않아 기본 아이콘이 뜬다.
창이 열린 뒤 WM_SETICON으로 우리 로고(teacher-manager.ico)를 직접 건다.

주의: 64비트 Windows에서 아이콘·창 핸들은 64비트다. ctypes 기본 restype는 c_int(32비트)라,
타입을 지정하지 않으면 핸들이 잘려서 아이콘이 안 걸린다 — 아래에서 restype/argtypes를 명시한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from brity_bridge import bundle_paths

WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IDI_APPLICATION = 32512
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x0010

_configured = False


def icon_path() -> Path:
    return bundle_paths.bundle_root() / "assets" / "teacher-manager.ico"


def _user32():
    """user32를 64비트 핸들에 맞는 타입으로 한 번만 설정해 돌려준다."""
    global _configured
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if not _configured:
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.SendMessageW.restype = wintypes.LPARAM
        user32.SendMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.LoadIconW.restype = wintypes.HANDLE  # argtypes는 두지 않는다(IDI_APPLICATION은 정수 리소스)
        _configured = True
    return user32


def load_hicon(size: int, *, load_image=None, path=None):
    """지정 크기의 아이콘 핸들 — 실패하면 None (호출자는 기본 아이콘으로 폴백)."""
    target = Path(path) if path else icon_path()
    if load_image is None:
        if sys.platform != "win32":
            return None
        load_image = _user32().LoadImageW
    try:
        handle = load_image(None, str(target), _IMAGE_ICON, size, size, _LR_LOADFROMFILE)
    except Exception:  # noqa: BLE001 - 아이콘 로드 실패가 실행을 막으면 안 된다
        return None
    return handle or None


def default_hicon():
    """윈도우 기본 프로그램 아이콘 — 우리 로고를 못 읽을 때만 쓰는 폴백."""
    if sys.platform != "win32":
        return None
    return _user32().LoadIconW(None, IDI_APPLICATION) or None


def apply_to_window(title: str, *, find_window=None, send_message=None, load_image=None) -> bool:
    """제목이 title인 창의 제목줄(작은)·작업표시줄(큰) 아이콘을 우리 로고로 바꾼다."""
    if find_window is None or send_message is None:
        if sys.platform != "win32":
            return False
        user32 = _user32()
        find_window = find_window or user32.FindWindowW
        send_message = send_message or user32.SendMessageW
    hwnd = find_window(None, title)
    if not hwnd:
        return False
    changed = False
    for kind, size in ((ICON_SMALL, 16), (ICON_BIG, 48)):
        handle = load_hicon(size, load_image=load_image)
        if handle:
            send_message(hwnd, WM_SETICON, kind, handle)
            changed = True
    return changed
