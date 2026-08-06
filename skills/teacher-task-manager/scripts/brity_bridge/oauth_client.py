"""빌드와 실행 중에 똑같이 쓰는 Google 데스크톱 OAuth 파일 판정.

Google이 내려 주는 ``installed`` 형식의 필수 여섯 항목만 요구한다. upstream
파일에 함께 오는 ``auth_provider_x509_cert_url`` 한 항목은 공식 고정 주소일
때만 선택적으로 허용하지만, 제품이 다시 만드는 동봉 파일에서는 생략한다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


MAX_OAUTH_BYTES = 64 * 1024
REQUIRED_DESKTOP_KEYS = {
    "client_id",
    "client_secret",
    "project_id",
    "auth_uri",
    "token_uri",
    "redirect_uris",
}
OPTIONAL_UPSTREAM_KEYS = {"auth_provider_x509_cert_url"}
DANGEROUS_KEYS = {
    "type",
    "private_key",
    "private_key_id",
    "access_token",
    "refresh_token",
    "id_token",
    "client_email",
}
_PLACEHOLDER = re.compile(
    r"(?i)^(?:your(?:[_ -].*)?|replace(?:[_ -].*)?|change.?me(?:[_ -].*)?|"
    r"placeholder(?:[_ -].*)?|example(?:[_ -].*)?|dummy(?:[_ -].*)?|test(?:[_ -].*)?)$"
)
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,62}[a-z0-9]$")


class OAuthClientFormatError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True)
class DesktopOAuthClient:
    client_id: str
    client_secret: str
    project_id: str
    auth_uri: str
    token_uri: str
    redirect_uris: tuple[str, ...]


def _fail(code: str, detail: str) -> None:
    raise OAuthClientFormatError(code, detail)


def _unique_pairs(pairs):
    made = {}
    for key, value in pairs:
        if key in made:
            _fail("OAUTH_JSON_INVALID", "JSON에 같은 항목 이름이 두 번 있습니다.")
        made[key] = value
    return made


def _contains_control(value) -> bool:
    if isinstance(value, str):
        return any(ord(character) < 32 for character in value)
    if isinstance(value, list):
        return any(_contains_control(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_control(key) or _contains_control(item) for key, item in value.items())
    return False


def _safe_text(value, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("OAUTH_VALUE_INVALID", f"{label} 값이 비었거나 앞뒤에 공백이 있습니다.")
    if _PLACEHOLDER.search(value) or "<" in value or ">" in value:
        _fail("OAUTH_VALUE_INVALID", f"{label}에 임시 예시값을 쓸 수 없습니다.")
    return value


def parse_desktop_oauth_bytes(raw: bytes) -> DesktopOAuthClient:
    """비밀값을 오류에 넣지 않고 desktop client bytes를 엄격히 판정한다."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_OAUTH_BYTES:
        _fail("OAUTH_FILE_TOO_LARGE", "OAuth 입력은 1바이트 이상 64KiB 이하여야 합니다.")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except OAuthClientFormatError:
        raise
    except (UnicodeError, json.JSONDecodeError):
        _fail("OAUTH_JSON_INVALID", "OAuth JSON을 안전하게 읽지 못했습니다.")
    if _contains_control(data):
        _fail("OAUTH_VALUE_INVALID", "OAuth JSON 값에 제어 문자가 있습니다.")
    if not isinstance(data, dict) or set(data) != {"installed"}:
        _fail("OAUTH_SHAPE_INVALID", "Google 데스크톱 앱 형식의 installed 항목 하나만 허용합니다.")
    installed = data["installed"]
    if not isinstance(installed, dict):
        _fail("OAUTH_SHAPE_INVALID", "installed 항목이 올바른 묶음이 아닙니다.")
    keys = set(installed)
    if (
        not REQUIRED_DESKTOP_KEYS.issubset(keys)
        or not keys.issubset(REQUIRED_DESKTOP_KEYS | OPTIONAL_UPSTREAM_KEYS)
        or keys & DANGEROUS_KEYS
    ):
        _fail("OAUTH_SHAPE_INVALID", "OAuth installed 항목의 이름이 승인한 데스크톱 형식과 다릅니다.")
    client_id = _safe_text(installed["client_id"], "client_id")
    client_secret = _safe_text(installed["client_secret"], "client_secret")
    project_id = _safe_text(installed["project_id"], "project_id")
    auth_uri = _safe_text(installed["auth_uri"], "auth_uri")
    token_uri = _safe_text(installed["token_uri"], "token_uri")
    redirects = installed["redirect_uris"]
    if not _CLIENT_ID.fullmatch(client_id) or not _PROJECT.fullmatch(project_id):
        _fail("OAUTH_VALUE_INVALID", "OAuth 앱 이름 또는 프로젝트 이름이 공식 데스크톱 형식과 다릅니다.")
    if auth_uri != "https://accounts.google.com/o/oauth2/auth" or token_uri != "https://oauth2.googleapis.com/token":
        _fail("OAUTH_VALUE_INVALID", "Google 인증 주소가 공식 고정 주소와 다릅니다.")
    if redirects != ["http://localhost"]:
        _fail("OAUTH_VALUE_INVALID", "redirect_uris에는 포트 없는 http://localhost 하나만 허용합니다.")
    optional = installed.get("auth_provider_x509_cert_url")
    if optional is not None and optional != "https://www.googleapis.com/oauth2/v1/certs":
        _fail("OAUTH_VALUE_INVALID", "Google 인증서 주소가 공식 고정 주소와 다릅니다.")
    return DesktopOAuthClient(
        client_id,
        client_secret,
        project_id,
        auth_uri,
        token_uri,
        tuple(redirects),
    )
