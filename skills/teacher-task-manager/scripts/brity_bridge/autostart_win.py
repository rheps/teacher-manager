from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "BrityCalendarBridge"


def autostart_command() -> list:
    from brity_bridge import bundle_paths

    if bundle_paths.is_frozen():
        return [str(bundle_paths.helper_executable()), "run"]
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    launcher = windowless if windowless.exists() else python
    return [str(launcher), "-m", "brity_bridge", "run"]


def _command_line() -> str:
    # 부팅 자동 실행은 트레이와 함께 대시보드도 띄운다. autostart_command()(프로그램이
    # 스스로 도우미를 다시 켤 때)에는 이 플래그를 넣지 않아야 대시보드가 중복으로 안 뜬다.
    from brity_bridge import bundle_paths

    if bundle_paths.is_frozen():
        return f'"{bundle_paths.helper_executable()}" run --launch-dashboard'
    # 레지스트리에는 작업 폴더와 무관하게 동작하는 파일 실행형을 저장한다.
    launcher = autostart_command()[0]
    main_py = Path(__file__).resolve().parent / "__main__.py"
    return f'"{launcher}" "{main_py}" run --launch-dashboard'


def _is_packaged() -> bool:
    from brity_bridge import bundle_paths

    return bundle_paths.is_packaged()


def enable_autostart() -> None:
    if _is_packaged():
        return  # 스토어판은 매니페스트 StartupTask가 담당 — 레지스트리는 무효라 만지지 않는다
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command_line())


def disable_autostart() -> None:
    if _is_packaged():
        return  # 스토어판 끄기는 Windows 설정 > 앱 > 시작 프로그램에서
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


def is_autostart_enabled() -> bool:
    if _is_packaged():
        return True  # 매니페스트 StartupTask 기본 켜짐
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
