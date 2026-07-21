# skills/teacher-task-manager/scripts/brity_bridge/single_instance.py
from __future__ import annotations

DEFAULT_MUTEX_NAME = "Local\\BrityBridgeHelper"
_ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(name: str = DEFAULT_MUTEX_NAME):
    """명명 뮤텍스를 만든다. 이미 있으면 None — 도우미가 이미 실행 중이라는 뜻."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return None
    return handle


def release_single_instance(handle) -> None:
    if not handle:
        return
    import ctypes

    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
