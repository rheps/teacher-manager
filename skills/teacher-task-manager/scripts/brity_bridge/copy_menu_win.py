from __future__ import annotations

import time
from dataclasses import dataclass

COPY_LABELS = ("복사", "copy")


@dataclass
class MenuOutcome:
    status: str  # copied | no-menu | no-copy-item | copy-disabled | not-win32-menu | timeout
    detail: str = ""


def _normalize_label(text: str) -> str:
    # "복사(&C)" → "복사", "Copy\tCtrl+C" → "copy"
    cleaned = text.split("\t")[0]
    cleaned = cleaned.replace("&", "")
    if cleaned.endswith(")") and "(" in cleaned:
        head, _, tail = cleaned.rpartition("(")
        if len(tail.rstrip(")")) <= 2:
            cleaned = head
    return cleaned.strip().lower()


def pick_copy_item(items: list) -> dict:
    """메뉴 항목 목록에서 복사 항목을 찾고, 하이라이트 이동에 필요한 아래 방향키 횟수를 센다.

    Windows 메뉴 하이라이트는 구분선을 건너뛰므로 moves는 비구분선 항목 순서 기준이다.
    """
    moves = 0
    for entry in items:
        if entry.get("separator"):
            continue
        moves += 1
        if _normalize_label(entry.get("text", "")) in COPY_LABELS:
            if not entry.get("enabled", False):
                return {"status": "copy-disabled", "moves": 0}
            return {"status": "copied", "moves": moves}
    return {"status": "no-copy-item", "moves": 0}


# --- 아래는 Windows 전용 실기 코드 ---

def _win():
    import ctypes
    from ctypes import wintypes

    return ctypes, wintypes


def _send_right_click():
    ctypes, _ = _win()
    MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def _find_context_menu_window(deadline: float):
    ctypes, _ = _win()
    find_window = ctypes.windll.user32.FindWindowW
    find_window.restype = ctypes.c_void_p  # 64비트 HWND 잘림 방지
    while time.monotonic() < deadline:
        hwnd = find_window("#32768", None)
        if hwnd:
            return hwnd
        time.sleep(0.03)
    return 0


def _menu_items(hwnd_menu_window) -> list:
    ctypes, wintypes = _win()
    user32 = ctypes.windll.user32
    MN_GETHMENU = 0x01E1
    MF_BYPOSITION, MF_DISABLED, MF_GRAYED, MF_SEPARATOR = 0x400, 0x2, 0x1, 0x800

    send_message = user32.SendMessageW
    send_message.restype = ctypes.c_void_p  # HMENU는 포인터 — 64비트 잘림 방지
    send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.GetMenuItemCount.argtypes = [ctypes.c_void_p]
    user32.GetMenuState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    user32.GetMenuStringW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint,
    ]

    hmenu = send_message(hwnd_menu_window, MN_GETHMENU, 0, 0)
    if not hmenu:
        return []
    items = []
    count = user32.GetMenuItemCount(hmenu)
    for position in range(max(count, 0)):
        state = user32.GetMenuState(hmenu, position, MF_BYPOSITION)
        separator = bool(state != -1 and (state & MF_SEPARATOR))
        buffer = ctypes.create_unicode_buffer(256)
        length = user32.GetMenuStringW(hmenu, position, buffer, 256, MF_BYPOSITION)
        text = buffer.value if length > 0 else ""
        enabled = state != -1 and not (state & (MF_DISABLED | MF_GRAYED))
        items.append({"text": text, "enabled": enabled, "position": position, "separator": separator})
    return items


def _press_key(virtual_key: int):
    ctypes, _ = _win()
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(virtual_key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(virtual_key, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.03)


def _close_menu():
    VK_ESCAPE = 0x1B
    _press_key(VK_ESCAPE)


def trigger_copy_at_cursor(timeout_seconds: float) -> MenuOutcome:
    """마우스 현재 위치에서 우클릭해 Brity 메뉴의 복사를 실행한다."""
    from brity_bridge import clipboard_win

    VK_DOWN, VK_RETURN = 0x28, 0x0D
    sequence_before = clipboard_win.sequence_number()
    deadline = time.monotonic() + timeout_seconds

    _send_right_click()
    hwnd = _find_context_menu_window(deadline)
    if not hwnd:
        return MenuOutcome("not-win32-menu", "컨텍스트 메뉴 창(#32768)을 찾지 못함")

    items = _menu_items(hwnd)
    if not items:
        _close_menu()
        return MenuOutcome("not-win32-menu", "메뉴 항목을 읽지 못함")

    picked = pick_copy_item(items)
    if picked["status"] != "copied":
        _close_menu()
        return MenuOutcome(picked["status"])

    for _ in range(picked["moves"]):
        _press_key(VK_DOWN)
    _press_key(VK_RETURN)

    while time.monotonic() < deadline:
        if clipboard_win.sequence_number() != sequence_before:
            return MenuOutcome("copied")
        time.sleep(0.05)
    return MenuOutcome("timeout", "클립보드가 바뀌지 않음")
