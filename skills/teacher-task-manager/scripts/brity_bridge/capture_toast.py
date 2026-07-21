"""단축키 상태창 — 단축키를 누른 순간 우하단에 진행 상태를 보여주는 작은 창.

트레이 풍선 알림은 Windows 알림 설정(집중 모드 등)에 눌려 안 보일 수 있다.
이 창은 항상 위에 떠서 "단축키가 눌렸다"는 사실과 진행 단계를 즉시 보여주고,
작업이 끝나면 결과를 잠깐 보여준 뒤 스스로 닫힌다. 창을 못 만들어도
캡처 작업 자체는 막지 않는다(전부 예외 무시).
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

PRESSED_TEXT = "단축키를 눌렀어요 — 메신저 화면을 읽을게요"
TITLE_TEXT = "Teacher Manager"

_STAGE_TEXTS = {
    "capture": "메신저 화면을 읽는 중이에요…",
    "analyze": "내용을 분석하는 중이에요…",
    "register": "Google에 등록하는 중이에요…",
}


def stage_text(step: str, message: str = "") -> str:
    """진행 단계를 선생님 눈높이 문구로 바꾼다. done/fail은 실제 결과 문구 우선."""
    if step == "done":
        return message or "처리를 마쳤어요"
    if step == "fail":
        return message or "처리하지 못했어요. 다시 시도해 주세요."
    return _STAGE_TEXTS.get(step, message or step)


# --- Win32 상수 ---
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x8
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x8000000
SW_SHOWNOACTIVATE = 4
SPI_GETWORKAREA = 0x30
CS_DROPSHADOW = 0x20000
WM_PAINT = 0x000F
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_APP_REFRESH = 0x8001
WM_APP_CLOSE = 0x8002
CLOSE_TIMER_ID = 1
SAFETY_TIMER_ID = 2
SAFETY_LIFETIME_MS = 3 * 60 * 1000  # 어떤 이유로든 3분 넘게 남지 않게
DT_SINGLELINE = 0x20
DT_END_ELLIPSIS = 0x8000
DT_WORDBREAK = 0x10
DT_NOPREFIX = 0x800
DI_NORMAL = 3
TRANSPARENT = 1

_BG_COLOR = 0x00FFFFFF        # 흰 배경 (COLORREF는 BGR)
_BORDER_COLOR = 0x00DFDFDF    # 옅은 회색 테두리
_ACCENT_COLOR = 0x00F68231    # 브랜드 블루 #3182F6 — 왼쪽 세로 띠
_TITLE_COLOR = 0x00222222
_BODY_COLOR = 0x00444444

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
)

# 공유 ctypes.windll은 다른 모듈이 restype/argtypes를 바꿔둘 수 있다 — 전용
# 인스턴스에 64비트 핸들에 맞는 서명을 직접 선언해 절단·넘침을 막는다.
if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.SetTimer.restype = ctypes.c_size_t
    _user32.SetTimer.argtypes = [
        wintypes.HWND, ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p,
    ]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UpdateWindow.argtypes = [wintypes.HWND]
    _user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.BeginPaint.restype = wintypes.HDC
    _user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.EndPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.FillRect.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p]
    _user32.FrameRect.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p]
    _user32.DrawIconEx.argtypes = [
        wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
    ]
    _user32.DrawTextW.argtypes = [
        wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
    ]
    _gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    _gdi32.CreateFontW.restype = ctypes.c_void_p
    _gdi32.SelectObject.restype = ctypes.c_void_p
    _gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
    _gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    _gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    _gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
else:  # pragma: no cover - Windows 전용 모듈
    _user32 = _gdi32 = _kernel32 = None


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class CaptureToast:
    """항상 위 우하단 상태창. show → update* → close(지연) 순서로 쓴다."""

    BASE_WIDTH = 344
    BASE_HEIGHT = 88
    BASE_MARGIN = 16

    def __init__(self):
        self._lock = threading.Lock()
        self._text = ""
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._shown = threading.Event()

    # --- 공개 API (다른 스레드에서 호출) ---

    def show(self, text: str) -> None:
        if sys.platform != "win32":
            return
        with self._lock:
            self._text = str(text)
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="capture-toast", daemon=True
            )
            self._thread.start()
        else:
            self._post(WM_APP_REFRESH, 0)

    def update(self, text: str) -> None:
        if sys.platform != "win32":
            return
        with self._lock:
            self._text = str(text)
        self._post(WM_APP_REFRESH, 0)

    def close(self, delay_seconds: float = 0.0) -> None:
        if sys.platform != "win32":
            return
        self._post(WM_APP_CLOSE, max(0, int(delay_seconds * 1000)))

    def wait_shown(self, timeout: float) -> bool:
        self._shown.wait(timeout)
        return self._hwnd is not None

    def wait_closed(self, timeout: float) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # --- 내부 ---

    def _post(self, message_id: int, wparam: int) -> None:
        if self._thread is None:
            return
        self._shown.wait(2)  # 창이 만들어지는 중이면 잠깐 기다린다
        hwnd = self._hwnd
        if hwnd:
            try:
                _user32.PostMessageW(hwnd, message_id, wparam, 0)
            except OSError:
                pass

    def _run(self) -> None:
        try:
            self._pump()
        except Exception:  # noqa: BLE001 - 상태창 실패가 캡처를 막으면 안 된다
            pass
        finally:
            self._hwnd = None
            self._shown.set()

    def _scale(self) -> float:
        try:
            dpi = _user32.GetDpiForSystem()
            return max(1.0, dpi / 96.0)
        except (AttributeError, OSError):
            return 1.0

    def _pump(self) -> None:
        user32 = _user32
        gdi32 = _gdi32
        kernel32 = _kernel32

        scale = self._scale()
        width = int(self.BASE_WIDTH * scale)
        height = int(self.BASE_HEIGHT * scale)
        margin = int(self.BASE_MARGIN * scale)

        instance = kernel32.GetModuleHandleW(None)
        self._wndproc = _WNDPROC(self._window_proc)  # GC 방지
        class_name = f"TeacherManagerToast{id(self):x}"
        window_class = _WNDCLASSW()
        window_class.style = CS_DROPSHADOW
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError()

        work = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
        x = work.right - width - margin
        y = work.bottom - height - margin

        from brity_bridge import app_icon

        icon_size = int(32 * scale)
        self._hicon = app_icon.load_hicon(icon_size) or app_icon.default_hicon()
        self._font_title = gdi32.CreateFontW(
            -int(15 * scale), 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, "Malgun Gothic"
        )
        self._font_body = gdi32.CreateFontW(
            -int(14 * scale), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Malgun Gothic"
        )
        self._icon_size = icon_size
        self._scale_value = scale

        hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            class_name, TITLE_TEXT, WS_POPUP,
            x, y, width, height, None, None, instance, None,
        )
        if not hwnd:
            raise ctypes.WinError()
        self._hwnd = hwnd
        user32.SetTimer(hwnd, SAFETY_TIMER_ID, SAFETY_LIFETIME_MS, None)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.UpdateWindow(hwnd)
        self._shown.set()

        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._hwnd = None
            gdi32.DeleteObject(self._font_title)
            gdi32.DeleteObject(self._font_body)
            user32.UnregisterClassW(class_name, instance)

    def _window_proc(self, hwnd, message_id, wparam, lparam):
        user32 = _user32
        if message_id == WM_APP_REFRESH:
            user32.InvalidateRect(hwnd, None, True)
            return 0
        if message_id == WM_APP_CLOSE:
            delay_ms = int(wparam)
            if delay_ms <= 0:
                user32.DestroyWindow(hwnd)
            else:
                user32.SetTimer(hwnd, CLOSE_TIMER_ID, delay_ms, None)
            return 0
        if message_id == WM_TIMER:
            user32.DestroyWindow(hwnd)
            return 0
        if message_id == WM_PAINT:
            try:
                self._paint(hwnd)
            except Exception:  # noqa: BLE001 - 그리기 실패로 메시지 루프가 죽으면 안 된다
                pass
            return 0
        if message_id == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message_id, wparam, lparam)

    def _paint(self, hwnd) -> None:
        user32 = _user32
        gdi32 = _gdi32
        scale = self._scale_value
        ps = _PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))

            background = gdi32.CreateSolidBrush(_BG_COLOR)
            border = gdi32.CreateSolidBrush(_BORDER_COLOR)
            accent = gdi32.CreateSolidBrush(_ACCENT_COLOR)
            try:
                user32.FillRect(hdc, ctypes.byref(rect), background)
                user32.FrameRect(hdc, ctypes.byref(rect), border)
                bar = wintypes.RECT(rect.left, rect.top, rect.left + int(4 * scale), rect.bottom)
                user32.FillRect(hdc, ctypes.byref(bar), accent)
            finally:
                gdi32.DeleteObject(background)
                gdi32.DeleteObject(border)
                gdi32.DeleteObject(accent)

            icon_size = self._icon_size
            icon_y = (rect.bottom - rect.top - icon_size) // 2
            if self._hicon:
                user32.DrawIconEx(
                    hdc, int(16 * scale), icon_y, self._hicon,
                    icon_size, icon_size, 0, None, DI_NORMAL,
                )

            gdi32.SetBkMode(hdc, TRANSPARENT)
            with self._lock:
                body_text = self._text
            text_left = int(16 * scale) + icon_size + int(12 * scale)

            old_font = gdi32.SelectObject(hdc, self._font_title)
            gdi32.SetTextColor(hdc, _TITLE_COLOR)
            title_rect = wintypes.RECT(
                text_left, int(14 * scale), rect.right - int(12 * scale), int(34 * scale)
            )
            user32.DrawTextW(
                hdc, TITLE_TEXT, -1, ctypes.byref(title_rect),
                DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
            )

            gdi32.SelectObject(hdc, self._font_body)
            gdi32.SetTextColor(hdc, _BODY_COLOR)
            body_rect = wintypes.RECT(
                text_left, int(38 * scale), rect.right - int(12 * scale),
                rect.bottom - int(10 * scale),
            )
            user32.DrawTextW(
                hdc, body_text, -1, ctypes.byref(body_rect), DT_WORDBREAK | DT_NOPREFIX
            )
            gdi32.SelectObject(hdc, old_font)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))
