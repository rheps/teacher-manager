from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import NamedTuple

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
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0x0, 0x1, 0x2, 0x4
# 아이콘이 어느 시대 규칙으로 메시지를 보낼지 정하는 값.
# 정하지 않으면 uVersion이 0(윈도우 95 동작)으로 남고, 그러면 알림 상자를 누른 것
# (NIN_BALLOONUSERCLICK)이 아예 전달되지 않는다 — 알림을 눌러 업데이트하는 길이
# 통째로 막힌다(MSDN Shell_NotifyIcon Remarks). 3은 wParam/lParam 배치가
# _window_proc이 읽는 (lparam & 0xFFFF)와 같아서 좌·우클릭 처리는 그대로 둔다.
NOTIFYICON_VERSION = 3
# 알림 상자 안에 그림을 넣지 않는다.
# 알림 맨 위 프로그램 이름 옆 아이콘은 이 값과 무관하게 나온다 — 그건 두 실행 파일과
# 시작 메뉴 바로가기에 붙인 이름표(app_identity.APP_USER_MODEL_ID)를 보고 윈도우가
# 고르는 것이다. 여기에 NIIF_USER(0x4)를 주면 상자 안에도 같은 V가 한 번 더 그려져
# 알림 하나에 V가 두 개로 보인다(2026-07-29 사용자 확인). NIIF_INFO(0x1)를 주면
# 윈도우 기본 안내 그림이 대신 들어간다. 둘 다 원하지 않으므로 0으로 둔다.
NIIF_NONE = 0x0
NIN_BALLOONUSERCLICK = 0x405  # 알림 상자를 누른 것

MF_STRING = 0x0
MF_GRAYED = 0x1    # 회색 글자
MF_DISABLED = 0x2  # 눌러도 아무 일이 없음 — 눌린 번호가 TrackPopupMenu에서 나오지 않는다
MF_CHECKED = 0x8
MF_SEPARATOR = 0x800
TPM_RIGHTBUTTON = 0x2
TPM_RETURNCMD = 0x100
IDI_APPLICATION = 32512

CMD_VERSION_LABEL = 1001  # 맨 윗줄 회색 글자 — 눌리지 않으므로 이 번호는 돌아오지 않는다
CMD_OPEN_DASHBOARD = 1002
CMD_TOGGLE_AUTOSTART = 1005
CMD_EXIT = 1006
CMD_CHECK_UPDATE = 1007
# 1003·1004는 지운 두 줄(마지막 결과·상태 폴더)이 쓰던 번호다. 다시 쓰지 않는다 —
# 옛 도우미가 아직 떠 있는 채로 새 도우미가 뜨는 순간이 있는데, 그때 옛 번호를
# 새 뜻으로 재활용하면 엉뚱한 동작이 걸린다.

# 메뉴 줄 사이 가로선. 눌리는 줄이 아니라서 번호와 글자가 없다.
MENU_SEPARATOR = None


class MenuItem(NamedTuple):
    """메뉴 한 줄."""

    command_id: int
    text: str
    checked: bool = False
    enabled: bool = True


def app_label() -> str:
    """메뉴 맨 윗줄에 보이는 글자 — 'Teacher Manager 1.8'."""
    from dashboard import version  # 순환 임포트를 피해 여기서 부른다

    return f"{version.BRANDING['name']} {version.APP_VERSION}"


def menu_items(autostart_enabled: bool, app_label_text: str) -> list:
    """트레이 아이콘을 오른쪽 클릭했을 때 위에서 아래로 나오는 줄 목록.

    MENU_SEPARATOR는 가로선이다. Win32 호출과 떼어 놓아서 시험이 메뉴 구성을
    그대로 확인할 수 있게 한다.
    """
    return [
        MenuItem(CMD_VERSION_LABEL, app_label_text, enabled=False),
        MENU_SEPARATOR,
        MenuItem(CMD_OPEN_DASHBOARD, "대시보드 열기"),
        MenuItem(CMD_CHECK_UPDATE, "새 버전 확인"),
        MENU_SEPARATOR,
        MenuItem(CMD_TOGGLE_AUTOSTART, "Windows 시작 시 자동 실행", checked=autostart_enabled),
        MENU_SEPARATOR,
        MenuItem(CMD_EXIT, "종료"),
    ]


def menu_flags(item: MenuItem) -> int:
    """한 줄을 윈도우에 넘길 때 붙이는 표시값."""
    flags = MF_STRING
    if item.checked:
        flags |= MF_CHECKED
    if not item.enabled:
        flags |= MF_GRAYED | MF_DISABLED
    return flags
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
        self._pending_update = None  # 알림으로 알린 새 버전 — 알림을 누르면 이걸로 시작한다
        self._update_check_running = False  # [새 버전 확인]이 인터넷에 나가 있는 중

    # --- 알림 ---

    def notify(self, title: str, body: str) -> None:
        # Shell_NotifyIcon 풍선 알림. 본문은 개인정보 없는 요약 문구만 넣는다.
        # 알림 자리는 하나뿐이라 새 알림은 앞의 알림을 덮는다. 오전에 뜬 새 버전
        # 알림 위로 오후에 '등록 완료'가 덮이면 화면에 보이는 알림은 그것이므로,
        # 결과를 보려고 누른 것을 "업데이트해 달라"로 받아 설치를 시작하면 안 된다.
        self._pending_update = None
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

    def _check_update_clicked(self, runner=None) -> None:
        """[새 버전 확인]을 눌렀다 — 인터넷에 나가 보고 결과를 알림으로 알린다.

        통신이 끝날 때까지 메시지 루프를 붙잡고 있으면 그동안 트레이 아이콘이
        오른쪽 클릭에도 왼쪽 클릭에도 응답하지 않는다. 그래서 다른 실로 넘긴다.
        runner는 시험이 갈아 끼우는 자리다.
        """
        import datetime as _dt

        if self._update_check_running:
            return  # 나가 있는 확인이 이미 하나 있다 — 연타로 두 번 나가지 않는다
        self._update_check_running = True

        def _look() -> None:
            try:
                found = check_update_now(
                    self.config_dir, _dt.date.today().isoformat(), notify=self.notify
                )
                if found:
                    # notify가 대기 중인 새 버전을 비우므로 이 대입은 그 뒤에 와야 한다.
                    self._pending_update = found
            except Exception:  # noqa: BLE001 - 확인이 실패해도 트레이는 계속 돈다
                pass
            finally:
                self._update_check_running = False

        start = runner or (
            lambda fn: threading.Thread(target=fn, name="check-update-now", daemon=True).start()
        )
        start(_look)

    def on_command(self, command_id: int) -> None:
        if command_id == CMD_OPEN_DASHBOARD:
            self._open_dashboard_clicked()
        elif command_id == CMD_CHECK_UPDATE:
            self._check_update_clicked()
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
        self._set_icon_version()

    def _set_icon_version(self) -> None:
        """알림 클릭 메시지를 받으려면 아이콘을 등록할 때마다 버전을 정해 줘야 한다.

        MSDN Shell_NotifyIcon: "NIM_SETVERSION must be called every time a notification
        area icon is added (NIM_ADD)". 빠뜨리면 uVersion이 0으로 남아
        NIN_BALLOONUSERCLICK이 오지 않고, _window_proc의 그 분기는 영영 돌지 않는다.
        아이콘 자물쇠는 부르는 쪽이 잡는다(NIM_ADD와 한 묶음이라 그 자리에 맞춘다).
        """
        self._icon_data.uTimeoutOrVersion = NOTIFYICON_VERSION
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(self._icon_data))

    def _readd_tray_icon(self) -> None:
        """탐색기 재시작 뒤 아이콘을 다시 등록한다 — 실패해도 도우미는 계속 돈다."""
        with self._icon_lock:
            try:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._icon_data))
                self._set_icon_version()  # 다시 등록할 때마다 버전도 다시 정해야 한다
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
        if message_id == WM_USER_TRAY and (lparam & 0xFFFF) == NIN_BALLOONUSERCLICK:
            self._update_balloon_clicked()
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

        for item in menu_items(autostart_win.is_autostart_enabled(), app_label()):
            if item is MENU_SEPARATOR:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            user32.AppendMenuW(menu, menu_flags(item), item.command_id, item.text)
        user32.SetMenuDefaultItem(menu, CMD_OPEN_DASHBOARD, 0)  # 굵게 — 기본 동작 표시

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
            # NIF_ICON을 함께 남긴다 — 이건 트레이(시계 옆)에 놓인 아이콘을 다시 지정하는
            # 표시라, 빼면 알림을 띄울 때마다 트레이 아이콘이 사라진다. 알림 상자 안 그림과는
            # 다른 자리다.
            self._icon_data.uFlags = NIF_INFO | NIF_ICON
            self._icon_data.szInfoTitle = title
            self._icon_data.szInfo = body
            self._icon_data.dwInfoFlags = NIIF_NONE
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._icon_data))

    def _update_balloon_clicked(self) -> None:
        """새 버전 알림을 눌렀다 — 바로 업데이트를 시작한다.

        _pending_update는 새 버전 알림이 화면에 떠 있는 동안에만 차 있다(다른 알림이
        그 자리를 덮으면 notify가 비운다). 비어 있으면 지금 눌린 알림은 새 버전
        알림이 아니라는 뜻이라 아무 일도 하지 않는다.
        """
        info = self._pending_update
        if not info:
            return
        self._pending_update = None
        from dashboard import engine, version

        def _start() -> None:
            result = engine.start_update(
                version.APP_VERSION, url=info["url"], latest=info["latest"],
                sha256=info["sha256"], config_dir=self.config_dir,
                # 여기서 도는 코드가 바로 그 도우미다. 기본값 stop_helper는 창 이름
                # BrityBridgeTrayWindow를 찾아 닫는데 그게 이 프로세스의 창이라,
                # 설치 파일을 띄우기도 전에 메시지 루프가 끝나고 인터프리터가 내려간다
                # — 트레이 아이콘만 사라지고 설치는 시작도 못 한다. 설치 파일에
                # /CLOSEAPPLICATIONS가 붙어 있어(SILENT_UPDATE_ARGS) 켜져 있는
                # 도우미·대시보드는 설치 프로그램이 알아서 닫는다.
                stop_before_launch=lambda: True,
                # 아무것도 끄지 않았으니 설치기를 못 띄워도 되살릴 것이 없다.
                resume_after_launch_failure=lambda: True,
            )
            result = result or {}
            if not result.get("started"):
                # 눌렀는데 아무 반응이 없으면 다음 기회는 빨라야 다음 날이다 —
                # start_update가 이미 사람이 읽을 한국어 이유를 담아 돌려준다.
                self.notify("Teacher Manager", result.get("reason") or "업데이트를 시작하지 못했어요")

        # non-daemon: 인터프리터 종료가 받는 중인 워커를 얼리지 않고 완료를 기다린다.
        # daemon이면 받는 도중에 트레이를 종료했을 때 받다 만 파일이 남는다
        # (on_hotkey가 같은 이유로 daemon=False를 쓴다).
        threading.Thread(target=_start, daemon=False).start()


# 버전 번호를 소리 내어 읽었을 때 마지막 마디의 일의 자리로 받침이 갈린다.
# 8은 '팔'(받침 있음), 9는 '구'(받침 없음), 10은 '십'(받침 있음), 14는 '십사'(받침 없음).
_ONES_DIGIT_ENDS_WITH_CONSONANT = {
    0: True,   # 영 · 십 · 이십
    1: True,   # 일
    2: False,  # 이
    3: True,   # 삼
    4: False,  # 사
    5: False,  # 오
    6: True,   # 육
    7: True,   # 칠
    8: True,   # 팔
    9: False,  # 구
}


def version_particle(version_text: str, after_consonant: str, after_vowel: str) -> str:
    """버전 번호 뒤에 붙는 조사를 고른다 — 1.8'을', 1.9'를', 1.9'가', 1.10'이'.

    번호 모양을 못 읽겠으면 받침 있는 쪽을 준다(지금까지 쓰던 문구가 그쪽이다).
    """
    tail = str(version_text or "").strip().split(".")[-1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    if not digits:
        return after_consonant
    return after_consonant if _ONES_DIGIT_ENDS_WITH_CONSONANT[int(digits) % 10] else after_vowel


def new_version_notice(latest: str) -> tuple:
    """새 버전이 있을 때 띄우는 알림 (굵은 줄, 아랫줄).

    직접 [새 버전 확인]을 눌렀을 때도, 하루 한 번 저절로 확인할 때도 같은 문구다 —
    같은 일을 알리는데 문구가 갈리면 다른 일이 생긴 줄 안다. 굵은 줄에 프로그램
    이름을 넣지 않는다. 알림 맨 위에 이미 'Teacher Manager'가 나온다.
    """
    return (
        f"새 버전 v{latest}{version_particle(latest, '이', '가')} 나왔습니다",
        "이 알림을 누르면 지금 설치합니다.",
    )


def up_to_date_notice(current: str) -> tuple:
    """이미 최신일 때 띄우는 알림 (굵은 줄, 아랫줄) — 굵은 줄을 비워 한 줄로 보인다.

    비우는 쪽이 아랫줄이 아닌 이유: 아랫줄(szInfo)에 빈 글자를 넘기는 것은
    "이 알림을 치워라"라는 뜻으로 정해져 있어서, 그러면 알림이 아예 안 뜬다
    (MSDN Shell_NotifyIcon NOTIFYICONDATA szInfo).
    """
    return (
        "",
        f"최신 버전인 v{current}{version_particle(current, '을', '를')} 쓰고 있습니다.",
    )


def check_update_now(config_dir, today, notify, checker=None) -> dict | None:
    """메뉴에서 [새 버전 확인]을 눌렀다 — 하루 한 번 규칙과 상관없이 지금 나가 본다.

    저절로 도는 update_watch_once와 세 가지가 다르다.

    1. 하루에 한 번이라는 제한을 보지 않는다. 직접 누른 것이라 누를 때마다 나간다.
    2. 오늘 [취소]를 눌렀던 버전도 그대로 알린다 — 그때는 "그만 물어라"였고
       지금은 선생님이 직접 물은 것이다.
    3. 새 버전이 없어도, 확인을 못 했어도 알림을 띄운다. 눌렀는데 아무 반응이
       없으면 안 눌린 줄 안다.

    알릴 것이 있으면 알림을 누를 때 바로 설치할 수 있게 그 정보를 돌려준다(없으면 None).
    """
    from dashboard import engine, version  # 순환 임포트를 피해 여기서 부른다

    look = checker or (lambda: engine.check_update(version.APP_VERSION))
    try:
        info = look()
    except Exception:  # noqa: BLE001 - 인터넷이 없어도 트레이는 계속 돈다
        info = None
    if not info or info.get("status") == "failed":
        notify(
            "새 버전을 확인하지 못했습니다",
            "인터넷 연결을 확인한 뒤 다시 눌러 주세요.",
        )
        return None
    # 확인에 성공했으니 오늘 몫의 자동 확인은 다시 나갈 필요가 없다. 실패를 여기 적으면
    # 아침에 와이파이가 아직 안 붙은 그날 하루는 자동 확인이 통째로 사라진다.
    engine.remember_update_checked(config_dir, today)
    if not info.get("available"):
        notify(*up_to_date_notice(version.APP_VERSION))
        return None
    notify(*new_version_notice(info["latest"]))
    return {"latest": info["latest"], "url": info["url"], "sha256": info["sha256"]}


def update_watch_once(config_dir, today, notify, checker=None) -> dict | None:
    """하루에 한 번 새 버전을 보고, 있으면 알림만 띄운다.

    쓰는 도중에 창을 띄우면 하던 일이 끊긴다. 알림은 누르지 않으면 그냥 사라진다.
    알릴 것이 있으면 알림을 누를 때 바로 설치를 시작할 수 있게 그 정보를 그대로
    돌려준다(없으면 None) — 참·거짓만으로는 _update_balloon_clicked가 무엇을
    받아 설치를 시작해야 할지 알 수 없다.
    """
    from dashboard import engine, version  # 순환 임포트를 피해 여기서 부른다

    if not engine.should_check_update(config_dir, today):
        return None
    look = checker or (lambda: engine.check_update(version.APP_VERSION))
    try:
        info = look()
    except Exception:  # noqa: BLE001 - 인터넷이 없어도 트레이는 계속 돈다
        return None
    # 진짜 checker(engine.check_update)는 통신이 실패해도 예외를 던지지 않고
    # {"status": "failed", ...}를 그냥 돌려준다 — 그걸 "오늘 확인함"으로 적으면
    # 아침에 와이파이가 아직 안 붙은 채로 뜬 그날 하루는 다시 확인할 길이 없어진다
    # (bridge.py의 update_offer가 같은 문제를 같은 방식으로 막는다).
    if (info or {}).get("status") != "failed":
        engine.remember_update_checked(config_dir, today)
    if not (info or {}).get("available"):
        return None
    if not engine.should_ask_update(config_dir, info["latest"], today):
        # 켤 때 뜬 확인 창에서 오늘 [취소]를 누른 그 버전이다. 창으로는 그만 묻기로
        # 했는데 알림으로 다시 들이밀면 같은 것을 두 번 묻는 것이고, 그 알림은
        # 화면에 남아 있다가 눌리면 방금 취소한 업데이트를 그대로 시작한다.
        return None
    notify(*new_version_notice(info["latest"]))
    return {"latest": info["latest"], "url": info["url"], "sha256": info["sha256"]}


def update_watch_loop(tray_app, announce_first_round: bool = True, sleep=None,
                      today=None) -> None:
    """한 시간마다 깨어나 날짜가 바뀌었는지 보고, 하루 한 번 새 버전을 확인한다.

    로그인 자동 실행처럼 대시보드를 같이 띄울 때는 첫 바퀴를 건너뛴다
    (announce_first_round=False) — 켤 때의 안내는 대시보드가 띄우는 '지금
    설치할까요?' 창이 맡는다. 둘 다 알리면 트레이가 0.3초 만에 알림을 띄우고 몇 초
    뒤 대시보드가 확인 창을 또 띄워 같은 새 버전을 두 번 알리게 된다.

    sleep·today는 시험이 갈아 끼우는 자리다.
    """
    import datetime as _dt

    rest = sleep or (lambda: time.sleep(3600))
    day = today or (lambda: _dt.date.today().isoformat())
    announce = announce_first_round
    while True:
        if announce:
            try:
                found = update_watch_once(
                    tray_app.config_dir, day(), notify=tray_app.notify
                )
                if found:
                    # notify가 대기 중인 새 버전을 비우므로 이 대입은 그 뒤에 와야 한다.
                    tray_app._pending_update = found
            except Exception:  # noqa: BLE001 - 확인이 실패해도 트레이는 계속 돈다
                pass
        announce = True  # 건너뛰는 것은 켤 때 한 바퀴뿐이다
        rest()


def run_tray(config_dir: Path, launch_dashboard: bool = False) -> None:
    from brity_bridge import bundle_paths, single_instance
    from brity_bridge import app_identity

    app_identity.apply_app_identity()  # 알림이 우리 아이콘으로 뜨게 한다

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

        def _on_ready() -> None:
            # TrayApp.run()이 _setup_window_and_icon()을 마친 뒤에만 부르는 자리다.
            # 그 전에 확인이 끝나 notify()가 돌면 아직 없는 _icon_data를 건드려
            # AttributeError가 나고, 그 예외가 update_watch_loop의 넓은 except에
            # 조용히 삼켜져 그날 알림이 통째로 사라진다(재검증 fix round 1) —
            # 그래서 첫 확인은 반드시 이 자리를 거쳐서만 돈다.
            threading.Thread(
                target=update_watch_loop,
                args=(app,),
                # 대시보드를 같이 띄우는 경우, 켤 때의 새 버전 안내는 대시보드
                # 확인 창이 맡는다 — 트레이까지 알리면 같은 것을 두 번 알린다.
                kwargs={"announce_first_round": not launch_dashboard},
                daemon=True,
            ).start()
            if launch_dashboard:
                open_dashboard(config_dir)

        app.on_ready = _on_ready
        app.run()
    finally:
        single_instance.release_single_instance(mutex)
