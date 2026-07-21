from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from brity_bridge.hotkey import HotkeySpec

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_HOTKEY_PAUSE = 0x8002
WM_HOTKEY_RESUME = 0x8003

_VK_NAMES = {
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt",
    0x5B: "win", 0x5C: "win",
}


class KeyStateTracker:
    def __init__(self, spec: HotkeySpec, callback):
        self.spec = spec
        self.callback = callback
        self.pressed: set[str] = set()
        self.fired = False

    def feed(self, name: str, is_down: bool) -> bool:
        if is_down:
            self.pressed.add(name)
            if not self.fired and self.pressed == set(self.spec.parts):
                self.fired = True
                self.callback()
                return True
        else:
            self.pressed.discard(name)
            if not self.pressed:
                self.fired = False
        return False


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


_CALLBACK_TYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HOOKPROC = _CALLBACK_TYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class ModifierHotkeyListener:
    def __init__(self, spec: HotkeySpec, callback):
        if not spec.modifier_only:
            raise ValueError("문자 없는 보조키 조합만 직접 감시할 수 있어요")
        self.tracker = KeyStateTracker(spec, callback)
        self._hook = None
        self._proc = None

    def install(self) -> bool:
        if sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        def callback(code, message, pointer):
            if code == HC_ACTION:
                event = ctypes.cast(pointer, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                name = _VK_NAMES.get(int(event.vkCode))
                if name:
                    if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        self.tracker.feed(name, True)
                    elif message in (WM_KEYUP, WM_SYSKEYUP):
                        self.tracker.feed(name, False)
            return user32.CallNextHookEx(self._hook, code, message, pointer)

        self._proc = _HOOKPROC(callback)
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0
        )
        return bool(self._hook)

    def close(self) -> None:
        if self._hook and sys.platform == "win32":
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
        self._hook = None


def probe_modifier_hotkey(spec: HotkeySpec, listener_factory=ModifierHotkeyListener) -> bool:
    listener = listener_factory(spec, lambda: None)
    try:
        return bool(listener.install())
    finally:
        listener.close()
