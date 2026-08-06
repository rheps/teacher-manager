"""동봉 GWS와 사용자가 승인해 설치한 GWS 중 안전한 실행 파일을 고른다.

이 모듈은 사용자 시간표·Google 로그인 자료를 읽지 않는다. 갱신 실행 파일은
LOCALAPPDATA의 별도 구성요소 폴더에만 두며, PATH나 npm 전역 설치본도 후보가 아니다.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence
from urllib.parse import urlparse

from . import bundle_paths, component_lock, process_win
from .tool_manifest import (
    BundledGwsSpec,
    ManagedNodeSpec,
    ManifestError,
    ToolsManifest,
    load_tools_manifest,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?$")
_VERSION_LINE = re.compile(
    r"^gws ([0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?)\r?$",
    re.MULTILINE,
)
_NUMERIC_NODE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_NUMERIC_TOOL_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ACTIVE_FIELDS = {
    "schema_version",
    "version",
    "executable_sha256",
    "archive_url",
    "installed_at",
    "app_min_version",
    "app_max_version",
    "approval_manifest_sha256",
    "login_store_compatible",
}
_APPROVAL_FIELDS = {
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
_CACHE: dict[tuple[object, ...], "GwsResolution"] = {}


@dataclass(frozen=True)
class GwsResolution:
    executable: Path
    version: str
    source: Literal["approved-update", "bundled"]
    recovered_from: str = ""


@dataclass(frozen=True)
class ActiveGwsMetadata:
    schema_version: int
    version: str
    executable_sha256: str
    archive_url: str
    installed_at: str
    app_min_version: str
    app_max_version: str
    approval_manifest_sha256: str
    login_store_compatible: bool


@dataclass(frozen=True)
class NodeRuntime:
    ready: bool
    code: str
    detail: str
    version: str
    root: Path | None
    node_exe: Path | None
    npm_cmd: Path | None
    npx_cmd: Path | None


class GwsRuntimeError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _component_dir_unavailable() -> GwsRuntimeError:
    return GwsRuntimeError(
        "GWS_RUNTIME_COMPONENT_DIR_UNAVAILABLE",
        "Windows LOCALAPPDATA 폴더를 찾지 못했습니다.",
    )


def _component_path_unsafe() -> GwsRuntimeError:
    return GwsRuntimeError(
        "GWS_RUNTIME_COMPONENT_PATH_UNSAFE",
        "Google 도구를 둘 Windows 앱 저장 폴더가 다른 위치를 가리킵니다.",
    )


def _path_key(path: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path))).casefold()


def _entry_is_reparse(path: Path, info) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    if reparse_flag and attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _validated_local_app_data(raw: str) -> Path:
    """GWS 구성요소가 로컬 drive의 실제 폴더 아래에만 놓이게 한다.

    여기서는 폴더를 만들지 않는다. 가장 가까운 기존 조상이 바로가기나 junction
    밖으로 빠지는지만 읽어서 확인하고, 실제 생성 직전에는 설치 쪽이 다시 확인한다.
    """
    value = str(raw or "").strip()
    if not value:
        raise _component_dir_unavailable()
    base = Path(value)
    if not base.is_absolute():
        raise _component_dir_unavailable()
    if os.name == "nt":
        windows_text = value.replace("/", "\\")
        # 공유 폴더, 장치 경로, drive 이름만 붙은 상대 경로는 앱 저장소가 아니다.
        if windows_text.startswith("\\\\") or not re.fullmatch(r"[A-Za-z]:", base.drive or ""):
            raise _component_dir_unavailable()
        if ":" in windows_text[len(base.drive):]:
            raise _component_dir_unavailable()

    absolute = Path(os.path.abspath(str(base)))
    existing = absolute
    while not os.path.lexists(existing):
        parent = existing.parent
        if parent == existing:
            raise _component_path_unsafe()
        existing = parent
    try:
        info = os.lstat(existing)
        if _entry_is_reparse(existing, info) or not stat.S_ISDIR(info.st_mode):
            raise _component_path_unsafe()
        if _path_key(existing.resolve(strict=True)) != _path_key(existing.absolute()):
            raise _component_path_unsafe()
    except GwsRuntimeError:
        raise
    except OSError as error:
        raise _component_path_unsafe() from error
    return absolute


def component_base(environ: Mapping[str, str] | None = None) -> Path:
    """사용자 설정과 분리된 구성요소 전용 기준 폴더를 돌려준다."""
    values = os.environ if environ is None else environ
    local_app_data = _validated_local_app_data(str(values.get("LOCALAPPDATA") or ""))
    return local_app_data / "BigSilverEduLab" / "TeacherManager" / "components"


def component_gws_root(environ: Mapping[str, str] | None = None) -> Path:
    return component_base(environ) / "gws"


def component_node_root(
    *, local_app_data: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    if local_app_data is not None:
        raw = str(local_app_data)
    else:
        values = os.environ if environ is None else environ
        raw = str(values.get("LOCALAPPDATA") or "")
    if not raw.strip():
        return None
    base = Path(raw)
    if not base.is_absolute():
        return None
    if os.name == "nt":
        windows_text = raw.replace("/", "\\")
        if windows_text.startswith("\\\\") or not base.drive:
            return None
    return base / "BigSilverEduLab" / "TeacherManager" / "components" / "node"


def _node_not_ready(code: str, detail: str, version: str = "") -> NodeRuntime:
    return NodeRuntime(False, code, detail, version, None, None, None, None)


def _required_node_spec(
    manifest: ManagedNodeSpec | None,
) -> tuple[ManagedNodeSpec | None, str]:
    if manifest is not None:
        return (
            (manifest, "")
            if isinstance(manifest, ManagedNodeSpec)
            else (None, "NODE_MANIFEST_INVALID")
        )
    path = bundle_paths.bundle_root() / "tools-manifest.json"
    if not path.is_file():
        return None, "NODE_MANIFEST_MISSING"
    try:
        tools = load_tools_manifest(path)
    except ManifestError:
        return None, "NODE_MANIFEST_INVALID"
    if not isinstance(tools.node, ManagedNodeSpec):
        return None, "NODE_MANIFEST_MISSING"
    return tools.node, ""


def resolve_node(
    *,
    manifest: ManagedNodeSpec | None = None,
    local_app_data: str | Path | None = None,
    run_command: Callable[[Sequence[str]], tuple[int, str]] = process_win.run_captured,
) -> NodeRuntime:
    """Teacher Manager 전용 Node만 확인하고 시스템 PATH는 후보로 보지 않는다."""
    spec, manifest_error = _required_node_spec(manifest)
    if spec is None:
        return _node_not_ready(
            manifest_error or "NODE_MANIFEST_MISSING",
            (
                "Teacher Manager 전용 Node 설치 목록이 올바르지 않습니다."
                if manifest_error == "NODE_MANIFEST_INVALID"
                else "Teacher Manager 전용 Node 설치 목록이 없습니다."
            ),
        )
    component = component_node_root(local_app_data=local_app_data)
    if component is None:
        return _node_not_ready(
            "NODE_COMPONENT_DIR_UNAVAILABLE",
            "Windows LOCALAPPDATA 폴더를 찾지 못했습니다.",
        )
    active_path = component / "active.json"
    if not os.path.lexists(active_path):
        return _node_not_ready(
            "NODE_NOT_INSTALLED",
            "Teacher Manager 전용 Node가 아직 준비되지 않았습니다.",
        )
    try:
        active_stat = os.lstat(active_path)
        active_attributes = int(getattr(active_stat, "st_file_attributes", 0) or 0)
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
        if (
            stat.S_ISLNK(active_stat.st_mode)
            or not stat.S_ISREG(active_stat.st_mode)
            or int(getattr(active_stat, "st_nlink", 1)) != 1
            or (reparse_flag and active_attributes & reparse_flag)
        ):
            return _node_not_ready(
                "NODE_PATH_UNSAFE",
                "Teacher Manager 전용 Node의 현재 판 기록이 다른 파일을 가리킵니다.",
            )
    except OSError:
        return _node_not_ready(
            "NODE_PATH_UNSAFE",
            "Teacher Manager 전용 Node의 현재 판 기록을 안전하게 확인하지 못했습니다.",
        )
    try:
        base = component.parents[3]
        expected_component = (
            base.absolute()
            / "BigSilverEduLab"
            / "TeacherManager"
            / "components"
            / "node"
        )
        resolved_base = base.resolve(strict=True)
        resolved_component = component.resolve(strict=True)
        resolved_active = active_path.resolve(strict=True)
    except OSError:
        return _node_not_ready(
            "NODE_PATH_UNSAFE",
            "Teacher Manager 전용 Node 폴더를 안전하게 확인하지 못했습니다.",
        )
    if (
        resolved_base != base.absolute()
        or resolved_component != expected_component
        or resolved_active != resolved_component / "active.json"
    ):
        return _node_not_ready(
            "NODE_PATH_UNSAFE",
            "Teacher Manager 전용 Node 폴더가 LOCALAPPDATA 밖을 가리킵니다.",
        )
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _node_not_ready(
            "NODE_ACTIVE_INVALID",
            "Teacher Manager 전용 Node의 현재 판 기록을 읽지 못했습니다.",
        )
    if (
        not isinstance(active, dict)
        or set(active) != {"schema_version", "version"}
        or type(active.get("schema_version")) is not int
        or active.get("schema_version") != 1
        or not isinstance(active.get("version"), str)
        or not active.get("version")
    ):
        return _node_not_ready(
            "NODE_ACTIVE_INVALID",
            "Teacher Manager 전용 Node의 현재 판 기록이 올바르지 않습니다.",
        )
    active_version = active["version"]
    if len(active_version) > 64 or not _NUMERIC_NODE_VERSION.fullmatch(active_version):
        return _node_not_ready(
            "NODE_ACTIVE_INVALID",
            "Teacher Manager 전용 Node의 현재 판 기록이 올바르지 않습니다.",
        )
    if active_version != spec.version:
        return _node_not_ready(
            "NODE_VERSION_MISMATCH",
            "준비된 Node 판이 이 Teacher Manager가 승인한 판과 다릅니다.",
            active_version,
        )

    expected_versions = component / "versions"
    expected_root = expected_versions / f"v{spec.version}"
    candidates = tuple(
        expected_root / Path(relative.replace("\\", "/")).name
        for relative in (
            spec.node_relative_path,
            spec.npm_relative_path,
            spec.npx_relative_path,
        )
    )
    if not all(path.is_file() for path in candidates):
        return _node_not_ready(
            "NODE_TOOLS_MISSING",
            "Teacher Manager 전용 Node, npm 또는 npx 파일이 빠져 있습니다.",
            active_version,
        )
    try:
        root = expected_root.resolve(strict=True)
        node_exe, npm_cmd, npx_cmd = (path.resolve(strict=True) for path in candidates)
        versions_root = expected_versions.resolve(strict=True)
    except OSError:
        return _node_not_ready(
            "NODE_TOOLS_MISSING",
            "Teacher Manager 전용 Node, npm 또는 npx 파일을 열 수 없습니다.",
            active_version,
        )
    if (
        versions_root != resolved_component / "versions"
        or root != versions_root / f"v{spec.version}"
        or any(
            path != root / expected_name
            for path, expected_name in (
                (node_exe, "node.exe"),
                (npm_cmd, "npm.cmd"),
                (npx_cmd, "npx.cmd"),
            )
        )
    ):
        return _node_not_ready(
            "NODE_PATH_UNSAFE",
            "Teacher Manager 전용 Node 폴더가 정해진 위치 밖을 가리킵니다.",
            active_version,
        )

    code, output = run_command([str(node_exe), "--version"])
    if code != 0 or str(output or "").strip() != f"v{spec.version}":
        return _node_not_ready(
            "NODE_VERSION_MISMATCH",
            "Teacher Manager 전용 Node가 승인된 판으로 실행되지 않았습니다.",
            active_version,
        )
    tool_versions: list[str] = []
    for path, name in ((npm_cmd, "npm"), (npx_cmd, "npx")):
        code, output = run_command([str(path), "--version"])
        reported = str(output or "").strip()
        if code != 0 or not _NUMERIC_TOOL_VERSION.fullmatch(reported):
            return _node_not_ready(
                "NODE_TOOL_VERSION_INVALID",
                f"Teacher Manager 전용 {name} 판 번호를 안전하게 확인하지 못했습니다.",
                active_version,
            )
        tool_versions.append(reported)
    if tool_versions[0] != tool_versions[1]:
        return _node_not_ready(
            "NODE_TOOL_VERSION_INVALID",
            "Teacher Manager 전용 npm과 npx 판 번호가 서로 다릅니다.",
            active_version,
        )
    return NodeRuntime(
        True,
        "NODE_READY",
        "Teacher Manager 전용 Node가 준비됐습니다.",
        spec.version,
        root,
        node_exe,
        npm_cmd,
        npx_cmd,
    )


def node_subprocess_env(
    runtime: NodeRuntime,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """관리형 Node 자식 실행에만 쓰는 환경 복사본을 만든다."""
    if not runtime.ready or runtime.root is None:
        raise ValueError("준비되지 않은 Teacher Manager 전용 Node입니다.")
    source = os.environ if environ is None else environ
    child = {str(key): str(value) for key, value in source.items()}
    old_path_values: list[str] = []
    for key in tuple(child):
        if key.upper() == "PATH":
            old_path_values.append(child.pop(key))
    managed = str(runtime.root)
    managed_key = ntpath.normcase(ntpath.normpath(managed)).casefold()
    path_entries = [managed]
    for value in old_path_values:
        for raw_entry in value.split(os.pathsep):
            entry = raw_entry.strip()
            if not entry:
                continue
            entry_key = ntpath.normcase(ntpath.normpath(entry)).casefold()
            if entry_key != managed_key:
                path_entries.append(entry)
    child["PATH"] = os.pathsep.join(path_entries)
    try:
        if runtime.root.resolve(strict=True) != runtime.root:
            raise ValueError("Node 실행 폴더가 구성요소 폴더 밖을 가리킵니다.")
        component_path = runtime.root.parent.parent
        component = component_path.resolve(strict=True)
    except OSError as error:
        raise ValueError("Node 실행 폴더를 안전하게 확인하지 못했습니다.") from error
    if component != component_path:
        raise ValueError("Node 실행 폴더가 구성요소 폴더 밖을 가리킵니다.")
    cache = component / "cache" / "npm"
    temporary = component / "temp"
    for path in (cache, temporary):
        resolved = path.resolve(strict=False)
        if resolved != path or not resolved.is_relative_to(component):
            raise ValueError("Node cache 또는 임시 폴더가 구성요소 폴더 밖을 가리킵니다.")
    child["npm_config_cache"] = str(cache)
    child["TEMP"] = str(temporary)
    child["TMP"] = str(temporary)
    child["npm_config_update_notifier"] = "false"
    child["npm_config_fund"] = "false"
    child["npm_config_audit"] = "false"
    return child


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def current_app_version() -> str:
    """화면 모듈을 불러오는 도중에도 순환하지 않도록 필요할 때만 판 번호를 읽는다."""
    from dashboard.version import APP_VERSION

    value = str(APP_VERSION or "").strip()
    if not _VERSION.fullmatch(value):
        raise GwsRuntimeError(
            "GWS_RUNTIME_INTERNAL_DAMAGE",
            "Teacher Manager 판 번호를 안전하게 읽지 못했습니다.",
        )
    return value


def _version_numbers(value: str) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        return None
    numeric = re.split(r"[-+]", value, maxsplit=1)[0]
    return tuple(int(part) for part in numeric.split("."))


def _compare_versions(left: str, right: str) -> int:
    left_parts = _version_numbers(left)
    right_parts = _version_numbers(right)
    if left_parts is None or right_parts is None:
        raise ValueError("판 번호 모양이 올바르지 않습니다.")
    width = max(len(left_parts), len(right_parts))
    normalized_left = left_parts + (0,) * (width - len(left_parts))
    normalized_right = right_parts + (0,) * (width - len(right_parts))
    return (normalized_left > normalized_right) - (normalized_left < normalized_right)


def _version_in_range(current: str, minimum: str, maximum: str) -> bool:
    try:
        return _compare_versions(current, minimum) >= 0 and _compare_versions(current, maximum) <= 0
    except ValueError:
        return False


def _valid_archive_url(version: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    expected = (
        "/googleworkspace/cli/releases/download/"
        f"v{version}/google-workspace-cli-x86_64-pc-windows-msvc.zip"
    )
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path == expected
    )


def _read_active(component_root: Path) -> tuple[ActiveGwsMetadata | None, str, str]:
    path = component_root / "active.json"
    if not path.is_file():
        return None, "", ""
    try:
        raw = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "", ""
    if not isinstance(raw, dict):
        return None, "", ""
    version = raw.get("version", "")
    rejected_version = version if isinstance(version, str) else ""
    if set(raw) != _ACTIVE_FIELDS:
        return None, rejected_version, "invalid-active-metadata"
    string_fields = _ACTIVE_FIELDS - {"schema_version", "login_store_compatible"}
    if (
        type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != 1
        or type(raw.get("login_store_compatible")) is not bool
        or not all(isinstance(raw.get(field), str) and raw[field] for field in string_fields)
    ):
        return None, rejected_version, "invalid-active-metadata"
    digest = raw["executable_sha256"]
    approval_digest = raw["approval_manifest_sha256"]
    if (
        not _VERSION.fullmatch(version)
        or not _VERSION.fullmatch(raw["app_min_version"])
        or not _VERSION.fullmatch(raw["app_max_version"])
        or _compare_versions(raw["app_min_version"], raw["app_max_version"]) > 0
        or not _SHA256.fullmatch(digest)
        or not _SHA256.fullmatch(approval_digest)
    ):
        return None, version if isinstance(version, str) else "", "invalid-active-metadata"
    if not _valid_archive_url(version, raw["archive_url"]):
        return None, version, "invalid-active-metadata"
    return ActiveGwsMetadata(**raw), version, ""


def _read_and_check_approval(
    executable: Path,
    active: ActiveGwsMetadata,
) -> str:
    approval_path = executable.parent / "approval.json"
    try:
        exact_bytes = approval_path.read_bytes()
    except OSError:
        return "approval-missing"
    if hashlib.sha256(exact_bytes).hexdigest() != active.approval_manifest_sha256:
        return "approval-hash-mismatch"
    try:
        raw = json.loads(exact_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return "approval-invalid"
    if not isinstance(raw, dict) or set(raw) != _APPROVAL_FIELDS:
        return "approval-invalid"
    string_fields = _APPROVAL_FIELDS - {"schema_version", "login_store_compatible"}
    if (
        type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != 1
        or type(raw.get("login_store_compatible")) is not bool
        or not all(isinstance(raw.get(field), str) for field in string_fields)
        or not raw.get("verified_on")
    ):
        return "approval-invalid"
    if (
        raw["platform"] != "windows"
        or raw["architecture"] != "x86_64"
        or raw["archive_filename"]
        != "google-workspace-cli-x86_64-pc-windows-msvc.zip"
        or not _SHA256.fullmatch(raw["archive_sha256"])
        or not _SHA256.fullmatch(raw["executable_sha256"])
        or not _valid_archive_url(raw["version"], raw["archive_url"])
    ):
        return "approval-invalid"
    matched_fields = (
        "version",
        "archive_url",
        "executable_sha256",
        "app_min_version",
        "app_max_version",
        "login_store_compatible",
    )
    if any(raw[field] != getattr(active, field) for field in matched_fields):
        return "approval-field-mismatch"
    return ""


def _read_bad_versions(component_root: Path) -> dict[str, dict[str, str]]:
    try:
        raw = _read_json(component_root / "bad-versions.json")
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    versions = raw.get("versions") if isinstance(raw, dict) else None
    if not isinstance(versions, dict):
        return {}
    return {
        str(version): dict(record)
        for version, record in versions.items()
        if isinstance(version, str) and isinstance(record, dict)
    }


def _record_bad_version(component_root: Path, version: str, reason: str) -> None:
    if not version:
        return
    with component_lock.exclusive_file_lock(component_root / "locks" / "update.lock"):
        versions = _read_bad_versions(component_root)
        failed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        versions[version] = {"code": reason, "failed_at": failed_at}
        payload = {"schema_version": 1, "versions": versions}
        component_lock.atomic_write_text_unique(
            component_root / "bad-versions.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )


def _try_record_bad_version(component_root: Path, version: str, reason: str) -> None:
    """기록 장치가 고장 나도 이미 검증된 동봉본 복귀는 계속한다."""
    try:
        _record_bad_version(component_root, version, reason)
    except Exception:  # noqa: BLE001 - 잠금·권한·디스크 오류는 복구를 막지 않는다
        pass


def _reported_version(output: str) -> str:
    matched = _VERSION_LINE.search(str(output or ""))
    return matched.group(1) if matched else ""


def _check_command(
    executable: Path,
    expected_version: str | None,
    run_command: Callable[[Sequence[str]], tuple[int, str]],
) -> tuple[bool, str]:
    code, output = run_command([str(executable), "--version"])
    if code != 0:
        return False, "version-check-failed"
    reported = _reported_version(output)
    if expected_version is not None and reported != expected_version:
        return False, "version-mismatch"
    if not reported:
        return False, "version-check-failed"
    code, _output = run_command([str(executable), "--help"])
    if code != 0:
        return False, "help-check-failed"
    return True, reported


def _bundled_executable(bundle_root: Path) -> Path:
    return Path(bundle_root) / "tools" / "gws" / "bundled" / "gws.exe"


def _resolved_update_executable(component_root: Path, version: str) -> tuple[Path | None, str]:
    """버전 폴더의 링크가 구성요소 폴더 밖을 가리키면 실행 후보에서 뺀다."""
    try:
        resolved_component_root = component_root.resolve(strict=True)
        versions_root = (component_root / "versions").resolve(strict=True)
        executable = (component_root / "versions" / version / "gws.exe").resolve(strict=True)
    except OSError:
        return None, "missing-executable"
    if versions_root == resolved_component_root or not versions_root.is_relative_to(
        resolved_component_root
    ):
        return None, "path-escape"
    if not executable.is_relative_to(versions_root):
        return None, "path-escape"
    if not executable.is_file():
        return None, "missing-executable"
    return executable, ""


def _fallback_to_bundled(
    bundled: Path,
    bundled_spec: BundledGwsSpec,
    recovered_from: str,
    run_command: Callable[[Sequence[str]], tuple[int, str]],
) -> GwsResolution:
    if not bundled.is_file():
        raise GwsRuntimeError(
            "GWS_RUNTIME_INTERNAL_DAMAGE",
            "동봉 Google Workspace CLI 실행 파일이 없습니다.",
        )
    try:
        executable_sha256 = _sha256(bundled)
    except OSError as error:
        raise GwsRuntimeError(
            "GWS_RUNTIME_INTERNAL_DAMAGE",
            "동봉 Google Workspace CLI 실행 파일을 읽지 못했습니다.",
        ) from error
    if executable_sha256 != bundled_spec.executable_sha256:
        raise GwsRuntimeError(
            "GWS_RUNTIME_INTERNAL_DAMAGE",
            "동봉 Google Workspace CLI 실행 파일의 SHA-256이 설치 목록과 다릅니다.",
        )
    healthy, version_or_reason = _check_command(
        bundled, bundled_spec.version, run_command
    )
    if not healthy:
        raise GwsRuntimeError(
            "GWS_RUNTIME_INTERNAL_DAMAGE",
            "동봉 Google Workspace CLI 실행 확인에 실패했습니다.",
        )
    return GwsResolution(bundled.resolve(), version_or_reason, "bundled", recovered_from)


def _required_gws_spec(
    bundle_root: Path,
    tools_manifest: ToolsManifest | None,
) -> BundledGwsSpec:
    try:
        manifest = (
            tools_manifest
            if tools_manifest is not None
            else load_tools_manifest(bundle_root / "tools-manifest.json")
        )
    except ManifestError as error:
        raise GwsRuntimeError(
            "GWS_RUNTIME_INTERNAL_DAMAGE",
            "동봉 도구 설치 목록을 읽지 못했습니다.",
        ) from error
    if not isinstance(manifest, ToolsManifest) or not isinstance(
        manifest.gws, BundledGwsSpec
    ):
        raise GwsRuntimeError(
            "GWS_RUNTIME_INTERNAL_DAMAGE",
            "동봉 도구 설치 목록에 Google Workspace CLI 정보가 없습니다.",
        )
    return manifest.gws


def _cache_key(
    component_root: Path,
    bundled: Path,
    active: ActiveGwsMetadata,
    resolved_executable: Path,
    app_version: str,
) -> tuple[object, ...]:
    active_file = component_root / "active.json"
    try:
        active_mtime = active_file.stat().st_mtime_ns
    except OSError:
        active_mtime = -1
    try:
        executable_mtime = resolved_executable.stat().st_mtime_ns
    except OSError:
        executable_mtime = -1
    return (
        str(component_root.resolve()),
        str(bundled.resolve()),
        active_mtime,
        active.version,
        active.executable_sha256,
        active.approval_manifest_sha256,
        app_version,
        str(resolved_executable),
        executable_mtime,
    )


def resolve_gws(
    *,
    bundle_root: Path | None = None,
    component_root: Path | None = None,
    tools_manifest: ToolsManifest | None = None,
    current_app_version: str | None = None,
    run_command: Callable[[Sequence[str]], tuple[int, str]] = process_win.run_captured,
    force_refresh: bool = False,
) -> GwsResolution:
    """승인 갱신본을 먼저 확인하고, 문제가 있으면 동봉본으로 안전하게 돌아간다."""
    if force_refresh:
        _CACHE.clear()
    bundle = Path(bundle_root) if bundle_root is not None else bundle_paths.bundle_root()
    bundled_spec = _required_gws_spec(bundle, tools_manifest)
    bundled = _bundled_executable(bundle)
    try:
        component = Path(component_root) if component_root is not None else component_gws_root()
    except GwsRuntimeError as error:
        # 사용자 계정의 갱신 저장 폴더가 없거나 안전하지 않아도, 설치본 안의
        # 검증된 기본 GWS는 그 폴더와 무관하게 계속 쓸 수 있어야 한다.
        return _fallback_to_bundled(
            bundled,
            bundled_spec,
            error.code,
            run_command,
        )
    active, rejected_version, rejected_reason = _read_active(component)
    if active is None:
        _try_record_bad_version(component, rejected_version, rejected_reason)
        return _fallback_to_bundled(bundled, bundled_spec, rejected_version, run_command)

    app_version = (
        str(current_app_version).strip()
        if current_app_version is not None
        else globals()["current_app_version"]()
    )
    version = active.version
    if not active.login_store_compatible:
        _try_record_bad_version(component, version, "login-store-incompatible")
        return _fallback_to_bundled(bundled, bundled_spec, version, run_command)
    if not _version_in_range(app_version, active.app_min_version, active.app_max_version):
        return _fallback_to_bundled(bundled, bundled_spec, version, run_command)
    if _compare_versions(version, bundled_spec.version) <= 0:
        return _fallback_to_bundled(bundled, bundled_spec, "", run_command)

    bad_versions = _read_bad_versions(component)
    if version in bad_versions:
        return _fallback_to_bundled(bundled, bundled_spec, version, run_command)

    executable, candidate_error = _resolved_update_executable(component, version)
    if executable is None:
        _try_record_bad_version(component, version, candidate_error)
        return _fallback_to_bundled(bundled, bundled_spec, version, run_command)

    approval_error = _read_and_check_approval(executable, active)
    if approval_error:
        _try_record_bad_version(component, version, approval_error)
        return _fallback_to_bundled(bundled, bundled_spec, version, run_command)

    try:
        executable_sha256 = _sha256(executable)
    except OSError:
        _try_record_bad_version(component, version, "executable-read-failed")
        return _fallback_to_bundled(bundled, bundled_spec, version, run_command)
    if executable_sha256 != active.executable_sha256:
        _try_record_bad_version(component, version, "hash-mismatch")
        return _fallback_to_bundled(bundled, bundled_spec, version, run_command)

    key = _cache_key(component, bundled, active, executable, app_version)
    if key in _CACHE:
        return _CACHE[key]

    healthy, version_or_reason = _check_command(executable, version, run_command)
    if healthy:
        result = GwsResolution(executable, version, "approved-update")
        _CACHE[key] = result
        return result
    reason = version_or_reason

    _try_record_bad_version(component, version, reason)
    return _fallback_to_bundled(bundled, bundled_spec, version, run_command)


def resolve_gws_executable(**kwargs) -> str:
    return str(resolve_gws(**kwargs).executable)
