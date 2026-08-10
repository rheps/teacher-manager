"""외부 HTTPS 주소를 한 번만 열고, 실패를 성공으로 돌려주지 않는다."""
from __future__ import annotations

import ctypes
import os
import subprocess
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


def _windows_protocol_handler_available(scheme: str) -> bool | None:
    """Windows가 해당 프로토콜의 실행 파일을 찾는지 읽는다. 모르면 None이다."""
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
        # ASSOCSTR_EXECUTABLE(2): 프로토콜을 맡은 실행 파일을 묻는다.
        size = wintypes.DWORD(0)
        first = query(0, 2, scheme, None, None, ctypes.byref(size))
        if first not in (0, 1) or size.value <= 1:  # S_OK 또는 S_FALSE만 정상 조회다.
            return False
        output = ctypes.create_unicode_buffer(size.value)
        second = query(0, 2, scheme, None, output, ctypes.byref(size))
        return second == 0 and bool(output.value.strip())
    except Exception:  # noqa: BLE001 - 판별 불능이면 기존 열기 길을 보존한다.
        return None


def _windows_https_handler_available() -> bool | None:
    """Windows가 HTTPS 연결 실행 파일을 찾는지 읽는다. 모르면 None이다."""
    return _windows_protocol_handler_available("https")


def _windows_edge_protocol_available() -> bool | None:
    """Windows가 microsoft-edge: 연결 실행 파일을 찾는지 읽는다. 모르면 None이다.

    Edge조차 없는 컴퓨터(예: 깨끗한 Windows Sandbox)에서 microsoft-edge:를
    그냥 열면 브라우저 대신 "이 링크를 열려면 장치에 새 앱이 필요합니다"라는
    앱 선택 창이 뜨고, os.startfile은 그것을 성공으로 돌려준다. 그래서 열기
    전에 연결 프로그램이 실제로 있는지 먼저 확인한다.
    """
    return _windows_protocol_handler_available("microsoft-edge")


def _open_edge_protocol(uri: str):
    # cmd.exe의 start를 거치지 않는다. Windows Shell에 Edge 프로토콜을 직접 넘긴다.
    return os.startfile(uri)  # noqa: S606 - 검증된 microsoft-edge: URI만 전달


def _find_edge_executable(environ=None, isfile=None):
    """Microsoft Edge 실행 파일(msedge.exe)의 정식 설치 경로를 찾는다. 없으면 None.

    기본 브라우저 지정이나 microsoft-edge: 프로토콜 등록과 무관하게, 파일만
    있으면 그 실행 파일로 바로 열 수 있다. 깨끗한 Windows Sandbox처럼 Edge는
    깔려 있지만 기본 앱·프로토콜 등록이 비어 있는 환경을 위한 길이다.
    """
    env = os.environ if environ is None else environ
    exists = os.path.isfile if isfile is None else isfile
    roots = [
        env.get("ProgramFiles(x86)"),
        env.get("ProgramFiles"),
        r"C:\Program Files (x86)",
        r"C:\Program Files",
    ]
    seen = set()
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        candidate = os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe")
        if exists(candidate):
            return candidate
    return None


def _open_with_edge_executable(url: str):
    """Edge 실행 파일을 직접 실행해 주소를 연다. 파일이 없으면 False를 돌려준다."""
    exe = _find_edge_executable()
    if not exe:
        return False
    # msedge.exe는 GUI 프로그램이라 검은 콘솔 창이 뜨지 않는다. 앱이 닫혀도
    # 브라우저는 남도록 자식으로 띄우기만 하고 기다리지 않는다.
    subprocess.Popen([exe, url])  # noqa: S603 - 정식 경로의 Edge와 검증된 HTTPS 주소만 전달
    return None


def _try_windows_edge(safe_url, *, edge_exe, edge, edge_state) -> dict | None:
    """Windows에서 Edge로 여는 두 길을 순서대로 시도한다.

    1) 실행 파일 직접 — 기본 브라우저·프로토콜 등록과 무관하게 열린다.
    2) microsoft-edge: 프로토콜 — 등록됐다고 확인된 경우에만. 등록이 없으면
       Windows의 "장치에 새 앱이 필요합니다" 창만 뜨므로 시도조차 하지 않는다.
    """
    try:
        if edge_exe(safe_url) is not False:  # 성공 시 None(=not False)을 돌려준다.
            return {"opened": True, "method": "edge-exe"}
    except Exception:  # noqa: BLE001 - 원문 오류와 주소는 밖으로 내보내지 않는다.
        pass
    if edge_state is not False:
        try:
            if edge(f"microsoft-edge:{safe_url}") is not False:
                return {"opened": True, "method": "edge"}
        except Exception:  # noqa: BLE001 - 원문 오류와 주소는 밖으로 내보내지 않는다.
            pass
    return None


def open_external_url(
    url,
    *,
    default_opener=None,
    edge_exe_opener=None,
    edge_opener=None,
    platform=None,
    https_handler_available=None,
    edge_protocol_available=None,
) -> dict:
    """검증된 HTTPS 주소를 열고 실제로 시작한 길만 성공으로 돌려준다."""
    safe_url = _validated_https_url(url)
    current_platform = sys.platform if platform is None else str(platform)
    default = default_opener or webbrowser.open
    edge_exe = edge_exe_opener or _open_with_edge_executable
    edge = edge_opener or _open_edge_protocol

    handler_state = None
    edge_state = None
    if current_platform == "win32":
        probe = https_handler_available or _windows_https_handler_available
        try:
            handler_state = probe()
        except Exception:  # noqa: BLE001 - 판별 실패는 기본 연결 시도로 되돌린다.
            handler_state = None
        edge_probe = edge_protocol_available or _windows_edge_protocol_available
        try:
            edge_state = edge_probe()
        except Exception:  # noqa: BLE001 - 판별 실패는 기존 Edge 길을 보존한다.
            edge_state = None

    # HTTPS 연결 프로그램이 없다고 확인된 Windows에서는 기본 브라우저 호출이
    # "새 앱이 필요합니다" 창을 띄우므로 건너뛰고, Edge 실행 파일로 바로 연다.
    if current_platform == "win32" and handler_state is False:
        opened = _try_windows_edge(safe_url, edge_exe=edge_exe, edge=edge, edge_state=edge_state)
        if opened is not None:
            return opened
        raise ExternalUrlOpenError()

    try:
        if bool(default(safe_url)):
            return {"opened": True, "method": "default"}
    except Exception:  # noqa: BLE001 - Windows에서는 아래 Edge 길을 한 번만 시도한다.
        pass

    if current_platform == "win32":
        opened = _try_windows_edge(safe_url, edge_exe=edge_exe, edge=edge, edge_state=edge_state)
        if opened is not None:
            return opened
    raise ExternalUrlOpenError()
