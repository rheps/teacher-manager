"""동봉 도구(WebView2)의 고정 다운로드 정보를 읽고 확인한다."""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WEBVIEW2_REQUIRED = (
    "file_version", "minimum_runtime_version", "platform", "architecture", "bootstrapper_url",
    "bootstrapper_filename", "bootstrapper_sha256", "publisher_common_name",
    "install_arguments", "license_id", "license_file",
)
_WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


class ManifestError(ValueError):
    code = "TOOLS_MANIFEST_INCOMPLETE"

    def __init__(self, detail: str):
        super().__init__(f"{self.code}: {detail}")


@dataclass(frozen=True)
class WebView2Spec:
    file_version: str
    minimum_runtime_version: str
    platform: str
    architecture: str
    bootstrapper_url: str
    bootstrapper_filename: str
    bootstrapper_sha256: str
    publisher_common_name: str
    install_arguments: tuple[str, str]
    license_id: str
    license_file: str


@dataclass(frozen=True)
class ToolsManifest:
    schema_version: int
    webview2: WebView2Spec | None


def _fail(detail: str) -> None:
    raise ManifestError(detail)


def _require_text(data: dict, field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        _fail(f"{field} 값이 없습니다.")
    return value


def _validate_relative_path(value: str, expected: str, detail: str) -> None:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized != expected or path.is_absolute() or ".." in path.parts:
        _fail(detail)


def _validate_webview2_spec(spec: WebView2Spec) -> None:
    try:
        parsed = urlparse(spec.bootstrapper_url)
        port = parsed.port
    except ValueError:
        _fail("Microsoft 공식 WebView2 Bootstrapper 주소가 맞지 않습니다.")
    if spec.platform != "windows" or spec.architecture != "universal":
        _fail("WebView2는 장치 구조를 자동으로 고르는 Windows 공통 자료여야 합니다.")
    if (
        spec.bootstrapper_url != _WEBVIEW2_BOOTSTRAPPER_URL
        or parsed.scheme != "https" or parsed.hostname != "go.microsoft.com"
        or port is not None or parsed.username is not None or parsed.password is not None
        or parsed.fragment
    ):
        _fail("Microsoft 공식 WebView2 Bootstrapper 주소가 맞지 않습니다.")
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", spec.file_version):
        _fail("WebView2 실제 파일 버전 형식이 맞지 않습니다.")
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", spec.minimum_runtime_version):
        _fail("WebView2 최소 Runtime 버전은 네 자리 숫자여야 합니다.")
    if spec.bootstrapper_filename != "MicrosoftEdgeWebview2Setup.exe":
        _fail("WebView2 Bootstrapper 파일 이름이 맞지 않습니다.")
    if not _SHA256.fullmatch(spec.bootstrapper_sha256):
        _fail("WebView2 SHA-256은 소문자 64글자여야 합니다.")
    if spec.publisher_common_name != "Microsoft Corporation":
        _fail("WebView2 게시자가 Microsoft Corporation이 아닙니다.")
    if spec.install_arguments != ("/silent", "/install"):
        _fail("WebView2 조용한 설치 인자와 순서가 맞지 않습니다.")
    if (
        spec.license_id != "Microsoft-Edge-WebView2"
        or spec.license_file != "licenses/webview2-evergreen-terms-en-us.json"
    ):
        _fail("WebView2 공식 약관 원본 정보가 맞지 않습니다.")


def _require_exact_keys(data: dict, required: tuple[str, ...], label: str) -> None:
    missing = sorted(set(required) - set(data))
    extra = sorted(set(data) - set(required))
    if missing:
        _fail(f"{label} 필수 항목이 없습니다: " + ", ".join(missing))
    if extra:
        _fail(f"{label}에 알 수 없는 항목이 있습니다: " + ", ".join(extra))


def load_tools_manifest(path: Path) -> ToolsManifest:
    """동봉 도구 manifest를 읽고 엄격히 검사한다."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _fail(f"읽을 수 없는 manifest입니다: {error}")
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != 1
    ):
        _fail("schema_version 1이 필요합니다.")
    _require_exact_keys(raw, ("schema_version", "webview2"), "manifest")
    for component in ("webview2",):
        if component not in raw:
            _fail(f"{component} 항목이 없습니다.")
        if raw[component] is not None and not isinstance(raw[component], dict):
            _fail(f"{component} 항목 형식이 맞지 않습니다.")
    webview2 = None
    if raw["webview2"] is not None:
        _require_exact_keys(raw["webview2"], _WEBVIEW2_REQUIRED, "WebView2")
        arguments = raw["webview2"].get("install_arguments")
        if not isinstance(arguments, list) or any(not isinstance(value, str) for value in arguments):
            _fail("WebView2 설치 인자는 글자 목록이어야 합니다.")
        values = {
            field: _require_text(raw["webview2"], field)
            for field in _WEBVIEW2_REQUIRED
            if field != "install_arguments"
        }
        values["install_arguments"] = tuple(arguments)
        webview2 = WebView2Spec(**values)
        _validate_webview2_spec(webview2)
    return ToolsManifest(1, webview2)


def require_build_complete(manifest: ToolsManifest) -> ToolsManifest:
    """최종 설치 파일을 만들기 전에는 동봉 도구의 공식 정보가 모두 있어야 한다."""
    if manifest.webview2 is None:
        _fail("최종 빌드에는 webview2 공식 정보가 필요합니다.")
    _validate_webview2_spec(manifest.webview2)
    return manifest


def write_complete_tools_manifest(
    path: Path,
    *,
    webview2: WebView2Spec,
) -> ToolsManifest:
    """공식 입력이 모두 맞을 때만 최종 manifest를 한 번에 바꾼다."""
    path = Path(path)
    data = {
        "schema_version": 1,
        "webview2": asdict(webview2) if webview2 is not None else None,
    }
    if data["webview2"] is not None:
        data["webview2"]["install_arguments"] = list(webview2.install_arguments)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        candidate = require_build_complete(load_tools_manifest(temporary))
        os.replace(temporary, path)
        return require_build_complete(load_tools_manifest(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
