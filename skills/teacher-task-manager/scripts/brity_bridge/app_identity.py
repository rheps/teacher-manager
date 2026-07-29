"""프로그램 이름표 — 윈도우가 알림을 보고 어느 프로그램인지 알아보게 한다.

두 실행 파일(TeacherManager.exe, TeacherManagerHelper.exe)이 같은 값을 붙이고,
시작 메뉴 바로가기에도 같은 값을 달아야 한다. 하나라도 다르면 윈도우가 짝을 못 찾아
오른쪽 아래 알림에 기본 아이콘을 쓴다. 그 짝짓기 결과는 캐시에 남는데 새로 설치하거나
탐색기가 다시 시작될 때 지워져서, 됐다 안 됐다 하는 것처럼 보였다(2026-07-29).
"""
from __future__ import annotations

import sys

APP_USER_MODEL_ID = "BigSilverEduLab.TeacherManager"


def _shell32_setter():
    """윈도우 API에서 실제 이름표 함수를 찾아온다.

    이 조회(속성 찾기) 자체가 AttributeError를 던질 수 있는 환경도 있어서
    apply_app_identity의 try 블록 안에서만 불러야 한다 — 밖에서 부르면 그
    실패가 run_tray()·dashboard.main() 맨 앞에서 그대로 튀어 프로그램이
    시작하다가 죽는다.
    """
    import ctypes

    return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID


def apply_app_identity(setter=None) -> bool:
    """이 프로세스에 이름표를 붙인다. 못 붙여도 실행은 계속한다."""
    try:
        if setter is None:
            if sys.platform != "win32":
                return False
            setter = _shell32_setter()
        setter(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 - 이름표를 못 붙여도 프로그램은 돌아야 한다
        return False
    return True
