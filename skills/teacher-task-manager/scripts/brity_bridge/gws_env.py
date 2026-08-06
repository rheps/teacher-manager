"""Google Workspace CLI 자식 명령에 넘길 환경을 안전하게 만든다.

일반 Google 명령과 로그인 명령을 분리한다. 제품이 가진 데스크톱 OAuth 값은
``gws auth login`` 자식 한 번에만 넘기고, status·Calendar·Tasks·Sheets 같은
일반 명령에는 새로 넣지 않는다. 기존 로그인 파일, 암호화 키, token cache와
사용자가 고른 keyring 방식은 읽거나 옮기거나 지우지 않는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from brity_bridge.oauth_client import OAuthClientFormatError, parse_desktop_oauth_bytes


CLIENT_FILE_NAME = "gws-oauth-client.json"
UPSTREAM_CLIENT_FILE_NAME = "client_secret.json"
ACCOUNT_STORAGE_ERROR_CODE = "GWS_ACCOUNT_STORAGE_OUTSIDE_USER"
ACCOUNT_STORAGE_ERROR_MESSAGE = (
    "Google 로그인 저장 위치가 현재 Windows 계정 폴더 밖을 가리키고 있어요. "
    "공용 또는 다른 계정의 환경 설정을 지운 뒤 Teacher Manager를 다시 열어 주세요."
)
_ACCOUNT_STORAGE_KEYS = (
    "GOOGLE_WORKSPACE_CLI_CONFIG_DIR",
    "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE",
)


class GwsAccountStorageError(RuntimeError):
    """공용/다른 Windows 계정의 GWS 로그인 저장소가 지정된 상태."""


def _running_on_windows() -> bool:
    return os.name == "nt"


def _windows_current_user_profile_dir() -> Path | None:
    """Windows가 현재 로그인 토큰에 연결한 실제 사용자 폴더를 읽는다."""

    if not _running_on_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        userenv = ctypes.WinDLL("userenv", use_last_error=True)

        advapi32.OpenProcessToken.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        )
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        userenv.GetUserProfileDirectoryW.argtypes = (
            wintypes.HANDLE,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        userenv.GetUserProfileDirectoryW.restype = wintypes.BOOL

        token = wintypes.HANDLE()
        token_query = 0x0008
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
        ):
            return None
        try:
            size = wintypes.DWORD(0)
            userenv.GetUserProfileDirectoryW(token, None, ctypes.byref(size))
            if size.value <= 1:
                return None
            buffer = ctypes.create_unicode_buffer(size.value)
            if not userenv.GetUserProfileDirectoryW(
                token, buffer, ctypes.byref(size)
            ):
                return None
            profile = Path(buffer.value)
            if not profile.is_absolute():
                return None
            return profile.resolve(strict=False)
        finally:
            kernel32.CloseHandle(token)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


@dataclass(frozen=True, repr=False)
class OAuthClientSelection:
    ready: bool
    source: Literal[
        "explicit_client_env",
        "gws_config",
        "bundled_client",
        "missing",
        "invalid",
        "conflict",
    ]
    client_id: str
    client_secret: str
    error_code: str


@dataclass(frozen=True)
class GwsLoginStoreSnapshot:
    credentials_enc_exists: bool
    file_encryption_key_exists: bool
    explicit_keyring_backend: str


@dataclass(frozen=True)
class GoogleConnectionStatus:
    gws_runtime_ready: bool
    oauth_client_ready: bool
    oauth_client_conflict: bool
    credential_override_present: bool
    account_storage_override_unsafe: bool
    login_state: Literal["not_checked", "logged_out", "logged_in", "error"]
    error_code: str


def _user_default_gws_config_dir(values: Mapping[str, str]) -> Path:
    if _running_on_windows():
        actual_profile = _windows_current_user_profile_dir()
        if actual_profile is None:
            raise GwsAccountStorageError(
                "현재 Windows 계정 폴더를 안전하게 확인하지 못했어요. "
                "Teacher Manager를 다시 열어 주세요."
            )
        return actual_profile / ".config" / "gws"
    user_profile = str(values.get("USERPROFILE") or "").strip()
    if user_profile:
        return Path(user_profile) / ".config" / "gws"
    return Path.home() / ".config" / "gws"


def _expanded_account_path(value: str, values: Mapping[str, str]) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    for key in ("USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        replacement = str(values.get(key) or "").strip()
        if replacement:
            text = re.sub(
                re.escape(f"%{key}%"),
                lambda _match, replacement=replacement: replacement,
                text,
                flags=re.IGNORECASE,
            )
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return None


def _account_roots(values: Mapping[str, str]) -> tuple[Path, ...]:
    # Windows에서는 USERPROFILE 문구를 안전 경계로 믿지 않는다. 현재 프로그램
    # 토큰을 Windows API에 직접 물어 얻은 폴더만 현재 계정의 경계다.
    if _running_on_windows():
        actual_profile = _windows_current_user_profile_dir()
        return (actual_profile,) if actual_profile is not None else ()

    # 다른 운영체제와 기존 시험 환경은 종전 홈 폴더 규칙을 유지한다.
    user_profile = _expanded_account_path(
        str(values.get("USERPROFILE") or ""), values
    )
    if user_profile is not None:
        return (user_profile,)

    # USERPROFILE이 없는 비정상/시험 환경에서는 운영체제가 알려 준 홈 폴더를
    # 마지막 경계로 쓴다. 환경값이 상대 경로이거나 읽을 수 없으면 안전하지 않다.
    return (Path.home().resolve(strict=False),)


def _inside_current_account(value: str, values: Mapping[str, str]) -> bool:
    candidate = _expanded_account_path(value, values)
    if candidate is None:
        return False
    candidate_text = os.path.normcase(str(candidate))
    for root in _account_roots(values):
        try:
            common = os.path.commonpath(
                [candidate_text, os.path.normcase(str(root))]
            )
        except ValueError:
            continue
        if common == os.path.normcase(str(root)):
            return True
    return False


def unsafe_account_storage_overrides(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """현재 Windows 계정 폴더 밖을 가리키는 GWS 로그인 환경값 이름."""

    values = os.environ if environ is None else environ
    return tuple(
        key
        for key in _ACCOUNT_STORAGE_KEYS
        if str(values.get(key) or "").strip()
        and not _inside_current_account(str(values.get(key) or ""), values)
    )


def default_gws_config_dir(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    explicit = str(values.get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR") or "").strip()
    if explicit and _inside_current_account(explicit, values):
        return Path(explicit)
    return _user_default_gws_config_dir(values)


def default_client_file_candidates() -> list[Path]:
    """upstream 기본 파일과 Release 동봉 파일만 돌려준다.

    예전 제품 전용 ``%USERPROFILE%\\TeacherTaskManager\\gws-oauth-client.json``은
    desktop client를 credentials override로 잘못 분류하던 길이므로 후보가 아니다.
    """
    from brity_bridge import bundle_paths

    return [
        default_gws_config_dir() / UPSTREAM_CLIENT_FILE_NAME,
        bundle_paths.bundle_root() / "assets" / CLIENT_FILE_NAME,
    ]


def _read_desktop_client(path: Path) -> tuple[str, str] | None:
    try:
        client = parse_desktop_oauth_bytes(Path(path).read_bytes())
    except (OSError, OAuthClientFormatError):
        return None
    return client.client_id, client.client_secret


def _selection(
    ready: bool,
    source: Literal[
        "explicit_client_env",
        "gws_config",
        "bundled_client",
        "missing",
        "invalid",
        "conflict",
    ],
    *,
    values: tuple[str, str] | None = None,
    error_code: str = "",
) -> OAuthClientSelection:
    client_id, client_secret = values or ("", "")
    return OAuthClientSelection(ready, source, client_id, client_secret, error_code)


def select_desktop_oauth_client(
    environ: Mapping[str, str],
    gws_config_dir: Path,
    bundled_client_path: Path | None,
) -> OAuthClientSelection:
    """로그인용 데스크톱 OAuth 한 쌍을 고르되 값은 화면에 내보내지 않는다."""
    explicit_id = str(environ.get("GOOGLE_WORKSPACE_CLI_CLIENT_ID") or "").strip()
    explicit_secret = str(environ.get("GOOGLE_WORKSPACE_CLI_CLIENT_SECRET") or "").strip()
    if bool(explicit_id) != bool(explicit_secret):
        return _selection(
            False,
            "invalid",
            error_code="OAUTH_CLIENT_ENV_INCOMPLETE",
        )
    if explicit_id and explicit_secret:
        return _selection(
            True,
            "explicit_client_env",
            values=(explicit_id, explicit_secret),
        )

    config_path = Path(gws_config_dir) / UPSTREAM_CLIENT_FILE_NAME
    bundled_path = Path(bundled_client_path) if bundled_client_path is not None else None
    config_exists = config_path.is_file()
    bundled_exists = bundled_path is not None and bundled_path.is_file()
    config_values = _read_desktop_client(config_path) if config_exists else None
    bundled_values = _read_desktop_client(bundled_path) if bundled_exists else None

    if config_exists and config_values is None:
        return _selection(False, "invalid", error_code="OAUTH_CONFIG_CLIENT_INVALID")
    if bundled_exists and bundled_values is None:
        return _selection(False, "invalid", error_code="OAUTH_BUNDLED_CLIENT_INVALID")
    if config_values is not None and bundled_values is not None:
        if config_values != bundled_values:
            return _selection(False, "conflict", error_code="OAUTH_CLIENT_CONFLICT")
        return _selection(True, "gws_config", values=config_values)
    if config_values is not None:
        return _selection(True, "gws_config", values=config_values)
    if bundled_values is not None:
        return _selection(True, "bundled_client", values=bundled_values)
    return _selection(False, "missing", error_code="OAUTH_CLIENT_MISSING")


def inspect_gws_login_store(
    config_dir: Path,
    environ: Mapping[str, str],
) -> GwsLoginStoreSnapshot:
    """로그인 저장소는 존재 여부만 보고 절대 만들거나 고치지 않는다."""
    root = Path(config_dir)
    backend = str(environ.get("GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND") or "").strip()
    return GwsLoginStoreSnapshot(
        credentials_enc_exists=(root / "credentials.enc").is_file(),
        file_encryption_key_exists=(root / ".encryption_key").is_file(),
        explicit_keyring_backend=backend,
    )


def prepare_gws_env(environ=os.environ, client_file_candidates=None) -> None:
    """옛 호출 자리와의 호환용 무동작 함수.

    이전 구현은 프로그램 전체 환경에 file keyring과 잘못된 credentials 파일을
    강제로 넣었다. 이제 자식 명령마다 :func:`gws_environ` 또는
    :func:`login_environ` 사본을 사용하므로 부모 환경은 그대로 둔다.
    """
    del environ, client_file_candidates


def gws_environ(base: Mapping[str, str] | None = None, client_file_candidates=None) -> dict[str, str]:
    """일반 GWS 자식에 넘길 계정별 환경 사본.

    공용 또는 다른 Windows 계정의 로그인 폴더를 가리키는 환경값은 자식에게
    넘기지 않는다. CONFIG_DIR은 현재 계정의 기본 폴더로 되돌린다.
    """
    del client_file_candidates
    values = os.environ if base is None else base
    made = dict(values)
    unsafe = set(unsafe_account_storage_overrides(values))
    if _running_on_windows() and not str(
        made.get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR") or ""
    ).strip():
        # USERPROFILE 환경값이 변조돼도 upstream 기본 폴더 계산에 맡기지 않는다.
        made["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = str(
            _user_default_gws_config_dir(values)
        )
    elif "GOOGLE_WORKSPACE_CLI_CONFIG_DIR" in unsafe:
        made["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = str(
            _user_default_gws_config_dir(values)
        )
    if "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE" in unsafe:
        made.pop("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", None)
    return made


def login_environ(
    base: Mapping[str, str],
    selection: OAuthClientSelection,
) -> dict[str, str]:
    """``gws auth login`` 자식 하나에만 필요한 client 값을 넣는다."""
    made = gws_environ(base)
    if not selection.ready:
        return made
    if selection.source in {"explicit_client_env", "bundled_client"}:
        made["GOOGLE_WORKSPACE_CLI_CLIENT_ID"] = selection.client_id
        made["GOOGLE_WORKSPACE_CLI_CLIENT_SECRET"] = selection.client_secret
    return made
