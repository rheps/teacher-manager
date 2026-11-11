"""공식 도구(WebView2)의 승인 증거를 검사하고 제품 입력을 한 번에 조립한다.

승인 파일은 다운로드나 설치를 하는 명령서가 아니다. 이미 별도 확인 단계에서 만든
JSON을 엄격하게 다시 읽고, 모든 파일이 온전할 때에만 제품 manifest와 공개 상태
문서를 바꾼다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Mapping
from urllib.parse import urlparse

from brity_bridge import component_lock, process_win
from brity_bridge.tool_manifest import (
    ManifestError,
    ToolsManifest,
    WebView2Spec,
    _validate_webview2_spec,
    load_tools_manifest,
    require_build_complete,
)


_TOOLS = ("webview2",)
_TOP_KEYS = (
    "approval_schema_version",
    "tool",
    "verified_at_utc",
    "source_approval_status",
    "spec",
    "evidence",
    "field_verification",
)
_WEBVIEW2_SPEC_KEYS = (
    "file_version", "minimum_runtime_version", "platform", "architecture",
    "bootstrapper_url", "bootstrapper_filename", "bootstrapper_sha256",
    "publisher_common_name", "install_arguments", "license_id", "license_file",
)
_WEBVIEW2_EVIDENCE_KEYS = (
    "download_page_url", "distribution_doc_url", "resolved_final_url",
    "resolved_final_host", "bootstrapper_bytes", "bootstrapper_local_sha256",
    "authenticode_status", "signer_subject", "signer_thumbprint", "pe_machine",
    "terms_source_url", "terms_locale", "terms_json_field", "terms_source_bytes",
    "terms_source_sha256", "license_file_sha256", "end_user_terms_acceptance",
    "smartscreen_notice_location", "windows_7_8_1_notice_policy", "build_proof_path",
    "build_proof_sha256", "install_command_not_run",
)
_BUILD_PROOF_KEYS = (
    "schema_version", "requirements_lock_sha256", "pywebview_version",
    "pythonnet_version", "core_sdk_file_version", "winforms_sdk_file_version",
    "loader_file_versions", "sdk_file_sha256", "compatible_runtime_minimum",
    "microsoft_evidence_url", "checked_at_utc",
)
_WEBVIEW_SDK_FILES = (
    "lib/Microsoft.Web.WebView2.Core.dll",
    "lib/Microsoft.Web.WebView2.WinForms.dll",
    "lib/runtimes/win-x86/native/WebView2Loader.dll",
    "lib/runtimes/win-x64/native/WebView2Loader.dll",
    "lib/runtimes/win-arm64/native/WebView2Loader.dll",
)
_WEBVIEW_LOADER_FILES = tuple(
    path for path in _WEBVIEW_SDK_FILES if path.endswith("WebView2Loader.dll")
)
_FIELD_KEYS = ("status", "items")
_NOTICE_POLICY_KEYS = ("policy", "verification")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_THUMBPRINT = re.compile(r"^[0-9a-fA-F]{40}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:(?<![a-z0-9+.-])[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)
_POSIX_PRIVATE_HOME_PATH = re.compile(
    r"(?i)(?<![a-z0-9+.-])/(?:home|users)/[^/\s]+(?:/|$)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:client[_-]?secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|authorization)\b\s*(?::|=)\s*\S+"
)
_SECRET_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}")
_SECRET_PREFIX = re.compile(r"(?i)\b(?:GOCSPX-|AIza|ya29\.)[a-z0-9._~-]+")
_PLACEHOLDER_SUBSTRING = re.compile(r"(?i)(?:todo|tbd|placeholder)")
_PLACEHOLDERS = {
    "placeholder", "tbd", "todo", "unknown", "pending", "not-set", "not set",
    "changeme", "temporary", "temp",
}
_WEBVIEW_TERMS_SHA256 = "e15b53f476b66f8335c18436998256dc9862b210242a8e4c7f7e14d2de53591d"
_WEBVIEW_TERMS_URL = "https://developer.microsoft.com/microsoft-edge/api/eula/webview2?locale=en-us"
_WEBVIEW_BOOTSTRAPPER_SHA256 = "e99838c51bb3379b244654aa77e33032d42fc2b5d224c5babce432d9fd3dcb28"
_WEBVIEW_BOOTSTRAPPER_FILE_VERSION = "1.3.251.21"
_WEBVIEW_EVIDENCE_PATHS = {
    "end_user_terms_acceptance": (
        "installer/webview2_terms.iss",
        "tests/test_webview2_terms.py",
    ),
    "smartscreen_notice_location": (
        "installer/webview2_terms.iss",
        "tests/test_webview2_terms.py",
    ),
    "windows_7_8_1_notice_policy.verification": (
        "installer/installer.iss",
        "tests/test_webview2_terms.py",
    ),
}
_WEBVIEW_EVIDENCE_MARKERS = {
    "installer/webview2_terms.iss": (
        "procedure CenterTeacherManagerWelcomeContent",
        "procedure CustomizeTeacherManagerWelcomePage",
        "if WizardSilent() and (not IsVerificationInstall)",
    ),
    "installer/installer.iss": (
        "MinVersion=10.0.17763",
        '#include "webview2_terms.iss"',
    ),
    "tests/test_webview2_terms.py": (
        "def test_installer_welcome_has_no_webview_notice_or_terms_link_and_installs_notices",
        'self.assertNotIn("TNewLinkLabel", include)',
        'self.assertNotIn("OpenWebView2Terms", include)',
        'self.assertNotIn("CreateCustomPage", include)',
        'self.assertIn("THIRD_PARTY_NOTICES.txt", spec)',
        'self.assertIn("licenses/webview2-evergreen-terms-en-us.json", spec)',
        "MinVersion=10.0.17763",
        "SmartScreen",
    ),
}


class ToolApprovalError(ValueError):
    code = "TOOL_SOURCE_APPROVAL_INVALID"

    def __init__(self, detail: str):
        super().__init__(f"{self.code}: {detail}")


@dataclass(frozen=True)
class ToolSourceApproval:
    approval_schema_version: int
    tool: Literal["webview2"]
    verified_at_utc: str
    source_approval_status: Literal["approved"]
    spec: dict[str, object]
    evidence: dict[str, object]
    field_verification: dict[str, object]


@dataclass(frozen=True)
class WebView2SourceFiles:
    bootstrapper: Path
    terms_source: Path
    packaged_terms: Path
    build_proof: Path
    requirements_lock: Path
    webview_package_root: Path
    evidence_root: Path


@dataclass(frozen=True)
class ToolSourceFiles:
    webview2: WebView2SourceFiles


@dataclass(frozen=True)
class AuthenticodeEvidence:
    status: str
    signer_subject: str
    signer_thumbprint: str


def _fail(detail: str) -> None:
    raise ToolApprovalError(detail)


def _read_file_bytes(path: Path, label: str) -> bytes:
    try:
        value = Path(path).read_bytes()
    except OSError as error:
        _fail(f"{label} 실제 입력 파일을 읽을 수 없습니다: {error}")
    if not value:
        _fail(f"{label} 실제 입력 파일이 비어 있습니다.")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_file_sha(path: Path, expected: str, label: str) -> bytes:
    value = _read_file_bytes(path, label)
    actual = _sha256_bytes(value)
    if actual != expected:
        _fail(f"{label} 실제 파일 SHA-256이 공식 고정값과 다릅니다: {actual}")
    return value


def _read_pe_machine(path: Path) -> int:
    value = _read_file_bytes(path, "WebView2 Bootstrapper")
    if len(value) < 0x40 or value[:2] != b"MZ":
        _fail("WebView2 Bootstrapper가 Windows 실행 파일이 아닙니다.")
    offset = int.from_bytes(value[0x3C:0x40], "little")
    if offset < 0 or offset + 6 > len(value) or value[offset:offset + 4] != b"PE\0\0":
        _fail("WebView2 Bootstrapper의 PE 머리글을 읽을 수 없습니다.")
    return int.from_bytes(value[offset + 4:offset + 6], "little")


def _verify_windows_authenticode(path: Path) -> AuthenticodeEvidence:
    if os.name != "nt":
        _fail("WebView2 Authenticode 서명은 Windows에서 실제 파일로 확인해야 합니다.")
    script = (
        "$ErrorActionPreference='Stop';"
        "Import-Module Microsoft.PowerShell.Security -ErrorAction Stop;"
        "$s=Get-AuthenticodeSignature -LiteralPath $env:TM_AUTHENTICODE_FILE;"
        "[ordered]@{status=[string]$s.Status;"
        "signer_subject=[string]$s.SignerCertificate.Subject;"
        "signer_thumbprint=[string]$s.SignerCertificate.Thumbprint}|ConvertTo-Json -Compress"
    )
    environment = os.environ.copy()
    environment["TM_AUTHENTICODE_FILE"] = str(Path(path).resolve())
    # PowerShell 7에서 물려받은 모듈 폴더를 Windows PowerShell 5가 먼저 읽으면
    # Security 모듈이 서로 섞여 서명 확인 명령을 불러오지 못할 수 있다.
    environment["PSModulePath"] = os.pathsep.join([
        str(Path.home() / "Documents" / "WindowsPowerShell" / "Modules"),
        str(Path(environment.get("ProgramFiles", r"C:\Program Files")) / "WindowsPowerShell" / "Modules"),
        str(Path(environment.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
    ])
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=30,
            check=False,
            **process_win.hidden_process_kwargs(),
        )
        if result.returncode != 0 or result.stderr.strip():
            _fail(f"WebView2 Authenticode 실제 확인이 실패했습니다: {result.stderr.strip()}")
        raw = json.loads(result.stdout)
        evidence = AuthenticodeEvidence(
            status=str(raw.get("status", "")),
            signer_subject=str(raw.get("signer_subject", "")),
            signer_thumbprint=str(raw.get("signer_thumbprint", "")),
        )
        if not evidence.status:
            _fail("WebView2 Authenticode 실제 확인 결과가 비어 있습니다.")
        return evidence
    except ToolApprovalError:
        raise
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        _fail(f"WebView2 Authenticode 실제 확인을 읽지 못했습니다: {error}")


def _read_windows_file_version(path: Path) -> str:
    if os.name != "nt":
        _fail("WebView2 Bootstrapper 파일 버전은 Windows에서 실제 파일로 확인해야 합니다.")
    environment = os.environ.copy()
    environment["TM_FILE_VERSION_PATH"] = str(Path(path).resolve())
    script = (
        "$ErrorActionPreference='Stop';"
        "$v=(Get-Item -LiteralPath $env:TM_FILE_VERSION_PATH).VersionInfo.FileVersion;"
        "if([string]::IsNullOrWhiteSpace([string]$v)){throw 'empty file version'};"
        "[Console]::Out.Write([string]$v)"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=30,
            check=False,
            **process_win.hidden_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _fail(f"WebView2 Bootstrapper 실제 파일 버전을 읽지 못했습니다: {error}")
    if result.returncode != 0 or result.stderr.strip() or not result.stdout.strip():
        _fail("WebView2 Bootstrapper 실제 파일 버전을 읽지 못했습니다.")
    return result.stdout.strip()


def _pairs_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"JSON에 중복 key가 있습니다: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[dict, bytes]:
    try:
        raw_bytes = Path(path).read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_pairs_without_duplicates)
    except ToolApprovalError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail(f"읽을 수 없는 승인 JSON입니다: {error}")
    if not isinstance(raw, dict):
        _fail("승인 JSON의 맨 바깥은 object여야 합니다.")
    return raw, raw_bytes


def _exact_keys(value: object, keys: tuple[str, ...], label: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{label}은 object여야 합니다.")
    missing = sorted(set(keys) - set(value))
    extra = sorted(set(value) - set(keys))
    if missing:
        _fail(f"{label} 필수 key가 없습니다: {', '.join(missing)}")
    if extra:
        _fail(f"{label}에 알 수 없는 key가 있습니다: {', '.join(extra)}")
    return value


def _reject_empty_null_placeholder(value: object, label: str, *, allow_empty_list: bool = False) -> None:
    if value is None:
        _fail(f"{label}에 null을 쓸 수 없습니다.")
    if isinstance(value, str):
        if not value.strip():
            _fail(f"{label}이 비어 있습니다.")
        if value.strip().lower() in _PLACEHOLDERS or _PLACEHOLDER_SUBSTRING.search(value):
            _fail(f"{label}에 임시 표시값을 쓸 수 없습니다.")
        if _WINDOWS_ABSOLUTE_PATH.search(value):
            _fail(f"{label}에 개인 컴퓨터 절대 경로를 쓸 수 없습니다.")
        if _POSIX_PRIVATE_HOME_PATH.search(value):
            _fail(f"{label}에 개인 경로를 쓸 수 없습니다.")
        if (
            _SECRET_ASSIGNMENT.search(value)
            or _SECRET_BEARER.search(value)
            or _SECRET_PREFIX.search(value)
        ):
            _fail(f"{label}에 공개할 수 없는 비밀값을 쓸 수 없습니다.")
        return
    if isinstance(value, list):
        if not value and not allow_empty_list:
            _fail(f"{label} 목록이 비어 있습니다.")
        for index, item in enumerate(value):
            _reject_empty_null_placeholder(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        if not value:
            _fail(f"{label} object가 비어 있습니다.")
        for key, item in value.items():
            _reject_empty_null_placeholder(item, f"{label}.{key}")


def _require_text(data: Mapping[str, object], key: str, label: str | None = None) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label or key}은 비어 있지 않은 글자여야 합니다.")
    if value.strip().lower() in _PLACEHOLDERS or _PLACEHOLDER_SUBSTRING.search(value):
        _fail(f"{label or key}에 임시 표시값을 쓸 수 없습니다.")
    return value


def _require_sha(data: Mapping[str, object], key: str) -> str:
    value = _require_text(data, key)
    if not _SHA256.fullmatch(value) or value == "0" * 64:
        _fail(f"{key}는 실제 소문자 SHA-256이어야 합니다.")
    return value


def _require_positive_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if type(value) is not int or value <= 0:
        _fail(f"{key}는 0보다 큰 정수여야 합니다.")
    return value


def _require_true(data: Mapping[str, object], key: str) -> None:
    if data.get(key) is not True:
        _fail(f"{key} 확인값은 true여야 합니다.")


def _require_evidence_location(data: Mapping[str, object], key: str) -> str:
    value = _require_text(data, key)
    if value.strip().lower() in {"true", "false", "yes", "no", "approved", "passed"}:
        _fail(f"{key}에는 단순 판정 대신 화면 파일 또는 자동 시험 이름을 적어야 합니다.")
    if not re.search(r"(?i)(?:test|[\\/]|\.(?:iss|py|js|md)\b)", value):
        _fail(f"{key}에는 화면 파일 또는 자동 시험 이름을 적어야 합니다.")
    return value


def _validate_evidence_files(root: Path, key: str, value: str) -> None:
    paths = _WEBVIEW_EVIDENCE_PATHS[key]
    if value != " + ".join(paths):
        _fail(f"{key} 증거 파일 목록이 정해진 값과 다릅니다.")
    try:
        resolved_root = Path(root).resolve(strict=True)
    except OSError:
        _fail(f"{key} 증거 폴더를 읽을 수 없습니다.")
    for relative in paths:
        candidate = Path(root).joinpath(*PurePosixPath(relative).parts)
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
                _fail(f"{key} 증거 파일이 정해진 폴더 안의 실제 파일이 아닙니다.")
            contents = resolved.read_text(encoding="utf-8-sig")
        except ToolApprovalError:
            raise
        except (OSError, UnicodeError):
            _fail(f"{key} 증거 파일을 읽을 수 없습니다.")
        folded_contents = contents.casefold()
        if any(
            marker.casefold() not in folded_contents
            for marker in _WEBVIEW_EVIDENCE_MARKERS[relative]
        ):
            _fail(f"{key} 증거 파일 내용이 실제 확인 절차와 맞지 않습니다.")


def _validate_utc(value: object, label: str) -> str:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        _fail(f"{label}은 Z로 끝나는 RFC 3339 UTC 시각이어야 합니다.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        _fail(f"{label}이 실제 달력 시각이 아닙니다: {error}")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail(f"{label}은 UTC여야 합니다.")
    return value


def _webview2_model(spec: dict[str, object]) -> WebView2Spec:
    arguments = spec.get("install_arguments")
    if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
        _fail("webview2.spec.install_arguments는 글자 목록이어야 합니다.")
    values = {
        key: _require_text(spec, key, f"webview2.spec.{key}")
        for key in _WEBVIEW2_SPEC_KEYS if key != "install_arguments"
    }
    values["install_arguments"] = tuple(arguments)
    model = WebView2Spec(**values)
    try:
        _validate_webview2_spec(model)
    except ManifestError as error:
        _fail(str(error))
    if model.bootstrapper_sha256 == "0" * 64:
        _fail("WebView2에 0으로 채운 임시 SHA-256을 쓸 수 없습니다.")
    if model.bootstrapper_sha256 != _WEBVIEW_BOOTSTRAPPER_SHA256:
        _fail("WebView2 Bootstrapper SHA-256이 공식 고정값과 다릅니다.")
    if model.file_version != _WEBVIEW_BOOTSTRAPPER_FILE_VERSION:
        _fail("WebView2 Bootstrapper 파일 버전이 공식 고정값과 다릅니다.")
    return model


def _validate_build_proof(
    approval_path: Path,
    spec: WebView2Spec,
    evidence: dict[str, object],
    source: WebView2SourceFiles,
    *,
    package_version_reader: Callable[[str], str] | None,
    file_version_reader: Callable[[Path], str] | None,
) -> None:
    relative_text = _require_text(evidence, "build_proof_path")
    relative = PurePosixPath(relative_text.replace("\\", "/"))
    if relative_text.replace("\\", "/") != "approvals/webview2-build-proof.json" or relative.is_absolute() or ".." in relative.parts:
        _fail("WebView2 build proof는 approvals/webview2-build-proof.json이어야 합니다.")
    proof_path = approval_path.parent.parent.joinpath(*relative.parts)
    if proof_path.resolve() != Path(source.build_proof).resolve():
        _fail("WebView2 build proof 실제 입력 경로가 승인 파일이 가리키는 파일과 다릅니다.")
    proof_data, proof_bytes = _read_json(proof_path)
    if hashlib.sha256(proof_bytes).hexdigest() != _require_sha(evidence, "build_proof_sha256"):
        _fail("WebView2 build proof 원문 SHA-256이 다릅니다.")
    try:
        from scripts.verify_webview_build_runtime import (
            WebViewBuildProof,
            verify_webview_build_runtime,
        )

        kwargs = {}
        if package_version_reader is not None:
            kwargs["package_version_reader"] = package_version_reader
        if file_version_reader is not None:
            kwargs["file_version_reader"] = file_version_reader
        proof_data = _exact_keys(proof_data, _BUILD_PROOF_KEYS, "WebView2 build proof")
        if type(proof_data["schema_version"]) is not int or proof_data["schema_version"] != 1:
            _fail("WebView2 build proof schema_version 1이 필요합니다.")
        proof = WebViewBuildProof(**{
            key: value for key, value in proof_data.items() if key != "schema_version"
        })
        current = verify_webview_build_runtime(
            Path(source.requirements_lock),
            Path(source.webview_package_root),
            expected_runtime_minimum=proof.compatible_runtime_minimum,
            evidence_url=proof.microsoft_evidence_url,
            checked_at_utc=proof.checked_at_utc,
            **kwargs,
        )
        if current != proof:
            _fail("WebView2 build proof가 현재 requirements와 SDK 실제 파일에 맞지 않습니다.")
    except ToolApprovalError:
        raise
    except Exception as error:
        _fail(f"WebView2 build proof가 현재 requirements와 SDK 실제 파일에 맞지 않습니다: {error}")
    if _read_file_bytes(proof_path, "WebView2 build proof") != proof_bytes:
        _fail("WebView2 build proof가 검사 도중 바뀌었습니다.")
    if proof.compatible_runtime_minimum != spec.minimum_runtime_version:
        _fail("WebView2 최소 Runtime 버전이 build proof와 다릅니다.")
    if spec.minimum_runtime_version != "146.0.3856.49":
        _fail("현재 고정 SDK가 증명한 WebView2 최소 Runtime은 146.0.3856.49입니다.")
    evidence_url = urlparse(proof.microsoft_evidence_url)
    if evidence_url.scheme != "https" or evidence_url.hostname != "learn.microsoft.com":
        _fail("WebView2 build proof의 Microsoft 공식 근거 주소가 맞지 않습니다.")
    _validate_utc(proof.checked_at_utc, "WebView2 build proof.checked_at_utc")


def _validate_webview2_evidence(
    approval_path: Path,
    spec: WebView2Spec,
    evidence: dict[str, object],
    source: WebView2SourceFiles,
    *,
    authenticode_verifier: Callable[[Path], AuthenticodeEvidence],
    package_version_reader: Callable[[str], str] | None,
    file_version_reader: Callable[[Path], str] | None,
    bootstrapper_file_version_reader: Callable[[Path], str] | None,
) -> None:
    if _require_text(evidence, "download_page_url") != "https://developer.microsoft.com/en-us/microsoft-edge/webview2/":
        _fail("WebView2 공식 다운로드 화면 주소가 맞지 않습니다.")
    if _require_text(evidence, "distribution_doc_url") != "https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution":
        _fail("WebView2 공식 배포 문서 주소가 맞지 않습니다.")
    final_url = urlparse(_require_text(evidence, "resolved_final_url"))
    final_host = _require_text(evidence, "resolved_final_host").lower()
    try:
        final_port = final_url.port
    except ValueError:
        _fail("WebView2 최종 다운로드 주소의 포트가 올바르지 않습니다.")
    if (
        final_url.scheme != "https" or not final_url.hostname or final_url.hostname.lower() != final_host
        or final_port is not None or final_url.username is not None or final_url.password is not None
        or not (final_host == "microsoft.com" or final_host.endswith(".microsoft.com"))
    ):
        _fail("WebView2 최종 다운로드 주소가 Microsoft HTTPS 주소가 아닙니다.")
    bootstrapper = _require_file_sha(
        source.bootstrapper, _WEBVIEW_BOOTSTRAPPER_SHA256, "WebView2 Bootstrapper"
    )
    if _require_positive_int(evidence, "bootstrapper_bytes") != len(bootstrapper):
        _fail("WebView2 Bootstrapper 실제 바이트 수가 승인 JSON과 다릅니다.")
    if _require_sha(evidence, "bootstrapper_local_sha256") != spec.bootstrapper_sha256:
        _fail("WebView2 로컬 Bootstrapper SHA-256이 spec과 다릅니다.")
    if _require_text(evidence, "authenticode_status") != "Valid":
        _fail("WebView2 Authenticode 상태가 Valid가 아닙니다.")
    signer_subject = _require_text(evidence, "signer_subject")
    if not re.search(r"(?:^|,\s*)CN=Microsoft Corporation(?:,|$)", signer_subject):
        _fail("WebView2 서명 주체가 Microsoft Corporation이 아닙니다.")
    thumbprint = _require_text(evidence, "signer_thumbprint")
    if not _THUMBPRINT.fullmatch(thumbprint) or thumbprint == "0" * 40:
        _fail("WebView2 서명 thumbprint가 올바르지 않습니다.")
    actual_signature = authenticode_verifier(Path(source.bootstrapper))
    if (
        actual_signature.status != "Valid"
        or actual_signature.signer_subject != signer_subject
        or actual_signature.signer_thumbprint.upper() != thumbprint.upper()
    ):
        _fail("WebView2 실제 Authenticode 서명 결과가 승인 JSON과 다릅니다.")
    machine = _require_text(evidence, "pe_machine").upper()
    if "I386" not in machine and "014C" not in machine and machine != "X86":
        _fail("WebView2 공통 Bootstrapper의 PE x86 표식이 없습니다.")
    if _read_pe_machine(Path(source.bootstrapper)) != 0x014C:
        _fail("WebView2 실제 Bootstrapper의 PE 구조가 x86 공통 설치 파일이 아닙니다.")
    reader = bootstrapper_file_version_reader or _read_windows_file_version
    try:
        actual_file_version = str(reader(Path(source.bootstrapper))).strip()
    except ToolApprovalError:
        raise
    except Exception as error:
        _fail(f"WebView2 Bootstrapper 실제 파일 버전을 읽지 못했습니다: {error}")
    if actual_file_version != spec.file_version:
        _fail("WebView2 Bootstrapper 실제 파일 버전이 승인 JSON과 다릅니다.")
    if _require_text(evidence, "terms_source_url") != _WEBVIEW_TERMS_URL:
        _fail("WebView2 공식 약관 API 주소가 맞지 않습니다.")
    if _require_text(evidence, "terms_locale") != "en-us" or _require_text(evidence, "terms_json_field") != "evergreenHtml":
        _fail("WebView2 공식 약관 locale 또는 JSON 필드가 맞지 않습니다.")
    if _require_positive_int(evidence, "terms_source_bytes") != 24429:
        _fail("WebView2 공식 약관 원문 바이트 수가 맞지 않습니다.")
    if _require_sha(evidence, "terms_source_sha256") != _WEBVIEW_TERMS_SHA256:
        _fail("WebView2 공식 약관 원문 SHA-256이 맞지 않습니다.")
    if _require_sha(evidence, "license_file_sha256") != _WEBVIEW_TERMS_SHA256:
        _fail("WebView2 제품 약관 파일이 공식 JSON 원문과 다릅니다.")
    terms_source = _require_file_sha(source.terms_source, _WEBVIEW_TERMS_SHA256, "WebView2 공식 약관")
    packaged_terms = _require_file_sha(source.packaged_terms, _WEBVIEW_TERMS_SHA256, "WebView2 제품 약관")
    if len(terms_source) != 24429 or packaged_terms != terms_source:
        _fail("WebView2 제품 약관 파일이 공식 JSON 원문과 바이트 단위로 다릅니다.")
    for key in ("end_user_terms_acceptance", "smartscreen_notice_location"):
        location = _require_evidence_location(evidence, key)
        _validate_evidence_files(source.evidence_root, key, location)
    policy = _exact_keys(evidence["windows_7_8_1_notice_policy"], _NOTICE_POLICY_KEYS, "windows_7_8_1_notice_policy")
    if policy["policy"] not in ("notice-shown", "unsupported-by-installer"):
        _fail("Windows 7/8.1 안내 정책값이 맞지 않습니다.")
    verification = _require_evidence_location(policy, "verification")
    _validate_evidence_files(
        source.evidence_root,
        "windows_7_8_1_notice_policy.verification",
        verification,
    )
    _validate_build_proof(
        approval_path,
        spec,
        evidence,
        source,
        package_version_reader=package_version_reader,
        file_version_reader=file_version_reader,
    )
    _require_true(evidence, "install_command_not_run")


def _spec_model(tool: str, spec: dict[str, object]):
    return _webview2_model(spec)


def _load_tool_source_approval_with_bytes(
    path: Path,
    *,
    expected_tool: str,
    source_files: WebView2SourceFiles | None = None,
    authenticode_verifier: Callable[[Path], AuthenticodeEvidence] = _verify_windows_authenticode,
    package_version_reader: Callable[[str], str] | None = None,
    file_version_reader: Callable[[Path], str] | None = None,
    bootstrapper_file_version_reader: Callable[[Path], str] | None = None,
) -> tuple[ToolSourceApproval, bytes]:
    if expected_tool not in _TOOLS:
        _fail(f"알 수 없는 도구 이름입니다: {expected_tool}")
    raw, raw_bytes = _read_json(Path(path))
    raw = _exact_keys(raw, _TOP_KEYS, "승인 JSON")
    if type(raw["approval_schema_version"]) is not int or raw["approval_schema_version"] != 1:
        _fail("approval_schema_version 1이 필요합니다.")
    if raw["tool"] != expected_tool:
        _fail(f"승인 파일의 tool은 {expected_tool}이어야 합니다.")
    verified_at = _validate_utc(raw["verified_at_utc"], "verified_at_utc")
    if raw["source_approval_status"] != "approved":
        _fail("source_approval_status는 모든 확인이 끝난 approved만 허용합니다.")
    field = _exact_keys(raw["field_verification"], _FIELD_KEYS, "field_verification")
    if field != {"status": "not-run", "items": []}:
        _fail("source 승인 단계의 field_verification은 not-run과 빈 items여야 합니다.")
    spec_keys = _WEBVIEW2_SPEC_KEYS
    evidence_keys = _WEBVIEW2_EVIDENCE_KEYS
    spec = _exact_keys(raw["spec"], spec_keys, f"{expected_tool}.spec")
    evidence = _exact_keys(raw["evidence"], evidence_keys, f"{expected_tool}.evidence")
    _reject_empty_null_placeholder(spec, f"{expected_tool}.spec")
    _reject_empty_null_placeholder(evidence, f"{expected_tool}.evidence")
    model = _spec_model(expected_tool, spec)
    if not isinstance(source_files, WebView2SourceFiles):
        _fail("WebView2 실제 입력 파일 경로를 명시해야 합니다.")
    _validate_webview2_evidence(
        Path(path),
        model,
        evidence,
        source_files,
        authenticode_verifier=authenticode_verifier,
        package_version_reader=package_version_reader,
        file_version_reader=file_version_reader,
        bootstrapper_file_version_reader=bootstrapper_file_version_reader,
    )
    approval = ToolSourceApproval(
        approval_schema_version=1,
        tool=expected_tool,
        verified_at_utc=verified_at,
        source_approval_status="approved",
        spec=dict(spec),
        evidence=dict(evidence),
        field_verification=dict(field),
    )
    return approval, raw_bytes


def load_tool_source_approval(
    path: Path,
    *,
    expected_tool: str,
    source_files: WebView2SourceFiles | None = None,
    authenticode_verifier: Callable[[Path], AuthenticodeEvidence] = _verify_windows_authenticode,
    package_version_reader: Callable[[str], str] | None = None,
    file_version_reader: Callable[[Path], str] | None = None,
    bootstrapper_file_version_reader: Callable[[Path], str] | None = None,
) -> ToolSourceApproval:
    """승인 JSON 하나를 읽되 애매하거나 덜 확인된 값은 모두 거부한다."""
    approval, _raw_bytes = _load_tool_source_approval_with_bytes(
        Path(path),
        expected_tool=expected_tool,
        source_files=source_files,
        authenticode_verifier=authenticode_verifier,
        package_version_reader=package_version_reader,
        file_version_reader=file_version_reader,
        bootstrapper_file_version_reader=bootstrapper_file_version_reader,
    )
    return approval


def _load_approval_set(
    approvals_dir: Path,
    source_files: ToolSourceFiles | None,
    *,
    authenticode_verifier: Callable[[Path], AuthenticodeEvidence],
    package_version_reader: Callable[[str], str] | None,
    file_version_reader: Callable[[Path], str] | None,
    bootstrapper_file_version_reader: Callable[[Path], str] | None,
):
    if not isinstance(source_files, ToolSourceFiles):
        _fail("동봉 도구의 실제 입력 파일 경로를 명시해야 합니다.")
    approvals_dir = Path(approvals_dir)
    approvals = {}
    hashes = {}
    for tool in _TOOLS:
        path = approvals_dir / f"{tool}.json"
        approval, raw_bytes = _load_tool_source_approval_with_bytes(
            path,
            expected_tool=tool,
            source_files=getattr(source_files, tool),
            authenticode_verifier=authenticode_verifier,
            package_version_reader=package_version_reader,
            file_version_reader=file_version_reader,
            bootstrapper_file_version_reader=bootstrapper_file_version_reader,
        )
        approvals[tool] = approval
        hashes[tool] = hashlib.sha256(raw_bytes).hexdigest()
    return approvals, hashes


def _display_value(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _table(rows) -> str:
    lines = ["| 항목 | 값 |", "|---|---|"]
    lines.extend(f"| {_display_value(key)} | {_display_value(value)} |" for key, value in rows)
    return "\n".join(lines)


def _status_section(tool: str, approval: ToolSourceApproval) -> str:
    title = {"webview2": "WebView2"}[tool]
    evidence = approval.evidence
    automatic_rows = [
        ("Authenticode", evidence["authenticode_status"]),
        ("build proof", f"{evidence['build_proof_path']} / {evidence['build_proof_sha256']}"),
        ("설치 명령", "실행하지 않음" if evidence["install_command_not_run"] else "실행함"),
    ]
    license_keys = (
        "terms_source_url", "terms_locale", "terms_json_field", "terms_source_sha256",
        "license_file_sha256", "end_user_terms_acceptance", "smartscreen_notice_location",
        "windows_7_8_1_notice_policy",
    )
    license_rows = [
        ("license_id", approval.spec["license_id"]),
        ("license_file", approval.spec["license_file"]),
        *((key, evidence[key]) for key in license_keys),
    ]
    return "\n\n".join([
        f"## {title}",
        "### spec\n\n" + _table(approval.spec.items()),
        "### official evidence\n\n" + _table(evidence.items()),
        "### automatic checks\n\n" + _table(automatic_rows),
        "### field verification\n\n" + _table(approval.field_verification.items()),
        "### license and notices\n\n" + _table(license_rows),
    ])


def render_tool_input_status(
    approvals: Mapping[str, ToolSourceApproval],
    approval_hashes: Mapping[str, str],
    *,
    generated_at_utc: str,
) -> str:
    """검증된 승인 JSON 값만 사용해 공개 상태 문서를 만든다."""
    generated_at = _validate_utc(generated_at_utc, "generated_at_utc")
    if set(approvals) != set(_TOOLS) or set(approval_hashes) != set(_TOOLS):
        _fail("상태 문서에는 WebView2 승인이 필요합니다.")
    for tool in _TOOLS:
        digest = approval_hashes[tool]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _fail(f"{tool} 승인 원문 SHA-256이 올바르지 않습니다.")
    front = [
        "---",
        "status_schema_version: 1",
        f"generated_at_utc: {generated_at}",
        "overall_status: SOURCE_APPROVED",
        "approval_files:",
    ]
    for tool in _TOOLS:
        front.extend([
            f"  {tool}:",
            f"    path: approvals/{tool}.json",
            f"    sha256: {approval_hashes[tool]}",
        ])
    front.extend(["---", "", "# 공식 도구 입력 확인", ""])
    sections = [_status_section(tool, approvals[tool]) for tool in _TOOLS]
    return "\n".join(front) + "\n\n".join(sections) + "\n"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_tool_input_status(
    approvals_dir: Path,
    output_path: Path,
    *,
    source_files: ToolSourceFiles | None = None,
    generated_at_utc: str | None = None,
    authenticode_verifier: Callable[[Path], AuthenticodeEvidence] = _verify_windows_authenticode,
    package_version_reader: Callable[[str], str] | None = None,
    file_version_reader: Callable[[Path], str] | None = None,
    bootstrapper_file_version_reader: Callable[[Path], str] | None = None,
) -> Path:
    """승인 파일이 모두 유효할 때만 상태 문서를 원자 교체한다."""
    approvals, hashes = _load_approval_set(
        approvals_dir,
        source_files,
        authenticode_verifier=authenticode_verifier,
        package_version_reader=package_version_reader,
        file_version_reader=file_version_reader,
        bootstrapper_file_version_reader=bootstrapper_file_version_reader,
    )
    text = render_tool_input_status(
        approvals,
        hashes,
        generated_at_utc=generated_at_utc or _now_utc(),
    )
    component_lock.atomic_write_text_unique(Path(output_path), text)
    return Path(output_path)


def _manifest_bytes(
    webview2: WebView2Spec,
) -> bytes:
    data = {
        "schema_version": 1,
        "webview2": asdict(webview2),
    }
    data["webview2"]["install_arguments"] = list(webview2.install_arguments)
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_candidate(path: Path, value: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with candidate.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            candidate.unlink()
        except OSError:
            pass
        raise
    return candidate


def _retry_filesystem_action(action: Callable[[], None]) -> None:
    last_error = None
    for attempt in range(3):
        try:
            action()
            return
        except OSError as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.02)
    assert last_error is not None
    raise last_error


def _restore_bytes(path: Path, previous: bytes | None) -> None:
    if previous is None:
        def remove_output() -> None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        _retry_filesystem_action(remove_output)
        return
    candidate = _write_candidate(path, previous)
    try:
        try:
            _retry_filesystem_action(lambda: os.replace(candidate, path))
        except OSError:
            # Windows 백신이나 파일 감시기가 이름 교체만 계속 막는 경우에도,
            # 이미 있던 파일 자체를 열 수 있으면 마지막으로 원래 바이트를 복구한다.
            with path.open("wb") as handle:
                handle.write(previous)
                handle.flush()
                os.fsync(handle.fileno())
            if path.read_bytes() != previous:
                raise OSError("이전 출력 바이트를 직접 복구하지 못했습니다.")
    finally:
        try:
            candidate.unlink()
        except OSError:
            pass


def _path_has_reparse_component(path: Path) -> bool:
    current = Path(os.path.abspath(os.fspath(path)))
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            pass
        except OSError as error:
            _fail(f"출력 경로를 안전하게 확인하지 못했습니다: {error}")
        else:
            attributes = getattr(info, "st_file_attributes", 0)
            if stat.S_ISLNK(info.st_mode) or attributes & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            ):
                return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _safe_output_path(path: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if _path_has_reparse_component(absolute):
        _fail(f"{label} 출력 경로에 symlink 또는 reparse/junction을 쓸 수 없습니다.")
    if absolute.exists() and not absolute.is_file():
        _fail(f"{label} 출력 경로는 파일이어야 합니다.")
    return absolute.resolve(strict=False)


def assemble_tool_approvals(
    approvals_dir: Path,
    manifest_path: Path,
    status_path: Path,
    *,
    source_files: ToolSourceFiles | None = None,
    generated_at_utc: str | None = None,
    authenticode_verifier: Callable[[Path], AuthenticodeEvidence] = _verify_windows_authenticode,
    package_version_reader: Callable[[str], str] | None = None,
    file_version_reader: Callable[[Path], str] | None = None,
    bootstrapper_file_version_reader: Callable[[Path], str] | None = None,
) -> ToolsManifest:
    """두 결과를 모두 완성·검사한 뒤 함께 교체하고, 둘째 실패면 첫째를 되돌린다."""
    manifest_path = _safe_output_path(Path(manifest_path), "manifest")
    status_path = _safe_output_path(Path(status_path), "상태 문서")
    if manifest_path == status_path:
        _fail("manifest와 상태 문서는 서로 다른 출력 파일이어야 합니다.")
    if manifest_path in status_path.parents or status_path in manifest_path.parents:
        _fail("manifest와 상태 문서는 부모와 자식 경로가 될 수 없습니다.")
    approvals, hashes = _load_approval_set(
        approvals_dir,
        source_files,
        authenticode_verifier=authenticode_verifier,
        package_version_reader=package_version_reader,
        file_version_reader=file_version_reader,
        bootstrapper_file_version_reader=bootstrapper_file_version_reader,
    )
    status_text = render_tool_input_status(
        approvals,
        hashes,
        generated_at_utc=generated_at_utc or _now_utc(),
    )
    webview2 = _webview2_model(approvals["webview2"].spec)
    manifest_value = _manifest_bytes(webview2)
    status_value = status_text.encode("utf-8")
    manifest_candidate = None
    status_candidate = None
    result = None
    try:
        manifest_candidate = _write_candidate(manifest_path, manifest_value)
        status_candidate = _write_candidate(status_path, status_value)
        require_build_complete(load_tools_manifest(manifest_candidate))
        if not status_candidate.read_text(encoding="utf-8").endswith("\n"):
            _fail("상태 문서 임시 파일이 완성되지 않았습니다.")
        lock_path = manifest_path.parent / ".teacher-manager-tool-approval-outputs.lock"
        with component_lock.exclusive_file_lock(lock_path):
            manifest_previous = manifest_path.read_bytes() if manifest_path.exists() else None
            status_previous = status_path.read_bytes() if status_path.exists() else None
            try:
                os.replace(manifest_candidate, manifest_path)
                os.replace(status_candidate, status_path)
                result = require_build_complete(load_tools_manifest(manifest_path))
                if manifest_path.read_bytes() != manifest_value:
                    _fail("완성된 manifest가 검사한 임시 파일과 다릅니다.")
                if status_path.read_bytes() != status_value:
                    _fail("완성된 상태 문서가 검사한 임시 파일과 다릅니다.")
            except BaseException as original_error:
                rollback_errors = []
                for output, previous in (
                    (status_path, status_previous),
                    (manifest_path, manifest_previous),
                ):
                    try:
                        _restore_bytes(output, previous)
                    except OSError as rollback_error:
                        rollback_errors.append(rollback_error)
                if rollback_errors:
                    _fail("출력 교체 실패 뒤 이전 파일을 바이트 그대로 되돌리지 못했습니다.")
                raise
        assert result is not None
        return result
    finally:
        for candidate in (manifest_candidate, status_candidate):
            if candidate is None:
                continue
            try:
                candidate.unlink()
            except OSError:
                pass
