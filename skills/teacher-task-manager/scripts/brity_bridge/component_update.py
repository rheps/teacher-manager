"""사용자가 선택할 수 있는 승인된 Google Workspace CLI 갱신 정보를 확인한다."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import stat
import struct
import time
import unicodedata
import urllib.error
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import urlopen

from . import bundle_paths, component_lock, deadline_io, process_win, tool_runtime


GWS_APPROVAL_MANIFEST_URL = (
    "https://github.com/rheps/teacher-manager/releases/latest/download/gws-version.json"
)
_APPROVAL_KEYS = {
    "schema_version",
    "version",
    "platform",
    "architecture",
    "archive_url",
    "archive_filename",
    "archive_sha256",
    "executable_sha256",
    "app_min_version",
    "app_max_version",
    "login_store_compatible",
    "verified_on",
    "notes",
}
_ALLOWED_MANIFEST_FINAL_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GWS_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_APP_VERSION = re.compile(r"^(\d+(?:\.\d+){1,3})(?:[-+].*)?$")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_APPROVAL_NOTES_CHARS = 300
_VERIFIED_ON = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHUNK_SIZE = 1024 * 1024
_UPDATE_LOCK_TIMEOUT_SECONDS = 2.0
_ALLOWED_ASSET_FINAL_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
_OFFICIAL_ARCHIVE_MEMBERS = {
    "CHANGELOG.md",
    "gws.exe",
    "LICENSE",
    "README.md",
}
_MAX_GWS_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_GWS_EXECUTABLE_BYTES = 128 * 1024 * 1024
_MAX_GWS_ARCHIVE_EXPANDED_BYTES = 160 * 1024 * 1024
_MAX_GWS_COMPRESSION_RATIO = 100
_MAX_GWS_CENTRAL_DIRECTORY_BYTES = 64 * 1024
_ZIP_EOCD = struct.Struct("<4s4H2LH")
_ZIP_CENTRAL_HEADER = struct.Struct("<4s6H3L5H2L")
_TRANSIENT_BAD_VERSION_CODES = {
    "NETWORK_OFFLINE",
    "NETWORK_PROXY_AUTH_REQUIRED",
    "NETWORK_TLS_INSPECTION_BLOCKED",
    "NETWORK_TIMEOUT",
    "APPROVAL_SERVER_UNAVAILABLE",
    "GWS_DOWNLOAD_NOT_FOUND",
    "GWS_DOWNLOAD_SERVER_UNAVAILABLE",
    "COMPONENT_DISK_FULL",
    "COMPONENT_FILE_LOCKED",
    "COMPONENT_DIR_NOT_WRITABLE",
    "COMPONENT_UPDATE_FAILED",
    "COMPONENT_SECURITY_BLOCKED",
    "GWS_DOWNLOAD_REDIRECT_UNSAFE",
}
_OWNED_PARTIAL_NAME = re.compile(
    r"^\.google-workspace-cli-x86_64-pc-windows-msvc\.zip\.\d+\.[0-9a-f]{32}\.partial$"
)
_OWNED_STAGING_NAME = re.compile(
    r"^\.\d+\.\d+\.\d+\.\d+\.[0-9a-f]{32}\.staging$"
)


class ApprovedManifestError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ApprovedGwsManifest:
    schema_version: int
    version: str
    platform: str
    architecture: str
    archive_url: str
    archive_filename: str
    archive_sha256: str
    executable_sha256: str
    app_min_version: str
    app_max_version: str
    login_store_compatible: bool
    verified_on: str
    notes: str


@dataclass(frozen=True)
class GwsUpdateOffer:
    manifest: ApprovedGwsManifest
    checked_on: str
    approval_bytes: bytes
    approval_sha256: str


@dataclass(frozen=True)
class GwsUpdateCheck:
    success: bool
    code: str
    detail: str
    offer: GwsUpdateOffer | None
    checked_on: str


@dataclass(frozen=True)
class GwsUpdateResult:
    success: bool
    code: str
    detail: str
    resolution: tool_runtime.GwsResolution | None


class _InstallError(RuntimeError):
    def __init__(self, code: str, detail: str, *, mark_bad: bool = True):
        self.code = code
        self.detail = detail
        self.mark_bad = mark_bad
        super().__init__(f"{code}: {detail}")


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApprovedManifestError("MANIFEST_INVALID", f"{key} 값이 없습니다.")
    return value


def _approval_notes(value: str) -> str:
    """화면과 확인창에 보여도 모양을 속일 수 없는 짧은 승인 설명만 받는다."""
    if len(value) > _MAX_APPROVAL_NOTES_CHARS:
        raise ApprovedManifestError("MANIFEST_INVALID", "notes는 300자 이하여야 합니다.")
    # 줄바꿈은 읽기 좋은 설명에 필요하지만, NUL·방향 뒤집기·숨은 제어 문자는
    # 파일 이름이나 다음 문장을 다른 것처럼 보이게 할 수 있으므로 승인하지 않는다.
    if any(ch != "\n" and unicodedata.category(ch).startswith("C") for ch in value):
        raise ApprovedManifestError("MANIFEST_INVALID", "notes에 화면용으로 쓸 수 없는 문자가 있습니다.")
    return value


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    match = _APP_VERSION.fullmatch(value)
    if not match:
        raise ApprovedManifestError("MANIFEST_INVALID", "앱 판 번호 형식이 맞지 않습니다.")
    numbers = [int(part) for part in match.group(1).split(".")]
    return tuple((numbers + [0] * 4)[:4])


def parse_approved_gws_manifest(
    raw: Mapping[str, object], current_app_version: str
) -> ApprovedGwsManifest:
    if not isinstance(raw, Mapping) or set(raw) != _APPROVAL_KEYS:
        raise ApprovedManifestError("MANIFEST_INVALID", "승인 항목이 빠졌거나 알 수 없는 항목이 있습니다.")
    if type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
        raise ApprovedManifestError("MANIFEST_INVALID", "schema_version 1이 필요합니다.")
    if raw.get("login_store_compatible") is not True:
        raise ApprovedManifestError(
            "LOGIN_STORE_NOT_APPROVED", "기존 Google 로그인 보존이 실제로 승인되지 않았습니다."
        )
    values = {key: _text(raw, key) for key in _APPROVAL_KEYS if key not in {"schema_version", "login_store_compatible"}}
    version = values["version"]
    if not _GWS_VERSION.fullmatch(version):
        raise ApprovedManifestError("MANIFEST_INVALID", "GWS 판 번호 형식이 맞지 않습니다.")
    if values["platform"] != "windows" or values["architecture"] != "x86_64":
        raise ApprovedManifestError("MANIFEST_INVALID", "Windows x64 승인본만 받을 수 있습니다.")
    filename = "google-workspace-cli-x86_64-pc-windows-msvc.zip"
    if values["archive_filename"] != filename:
        raise ApprovedManifestError("MANIFEST_INVALID", "공식 Windows 압축 파일 이름이 아닙니다.")
    parsed = urlparse(values["archive_url"])
    expected_path = f"/googleworkspace/cli/releases/download/v{version}/{filename}"
    try:
        parsed_port = parsed.port
    except ValueError as error:
        raise ApprovedManifestError("MANIFEST_INVALID", "공식 GWS 주소의 포트 형식이 맞지 않습니다.") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed_port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
    ):
        raise ApprovedManifestError("MANIFEST_INVALID", "공식 고정 GWS Release 주소가 아닙니다.")
    if not _SHA256.fullmatch(values["archive_sha256"]) or not _SHA256.fullmatch(values["executable_sha256"]):
        raise ApprovedManifestError("MANIFEST_INVALID", "두 SHA-256 값이 맞지 않습니다.")
    if not _VERIFIED_ON.fullmatch(values["verified_on"]):
        raise ApprovedManifestError("MANIFEST_INVALID", "승인 날짜 형식이 맞지 않습니다.")
    try:
        date.fromisoformat(values["verified_on"])
    except ValueError as error:
        raise ApprovedManifestError("MANIFEST_INVALID", "승인 날짜 형식이 맞지 않습니다.") from error
    minimum = _version_tuple(values["app_min_version"])
    maximum = _version_tuple(values["app_max_version"])
    current = _version_tuple(current_app_version)
    if minimum > maximum or not minimum <= current <= maximum:
        raise ApprovedManifestError("APP_VERSION_NOT_APPROVED", "현재 Teacher Manager 판은 이 GWS 승인 범위에 없습니다.")
    return ApprovedGwsManifest(
        schema_version=1,
        version=version,
        platform=values["platform"],
        architecture=values["architecture"],
        archive_url=values["archive_url"],
        archive_filename=values["archive_filename"],
        archive_sha256=values["archive_sha256"],
        executable_sha256=values["executable_sha256"],
        app_min_version=values["app_min_version"],
        app_max_version=values["app_max_version"],
        login_store_compatible=True,
        verified_on=values["verified_on"],
        notes=_approval_notes(values["notes"]),
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ApprovedManifestError("MANIFEST_INVALID", f"중복 항목이 있습니다: {key}")
        result[key] = value
    return result


def _safe_json_bytes(raw: bytes) -> Mapping[str, object]:
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise ApprovedManifestError("MANIFEST_INVALID", "승인 파일 크기가 맞지 않습니다.")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except ApprovedManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovedManifestError("MANIFEST_INVALID", "승인 JSON을 읽지 못했습니다.") from error
    if not isinstance(value, Mapping):
        raise ApprovedManifestError("MANIFEST_INVALID", "승인 JSON 바깥 형식이 맞지 않습니다.")
    return value


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _state_path(root: Path) -> Path:
    return root / "update-check.json"


def _write_state(root: Path, state: Mapping[str, object]) -> None:
    component_lock.atomic_write_text_unique(
        _state_path(root), json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n"
    )


def _read_cached(
    root: Path,
    day: str,
    current_app_version: str,
    bundled_gws_version: str,
) -> GwsUpdateCheck | None:
    try:
        raw = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected = {
        "schema_version",
        "checked_on",
        "app_version",
        "bundled_gws_version",
        "success",
        "code",
        "detail",
        "offer_file",
        "offer_sha256",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != expected
        or raw.get("schema_version") != 1
        or raw.get("checked_on") != day
        or raw.get("app_version") != current_app_version
        or raw.get("bundled_gws_version") != bundled_gws_version
    ):
        return None
    # 인터넷 끊김, 기관 프록시, 시간 초과, 서버 장애, 로컬 저장 실패는 잠깐 뒤
    # 풀릴 수 있다. 예전 판이 남긴 실패 기록도 같은 날 [다시 점검]에서 재사용하지
    # 않는다. 하루 동안 되돌려 주는 기록은 정상 확인 결과만 허용한다.
    if raw.get("success") is not True:
        return None
    code = raw.get("code")
    detail = raw.get("detail")
    if (
        code == "UP_TO_DATE"
        and isinstance(detail, str)
        and raw.get("offer_file") == ""
        and raw.get("offer_sha256") == ""
    ):
        return GwsUpdateCheck(True, code, detail, None, day)
    if code != "UPDATE_AVAILABLE" or not isinstance(detail, str):
        return None
    filename = raw.get("offer_file")
    digest = raw.get("offer_sha256")
    if not isinstance(filename, str) or Path(filename).name != filename or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return None
    try:
        approval_bytes = (root / "offers" / filename).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(approval_bytes).hexdigest() != digest:
        return None
    try:
        manifest = parse_approved_gws_manifest(_safe_json_bytes(approval_bytes), current_app_version)
    except ApprovedManifestError:
        return None
    offer = GwsUpdateOffer(manifest, day, approval_bytes, digest)
    return GwsUpdateCheck(True, "UPDATE_AVAILABLE", detail, offer, day)


def _safe_failure(error: BaseException) -> tuple[str, str]:
    if isinstance(error, urllib.error.HTTPError) and error.code == 404:
        return (
            "APPROVAL_NOT_PUBLISHED",
            "새 Google 도구 승인 파일이 아직 공개되지 않아 현재 기본판을 계속 사용합니다.",
        )
    if isinstance(error, urllib.error.HTTPError) and error.code == 407:
        return "NETWORK_PROXY_AUTH_REQUIRED", "학교나 기관의 인터넷 인증이 필요해 확인하지 못했습니다."
    if isinstance(error, urllib.error.HTTPError):
        # 서버가 HTTP 응답을 돌려줬으므로 인터넷이 없는 상황은 아니다. 상태 번호나
        # 서버 원문은 화면에 내보내지 않고, 잠시 뒤 재시도할 수 있다는 것만 알린다.
        return (
            "APPROVAL_SERVER_UNAVAILABLE",
            "새 Google 도구 승인 파일 서버가 응답했지만 지금 확인을 마치지 못했습니다. 잠시 뒤 다시 점검해 주세요.",
        )
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "NETWORK_TIMEOUT", "인터넷 응답을 기다리는 시간이 지났습니다."
    if isinstance(error, (ssl.SSLCertVerificationError, ssl.CertificateError)):
        return "NETWORK_TLS_INSPECTION_BLOCKED", "보안 인증서 확인 때문에 인터넷 연결을 확인하지 못했습니다."
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return "NETWORK_TLS_INSPECTION_BLOCKED", "보안 인증서 확인 때문에 인터넷 연결을 확인하지 못했습니다."
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "NETWORK_TIMEOUT", "인터넷 응답을 기다리는 시간이 지났습니다."
        return "NETWORK_OFFLINE", "인터넷에 연결되지 않아 새 Google 도구를 확인하지 못했습니다."
    if isinstance(error, OSError):
        # 내려받는 도중 Windows 통신 계층이 평범한 OSError만 던지는 경우가 있다.
        return "NETWORK_OFFLINE", "인터넷에 연결되지 않아 새 Google 도구를 확인하지 못했습니다."
    return (
        "APPROVAL_CHECK_FAILED",
        "새 Google 도구 승인 파일을 확인하는 중 문제가 생겼습니다. 잠시 뒤 다시 점검해 주세요.",
    )


def _safe_final_manifest_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in _ALLOWED_MANIFEST_FINAL_HOSTS
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _bundled_gws_version() -> str:
    try:
        return tool_runtime._required_gws_spec(bundle_paths.bundle_root(), None).version
    except tool_runtime.GwsRuntimeError as error:
        raise _InstallError(
            error.code,
            "설치된 Teacher Manager의 기본 Google 도구 정보를 읽지 못했습니다. 설치 파일을 다시 실행해 주세요.",
            mark_bad=False,
        ) from error


def check_gws_update(
    current_app_version: str,
    *,
    component_root: Path | None = None,
    opener=urlopen,
    today: date | None = None,
    timeout_seconds: float = 15.0,
) -> GwsUpdateCheck:
    day = (today or date.today()).isoformat()
    try:
        root = Path(component_root) if component_root is not None else tool_runtime.component_gws_root()
    except tool_runtime.GwsRuntimeError as error:
        return GwsUpdateCheck(
            False,
            error.code,
            "새 Google 도구를 저장할 Windows 폴더를 사용할 수 없어 갱신을 확인하지 못했습니다. "
            "설치본에 든 기본 Google 도구는 계속 사용할 수 있습니다.",
            None,
            day,
        )
    try:
        bundled_gws_version = _bundled_gws_version()
        _ensure_safe_component_directory(root, root)
        _ensure_safe_component_directory(root, root / "locks")
        _ensure_safe_component_directory(root, root / "offers")
        check_lock = root / "locks" / "update-check.lock"
        _ensure_safe_lock_entry(root, check_lock)
        with component_lock.exclusive_file_lock(check_lock, timeout=2.0):
            # 잠금을 기다리는 동안 폴더나 잠금 파일이 바뀌지 않았는지 다시 본다.
            _ensure_safe_component_directory(root, root)
            _ensure_safe_component_directory(root, root / "locks")
            _ensure_safe_component_directory(root, root / "offers")
            _ensure_safe_lock_entry(root, check_lock)
            cached = _read_cached(
                root,
                day,
                current_app_version,
                bundled_gws_version,
            )
            if cached is not None:
                return cached
            download_finished = False
            try:
                deadline = time.monotonic() + max(0.0, float(timeout_seconds))
                with opener(GWS_APPROVAL_MANIFEST_URL, timeout=float(timeout_seconds)) as response:
                    final_url = response.geturl()
                    if not _safe_final_manifest_url(final_url):
                        raise ApprovedManifestError("MANIFEST_DOWNLOAD_UNSAFE", "공식 GitHub 다운로드 서버로 끝나지 않았습니다.")
                    approval_bytes = deadline_io.read_before(
                        response,
                        _MAX_MANIFEST_BYTES + 1,
                        deadline,
                    )
                download_finished = True
                manifest = parse_approved_gws_manifest(_safe_json_bytes(approval_bytes), current_app_version)
                if tool_runtime._compare_versions(
                    manifest.version, bundled_gws_version
                ) <= 0:
                    result = GwsUpdateCheck(
                        True,
                        "UP_TO_DATE",
                        "현재 기본 Google 도구가 승인 목록과 같거나 더 최신입니다.",
                        None,
                        day,
                    )
                    _write_state(root, {
                        "schema_version": 1,
                        "checked_on": day,
                        "app_version": current_app_version,
                        "bundled_gws_version": bundled_gws_version,
                        "success": True,
                        "code": result.code,
                        "detail": result.detail,
                        "offer_file": "",
                        "offer_sha256": "",
                    })
                    return result
                digest = hashlib.sha256(approval_bytes).hexdigest()
                filename = f"gws-{manifest.version}-{digest}.json"
                _atomic_write_bytes(root / "offers" / filename, approval_bytes)
                result = GwsUpdateCheck(
                    True,
                    "UPDATE_AVAILABLE",
                    "승인된 Google 도구 새 판을 확인했습니다.",
                    GwsUpdateOffer(manifest, day, approval_bytes, digest),
                    day,
                )
                _write_state(root, {
                    "schema_version": 1,
                    "checked_on": day,
                    "app_version": current_app_version,
                    "bundled_gws_version": bundled_gws_version,
                    "success": True,
                    "code": result.code,
                    "detail": result.detail,
                    "offer_file": filename,
                    "offer_sha256": digest,
                })
                return result
            except ApprovedManifestError as error:
                code = error.code
                detail = "공식 승인 파일의 모양이 맞지 않아 현재 기본판을 계속 사용합니다."
            except Exception as error:  # 인터넷 라이브러리의 여러 Windows 오류를 안전 문장으로 줄인다.
                if download_finished and isinstance(error, OSError):
                    local = _local_failure(error, stage="write")
                    code, detail = local.code, local.detail
                else:
                    code, detail = _safe_failure(error)
            result = GwsUpdateCheck(False, code, detail, None, day)
            return result
    except _InstallError as error:
        return GwsUpdateCheck(False, error.code, error.detail, None, day)
    except (OSError, TimeoutError):
        return GwsUpdateCheck(
            False,
            "COMPONENT_CHECK_BUSY",
            "다른 준비 작업이 끝난 뒤 다시 확인해 주세요.",
            None,
            day,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(_CHUNK_SIZE):
            digest.update(block)
    return digest.hexdigest()


def _safe_asset_final_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in _ALLOWED_ASSET_FINAL_HOSTS
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _local_failure(error: BaseException, *, stage: str) -> _InstallError:
    number = getattr(error, "errno", None)
    winerror = getattr(error, "winerror", None)
    if number == getattr(os, "ENOSPC", 28) or number == 28:
        return _InstallError(
            "COMPONENT_DISK_FULL",
            "저장 공간이 부족해 Google 도구를 갱신하지 못했습니다.",
            mark_bad=False,
        )
    if winerror in {32, 33}:
        return _InstallError(
            "COMPONENT_FILE_LOCKED",
            "다른 프로그램이 갱신 파일을 사용하고 있습니다.",
            mark_bad=False,
        )
    if stage == "execute":
        return _InstallError(
            "COMPONENT_SECURITY_BLOCKED",
            "보안 프로그램이 새 Google 도구 실행을 막았습니다.",
            mark_bad=False,
        )
    if isinstance(error, PermissionError) or number in {
        getattr(os, "EACCES", 13),
        getattr(os, "EPERM", 1),
        getattr(os, "EROFS", 30),
    }:
        return _InstallError(
            "COMPONENT_DIR_NOT_WRITABLE",
            "구성요소 폴더에 파일을 쓸 수 없습니다.",
            mark_bad=False,
        )
    return _InstallError(
        "COMPONENT_UPDATE_FAILED",
        "Google 도구 갱신 파일을 안전하게 저장하지 못했습니다.",
        mark_bad=False,
    )


def _network_failure(error: BaseException) -> _InstallError:
    number = getattr(error, "errno", None)
    winerror = getattr(error, "winerror", None)
    if number == 28:
        return _local_failure(error, stage="write")
    if winerror in {32, 33}:
        return _local_failure(error, stage="write")
    if isinstance(error, urllib.error.HTTPError) and error.code == 404:
        return _InstallError(
            "GWS_DOWNLOAD_NOT_FOUND",
            "승인된 Google 도구의 공식 압축 파일을 찾지 못했습니다. 잠시 뒤 다시 점검해 주세요.",
            mark_bad=False,
        )
    if isinstance(error, urllib.error.HTTPError) and error.code != 407:
        return _InstallError(
            "GWS_DOWNLOAD_SERVER_UNAVAILABLE",
            "Google 공식 파일 서버가 응답했지만 지금 압축 파일을 받지 못했습니다. 잠시 뒤 다시 눌러 주세요.",
            mark_bad=False,
        )
    code, detail = _safe_failure(error)
    return _InstallError(code, detail, mark_bad=False)


def _validate_offer_again(offer: GwsUpdateOffer) -> ApprovedGwsManifest:
    if not isinstance(offer, GwsUpdateOffer):
        raise _InstallError("UPDATE_OFFER_CHANGED", "확인했던 갱신 정보가 달라져 적용하지 않았습니다.", mark_bad=False)
    digest = hashlib.sha256(offer.approval_bytes).hexdigest()
    if digest != offer.approval_sha256:
        raise _InstallError("UPDATE_OFFER_CHANGED", "확인했던 갱신 정보가 달라져 적용하지 않았습니다.", mark_bad=False)
    try:
        parsed = parse_approved_gws_manifest(
            _safe_json_bytes(offer.approval_bytes),
            tool_runtime.current_app_version(),
        )
    except (ApprovedManifestError, OSError, ValueError) as error:
        raise _InstallError(
            "UPDATE_OFFER_CHANGED",
            "확인했던 갱신 정보를 다시 확인하지 못해 적용하지 않았습니다.",
            mark_bad=False,
        ) from error
    if parsed != offer.manifest:
        raise _InstallError("UPDATE_OFFER_CHANGED", "확인했던 갱신 정보가 달라져 적용하지 않았습니다.", mark_bad=False)
    return parsed


def _offer_not_approved() -> _InstallError:
    return _InstallError(
        "UPDATE_OFFER_NOT_APPROVED",
        "공식 확인 단계에서 저장한 것과 같은 Google 도구 갱신 정보가 아닙니다.",
        mark_bad=False,
    )


def _validate_saved_official_offer(root: Path, offer: GwsUpdateOffer) -> None:
    """공식 조회가 원자 저장한 결정과 설치 버튼의 원문이 정확히 같은지 확인한다."""
    filename = f"gws-{offer.manifest.version}-{offer.approval_sha256}.json"
    expected_state_fields = {
        "schema_version",
        "checked_on",
        "app_version",
        "bundled_gws_version",
        "success",
        "code",
        "detail",
        "offer_file",
        "offer_sha256",
    }
    state_path = root / "update-check.json"
    saved_path = root / "offers" / filename
    try:
        if state_path.resolve(strict=True) != _absolute_path(state_path):
            raise _offer_not_approved()
        if saved_path.resolve(strict=True) != _absolute_path(saved_path):
            raise _offer_not_approved()
        state = _safe_json_bytes(state_path.read_bytes())
        saved = saved_path.read_bytes()
    except (OSError, ApprovedManifestError):
        raise _offer_not_approved()
    if (
        set(state) != expected_state_fields
        or type(state.get("schema_version")) is not int
        or state.get("schema_version") != 1
        or state.get("checked_on") != offer.checked_on
        or state.get("app_version") != tool_runtime.current_app_version()
        or state.get("bundled_gws_version") != _bundled_gws_version()
        or state.get("success") is not True
        or state.get("code") != "UPDATE_AVAILABLE"
        or not isinstance(state.get("detail"), str)
        or state.get("offer_file") != filename
        or state.get("offer_sha256") != offer.approval_sha256
        or saved != offer.approval_bytes
        or hashlib.sha256(saved).hexdigest() != offer.approval_sha256
    ):
        raise _offer_not_approved()


def _path_unsafe() -> _InstallError:
    return _InstallError(
        "COMPONENT_PATH_UNSAFE",
        "Google 도구 구성요소 폴더가 정해진 위치 밖을 가리킵니다.",
        mark_bad=False,
    )


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(str(path)))


def _is_reparse_entry(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _assert_existing_path_is_direct(path: Path) -> None:
    """이미 있는 경로가 링크·정션을 거치지 않고 적힌 그 자리에 있는지 확인한다."""
    path = _absolute_path(path)
    if not os.path.lexists(path):
        return
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise _path_unsafe() from error
    if _is_reparse_entry(info) or resolved != path:
        raise _path_unsafe()


def _nearest_existing_ancestor(path: Path) -> Path:
    candidate = _absolute_path(path)
    while not os.path.lexists(candidate):
        parent = candidate.parent
        if parent == candidate:
            raise _path_unsafe()
        candidate = parent
    return candidate


def _ensure_safe_component_directory(root: Path, directory: Path) -> Path:
    """새 파일을 놓기 전에 root와 바로 아래 폴더의 링크·정션 탈출을 막는다."""
    expected_root = _absolute_path(root)
    expected_directory = _absolute_path(directory)
    if expected_directory != expected_root and not expected_directory.is_relative_to(expected_root):
        raise _path_unsafe()
    try:
        # parents=True가 연결 폴더 너머에 새 폴더를 만들기 전에, 가장 가까운
        # 기존 조상부터 실제로 적힌 그 자리인지 먼저 확인한다.
        _assert_existing_path_is_direct(_nearest_existing_ancestor(expected_root))
        root.mkdir(parents=True, exist_ok=True)
        _assert_existing_path_is_direct(expected_root)
        _assert_existing_path_is_direct(_nearest_existing_ancestor(expected_directory))
        directory.mkdir(parents=True, exist_ok=True)
        _assert_existing_path_is_direct(expected_directory)
        resolved = expected_directory.resolve(strict=True)
    except _InstallError:
        raise
    except OSError as error:
        raise _local_failure(error, stage="write") from error
    if resolved != expected_directory:
        raise _path_unsafe()
    return resolved


def _ensure_safe_lock_entry(root: Path, path: Path) -> None:
    """잠금 파일이 바깥 파일의 링크가 아닌지, 한 바이트를 쓰기 전에 확인한다."""
    expected_root = _absolute_path(root)
    expected = _absolute_path(path)
    if not expected.is_relative_to(expected_root):
        raise _path_unsafe()
    _ensure_safe_component_directory(root, expected.parent)
    if not os.path.lexists(expected):
        return
    try:
        info = expected.lstat()
        resolved = expected.resolve(strict=True)
    except OSError as error:
        raise _path_unsafe() from error
    if (
        _is_reparse_entry(info)
        or not stat.S_ISREG(info.st_mode)
        or int(getattr(info, "st_nlink", 1)) != 1
        or resolved != expected
    ):
        raise _path_unsafe()


def _download_archive(
    manifest: ApprovedGwsManifest,
    partial: Path,
    *,
    opener,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    try:
        response_context = opener(manifest.archive_url, timeout=float(timeout_seconds))
        with response_context as response:
            if not _safe_asset_final_url(response.geturl()):
                raise _InstallError(
                    "GWS_DOWNLOAD_REDIRECT_UNSAFE",
                    "공식 GitHub 다운로드 서버로 끝나지 않아 적용하지 않았습니다.",
                    mark_bad=False,
                )
            try:
                handle_context = partial.open("xb")
            except OSError as error:
                raise _local_failure(error, stage="write") from error
            with handle_context as handle:
                downloaded = 0
                while True:
                    try:
                        block = deadline_io.read_before(response, _CHUNK_SIZE, deadline)
                    except Exception as error:  # URL 응답은 Windows마다 다른 하위 오류를 낸다.
                        raise _network_failure(error) from error
                    if not block:
                        break
                    downloaded += len(block)
                    if downloaded > _MAX_GWS_ARCHIVE_BYTES:
                        raise _InstallError(
                            "GWS_ARCHIVE_LIMIT",
                            "받은 Google 도구 압축 파일이 안전 크기를 넘었습니다.",
                        )
                    try:
                        handle.write(block)
                    except OSError as error:
                        raise _local_failure(error, stage="write") from error
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError as error:
                    raise _local_failure(error, stage="write") from error
    except _InstallError:
        raise
    except Exception as error:
        raise _network_failure(error) from error


def _unsafe_zip_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    windows_absolute = len(name) >= 3 and name[1] == ":" and name[2] == "/"
    is_link = stat.S_ISLNK(info.external_attr >> 16)
    return (
        not name
        or name.startswith("/")
        or windows_absolute
        or ".." in path.parts
        or is_link
    )


def _zip_declared_entry_count(archive_path: Path) -> int:
    """ZipFile이 목록을 만들기 전에 중앙 디렉터리를 작게 나눠 직접 확인한다."""
    try:
        size = archive_path.stat().st_size
        if size > _MAX_GWS_ARCHIVE_BYTES:
            raise _InstallError(
                "GWS_ARCHIVE_LIMIT",
                "받은 Google 도구 압축 파일이 안전 크기를 넘었습니다.",
            )
        tail_start = max(0, size - (65535 + _ZIP_EOCD.size))
        with archive_path.open("rb") as handle:
            handle.seek(tail_start)
            tail = handle.read()
    except _InstallError:
        raise
    except OSError as error:
        raise _local_failure(error, stage="read") from error
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < _ZIP_EOCD.size:
        raise _InstallError("GWS_ARCHIVE_UNSAFE", "압축 파일의 끝 기록을 안전하게 읽지 못했습니다.")
    (
        _signature,
        disk,
        start_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment,
    ) = (
        _ZIP_EOCD.unpack_from(tail, offset)
    )
    if offset + _ZIP_EOCD.size + comment != len(tail):
        raise _InstallError("GWS_ARCHIVE_UNSAFE", "압축 파일의 끝 기록이 올바르지 않습니다.")
    expected_count = len(_OFFICIAL_ARCHIVE_MEMBERS)
    eocd_absolute = tail_start + offset
    if (
        disk != 0
        or start_disk != 0
        or disk_entries != total_entries
        or central_size > _MAX_GWS_CENTRAL_DIRECTORY_BYTES
        or central_offset + central_size != eocd_absolute
    ):
        raise _InstallError(
            "GWS_ARCHIVE_LAYOUT",
            "압축 파일의 중앙 목록 크기와 항목 수가 공식 Windows 압축본과 다릅니다.",
        )

    actual_entries = 0
    remaining = int(central_size)
    try:
        with archive_path.open("rb") as handle:
            handle.seek(int(central_offset))
            while remaining:
                if remaining < _ZIP_CENTRAL_HEADER.size:
                    raise _InstallError(
                        "GWS_ARCHIVE_UNSAFE",
                        "압축 파일의 중앙 목록이 중간에서 끊겼습니다.",
                    )
                fixed = handle.read(_ZIP_CENTRAL_HEADER.size)
                if len(fixed) != _ZIP_CENTRAL_HEADER.size:
                    raise _InstallError(
                        "GWS_ARCHIVE_UNSAFE",
                        "압축 파일의 중앙 목록을 끝까지 읽지 못했습니다.",
                    )
                fields = _ZIP_CENTRAL_HEADER.unpack(fixed)
                if fields[0] != b"PK\x01\x02" or fields[13] != 0:
                    raise _InstallError(
                        "GWS_ARCHIVE_UNSAFE",
                        "압축 파일의 중앙 목록 형식이 올바르지 않습니다.",
                    )
                variable_size = int(fields[10]) + int(fields[11]) + int(fields[12])
                entry_size = _ZIP_CENTRAL_HEADER.size + variable_size
                if entry_size > remaining:
                    raise _InstallError(
                        "GWS_ARCHIVE_UNSAFE",
                        "압축 파일의 중앙 목록 길이가 올바르지 않습니다.",
                    )
                variable = handle.read(variable_size)
                if len(variable) != variable_size:
                    raise _InstallError(
                        "GWS_ARCHIVE_UNSAFE",
                        "압축 파일의 중앙 목록을 끝까지 읽지 못했습니다.",
                    )
                name_size = int(fields[10])
                try:
                    member_name = variable[:name_size].decode(
                        "utf-8" if int(fields[3]) & 0x800 else "cp437"
                    )
                except UnicodeDecodeError as error:
                    raise _InstallError(
                        "GWS_ARCHIVE_UNSAFE",
                        "압축 파일의 항목 이름을 안전하게 읽지 못했습니다.",
                    ) from error
                probe = zipfile.ZipInfo(member_name)
                probe.external_attr = int(fields[15])
                if member_name.endswith(("/", "\\")) or _unsafe_zip_member(probe):
                    raise _InstallError(
                        "GWS_ARCHIVE_UNSAFE",
                        "압축 파일에 안전하지 않은 경로나 링크가 있습니다.",
                    )
                remaining -= entry_size
                actual_entries += 1
                if actual_entries > expected_count:
                    raise _InstallError(
                        "GWS_ARCHIVE_LAYOUT",
                        "압축 파일의 항목 수가 공식 Windows 압축본과 다릅니다.",
                    )
    except _InstallError:
        raise
    except OSError as error:
        raise _local_failure(error, stage="read") from error
    if actual_entries != expected_count:
        raise _InstallError(
            "GWS_ARCHIVE_LAYOUT",
            "압축 파일의 항목 수가 공식 Windows 압축본과 다릅니다.",
        )
    return actual_entries


def _archive_layout_is_safe(infos: list[zipfile.ZipInfo]) -> bool:
    if len(infos) != len(_OFFICIAL_ARCHIVE_MEMBERS):
        return False
    names = [info.filename.replace("\\", "/") for info in infos]
    if set(names) != _OFFICIAL_ARCHIVE_MEMBERS or len(set(names)) != len(names):
        return False
    expanded = 0
    for info in infos:
        if info.is_dir() or _unsafe_zip_member(info):
            return False
        expanded += int(info.file_size)
        if expanded > _MAX_GWS_ARCHIVE_EXPANDED_BYTES:
            return False
        if info.filename == "gws.exe" and info.file_size > _MAX_GWS_EXECUTABLE_BYTES:
            return False
        allowed_expansion = max(1024 * 1024, int(info.compress_size) * _MAX_GWS_COMPRESSION_RATIO)
        if info.file_size > allowed_expansion:
            return False
    return True


def _extract_verified_executable(
    archive_path: Path,
    manifest: ApprovedGwsManifest,
    staging: Path,
) -> Path:
    try:
        archive_digest = _sha256_file(archive_path)
    except OSError as error:
        raise _local_failure(error, stage="read") from error
    if archive_digest != manifest.archive_sha256:
        raise _InstallError("GWS_ARCHIVE_SHA256_MISMATCH", "받은 압축 파일의 SHA-256이 승인값과 다릅니다.")
    if _zip_declared_entry_count(archive_path) > 64:
        raise _InstallError(
            "GWS_ARCHIVE_LAYOUT",
            "압축 파일의 항목이 지나치게 많습니다.",
        )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if any(info.is_dir() or _unsafe_zip_member(info) for info in infos):
                raise _InstallError(
                    "GWS_ARCHIVE_UNSAFE",
                    "압축 파일에 안전하지 않은 경로나 링크가 있습니다.",
                )
            if not _archive_layout_is_safe(infos):
                if len(infos) != len(_OFFICIAL_ARCHIVE_MEMBERS) or {
                    info.filename.replace("\\", "/") for info in infos
                } != _OFFICIAL_ARCHIVE_MEMBERS:
                    raise _InstallError(
                        "GWS_ARCHIVE_LAYOUT",
                        "압축 파일의 항목 이름과 개수가 공식 Windows 압축본과 다릅니다.",
                    )
                raise _InstallError(
                    "GWS_ARCHIVE_LIMIT",
                    "압축 파일을 펼친 크기나 압축 비율이 안전 범위를 넘었습니다.",
                )
            executables = [
                info
                for info in infos
                if info.filename == "gws.exe"
            ]
            if len(executables) != 1:
                raise _InstallError("GWS_ARCHIVE_LAYOUT", "압축 파일에는 gws.exe가 정확히 하나 있어야 합니다.")
            executable = staging / "gws.exe"
            try:
                with archive.open(executables[0]) as source, executable.open("xb") as target:
                    while block := source.read(_CHUNK_SIZE):
                        target.write(block)
                    target.flush()
                    os.fsync(target.fileno())
            except OSError as error:
                raise _local_failure(error, stage="write") from error
    except _InstallError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise _InstallError("GWS_ARCHIVE_UNSAFE", "압축 파일을 안전하게 읽지 못했습니다.") from error
    if _sha256_file(executable) != manifest.executable_sha256:
        raise _InstallError("GWS_EXECUTABLE_SHA256_MISMATCH", "gws.exe의 SHA-256이 승인값과 다릅니다.")
    return executable


def _smoke_update(
    executable: Path,
    version: str,
    run_command: Callable[[Sequence[str]], tuple[int, str]],
) -> None:
    try:
        returncode, output = run_command([str(executable), "--version"])
    except Exception as error:
        raise _local_failure(error, stage="execute") from error
    if returncode in {126, 127}:
        raise _InstallError(
            "COMPONENT_SECURITY_BLOCKED",
            "보안 프로그램이 새 Google 도구 실행을 막았습니다.",
            mark_bad=False,
        )
    if returncode != 0:
        raise _InstallError("GWS_VERSION_CHECK_FAILED", "새 Google 도구의 판 번호를 실행해 확인하지 못했습니다.")
    if tool_runtime._reported_version(output) != version:
        raise _InstallError("GWS_VERSION_MISMATCH", "새 Google 도구가 승인된 판 번호를 보고하지 않았습니다.")
    try:
        returncode, _output = run_command([str(executable), "--help"])
    except Exception as error:
        raise _local_failure(error, stage="execute") from error
    if returncode in {126, 127}:
        raise _InstallError(
            "COMPONENT_SECURITY_BLOCKED",
            "보안 프로그램이 새 Google 도구 실행을 막았습니다.",
            mark_bad=False,
        )
    if returncode != 0:
        raise _InstallError("GWS_HELP_CHECK_FAILED", "새 Google 도구의 도움말 실행을 확인하지 못했습니다.")


def _active_bytes(root: Path) -> bytes | None:
    path = root / "active.json"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _local_failure(error, stage="read") from error


def _active_matches_offer(
    root: Path,
    manifest: ApprovedGwsManifest,
    approval_sha256: str,
) -> bool:
    active, _version, _reason = tool_runtime._read_active(root)
    return bool(
        active is not None
        and active.version == manifest.version
        and active.executable_sha256 == manifest.executable_sha256
        and active.approval_manifest_sha256 == approval_sha256
        and active.archive_url == manifest.archive_url
        and active.app_min_version == manifest.app_min_version
        and active.app_max_version == manifest.app_max_version
        and active.login_store_compatible is True
    )


def _write_bad_version_locked(root: Path, version: str, code: str) -> None:
    if not version:
        return
    versions = tool_runtime._read_bad_versions(root)
    failed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    versions[version] = {"code": code, "failed_at": failed_at}
    component_lock.atomic_write_text_unique(
        root / "bad-versions.json",
        json.dumps(
            {"schema_version": 1, "versions": versions},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )


def _try_write_bad_version_locked(root: Path, version: str, code: str) -> None:
    try:
        _write_bad_version_locked(root, version, code)
    except Exception:  # 읽기 전용·디스크 오류에서도 기존 실행본은 계속 돌려준다.
        pass


def _clear_transient_bad_version_locked(root: Path, version: str) -> bool:
    versions = tool_runtime._read_bad_versions(root)
    record = versions.get(version)
    if not isinstance(record, dict) or record.get("code") not in _TRANSIENT_BAD_VERSION_CODES:
        return False
    versions.pop(version, None)
    component_lock.atomic_write_text_unique(
        root / "bad-versions.json",
        json.dumps(
            {"schema_version": 1, "versions": versions},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )
    return True


def gws_version_permanently_rejected(
    version: str,
    *,
    component_root: Path | None = None,
) -> bool:
    """앞서 안전검사에서 거부되어 같은 판을 다시 받을 수 없는지 알려준다."""
    if not isinstance(version, str) or not version:
        return False
    try:
        root = (
            Path(component_root)
            if component_root is not None
            else tool_runtime.component_gws_root()
        )
        record = tool_runtime._read_bad_versions(root).get(version)
    except (OSError, tool_runtime.GwsRuntimeError):
        return False
    if not isinstance(record, dict):
        return False
    code = record.get("code")
    return isinstance(code, str) and bool(code) and code not in _TRANSIENT_BAD_VERSION_CODES


def _restore_active_locked(root: Path, previous: bytes | None) -> None:
    path = root / "active.json"
    if previous is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    _atomic_write_bytes(path, previous)


def _remove_owned_staging(path: Path, versions_root: Path) -> _InstallError | None:
    if path.parent != versions_root or not _OWNED_STAGING_NAME.fullmatch(path.name):
        return _path_unsafe()
    try:
        if not os.path.lexists(path):
            return None
        if (
            path.resolve(strict=True) != _absolute_path(path)
            or versions_root.resolve(strict=True) != _absolute_path(versions_root)
        ):
            return _path_unsafe()
    except OSError as error:
        return _local_failure(error, stage="read")
    last_error: OSError | None = None
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return None
        except FileNotFoundError:
            return None
        except OSError as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.02)
    return _local_failure(last_error or OSError("staging cleanup failed"), stage="write")


def _cleanup_stale_owned_staging(versions_root: Path) -> _InstallError | None:
    try:
        candidates = tuple(versions_root.iterdir())
    except OSError as error:
        return _local_failure(error, stage="read")
    for path in candidates:
        if not _OWNED_STAGING_NAME.fullmatch(path.name):
            continue
        failure = _remove_owned_staging(path, versions_root)
        if failure is not None:
            return failure
    return None


def _unlink_owned_partial(path: Path, downloads_root: Path) -> _InstallError | None:
    if path.parent != downloads_root or not path.name.startswith(".") or not path.name.endswith(".partial"):
        return _path_unsafe()
    last_error: OSError | None = None
    for attempt in range(3):
        try:
            path.unlink()
            return None
        except FileNotFoundError:
            return None
        except OSError as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.02)
    return _local_failure(last_error or OSError("partial cleanup failed"), stage="write")


def _cleanup_stale_owned_partials(downloads_root: Path) -> _InstallError | None:
    try:
        candidates = tuple(downloads_root.iterdir())
    except OSError as error:
        return _local_failure(error, stage="read")
    for path in candidates:
        if not _OWNED_PARTIAL_NAME.fullmatch(path.name):
            continue
        failure = _unlink_owned_partial(path, downloads_root)
        if failure is not None:
            return failure
    return None


def _remove_new_version(path: Path, versions_root: Path) -> _InstallError | None:
    expected = _absolute_path(versions_root) / path.name
    try:
        if not os.path.lexists(path):
            return None
        if path.resolve(strict=True) != expected or versions_root.resolve(strict=True) != _absolute_path(versions_root):
            return _path_unsafe()
    except OSError as error:
        return _local_failure(error, stage="read")
    last_error: OSError | None = None
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return None
        except FileNotFoundError:
            return None
        except OSError as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.02)
    return _local_failure(last_error or OSError("version cleanup failed"), stage="write")


def _failure_result(
    error: _InstallError,
    resolution: tool_runtime.GwsResolution | None,
) -> GwsUpdateResult:
    return GwsUpdateResult(False, error.code, error.detail, resolution)


def _current_resolution(
    root: Path,
    run_command: Callable[[Sequence[str]], tuple[int, str]],
) -> tool_runtime.GwsResolution:
    return tool_runtime.resolve_gws(
        component_root=root,
        run_command=run_command,
        force_refresh=True,
    )


def install_gws_update(
    offer: GwsUpdateOffer,
    *,
    component_root: Path | None = None,
    opener=urlopen,
    run_command: Callable[[Sequence[str]], tuple[int, str]] = process_win.run_captured,
    timeout_seconds: float = 60.0,
) -> GwsUpdateResult:
    """승인 원문을 다시 확인한 한 판만 원자 적용하고 실제 선택 결과까지 재확인한다."""
    try:
        root = Path(component_root) if component_root is not None else tool_runtime.component_gws_root()
    except tool_runtime.GwsRuntimeError as error:
        bundled_resolution: tool_runtime.GwsResolution | None = None
        try:
            bundled_resolution = tool_runtime.resolve_gws(
                run_command=run_command,
                force_refresh=True,
            )
        except tool_runtime.GwsRuntimeError:
            pass
        detail = (
            "새 Google 도구를 저장할 Windows 폴더를 사용할 수 없어 새 판을 적용하지 못했습니다. "
            "설치본에 든 기본 Google 도구는 계속 사용할 수 있습니다."
            if bundled_resolution is not None
            else "새 Google 도구 저장 폴더와 설치본의 기본 Google 도구를 확인하지 못했습니다. 설치 파일을 다시 실행해 주세요."
        )
        return GwsUpdateResult(False, error.code, detail, bundled_resolution)
    current: tool_runtime.GwsResolution | None = None
    try:
        with component_lock.exclusive_lifecycle_mutex(timeout=_UPDATE_LOCK_TIMEOUT_SECONDS):
            try:
                # 화면에서 받은 승인 원문도 Setup과 같은 수명주기 잠금을 잡은 뒤
                # 읽는다. 잠금이 바쁘면 어느 offer와 폴더도 먼저 들여다보지 않는다.
                manifest = _validate_offer_again(offer)
                version = manifest.version
                partial: Path | None = None
                staging: Path | None = None
                versions_root = root / "versions"
                version_root = versions_root / version
                downloads = root / "downloads"
                previous_active: bytes | None = None
                installed = False
                already_installed = False
                moved_new_version = False
                active_switched = False
                operation_error: _InstallError | None = None

                _ensure_safe_component_directory(root, root)
                _ensure_safe_component_directory(root, root / "locks")
                _ensure_safe_component_directory(root, root / "offers")
                _validate_saved_official_offer(root, offer)
                update_lock = root / "locks" / "update.lock"
                # resolver도 손상된 활성본을 발견하면 이 잠금 파일을 쓴다. 그러므로
                # resolver보다 먼저 링크·하드링크가 아닌지 확인한다.
                _ensure_safe_lock_entry(root, update_lock)
                try:
                    current = _current_resolution(root, run_command)
                except tool_runtime.GwsRuntimeError as error:
                    return _failure_result(
                        _InstallError(
                            error.code,
                            "설치된 Teacher Manager의 기본 Google 도구가 손상됐습니다. 설치 파일을 다시 실행해 주세요.",
                            mark_bad=False,
                        ),
                        None,
                    )
                version_comparison = tool_runtime._compare_versions(version, current.version)
                if version_comparison < 0 or (
                    version_comparison == 0 and current.source == "bundled"
                ):
                    return _failure_result(
                        _InstallError(
                            "COMPONENT_VERSION_NOT_NEWER",
                            "현재 Google 도구와 같거나 더 오래된 판은 적용하지 않습니다.",
                            mark_bad=False,
                        ),
                        current,
                    )
                _ensure_safe_lock_entry(root, update_lock)
                with component_lock.exclusive_file_lock(
                    update_lock,
                    timeout=_UPDATE_LOCK_TIMEOUT_SECONDS,
                ):
                    _ensure_safe_component_directory(root, root / "locks")
                    _ensure_safe_lock_entry(root, update_lock)
                    _clear_transient_bad_version_locked(root, version)
                    if version in tool_runtime._read_bad_versions(root):
                        return _failure_result(
                            _InstallError(
                                "COMPONENT_VERSION_REJECTED",
                                "앞서 실패한 같은 Google 도구 판은 다시 받지 않습니다.",
                                mark_bad=False,
                            ),
                            current,
                        )
                    # 이미 적용된 판이어도 이전 중단이 남긴 우리 형식의 찌꺼기는
                    # 지운다. 이름이 다른 파일과 폴더는 건드리지 않는다.
                    _ensure_safe_component_directory(root, downloads)
                    _ensure_safe_component_directory(root, versions_root)
                    stale_staging_error = _cleanup_stale_owned_staging(versions_root)
                    if stale_staging_error is not None:
                        raise stale_staging_error
                    stale_cleanup_error = _cleanup_stale_owned_partials(downloads)
                    if stale_cleanup_error is not None:
                        raise stale_cleanup_error
                    if _active_matches_offer(root, manifest, offer.approval_sha256):
                        already_installed = True
                    else:
                        try:
                            previous_active = _active_bytes(root)
                            if os.path.lexists(version_root):
                                if (
                                    current is not None
                                    and current.source == "approved-update"
                                    and current.version == version
                                ):
                                    raise _InstallError(
                                        "COMPONENT_VERSION_FOLDER_EXISTS",
                                        "같은 판의 현재 Google 도구 폴더는 자동으로 바꾸지 않습니다.",
                                        mark_bad=False,
                                    )
                                old_version_cleanup = _remove_new_version(
                                    version_root, versions_root
                                )
                                if old_version_cleanup is not None:
                                    raise old_version_cleanup
                            partial = downloads / (
                                f".{manifest.archive_filename}.{os.getpid()}."
                                f"{uuid.uuid4().hex}.partial"
                            )
                            _ensure_safe_component_directory(root, downloads)
                            _download_archive(
                                manifest,
                                partial,
                                opener=opener,
                                timeout_seconds=timeout_seconds,
                            )
                            _ensure_safe_component_directory(root, versions_root)
                            staging = versions_root / (
                                f".{version}.{os.getpid()}.{uuid.uuid4().hex}.staging"
                            )
                            staging.mkdir()
                            if staging.resolve(strict=True) != _absolute_path(staging):
                                raise _path_unsafe()
                            executable = _extract_verified_executable(partial, manifest, staging)
                            _atomic_write_bytes(staging / "approval.json", offer.approval_bytes)
                            _smoke_update(executable, version, run_command)
                            if os.path.lexists(version_root):
                                raise _InstallError(
                                    "COMPONENT_VERSION_FOLDER_EXISTS",
                                    "같은 Google 도구 판 폴더가 이미 있어 적용하지 않았습니다.",
                                )
                            _ensure_safe_component_directory(root, versions_root)
                            try:
                                os.replace(staging, version_root)
                            except OSError as error:
                                raise _local_failure(error, stage="replace") from error
                            staging = None
                            moved_new_version = True
                            metadata = tool_runtime.ActiveGwsMetadata(
                                schema_version=1,
                                version=version,
                                executable_sha256=manifest.executable_sha256,
                                archive_url=manifest.archive_url,
                                installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                                app_min_version=manifest.app_min_version,
                                app_max_version=manifest.app_max_version,
                                approval_manifest_sha256=offer.approval_sha256,
                                login_store_compatible=True,
                            )
                            try:
                                component_lock.atomic_write_text_unique(
                                    root / "active.json",
                                    json.dumps(
                                        {
                                            field: getattr(metadata, field)
                                            for field in tool_runtime._ACTIVE_FIELDS
                                        },
                                        ensure_ascii=False,
                                        sort_keys=True,
                                    )
                                    + "\n",
                                )
                            except OSError as error:
                                raise _local_failure(error, stage="replace") from error
                            active_switched = True
                            installed = True
                        except _InstallError as error:
                            operation_error = error
                        except OSError as error:
                            operation_error = _local_failure(error, stage="write")
                        finally:
                            post_switch_cleanup_error: _InstallError | None = None
                            if partial is not None:
                                cleanup_error = _unlink_owned_partial(partial, downloads)
                                if cleanup_error is not None:
                                    post_switch_cleanup_error = cleanup_error
                            if staging is not None:
                                cleanup_error = _remove_owned_staging(
                                    staging, versions_root
                                )
                                if cleanup_error is not None:
                                    post_switch_cleanup_error = cleanup_error
                            # active.json 교체 뒤에는 새 버전 폴더가 현재 실행 대상이다.
                            # 임시 파일 청소 실패만으로 그 폴더를 지우면 안 된다. 남은
                            # 찌꺼기는 다음 호출의 시작 청소에서 다시 지운다.
                            if not active_switched and post_switch_cleanup_error is not None:
                                operation_error = post_switch_cleanup_error
                            if (
                                operation_error is not None
                                and moved_new_version
                                and not active_switched
                            ):
                                cleanup_error = _remove_new_version(version_root, versions_root)
                                if cleanup_error is not None:
                                    operation_error = cleanup_error
                                else:
                                    moved_new_version = False
                            if operation_error is not None and operation_error.mark_bad:
                                _try_write_bad_version_locked(root, version, operation_error.code)

                if operation_error is not None:
                    return _failure_result(operation_error, current)

                if installed or already_installed:
                    try:
                        resolved = _current_resolution(root, run_command)
                    except Exception:
                        resolved = current
                    expected = (root / "versions" / version / "gws.exe").resolve()
                    if (
                        resolved.source == "approved-update"
                        and resolved.version == version
                        and resolved.executable == expected
                    ):
                        return GwsUpdateResult(
                            True,
                            (
                                "COMPONENT_UPDATE_INSTALLED"
                                if installed
                                else "COMPONENT_UPDATE_ALREADY_INSTALLED"
                            ),
                            (
                                "승인된 Google 도구 새 판을 적용했습니다."
                                if installed
                                else "승인된 Google 도구 새 판이 이미 적용되어 있습니다."
                            ),
                            resolved,
                        )

                    failure = _InstallError(
                        "COMPONENT_RESOLUTION_FAILED",
                        "새 Google 도구를 적용한 뒤 실제 선택 결과를 확인하지 못했습니다.",
                    )
                    _ensure_safe_lock_entry(root, update_lock)
                    with component_lock.exclusive_file_lock(
                        update_lock,
                        timeout=_UPDATE_LOCK_TIMEOUT_SECONDS,
                    ):
                        _ensure_safe_lock_entry(root, update_lock)
                        if installed:
                            restored = False
                            try:
                                _restore_active_locked(root, previous_active)
                                restored = True
                            except OSError:
                                # 복원하지 못했다면 active가 아직 새 판을 가리킨다.
                                # 그 실행 폴더를 남겨 깨진 포인터를 만들지 않는다.
                                restored = False
                            if restored:
                                _remove_new_version(version_root, versions_root)
                        _try_write_bad_version_locked(root, version, failure.code)
                    return _failure_result(failure, current)
            except _InstallError as error:
                return _failure_result(error, current)
            except TimeoutError:
                return _failure_result(
                    _InstallError(
                        "COMPONENT_UPDATE_BUSY",
                        "다른 설치나 갱신이 끝난 뒤 다시 눌러 주세요.",
                        mark_bad=False,
                    ),
                    current,
                )
            except OSError as error:
                return _failure_result(_local_failure(error, stage="write"), current)
    except TimeoutError:
        return _failure_result(
            _InstallError(
                "COMPONENT_UPDATE_BUSY",
                "다른 설치나 갱신이 끝난 뒤 다시 눌러 주세요.",
                mark_bad=False,
            ),
            None,
        )
    except OSError as error:
        return _failure_result(_local_failure(error, stage="write"), current)

    return _failure_result(
        _InstallError("COMPONENT_UPDATE_FAILED", "Google 도구 갱신을 끝내지 못했습니다."),
        current,
    )
