"""외부 HTTPS 주소를 한 번만 열고, 실패를 성공으로 돌려주지 않는다."""
from __future__ import annotations

import ctypes
import os
import sys
import webbrowser
from ctypes import wintypes
from urllib.parse import urlsplit


NO_EXTERNAL_BROWSER = "NO_EXTERNAL_BROWSER"
_NO_BROWSER_MESSAGE = (
    "이 컴퓨터에서 웹 브라우저를 열지 못했어요. "
    "기본 브라우저 또는 Microsoft Edge를 준비한 뒤 다시 눌러 주세요."
)


class ExternalUrlOpenError(RuntimeError):
    """주소나 운영체제 원문을 담지 않는 외부 브라우저 실패."""

    code = NO_EXTERNAL_BROWSER

    def __init__(self):
        super().__init__(_NO_BROWSER_MESSAGE)


def _validated_https_url(value) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("https 주소만 열 수 있어요")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("https 주소만 열 수 있어요")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port  # 잘못된 포트 모양도 열기 전에 막는다.
    except (TypeError, ValueError):
        raise ValueError("https 주소만 열 수 있어요") from None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in parsed.netloc
    ):
        raise ValueError("https 주소만 열 수 있어요")
    return value


def _windows_https_handler_available() -> bool | None:
    """Windows가 HTTPS 연결 실행 파일을 찾는지 읽는다. 모르면 None이다."""
    if sys.platform != "win32":
        return None
    try:
        query = ctypes.WinDLL("Shlwapi", use_last_error=True).AssocQueryStringW
        query.argtypes = (
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        query.restype = ctypes.c_long
        # ASSOCSTR_EXECUTABLE(2): https 프로토콜을 맡은 실행 파일을 묻는다.
        size = wintypes.DWORD(0)
        first = query(0, 2, "https", None, None, ctypes.byref(size))
        if first not in (0, 1) or size.value <= 1:  # S_OK 또는 S_FALSE만 정상 조회다.
            return False
        output = ctypes.create_unicode_buffer(size.value)
        second = query(0, 2, "https", None, output, ctypes.byref(size))
        return second == 0 and bool(output.value.strip())
    except Exception:  # noqa: BLE001 - 판별 불능이면 기존 기본 브라우저 길을 보존한다.
        return None


def _open_edge_protocol(uri: str):
    # cmd.exe의 start를 거치지 않는다. Windows Shell에 Edge 프로토콜을 직접 넘긴다.
    return os.startfile(uri)  # noqa: S606 - 검증된 microsoft-edge: URI만 전달


def open_external_url(
    url,
    *,
    default_opener=None,
    edge_opener=None,
    platform=None,
    https_handler_available=None,
) -> dict:
    """검증된 HTTPS 주소를 열고 실제로 시작한 길만 성공으로 돌려준다."""
    safe_url = _validated_https_url(url)
    current_platform = sys.platform if platform is None else str(platform)
    default = default_opener or webbrowser.open
    edge = edge_opener or _open_edge_protocol

    handler_state = None
    if current_platform == "win32":
        probe = https_handler_available or _windows_https_handler_available
        try:
            handler_state = probe()
        except Exception:  # noqa: BLE001 - 판별 실패는 기본 연결 시도로 되돌린다.
            handler_state = None

    # HTTPS 연결 프로그램이 없다고 확인된 Windows에서는 앱 선택 오류 창을 피한다.
    if current_platform == "win32" and handler_state is False:
        try:
            edge_result = edge(f"microsoft-edge:{safe_url}")
            if edge_result is not False:  # os.startfile은 성공 시 None을 돌려준다.
                return {"opened": True, "method": "edge"}
        except Exception:  # noqa: BLE001 - 원문 오류와 주소는 밖으로 내보내지 않는다.
            pass
        raise ExternalUrlOpenError()

    try:
        if bool(default(safe_url)):
            return {"opened": True, "method": "default"}
    except Exception:  # noqa: BLE001 - Windows에서는 아래 Edge 길을 한 번만 시도한다.
        pass

    if current_platform == "win32":
        try:
            edge_result = edge(f"microsoft-edge:{safe_url}")
            if edge_result is not False:
                return {"opened": True, "method": "edge"}
        except Exception:  # noqa: BLE001 - 원문 오류와 주소는 밖으로 내보내지 않는다.
            pass
    raise ExternalUrlOpenError()
