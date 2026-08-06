"""AI 연결을 눌렀을 때만 Teacher Manager 전용 Node를 안전하게 준비한다.

시스템 Node, npm, npx, winget과 PATH는 후보로 보지 않는다. 내려받은 ZIP은
공식 고정 주소와 SHA-256을 확인하고, 임시 폴더에서 전체 경로로 실행해 본 뒤
현재 판 기록을 마지막에 바꾼다.
"""
from __future__ import annotations

import errno
import hashlib
import io
import json
import ntpath
import os
import re
import shutil
import socket
import ssl
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import component_lock, deadline_io, process_win, tool_runtime
from .tool_manifest import ManagedNodeSpec


_TOOL_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
_MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
_MAX_MEMBERS = 50_000


@dataclass(frozen=True)
class NodePreparationResult:
    success: bool
    code: str
    detail: str
    runtime: tool_runtime.NodeRuntime


class _PrepareError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _missing_runtime(code="NODE_NOT_INSTALLED", detail="Teacher Manager 전용 Node가 아직 준비되지 않았습니다."):
    return tool_runtime.NodeRuntime(False, code, detail, "", None, None, None, None)


def _result(success: bool, code: str, detail: str, runtime=None) -> NodePreparationResult:
    return NodePreparationResult(bool(success), str(code), str(detail), runtime or _missing_runtime(code, detail))


def _required_spec(manifest: ManagedNodeSpec | None) -> ManagedNodeSpec:
    spec, error = tool_runtime._required_node_spec(manifest)  # 같은 묶음의 엄격한 manifest 판정 재사용
    if spec is None:
        raise _PrepareError(
            error or "NODE_MANIFEST_MISSING",
            "Teacher Manager 전용 Node 설치 목록을 안전하게 읽지 못했습니다.",
        )
    return spec


def _validate_download_url(spec: ManagedNodeSpec) -> None:
    try:
        parsed = urlparse(spec.archive_url)
        port = parsed.port
    except ValueError as error:
        raise _PrepareError("NODE_DOWNLOAD_URL_INVALID", "Node 공식 다운로드 주소가 올바르지 않습니다.") from error
    expected_name = f"node-v{spec.version}-win-x64.zip"
    expected_path = f"/dist/v{spec.version}/{expected_name}"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "nodejs.org"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
        or spec.archive_filename != expected_name
    ):
        raise _PrepareError("NODE_DOWNLOAD_URL_INVALID", "Node 공식 고정 다운로드 주소가 아닙니다.")


def _validate_final_url(value: str, spec: ManagedNodeSpec) -> None:
    try:
        parsed = urlparse(str(value or ""))
        port = parsed.port
    except ValueError as error:
        raise _PrepareError("NODE_DOWNLOAD_REDIRECT_UNSAFE", "Node 다운로드가 공식 주소에서 끝나지 않았습니다.") from error
    expected_path = f"/dist/v{spec.version}/{spec.archive_filename}"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "nodejs.org"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
    ):
        raise _PrepareError("NODE_DOWNLOAD_REDIRECT_UNSAFE", "Node 다운로드가 nodejs.org 공식 주소에서 끝나지 않았습니다.")


def _network_error(error: BaseException) -> _PrepareError:
    if isinstance(error, HTTPError):
        if error.code == 407:
            return _PrepareError(
                "NETWORK_PROXY_AUTH_REQUIRED",
                "학교나 기관의 인터넷 중계 서버 로그인이 필요합니다. 인터넷 담당자에게 확인한 뒤 다시 눌러 주세요.",
            )
        if error.code == 404:
            return _PrepareError(
                "NODE_DOWNLOAD_NOT_FOUND",
                "Node 공식 서버에서 승인된 설치 파일을 찾지 못했습니다. 잠시 뒤 다시 눌러 주세요.",
            )
        return _PrepareError(
            "NODE_DOWNLOAD_SERVER_UNAVAILABLE",
            "Node 공식 서버가 응답했지만 지금 설치 파일을 받지 못했습니다. 잠시 뒤 다시 눌러 주세요.",
        )
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (ssl.SSLCertVerificationError, ssl.SSLError)):
        return _PrepareError(
            "NETWORK_TLS_INSPECTION_BLOCKED",
            "학교 보안 인증서 때문에 Node 공식 파일을 확인하지 못했습니다. 인터넷 담당자에게 nodejs.org 허용을 요청해 주세요.",
        )
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return _PrepareError("NETWORK_TIMEOUT", "Node 다운로드 시간이 너무 오래 걸렸습니다. 인터넷을 확인하고 다시 눌러 주세요.")
    return _PrepareError("NETWORK_OFFLINE", "Node 공식 파일에 연결하지 못했습니다. 인터넷을 확인하고 다시 눌러 주세요.")


def _file_error(error: OSError) -> _PrepareError:
    if getattr(error, "errno", None) == errno.ENOSPC or getattr(error, "winerror", None) == 112:
        return _PrepareError("COMPONENT_DISK_FULL", "저장 공간이 부족해 AI 연결 도구를 준비하지 못했습니다.")
    if getattr(error, "winerror", None) in {32, 33}:
        return _PrepareError("COMPONENT_FILE_LOCKED", "다른 프로그램이 AI 연결 도구 파일을 사용 중입니다. 잠시 뒤 다시 눌러 주세요.")
    if isinstance(error, PermissionError) or getattr(error, "errno", None) in {errno.EACCES, errno.EROFS, errno.EPERM}:
        return _PrepareError("COMPONENT_DIR_NOT_WRITABLE", "이 Windows 계정의 앱 저장 폴더에 쓸 수 없습니다.")
    return _PrepareError("NODE_PREPARE_FAILED", "AI 연결 도구를 준비하는 중 파일을 다루지 못했습니다.")


def _path_key(path: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path))).casefold()


def _lexists(path: Path) -> bool:
    """깨진 바로가기까지 포함해 그 이름이 이미 쓰이는지 본다."""
    return os.path.lexists(str(path))


def _is_reparse_entry(path: Path, entry_stat=None) -> bool:
    """Windows junction·symbolic link 같은 다른 위치 연결을 찾는다."""
    value = entry_stat if entry_stat is not None else os.lstat(path)
    if stat.S_ISLNK(value.st_mode):
        return True
    attributes = int(getattr(value, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    if reparse_flag and attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _unsafe_component_path() -> _PrepareError:
    return _PrepareError("NODE_COMPONENT_PATH_UNSAFE", "AI 연결 도구 폴더가 다른 위치를 가리킵니다.")


def _require_real_directory(path: Path, *, create: bool) -> None:
    """바로가기·junction을 따라 앱 전용 폴더 밖에 쓰지 않게 한다."""
    try:
        if _lexists(path):
            entry_stat = os.lstat(path)
            if (
                _is_reparse_entry(path, entry_stat)
                or not stat.S_ISDIR(entry_stat.st_mode)
                or _path_key(path.resolve(strict=True)) != _path_key(path.absolute())
            ):
                raise _unsafe_component_path()
            return
        if not create:
            raise _unsafe_component_path()
        path.mkdir(exist_ok=False)
        entry_stat = os.lstat(path)
        if (
            _is_reparse_entry(path, entry_stat)
            or not stat.S_ISDIR(entry_stat.st_mode)
            or _path_key(path.resolve(strict=True)) != _path_key(path.absolute())
        ):
            raise _unsafe_component_path()
    except _PrepareError:
        raise
    except OSError as error:
        raise _file_error(error) from error


def _require_safe_active_file(path: Path) -> None:
    """현재 판 기록은 일반 파일 하나여야 하며, 외부 파일과 hardlink면 거부한다."""
    try:
        if not _lexists(path):
            return
        entry_stat = os.lstat(path)
        if (
            _is_reparse_entry(path, entry_stat)
            or not stat.S_ISREG(entry_stat.st_mode)
            or int(getattr(entry_stat, "st_nlink", 1)) != 1
            or _path_key(path.resolve(strict=True)) != _path_key(path.absolute())
        ):
            raise _unsafe_component_path()
    except _PrepareError:
        raise
    except OSError as error:
        raise _file_error(error) from error


def _require_optional_real_directory(path: Path) -> None:
    if _lexists(path):
        _require_real_directory(path, create=False)


def _validate_activation_paths(component: Path, spec: ManagedNodeSpec) -> None:
    """내려받기 전과 교체 직전에 쓰거나 지울 자리를 다시 확인한다."""
    _require_real_directory(component, create=False)
    versions = component / "versions"
    _require_real_directory(versions, create=False)
    _require_safe_active_file(component / "active.json")
    _require_optional_real_directory(versions / f"v{spec.version}")


def _remove_real_tree(path: Path) -> None:
    """실제 앱 폴더임을 확인한 디렉터리만 지운다."""
    if not _lexists(path):
        return
    _require_real_directory(path, create=False)
    delete_path: str | Path = path
    if os.name == "nt":
        # Node의 npm 폴더는 파일 이름이 깊고 길다. 일반 경로로 지우면 Windows의
        # 260글자 제한에서 일부 파일만 남을 수 있으므로, 확인이 끝난 같은 절대
        # 경로에 Windows 긴 경로 표식만 붙여 지운다.
        absolute = str(path.absolute())
        delete_path = (
            "\\\\?\\UNC\\" + absolute[2:]
            if absolute.startswith("\\\\")
            else "\\\\?\\" + absolute
        )
    shutil.rmtree(delete_path)


def _remove_owned_file_if_safe(path: Path) -> None:
    """임시 파일 이름이 연결 파일로 바뀌었으면 건드리지 않고 남긴다."""
    try:
        if not _lexists(path):
            return
        entry_stat = os.lstat(path)
        if (
            _is_reparse_entry(path, entry_stat)
            or not stat.S_ISREG(entry_stat.st_mode)
            or int(getattr(entry_stat, "st_nlink", 1)) != 1
            or _path_key(path.resolve(strict=True)) != _path_key(path.absolute())
        ):
            return
        path.unlink()
    except OSError:
        return


def _ensure_component_dirs(component: Path) -> None:
    base = component.parents[3]
    if not base.is_absolute():
        raise _PrepareError("NODE_COMPONENT_PATH_UNSAFE", "Windows 앱 저장 폴더가 올바르지 않습니다.")
    # 아직 없는 LOCALAPPDATA 시험 폴더도 만들 수 있지만, 이미 있는 부모가 다른
    # 위치를 가리키면 그 아래에는 한 글자도 쓰지 않는다.
    _require_real_directory(base.parent, create=False)
    current = base.parent
    for part in component.relative_to(base.parent).parts:
        current = current / part
        _require_real_directory(current, create=True)
    for relative in (("temp",), ("cache",), ("cache", "npm"), ("locks",), ("versions",)):
        current = component
        for part in relative:
            current = current / part
            _require_real_directory(current, create=True)


def _download(spec: ManagedNodeSpec, partial: Path, opener, timeout_seconds: float) -> None:
    request = Request(spec.archive_url, headers={"User-Agent": "TeacherManager-Node-Prepare/1"})
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    try:
        response = opener(request, timeout=float(timeout_seconds))
        with response:
            _validate_final_url(response.geturl(), spec)
            digest = hashlib.sha256()
            total = 0
            with partial.open("xb") as handle:
                while True:
                    block = deadline_io.read_before(response, 1024 * 1024, deadline)
                    if not block:
                        break
                    total += len(block)
                    if total > _MAX_ARCHIVE_BYTES:
                        raise _PrepareError("NODE_ARCHIVE_TOO_LARGE", "Node 압축 파일이 허용 크기를 넘었습니다.")
                    handle.write(block)
                    digest.update(block)
                handle.flush()
                os.fsync(handle.fileno())
    except _PrepareError:
        raise
    except HTTPError as error:
        mapped = _network_error(error)
        try:
            error.close()
        except Exception:
            pass
        raise mapped from error
    except (URLError, TimeoutError, socket.timeout) as error:
        raise _network_error(error) from error
    except OSError as error:
        raise _file_error(error) from error
    if digest.hexdigest() != spec.archive_sha256:
        raise _PrepareError("NODE_ARCHIVE_HASH_MISMATCH", "내려받은 Node 파일이 공식 확인값과 다릅니다.")


def _safe_members(bundle: zipfile.ZipFile, spec: ManagedNodeSpec) -> list[tuple[zipfile.ZipInfo, tuple[str, ...]]]:
    expected_root = f"node-v{spec.version}-win-x64"
    infos = bundle.infolist()
    if not infos or len(infos) > _MAX_MEMBERS:
        raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일의 항목 수가 올바르지 않습니다.")
    seen: set[str] = set()
    members: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    total = 0
    required = {"node.exe", "npm.cmd", "npx.cmd"}
    found: set[str] = set()
    for info in infos:
        raw = str(info.filename or "")
        if not raw or "\x00" in raw or "\\" in raw or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
            raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일에 Windows 폴더 밖 경로가 있습니다.")
        path = PurePosixPath(raw)
        parts = tuple(part for part in path.parts if part not in ("", "."))
        if not parts or ".." in parts or parts[0] != expected_root:
            raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일의 최상위 폴더가 올바르지 않습니다.")
        if len(parts) == 1 and not info.is_dir():
            raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일의 최상위 폴더가 올바르지 않습니다.")
        key = "/".join(parts).casefold()
        if key in seen:
            raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일에 이름이 겹치는 항목이 있습니다.")
        seen.add(key)
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일에 바로가기나 특수 파일이 있습니다.")
        if info.file_size < 0 or info.file_size > _MAX_EXPANDED_BYTES:
            raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일의 항목 크기가 올바르지 않습니다.")
        total += info.file_size
        if total > _MAX_EXPANDED_BYTES:
            raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축을 풀었을 때 크기가 너무 큽니다.")
        relative = parts[1:]
        if len(relative) == 1 and relative[0].casefold() in required:
            found.add(relative[0].casefold())
        members.append((info, relative))
    if found != required:
        raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node, npm 또는 npx 파일이 압축 안에 없습니다.")
    return members


def _extract(partial: Path, stage_root: Path, spec: ManagedNodeSpec) -> None:
    try:
        with zipfile.ZipFile(partial, "r") as bundle:
            members = _safe_members(bundle, spec)
            stage_root.mkdir(parents=True, exist_ok=False)
            for info, relative in members:
                if not relative:
                    continue
                destination = stage_root.joinpath(*relative)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info, "r") as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
    except _PrepareError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node 압축 파일을 안전하게 열지 못했습니다.") from error
    except OSError as error:
        raise _file_error(error) from error


def _stage_env(stage_root: Path, component: Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    child = {str(key): str(value) for key, value in source.items()}
    old_paths = []
    for key in tuple(child):
        if key.upper() == "PATH":
            old_paths.append(child.pop(key))
    entries = [str(stage_root)]
    managed_key = ntpath.normcase(ntpath.normpath(str(stage_root))).casefold()
    for value in old_paths:
        for raw in value.split(os.pathsep):
            if raw and ntpath.normcase(ntpath.normpath(raw)).casefold() != managed_key:
                entries.append(raw)
    child["PATH"] = os.pathsep.join(entries)
    child["npm_config_cache"] = str(component / "cache" / "npm")
    child["TEMP"] = str(component / "temp")
    child["TMP"] = str(component / "temp")
    child["npm_config_update_notifier"] = "false"
    child["npm_config_fund"] = "false"
    child["npm_config_audit"] = "false"
    return child


def _run(run_command, args: Sequence[str], *, env: Mapping[str, str], timeout: float) -> tuple[int, str]:
    return run_command(list(args), env=dict(env), timeout=timeout)


def _smoke(stage_root: Path, component: Path, spec: ManagedNodeSpec, run_command, timeout: float) -> None:
    node = stage_root / "node.exe"
    npm = stage_root / "npm.cmd"
    npx = stage_root / "npx.cmd"
    if not all(path.is_file() for path in (node, npm, npx)):
        raise _PrepareError("NODE_ARCHIVE_UNSAFE", "Node, npm 또는 npx 파일이 빠져 있습니다.")
    environment = _stage_env(stage_root, component)
    commands = (
        ([str(node), "--version"], f"v{spec.version}", "node"),
        ([str(npm), "--version"], None, "npm"),
        ([str(npx), "--version"], None, "npx"),
    )
    versions = []
    for args, exact, name in commands:
        code, output = _run(run_command, args, env=environment, timeout=timeout)
        reported = str(output or "").strip()
        if code == 127 or "blocked" in reported.casefold() or "access is denied" in reported.casefold():
            raise _PrepareError("NODE_SECURITY_BLOCKED", f"보안 프로그램이 Teacher Manager 전용 {name} 실행을 막았습니다.")
        if code != 0:
            raise _PrepareError("NODE_SMOKE_FAILED", f"Teacher Manager 전용 {name} 실행 확인에 실패했습니다.")
        if exact is not None and reported != exact:
            raise _PrepareError("NODE_VERSION_MISMATCH", "Node 판 번호가 승인된 판과 다릅니다.")
        if name in {"npm", "npx"}:
            if not _TOOL_VERSION.fullmatch(reported):
                raise _PrepareError("NODE_TOOL_VERSION_INVALID", f"{name} 판 번호를 확인하지 못했습니다.")
            versions.append(reported)
    if len(versions) != 2 or versions[0] != versions[1]:
        raise _PrepareError("NODE_TOOL_VERSION_INVALID", "npm과 npx 판 번호가 서로 다릅니다.")


def _atomic_bytes(path: Path, data: bytes | None) -> None:
    if data is None:
        _require_safe_active_file(path)
        if _lexists(path):
            path.unlink()
        return
    _require_safe_active_file(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    if _lexists(temporary):
        raise _unsafe_component_path()
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        _remove_owned_file_if_safe(temporary)


def _activate(stage_root: Path, component: Path, spec: ManagedNodeSpec, run_command) -> tool_runtime.NodeRuntime:
    versions = component / "versions"
    target = versions / f"v{spec.version}"
    active = component / "active.json"
    _validate_activation_paths(component, spec)
    backup = versions / f".v{spec.version}.backup-{uuid.uuid4().hex}"
    if _lexists(backup):
        raise _unsafe_component_path()
    old_active = active.read_bytes() if _lexists(active) else None
    backed_up = False
    placed = False
    try:
        _validate_activation_paths(component, spec)
        if _lexists(backup):
            raise _unsafe_component_path()
        if _lexists(target):
            _require_real_directory(target, create=False)
            os.rename(target, backup)
            backed_up = True
        _require_real_directory(versions, create=False)
        _require_real_directory(stage_root, create=False)
        if _lexists(target):
            raise _unsafe_component_path()
        if backed_up:
            _require_real_directory(backup, create=False)
        os.rename(stage_root, target)
        placed = True
        _require_real_directory(target, create=False)
        _require_safe_active_file(active)
        _atomic_bytes(
            active,
            (
                json.dumps(
                    {"schema_version": 1, "version": spec.version},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
        )
        runtime = tool_runtime.resolve_node(
            manifest=spec, local_app_data=component.parents[3], run_command=run_command,
        )
        if not runtime.ready:
            raise _PrepareError(runtime.code, runtime.detail)
        if backed_up:
            _remove_real_tree(backup)
            backed_up = False
        return runtime
    except BaseException as original_error:
        # 한 복구가 막혀도 나머지는 반드시 각각 시도한다. 예를 들어 새 target이
        # 잠겨 삭제되지 않더라도 active.json만큼은 이전 값으로 되돌려, 다음 실행이
        # 절반만 바뀐 Node를 현재 판으로 잘못 믿지 않게 한다.
        rollback_errors: list[BaseException] = []
        if placed:
            try:
                _remove_real_tree(target)
                placed = False
            except Exception as error:  # noqa: BLE001 - 다음 복구를 계속하기 위해 모은다
                rollback_errors.append(error)
        if backed_up:
            try:
                _require_real_directory(backup, create=False)
                if _lexists(target):
                    raise _unsafe_component_path()
                os.rename(backup, target)
                backed_up = False
            except Exception as error:  # noqa: BLE001 - active 기록 복구는 계속한다
                rollback_errors.append(error)
        try:
            _atomic_bytes(active, old_active)
        except Exception as error:  # noqa: BLE001 - 복구 미완료를 숨기지 않고 아래에서 알린다
            rollback_errors.append(error)
        if rollback_errors:
            raise _PrepareError(
                "NODE_ROLLBACK_INCOMPLETE",
                "Node 교체를 되돌리는 중 일부 파일이 잠겨 완전히 복구하지 못했습니다. "
                "Teacher Manager를 다시 실행하지 말고 컴퓨터를 다시 시작한 뒤 설치를 다시 눌러 주세요.",
            ) from original_error
        raise


def prepare_managed_node(
    *,
    manifest: ManagedNodeSpec | None = None,
    local_app_data: str | Path | None = None,
    opener: Callable = urlopen,
    timeout_seconds: float = 60.0,
    run_command: Callable = process_win.run_captured,
) -> NodePreparationResult:
    """공식 ZIP을 내려받아 AI 연결 전용 Node를 준비한다."""
    prior = _missing_runtime()
    partial = None
    stage_parent = None
    try:
        spec = _required_spec(manifest)
        _validate_download_url(spec)
        component = tool_runtime.component_node_root(local_app_data=local_app_data)
        if component is None:
            raise _PrepareError("NODE_COMPONENT_DIR_UNAVAILABLE", "Windows 앱 저장 폴더를 찾지 못했습니다.")
        lock_timeout = min(10.0, max(0.05, float(timeout_seconds)))
        try:
            with component_lock.exclusive_lifecycle_mutex(timeout=lock_timeout):
                _ensure_component_dirs(component)
                _validate_activation_paths(component, spec)
                with component_lock.exclusive_file_lock(component / "locks" / "install.lock", timeout=lock_timeout):
                    _ensure_component_dirs(component)
                    _validate_activation_paths(component, spec)
                    current = tool_runtime.resolve_node(
                        manifest=spec, local_app_data=local_app_data, run_command=run_command,
                    )
                    prior = current
                    if current.ready:
                        return _result(True, "NODE_READY", current.detail, current)
                    # Node ZIP의 npm 안에는 폴더 이름이 깊게 겹친 파일이 있다. 임시
                    # 이름까지 길면 오래된 Windows 260글자 제한을 넘으므로 짧고
                    # 충돌하지 않는 이름만 쓴다.
                    token = f"{os.getpid():x}{uuid.uuid4().hex[:8]}"
                    partial = component / "temp" / f"d-{token}.partial"
                    stage_parent = component / "temp" / f"s-{token}"
                    stage_root = stage_parent
                    _download(spec, partial, opener, timeout_seconds)
                    _extract(partial, stage_root, spec)
                    _smoke(stage_root, component, spec, run_command, timeout_seconds)
                    _validate_activation_paths(component, spec)
                    runtime = _activate(stage_root, component, spec, run_command)
                    return _result(True, "NODE_READY", runtime.detail, runtime)
        except TimeoutError as error:
            raise _PrepareError("COMPONENT_UPDATE_BUSY", "설치 또는 다른 도구 준비가 진행 중입니다. 잠시 뒤 다시 눌러 주세요.") from error
        except OSError as error:
            raise _file_error(error) from error
    except _PrepareError as error:
        return _result(False, error.code, error.detail, prior)
    except OSError as error:
        mapped = _file_error(error)
        return _result(False, mapped.code, mapped.detail, prior)
    except Exception:
        return _result(False, "NODE_PREPARE_FAILED", "AI 연결 도구를 준비하지 못했습니다. 잠시 뒤 다시 눌러 주세요.", prior)
    finally:
        if partial is not None:
            _remove_owned_file_if_safe(partial)
        if stage_parent is not None:
            try:
                _remove_real_tree(stage_parent)
            except (OSError, _PrepareError):
                pass
