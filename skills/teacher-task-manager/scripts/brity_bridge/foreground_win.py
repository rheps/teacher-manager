from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def foreground_process_name() -> str:
    """앞 창을 가진 프로세스의 실행 파일 이름을 소문자로 돌려준다. 실패하면 빈 문자열."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Configure Win32 function signatures for 64-bit pointer handling
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)
