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
    "화면 부품(pywebview)이 없어요. 배포판 프로그램에는 들어 있고,\n"
    "소스로 실행할 때만 한 번 설치하면 돼요: pip install pywebview"
)
WEBVIEW2_HELP_MESSAGE = (
    "화면을 여는 데 실패했어요. Windows 구성 요소인 WebView2가 없을 수 있어요.\n"
    "Windows 11에는 기본 내장이고, Windows 10은 아래 주소에서 설치할 수 있어요:\n"
    "https://developer.microsoft.com/microsoft-edge/webview2/"
)
SETTINGS_RECOVERY_HELP_MESSAGE = (
    "기존 설정을 되살리는 중 문제가 생겼어요. 새 정보를 다시 입력하지 마세요.\n"
    "기존 백업은 그대로 보관되어 있습니다. 프로그램을 다시 실행해 주세요."
)
DEV_RESET_WARNING_MESSAGE = (
    "설정 준비 중 문제가 있었지만 실행은 계속해요.\n"
    "옮기던 설정은 백업 폴더에 그대로 보존됩니다."
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


def _run_background(config_dir) -> None:
    try:
        from brity_bridge import paths
        from dashboard import engine
        from dashboard.bridge import Api

        config_dir = Path(config_dir)
        try:
            app_info = Api(config_dir).get_app_info()
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


def build_parser() -> argparse.ArgumentParser:
    from brity_bridge import paths

    parser = argparse.ArgumentParser(prog="dashboard", description="티처 매니저")
    parser.add_argument("--config-dir", default=str(paths.default_config_dir()))
    parser.add_argument("--verify-settings-only", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv=None, webview_module=_IMPORT, notify=None, background=None) -> int:
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

    from brity_bridge import bundle_paths
    from dashboard import version

    app_name = version.BRANDING["name"]
    from brity_bridge import app_identity

    app_identity.apply_app_identity()  # 트레이와 같은 이름표를 써야 한 프로그램으로 보인다

    from brity_bridge import gws_env

    gws_env.prepare_gws_env()  # gws 로그인이 수시로 풀리지 않게 키 보관 방식을 고정한다
    args = build_parser().parse_args(raw_argv)
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
        message = f"{DEV_RESET_WARNING_MESSAGE}\n(자세한 원인: {error})"
        print(message)
        _notify_safely(notify, app_name, message)
    api = Api(Path(args.config_dir))
    if args.verify_settings_only:
        if not installed:
            return 1
        app_info = api.get_app_info()
        return 0 if (app_info or {}).get("data", {}).get("mode") == "home" else 1

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
