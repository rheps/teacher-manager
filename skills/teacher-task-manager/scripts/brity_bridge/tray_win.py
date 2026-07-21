from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from brity_bridge import (
    attach_read,
    autostart_win,
    capture_store,
    capture_toast,
    hotkey,
    hotkey_win,
    paths,
    pipeline,
    screen_read,
    settings as settings_module,
    status_log,
)

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_HOTKEY = 0x0312
WM_USER_TRAY = 0x8001  # WM_APP + 1
SW_RESTORE = 9
# 닫기 요청 시 진행 중인 캡처 저장을 기다려 주는 상한 — 넘으면 강제 진행.
CLOSE_WAIT_SECONDS = 20.0

# 대시보드(설치 마법사 포함) 창 제목 — dashboard/version.py BRANDING["name"]과 같아야 한다(테스트로 고정).
DASHBOARD_WINDOW_TITLE = "Teacher Manager"


def close_dashboard_windows(find_window_ex=None, post_message=None) -> int:
    """열려 있는 대시보드 창 전부에 닫기 요청을 보낸다. 보낸 개수를 돌려준다.

    트레이의 [종료]는 도우미 프로세스만 끝내므로, 별도 프로세스인 대시보드 창도
    여기서 함께 닫아야 사용자에게 "프로그램 전체 종료"가 된다.
    """
    if find_window_ex is None or post_message is None:
        user32 = ctypes.windll.user32
        user32.FindWindowExW.restype = wintypes.HWND
        user32.FindWindowExW.argtypes = [
            wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ]
        find_window_ex = find_window_ex or user32.FindWindowExW
        post_message = post_message or user32.PostMessageW
    closed = 0
    handle = None
    while closed < 16:  # 같은 제목 창이 비정상적으로 많아도 무한히 돌지 않게
        handle = find_window_ex(None, handle, None, DASHBOARD_WINDOW_TITLE)
        if not handle:
            break
        post_message(handle, WM_CLOSE, 0, 0)
        closed += 1
    return closed


def dashboard_launch_command(config_dir: Path) -> list:
    """대시보드를 새 프로세스로 띄우는 명령. 설치본은 exe, 개발은 pythonw 모듈 실행."""
    from brity_bridge import bundle_paths

    if bundle_paths.is_frozen():
        return [str(bundle_paths.dashboard_executable()), "--config-dir", str(config_dir)]
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    launcher = windowless if windowless.exists() else python
    main_py = Path(__file__).resolve().parent.parent / "dashboard" / "__main__.py"
    return [str(launcher), str(main_py), "--config-dir", str(config_dir)]


def open_dashboard(config_dir: Path, find_window=None, show_window=None,
                   set_foreground=None, popen=None) -> str:
    """대시보드 창이 있으면 앞으로 가져오고, 없으면 새로 띄운다.

    process_win.popen_hidden은 첫 창을 숨기는 STARTUPINFO를 넘기므로 GUI인
    대시보드에는 쓰지 않는다 — pythonw·TeacherManager.exe는 어차피 콘솔이 없다.
    """
    if find_window is None or show_window is None or set_foreground is None:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        find_window = find_window or user32.FindWindowW
        show_window = show_window or user32.ShowWindow
        set_foreground = set_foreground or user32.SetForegroundWindow
    handle = find_window(None, DASHBOARD_WINDOW_TITLE)
    if handle:
        show_window(handle, SW_RESTORE)  # 최소화돼 있어도 복원해서 보여준다
        set_foreground(handle)
        return "focused"
    (popen or subprocess.Popen)(dashboard_launch_command(config_dir))
    return "launched"

NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x0, 0x1, 0x2
NIIF_INFO = 0x1

MF_STRING = 0x0
MF_CHECKED = 0x8
MF_SEPARATOR = 0x800
TPM_RIGHTBUTTON = 0x2
TPM_RETURNCMD = 0x100
IDI_APPLICATION = 32512

CMD_OPEN_DASHBOARD = 1002
CMD_SHOW_LAST = 1003
CMD_OPEN_STATE = 1004
CMD_TOGGLE_AUTOSTART = 1005
CMD_EXIT = 1006
HOTKEY_ID = 1

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
)


class NOTIFYICONDATAW(ctypes.Structure):
    # guidItem·hBalloonIcon 없는 구식 크기 — cbSize가 크기를 알려주므로 Windows가 그대로 받는다.
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class TrayApp:
    def __init__(self, config_dir: Path, toast_factory=None):
        self.config_dir = Path(config_dir)
        self.settings = settings_module.load_settings(paths.settings_path(self.config_dir))
        self.busy = threading.Lock()
        self._icon_lock = threading.Lock()
        self.hwnd = None
        self._modifier_listener = None
        self._registered_hotkey = False
        self._hotkey_paused_until = 0.0
        self._toast_factory = toast_factory or capture_toast.CaptureToast
        self._toast = None
        self.on_ready = None  # 트레이 아이콘 등록 직후 한 번 부른다 (부팅 시 대시보드 동반 실행)
        self._dashboard_click_guard = 0.0
        self._close_pending = False  # 캡처 저장을 기다리는 닫기 대기자 존재 여부
        self._close_forced = False  # 대기 상한 초과 — 다음 WM_CLOSE는 그대로 진행
        self._taskbar_created_message = 0  # 탐색기 재시작 알림 — 아이콘을 다시 등록해야 한다

    # --- 알림 ---

    def notify(self, title: str, body: str) -> None:
        # Shell_NotifyIcon 풍선 알림. 본문은 개인정보 없는 요약 문구만 넣는다.
        self._modify_balloon(title[:60], body[:200])

    # --- 단축키 처리 ---

    def _dispatch_hotkey(self) -> None:
        if time.monotonic() < self._hotkey_paused_until:
            return
        self.on_hotkey()

    def on_hotkey(self) -> None:
        if not self.busy.acquire(blocking=False):
            self.notify("Brity 연결 도우미", "이미 처리 중입니다. 끝난 뒤 다시 눌러 주세요.")
            return
        # 워커를 띄우기 전에 즉시 상태창부터 — "눌렸다"는 반응이 바로 보여야 한다.
        toast = None
        try:
            toast = self._toast_factory()
            toast.show(capture_toast.PRESSED_TEXT)
        except Exception:  # noqa: BLE001 - 상태창 실패가 캡처를 막으면 안 된다
            toast = None
        self._toast = toast
        # non-daemon: 인터프리터 종료가 저장 중인 워커를 얼리지 않고 완료를 기다린다.
        threading.Thread(target=self._capture_once_locked, daemon=False).start()

    def _capture_idle(self) -> bool:
        if self.busy.acquire(blocking=False):
            self.busy.release()
            return True
        return False

    def _close_after_capture(self, hwnd, post=None, wait_seconds: float = CLOSE_WAIT_SECONDS) -> None:
        # 캡처(저장 포함)가 끝나기를 기다렸다가 닫기를 다시 요청한다.
        if self.busy.acquire(timeout=wait_seconds):
            self.busy.release()
        else:
            # 워커가 매달렸다 — 더 붙잡으면 종료가 안 되니 강제 진행한다.
            # 저장은 원자 교체라 최악에도 이전 완본이 남는다.
            self._close_forced = True
        if post is None:
            post = ctypes.windll.user32.PostMessageW
        post(hwnd, WM_CLOSE, 0, 0)

    def _capture_once_locked(self) -> None:
        toast = self._toast
        writer = capture_store.ProgressWriter(paths.bridge_state_dir(self.config_dir))

        def emit(step: str, message: str = "") -> None:
            writer.emit(step, message)
            if toast is not None:
                try:
                    toast.update(capture_toast.stage_text(step, message))
                except Exception:  # noqa: BLE001 - 상태창 실패가 캡처를 막으면 안 된다
                    pass

        emit("capture")
        try:
            self._capture_once(emit)
        except Exception:  # noqa: BLE001 - 워커가 소리 없이 죽으면 안 된다
            message = "처리 중 예상하지 못한 오류가 발생했습니다. 다시 시도해 주세요."
            emit("fail", message)
            self.notify("Brity 연결 도우미", message)
        finally:
            if toast is not None:
                try:
                    toast.close(delay_seconds=4)  # 결과를 읽을 시간을 준 뒤 닫는다
                except Exception:  # noqa: BLE001
                    pass
            self._toast = None
            self.busy.release()

    def _capture_once(self, emit) -> None:
        capture = screen_read.capture_brity_text()
        if not capture.ok:
            message = screen_read.capture_failure_message(capture.reason)
            emit("fail", message)
            self.notify("Brity 연결 도우미", message)
            return

        try:
            record, note = screen_read.build_screen_record(
                capture, Path(self.settings.brity_download_dir)
            )
        except attach_read.AttachmentBlocked as error:
            message = error.message or "첨부파일을 먼저 내려받아 주세요."
            pipeline.record_preflight_failure(
                self.config_dir,
                message,
                "등록하지 않음 · " + message,
            )
            file_names = "\n".join(error.names)
            emit("fail", message)
            self.notify(
                "Brity 연결 도우미",
                message + ("\n" + file_names if file_names else ""),
            )
            return
        result = pipeline.run_capture_flow(
            pipeline.CaptureContext(clipboard_text=None, clipboard_html=None),
            self.config_dir,
            self.settings,
            record=record,
            progress=emit,
        )
        body = result.message
        if note:
            body = body + "\n" + note
        self.notify("Brity 연결 도우미", body)

    # --- 트레이 메뉴 ---

    def _open_dashboard_clicked(self) -> None:
        # 대시보드가 뜨는 몇 초 사이의 연타로 창이 두 개 생기지 않게 잠깐 무시한다.
        now = time.monotonic()
        if now < self._dashboard_click_guard:
            return
        self._dashboard_click_guard = now + 4.0
        try:
            open_dashboard(self.config_dir)
        except Exception:  # noqa: BLE001 - 대시보드 실행 실패가 트레이를 죽이면 안 된다
            self.notify("Teacher Manager", "대시보드를 여는 데 실패했습니다. 바탕화면 아이콘으로 실행해 주세요.")

    def on_command(self, command_id: int) -> None:
        if command_id == CMD_OPEN_DASHBOARD:
            self._open_dashboard_clicked()
        elif command_id == CMD_SHOW_LAST:
            last = status_log.read_last_status(paths.bridge_state_dir(self.config_dir))
            if last:
                self.notify("마지막 결과", f"{last.get('when', '')} {last.get('message', '')}")
            else:
                self.notify("마지막 결과", "아직 처리한 메시지가 없습니다.")
        elif command_id == CMD_OPEN_STATE:
            os.startfile(str(paths.bridge_state_dir(self.config_dir)))
        elif command_id == CMD_TOGGLE_AUTOSTART:
            if autostart_win.is_autostart_enabled():
                autostart_win.disable_autostart()
                self.notify("Brity 연결 도우미", "Windows 시작 시 자동 실행을 껐습니다.")
            else:
                autostart_win.enable_autostart()
                self.notify("Brity 연결 도우미", "Windows 시작 시 자동 실행을 켰습니다.")
        elif command_id == CMD_EXIT:
            close_dashboard_windows()  # 대시보드·설치 마법사 창도 함께 닫는다
            ctypes.windll.user32.DestroyWindow(self.hwnd)

    # --- 창·아이콘·메시지 루프 ---

    def _setup_window_and_icon(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # 64비트 핸들 잘림 방지
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]

        instance = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._window_proc)  # GC 방지를 위해 속성으로 보관
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = "BrityBridgeTrayWindow"
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError()
        # 메시지 전용 창은 트레이 알림을 받지 못하므로 일반 숨김 창을 만든다.
        self.hwnd = user32.CreateWindowExW(
            0, window_class.lpszClassName, "Brity 연결 도우미", 0, 0, 0, 0, 0, None, None, instance, None
        )

        # 탐색기(explorer.exe)가 다시 시작되면 등록된 트레이 아이콘이 전부 지워지고
        # 이 메시지가 뿌려진다. 받아서 다시 등록하지 않으면 도우미는 살아 있는데
        # 파란 체크 아이콘과 그 우클릭 메뉴만 영영 사라진다.
        self._taskbar_created_message = user32.RegisterWindowMessageW("TaskbarCreated")

        self._icon_data = NOTIFYICONDATAW()
        self._icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self._icon_data.hWnd = self.hwnd
        self._icon_data.uID = 1
        self._icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._icon_data.uCallbackMessage = WM_USER_TRAY
        from brity_bridge import app_icon

        # 우리 로고 — 파일을 못 읽는 특수 상황에서만 기본 아이콘으로 폴백
        # (app_icon이 64비트 핸들에 맞는 restype로 로드한다 — 잘리면 트레이에 로고가 안 뜬다)
        self._icon_data.hIcon = app_icon.load_hicon(16) or app_icon.default_hicon()
        self._icon_data.szTip = "Teacher Manager"
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._icon_data))

    def _readd_tray_icon(self) -> None:
        """탐색기 재시작 뒤 아이콘을 다시 등록한다 — 실패해도 도우미는 계속 돈다."""
        with self._icon_lock:
            try:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._icon_data))
            except Exception:  # noqa: BLE001 - 재등록 실패가 캡처를 막으면 안 된다
                pass

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._setup_window_and_icon()

        # 도우미 창이 이미 있으므로, 여기서 뜨는 대시보드가 도우미를 또 띄우지 않는다.
        if self.on_ready is not None:
            try:
                self.on_ready()
            except Exception:  # noqa: BLE001 - 동반 실행 실패가 트레이 시작을 막으면 안 된다
                pass

        try:
            spec = hotkey.parse_hotkey(self.settings.hotkey)
        except ValueError:
            self.notify("Brity 연결 도우미", "settings.json의 단축키 형식이 잘못돼 트레이 메뉴만 동작합니다.")
        else:
            if spec.modifier_only:
                self._modifier_listener = hotkey_win.ModifierHotkeyListener(spec, self._dispatch_hotkey)
                registered = self._modifier_listener.install()
            else:
                registered = bool(user32.RegisterHotKey(self.hwnd, HOTKEY_ID, spec.modifiers, spec.key_code))
                self._registered_hotkey = registered
            if not registered:
                self.notify(
                    "Brity 연결 도우미",
                    f"단축키 {self.settings.hotkey} 등록에 실패했습니다. 다른 프로그램이 쓰고 있을 수 있습니다. "
                    "설정 대시보드에서 단축키를 바꾼 뒤 다시 적용해 주세요.",
                )
            else:
                self.notify(
                    "Brity 연결 도우미",
                    f"도우미가 시작됐습니다 — 단축키 {self.settings.hotkey}",
                )

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _window_proc(self, hwnd, message_id, wparam, lparam):
        user32 = ctypes.windll.user32
        if self._taskbar_created_message and message_id == self._taskbar_created_message:
            self._readd_tray_icon()  # 탐색기가 다시 켜졌다 — 아이콘을 되살린다
            return 0
        if message_id == WM_HOTKEY and wparam == HOTKEY_ID:
            self._dispatch_hotkey()
            return 0
        if message_id == hotkey_win.WM_HOTKEY_PAUSE:
            seconds = min(30, max(1, int(wparam or 15)))
            self._hotkey_paused_until = time.monotonic() + seconds
            return 0
        if message_id == hotkey_win.WM_HOTKEY_RESUME:
            self._hotkey_paused_until = 0.0
            return 0
        if message_id == WM_USER_TRAY and (lparam & 0xFFFF) == WM_RBUTTONUP:
            self._show_menu()
            return 0
        if message_id == WM_USER_TRAY and (lparam & 0xFFFF) == WM_LBUTTONUP:
            self._open_dashboard_clicked()  # 아이콘 클릭 한 번으로 대시보드
            return 0
        if message_id == WM_COMMAND:
            self.on_command(wparam & 0xFFFF)
            return 0
        if message_id == WM_CLOSE and not self._close_forced and not self._capture_idle():
            # 캡처 저장 도중 창을 파괴하면 메인 스레드 종료가 워커를 즉살해
            # 잘린 파일이 남는다(재검증 9-F2). 저장이 끝난 뒤 다시 닫는다.
            if not self._close_pending:
                self._close_pending = True
                threading.Thread(
                    target=self._close_after_capture, args=(hwnd,), daemon=True
                ).start()
            return 0
        if message_id == WM_DESTROY:
            with self._icon_lock:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._icon_data))
            if self._modifier_listener is not None:
                self._modifier_listener.close()
                self._modifier_listener = None
            if self._registered_hotkey:
                user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                self._registered_hotkey = False
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message_id, wparam, lparam)

    def _show_menu(self) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()

        def add(command_id: int, text: str, checked: bool = False) -> None:
            user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if checked else 0), command_id, text)

        add(CMD_OPEN_DASHBOARD, "대시보드 열기")
        user32.SetMenuDefaultItem(menu, CMD_OPEN_DASHBOARD, 0)  # 굵게 — 기본 동작 표시
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        add(CMD_SHOW_LAST, "마지막 결과 보기")
        add(CMD_OPEN_STATE, "상태 폴더 열기")
        add(CMD_TOGGLE_AUTOSTART, "Windows 시작 시 자동 실행", checked=autostart_win.is_autostart_enabled())
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        add(CMD_EXIT, "종료")

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)  # 메뉴 밖 클릭 시 닫히게 하는 표준 절차
        chosen = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, self.hwnd, None
        )
        user32.DestroyMenu(menu)
        if chosen:
            self.on_command(chosen)

    def _modify_balloon(self, title: str, body: str) -> None:
        with self._icon_lock:
            self._icon_data.uFlags = NIF_INFO
            self._icon_data.szInfoTitle = title
            self._icon_data.szInfo = body
            self._icon_data.dwInfoFlags = NIIF_INFO
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._icon_data))


def run_tray(config_dir: Path, launch_dashboard: bool = False) -> None:
    from brity_bridge import bundle_paths, single_instance

    mutex = single_instance.acquire_single_instance()
    if mutex is None:
        if launch_dashboard:
            # 이미 실행 중인데 다시 켰다는 건 프로그램을 보고 싶다는 뜻 — 대시보드를 보여준다.
            try:
                open_dashboard(config_dir)
            except Exception:  # noqa: BLE001
                pass
            return
        import ctypes

        MB_ICONINFORMATION = 0x40
        ctypes.windll.user32.MessageBoxW(
            None, "Brity 연결 도우미가 이미 실행 중입니다.", "Brity 연결 도우미", MB_ICONINFORMATION
        )
        return
    try:
        # 예전 버전이 남긴 자동 실행 명령(--launch-dashboard 없는)을 현재 형식으로 새로 고친다.
        try:
            if bundle_paths.is_frozen() and autostart_win.is_autostart_enabled():
                autostart_win.enable_autostart()
        except Exception:  # noqa: BLE001 - 레지스트리 손질 실패가 시작을 막으면 안 된다
            pass
        app = TrayApp(config_dir)
        if launch_dashboard:
            app.on_ready = lambda: open_dashboard(config_dir)
        app.run()
    finally:
        single_instance.release_single_instance(mutex)
