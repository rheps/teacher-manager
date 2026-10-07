# skills/teacher-task-manager/scripts/dashboard/__main__.py
"""티처 매니저 진입점 — 화면을 띄우고 감시 도우미와 바탕화면 아이콘을 준비한다.

pywebview는 배포판 프로그램에 포함된다. 소스로 실행할 때만 pip 설치가 필요하다.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WEB_INDEX = Path(__file__).resolve().parent / "web" / "index.html"
_IMPORT = object()  # "실제 pywebview를 임포트해라" 표식

MISSING_WEBVIEW_MESSAGE = (
    "Teacher Manager 화면 파일이 빠져 있어 실행하지 못했어요.\n"
    "공식 Teacher Manager 설치 파일을 다시 받아 설치해 주세요."
)
WEBVIEW2_HELP_MESSAGE = (
    "화면을 표시하는 Microsoft Edge WebView2를 사용할 수 없어 Teacher Manager를 열지 못했어요.\n"
    "Microsoft 공식 설치 페이지에서 Microsoft Edge WebView2 Runtime을 설치해 주세요:\n"
    "https://developer.microsoft.com/microsoft-edge/webview2/"
)
SETTINGS_RECOVERY_HELP_MESSAGE = (
    "기존 설정을 되살리는 중 문제가 생겼어요. 새 정보를 다시 입력하지 마세요.\n"
    "기존 백업은 그대로 보관되어 있습니다. 프로그램을 다시 실행해 주세요."
)
DEV_RESET_WARNING_MESSAGE = (
    "기존 설정을 준비하는 중 문제가 생겼지만 Teacher Manager는 계속 열어요.\n"
    "옮기던 설정은 백업 폴더에 그대로 보관되어 있습니다."
)
INITIAL_WINDOW_WIDTH = 980
INITIAL_WINDOW_HEIGHT_FALLBACK = 700
INITIAL_WINDOW_HEIGHT_RATIO = 0.88
MINIMUM_WINDOW_WIDTH = 900
MINIMUM_WINDOW_HEIGHT = 640


def _usable_screen_height() -> int | None:
    """Windows 작업표시줄을 뺀 기본 화면의 세로 길이를 읽는다."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        work_area = wintypes.RECT()
        success = ctypes.windll.user32.SystemParametersInfoW(
            0x0030, 0, ctypes.byref(work_area), 0
        )
        height = work_area.bottom - work_area.top
        return height if success and height > 0 else None
    except Exception:  # noqa: BLE001 - 화면 크기를 못 읽어도 기존 크기로 열 수 있다
        return None


def _initial_window_height() -> int:
    usable_height = _usable_screen_height()
    if usable_height is None:
        return INITIAL_WINDOW_HEIGHT_FALLBACK
    return max(1, round(usable_height * INITIAL_WINDOW_HEIGHT_RATIO))


def _default_notify(title: str, body: str) -> None:
    """검은 창 없이 실행해도 오류를 볼 수 있도록 Windows 안내창을 띄운다."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, body, title, 0x10)
    except Exception:  # noqa: BLE001 - 안내창 실패가 종료 흐름을 막으면 안 된다
        pass


def _notify_safely(notify, title: str, body: str) -> None:
    try:
        notify(title, body)
    except Exception:  # noqa: BLE001 - 안내창이 안 떠도 정해진 종료값은 돌려준다
        pass


def _background_setup(app_info, ensure_helper, ensure_shortcut) -> None:
    """설정이 끝났으면 감시 도우미를 켜고, 바로가기는 항상 한 번 준비한다."""
    try:
        if (app_info or {}).get("data", {}).get("mode") == "home":
            ensure_helper()
    except Exception:  # noqa: BLE001 - 도우미 실패와 바로가기 준비는 서로 막지 않는다
        pass
    try:
        ensure_shortcut()
    except Exception:  # noqa: BLE001 - 바로가기 실패가 대시보드를 막으면 안 된다
        pass


def _start_dns_warming() -> None:
    # 이름 확인이 느린 컴퓨터를 위해 필요한 이름을 배경에서 미리 찾아 둔다(2026-09-05).
    try:
        from brity_bridge import dns_warm, tools_cli

        dns_warm.start_shared(extra_hosts=dns_warm.hosts_from_urls(tools_cli.bundled_central_chat_sender_url()))
    except Exception:  # noqa: BLE001 - 예열 실패가 화면 실행을 막으면 안 된다
        pass


def _run_background(config_dir) -> None:
    _start_dns_warming()
    try:
        from brity_bridge import paths
        from dashboard import engine
        from dashboard.bridge import Api

        config_dir = Path(config_dir)
        try:
            api = Api(config_dir)
            api.google_status()
            app_info = api.get_app_info()
        except Exception:  # noqa: BLE001 - 설정 확인 실패 시 도우미만 건너뛴다
            app_info = {}
        _background_setup(
            app_info,
            engine.ensure_helper_running,
            lambda: engine.ensure_desktop_shortcut(config_dir, paths.skill_root()),
        )
    except Exception:  # noqa: BLE001 - 배경 준비 전체가 화면 실행을 막으면 안 된다
        pass


def _spawn_background(config_dir) -> None:
    threading.Thread(target=_run_background, args=(config_dir,), daemon=True).start()


# 한 사용자(로그온 세션)에 대시보드 창은 하나만 둔다. 두 번째 실행은 기존 창을 앞으로
# 가져오고 끝난다. 도우미(Local\BrityBridgeHelper)와 이름이 달라 서로 막지 않는다.
DASHBOARD_MUTEX_NAME = "Local\\BigSilverEduLab.TeacherManager.Dashboard"
_ERROR_ALREADY_EXISTS = 183
# 기존 창을 못 찾으면 앞 실행이 막 끝나는 중(업데이트 뒤 새 설치본의 첫 실행)이거나
# 아직 창을 띄우는 중일 수 있다. 이 시간 안에 이름이 풀리면 이어서 창을 연다.
SECOND_INSTANCE_WAIT_SECONDS = 8.0
SECOND_INSTANCE_POLL_SECONDS = 0.25
_SW_SHOW = 5
_SW_RESTORE = 9


def _try_acquire_dashboard_mutex(name: str = DASHBOARD_MUTEX_NAME):
    """이름 붙은 뮤텍스를 만든다. (핸들, 이미_있음). 만들지 못하면 (None, False)."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None, False
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None, True
    return handle, False


def _release_dashboard_mutex(handle) -> None:
    if not handle:
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:  # noqa: BLE001 - 종료 중 정리 실패는 프로세스 종료가 대신 푼다
        pass


def _process_image_name(pid: int) -> str:
    """프로세스 실행 파일 이름(소문자). 읽지 못하면 빈 문자열."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name.lower()
    finally:
        kernel32.CloseHandle(process)


def _is_dashboard_image(image: str) -> bool:
    """창 주인이 대시보드 실행 파일인지 본다. 소스 실행은 python·pythonw다."""
    if not image:
        return False
    own = Path(sys.executable).name.lower()
    if image == own or image == "teachermanager.exe":
        return True
    return image.startswith("python") and own.startswith("python")


def _find_existing_dashboard_window(title: str):
    """제목이 title이고 대시보드 프로세스(자기 자신 제외)가 가진 최상위 창."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    own_pid = os.getpid()
    handle = None
    for _ in range(32):  # 같은 제목 창이 비정상적으로 많아도 무한히 돌지 않게
        handle = user32.FindWindowExW(None, handle, None, title)
        if not handle:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value and pid.value != own_pid and _is_dashboard_image(_process_image_name(pid.value)):
            return handle
    return None


def _focus_window(handle) -> str:
    """기존 창을 복원해 앞으로 가져온다. 멈춘 창에도 막히지 않게 비동기 호출만 쓴다."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    for fn in (user32.IsIconic, user32.IsWindowVisible, user32.IsHungAppWindow,
               user32.SetForegroundWindow):
        fn.argtypes = [wintypes.HWND]
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    if user32.IsHungAppWindow(handle):
        return "hung"
    if user32.IsIconic(handle):
        user32.ShowWindowAsync(handle, _SW_RESTORE)
    elif not user32.IsWindowVisible(handle):
        user32.ShowWindowAsync(handle, _SW_SHOW)
    return "focused" if user32.SetForegroundWindow(handle) else "focus-refused"


def _log_instance_event(config_dir, event: str) -> None:
    """두 번째 실행 처리 결과를 한 줄 남긴다. 시각·프로세스 번호·결과 말고는 적지 않는다."""
    from datetime import datetime

    now = datetime.now()
    line = f"{now:%Y-%m-%d %H:%M:%S}\t{os.getpid()}\tsecond-instance\t{event}"
    print(line)
    try:
        from brity_bridge import account_sessions, paths

        logs = paths.logs_dir(account_sessions.root_config_dir(Path(config_dir)))
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / f"dashboard-{now:%Y-%m-%d}.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001 - 기록 실패가 두 번째 실행 종료를 막으면 안 된다
        pass


def _acquire_single_instance(config_dir, title: str, *, acquire=None, find_window=None,
                             focus=None, log=None, sleep=None, clock=None,
                             wait_seconds=SECOND_INSTANCE_WAIT_SECONDS):
    """대시보드 이름을 차지한다. (계속_실행, 핸들).

    이미 다른 대시보드가 있으면 그 창을 앞으로 가져오고 (False, None)을 돌려준다.
    창을 못 찾으면 잠깐 기다리며 창이 뜨거나 앞 실행이 끝나 이름이 풀리는지 본다.
    끝까지 창도 없고 이름도 안 풀리면 새 창을 열지 않고 끝낸다(기록만 남긴다).
    """
    import time

    acquire = acquire or _try_acquire_dashboard_mutex
    find_window = find_window or _find_existing_dashboard_window
    focus = focus or _focus_window
    log = log or (lambda event: _log_instance_event(config_dir, event))
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    try:
        handle, exists = acquire()
    except Exception:  # noqa: BLE001 - 이름표 확인이 안 되면 예전처럼 그냥 연다
        return True, None
    if not exists:
        return True, handle
    deadline = clock() + wait_seconds
    while True:
        try:
            window = find_window(title)
        except Exception:  # noqa: BLE001
            window = None
        if window:
            try:
                result = focus(window)
            except Exception:  # noqa: BLE001
                result = "focus-error"
            log(result)
            return False, None
        try:
            handle, exists = acquire()
        except Exception:  # noqa: BLE001
            handle, exists = None, True
        if not exists:
            return True, handle
        if clock() >= deadline:
            log("no-window")
            return False, None
        sleep(SECOND_INSTANCE_POLL_SECONDS)


def build_parser() -> argparse.ArgumentParser:
    from brity_bridge import paths

    parser = argparse.ArgumentParser(prog="dashboard", description="티처 매니저")
    parser.add_argument("--config-dir", default=str(paths.default_config_dir()))
    parser.add_argument("--verify-settings-only", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv=None, webview_module=_IMPORT, notify=None, background=None,
         single_instance=None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["--ai-process-worker"]:
        # 사용자 설정이나 화면을 읽기 전에 시작 신호를 기다리는 내부 작업자로 갈라진다.
        from brity_bridge import process_supervision

        return process_supervision.worker_main(raw_argv[1:])
    if "--probe-webview2" in raw_argv:
        # 이 검사는 사용자 설정·도우미·바로가기보다 먼저 갈라진다. pywebview를
        # 읽기 전 Stable 채널만 고정해 Beta/Dev/Canary를 준비 완료로 세지 않는다.
        os.environ["WEBVIEW2_RELEASE_CHANNELS"] = "0"
        from dashboard import webview2_probe

        injected_webview = None if webview_module is _IMPORT else webview_module
        return webview2_probe.webview2_probe_main(
            raw_argv, webview_module=injected_webview
        )

    from dashboard import version

    app_name = version.BRANDING["name"]
    from brity_bridge import app_identity

    app_identity.apply_app_identity()  # 트레이와 같은 이름표를 써야 한 프로그램으로 보인다

    from brity_bridge import gws_env

    gws_env.prepare_gws_env()  # gws 로그인이 수시로 풀리지 않게 키 보관 방식을 고정한다
    args = build_parser().parse_args(raw_argv)
    # 화면을 여는 실행만 한 개로 제한한다. 설치 확인(--verify-settings-only)과 위에서 먼저
    # 갈라진 --ai-process-worker·--probe-webview2는 제외한다. 시험처럼 가짜 화면 모듈을
    # 넘긴 실행과 Windows가 아닌 환경에서는 기본으로 확인하지 않는다.
    if single_instance is None and webview_module is _IMPORT and sys.platform == "win32":
        single_instance = _acquire_single_instance
    mutex = None
    if single_instance is not None and not args.verify_settings_only:
        keep_running, mutex = single_instance(args.config_dir, app_name)
        if not keep_running:
            return 0
    try:
        return _run_app(args, app_name, webview_module, notify, background)
    finally:
        # 창이 닫히면 바로 이름을 푼다. 남은 작업 스레드가 프로세스를 잠시 붙잡아도
        # 업데이트 뒤 새 설치본의 첫 실행이 막히지 않게 한다.
        _release_dashboard_mutex(mutex)


def _run_app(args, app_name, webview_module, notify, background) -> int:
    from brity_bridge import bundle_paths
    from dashboard import version

    notify = notify or _default_notify
    background = background or _spawn_background
    if webview_module is _IMPORT:
        os.environ["WEBVIEW2_RELEASE_CHANNELS"] = "0"
        try:
            import webview as webview_module  # type: ignore[no-redef]
        except ImportError:
            webview_module = None
    if webview_module is None:
        print(MISSING_WEBVIEW_MESSAGE)
        _notify_safely(notify, app_name, MISSING_WEBVIEW_MESSAGE)
        return 1

    from dashboard import fresh_start
    from dashboard.bridge import Api

    installed = bundle_paths.is_frozen()
    try:
        fresh_start.prepare_for_start(
            Path(args.config_dir),
            version.APP_VERSION,
            installed=installed,
        )
    except fresh_start.RecoveryError:
        print(SETTINGS_RECOVERY_HELP_MESSAGE)
        _notify_safely(notify, app_name, SETTINGS_RECOVERY_HELP_MESSAGE)
        return 1
    except Exception as error:  # noqa: BLE001 - 초기화 실패가 실행을 막으면 안 되지만 조용히 삼키지도 않는다
        print(f"{DEV_RESET_WARNING_MESSAGE}\n(자세한 원인: {error})")
        _notify_safely(notify, app_name, DEV_RESET_WARNING_MESSAGE)
    api = Api(Path(args.config_dir))
    if args.verify_settings_only:
        if not installed:
            return 1
        # This installed-package check proves saved local setup survived. It
        # returns only an exit code and must not expose drafts, check Google,
        # deliver reports or grant the user-facing bootstrap identity bypass.
        try:
            return 0 if api._load_state().get("completed") is True else 1
        except Exception:
            return 1

    def _apply_window_icon():
        # 창이 뜬 직후엔 제목이 아직 안 잡혀 FindWindow가 실패할 수 있다 — 잠깐 재시도한다.
        import threading
        import time

        def worker():
            try:
                from brity_bridge import app_icon

                for _ in range(20):
                    if app_icon.apply_to_window(app_name):
                        return
                    time.sleep(0.15)
            except Exception:  # noqa: BLE001 - 아이콘 실패가 실행을 막으면 안 된다
                pass

        threading.Thread(target=worker, daemon=True).start()

    try:
        initial_height = _initial_window_height()
        window = webview_module.create_window(
            app_name,
            url=str(WEB_INDEX),
            js_api=api,
            width=INITIAL_WINDOW_WIDTH,
            height=initial_height,
            maximized=True,
            min_size=(
                MINIMUM_WINDOW_WIDTH,
                min(MINIMUM_WINDOW_HEIGHT, initial_height),
            ),
        )
        try:
            window.events.shown += _apply_window_icon
        except Exception:  # noqa: BLE001 - 이벤트 미지원 환경에서도 실행은 계속
            pass
        try:
            background(Path(args.config_dir))
        except Exception:  # noqa: BLE001 - 배경 준비 실패가 화면 실행을 막으면 안 된다
            pass
        webview_module.start(gui="edgechromium")
    except Exception as error:  # noqa: BLE001 - WebView2 부재 등 환경 문제를 사람 말로
        print(WEBVIEW2_HELP_MESSAGE)
        print(f"(자세한 원인: {error})")
        _notify_safely(notify, app_name, WEBVIEW2_HELP_MESSAGE)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
