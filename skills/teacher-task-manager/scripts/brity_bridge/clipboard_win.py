from __future__ import annotations

import ctypes
import time
from ctypes import c_void_p, wintypes
from dataclasses import dataclass

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


@dataclass
class ClipboardSnapshot:
    text: str | None
    html: bytes | None


def _user32():
    """Get user32 DLL with configured function signatures."""
    user32 = ctypes.windll.user32

    # Configure GetClipboardData
    if not hasattr(user32.GetClipboardData, '_configured'):
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = c_void_p
        user32.GetClipboardData._configured = True

    # Configure SetClipboardData
    if not hasattr(user32.SetClipboardData, '_configured'):
        user32.SetClipboardData.argtypes = [wintypes.UINT, c_void_p]
        user32.SetClipboardData.restype = c_void_p
        user32.SetClipboardData._configured = True

    return user32


def _kernel32():
    """Get kernel32 DLL with configured function signatures."""
    kernel32 = ctypes.windll.kernel32

    # GlobalAlloc
    if not hasattr(kernel32.GlobalAlloc, '_configured'):
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = c_void_p
        kernel32.GlobalAlloc._configured = True

    # GlobalLock/Unlock/Free/Size
    if not hasattr(kernel32.GlobalLock, '_configured'):
        kernel32.GlobalLock.argtypes = [c_void_p]
        kernel32.GlobalLock.restype = c_void_p
        kernel32.GlobalLock._configured = True

    if not hasattr(kernel32.GlobalUnlock, '_configured'):
        kernel32.GlobalUnlock.argtypes = [c_void_p]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalUnlock._configured = True

    if not hasattr(kernel32.GlobalFree, '_configured'):
        kernel32.GlobalFree.argtypes = [c_void_p]
        kernel32.GlobalFree.restype = c_void_p
        kernel32.GlobalFree._configured = True

    if not hasattr(kernel32.GlobalSize, '_configured'):
        kernel32.GlobalSize.argtypes = [c_void_p]
        kernel32.GlobalSize.restype = ctypes.c_size_t
        kernel32.GlobalSize._configured = True

    return kernel32


def _html_format_id() -> int:
    return _user32().RegisterClipboardFormatW("HTML Format")


def _open_clipboard(retries: int = 10) -> bool:
    for _ in range(retries):
        if _user32().OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def sequence_number() -> int:
    return _user32().GetClipboardSequenceNumber()


def _read_text_locked() -> str | None:
    handle = _user32().GetClipboardData(CF_UNICODETEXT)
    if not handle:
        return None
    pointer = _kernel32().GlobalLock(handle)
    if not pointer:
        return None
    try:
        # wstring_at without size will read until null terminator
        return ctypes.wstring_at(pointer)
    except OSError:
        # Clipboard may be in an invalid state
        return None
    finally:
        _kernel32().GlobalUnlock(handle)


def _read_bytes_locked(format_id: int) -> bytes | None:
    handle = _user32().GetClipboardData(format_id)
    if not handle:
        return None
    pointer = _kernel32().GlobalLock(handle)
    if not pointer:
        return None
    try:
        size = _kernel32().GlobalSize(handle)
        return ctypes.string_at(pointer, size).rstrip(b"\x00")
    finally:
        _kernel32().GlobalUnlock(handle)


def read_snapshot() -> ClipboardSnapshot:
    if not _open_clipboard():
        return ClipboardSnapshot(text=None, html=None)
    try:
        return ClipboardSnapshot(text=_read_text_locked(), html=_read_bytes_locked(_html_format_id()))
    finally:
        _user32().CloseClipboard()


def _set_text_locked(text: str) -> None:
    # Allocate memory for text (size in bytes, including null terminator)
    text_bytes = (text + '\0').encode('utf-16-le')
    size = len(text_bytes)

    handle = _kernel32().GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        raise OSError("GlobalAlloc failed")

    pointer = _kernel32().GlobalLock(handle)
    if not pointer:
        _kernel32().GlobalFree(handle)
        raise OSError("GlobalLock failed")

    try:
        ctypes.memmove(pointer, text_bytes, len(text_bytes))
    finally:
        _kernel32().GlobalUnlock(handle)

    _user32().SetClipboardData(CF_UNICODETEXT, handle)


def _set_bytes_locked(format_id: int, raw: bytes) -> None:
    data = raw + b"\x00"
    size = len(data)

    handle = _kernel32().GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        raise OSError("GlobalAlloc failed")

    pointer = _kernel32().GlobalLock(handle)
    if not pointer:
        _kernel32().GlobalFree(handle)
        raise OSError("GlobalLock failed")

    try:
        ctypes.memmove(pointer, data, size)
    finally:
        _kernel32().GlobalUnlock(handle)

    _user32().SetClipboardData(format_id, handle)


def restore_snapshot(snapshot: ClipboardSnapshot) -> None:
    if not _open_clipboard():
        return
    try:
        _user32().EmptyClipboard()
        if snapshot.text is not None:
            _set_text_locked(snapshot.text)
        if snapshot.html is not None:
            _set_bytes_locked(_html_format_id(), snapshot.html)
    finally:
        _user32().CloseClipboard()


def write_text_for_test(text: str) -> None:
    restore_snapshot(ClipboardSnapshot(text=text, html=None))


def write_html_for_test(text: str, html_raw: bytes) -> None:
    restore_snapshot(ClipboardSnapshot(text=text, html=html_raw))
