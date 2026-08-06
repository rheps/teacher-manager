"""AI 스킬 설치 명령이 실제 사용자 폴더를 건드리지 않게 격리한다."""
from __future__ import annotations

import os
import json
import re
import shutil
import stat
import tempfile
import time
import uuid
import hashlib
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from brity_bridge import component_lock, deadline_io
from brity_bridge.process_supervision import run_supervised_command


STAGE_PREFIX = "TeacherManager-AiSkill-"
STAGE_MARKER = ".teacher-manager-owned-stage"
AI_SKILL_INSTALL_MUTEX_NAME = "TeacherManagerAiSkillInstall-93480A78-D609-47A4-B1EE-B30A64BB51B7"
APPROVED_ARCHIVE_MAX_BYTES = 128 * 1024 * 1024
APPROVED_SKILL_MAX_BYTES = 256 * 1024 * 1024
PUBLIC_SKILL_REQUIRED_FILES = (
    "SKILL.md",
    "release.json",
    "agents/openai.yaml",
    "references/1_quick_check.md",
    "references/2_calendar_selection.md",
    "references/3_time_analysis.md",
    "references/4_execution_guide.md",
    "references/99_exception_handling.md",
    "assets/Code.gs",
    "assets/appsscript.json",
    "assets/attendance-workbook.xlsx",
    "assets/absence-report-template.docx",
    "assets/teacher-manager.ico",
    "templates/README-setup.txt",
    "templates/teacher-profile.csv",
    "scripts/brity_bridge/__init__.py",
    "scripts/dashboard/__init__.py",
    "scripts/dashboard/web/index.html",
    "scripts/dashboard/web/app.js",
    "scripts/dashboard/web/app.css",
    "시작이 안될 때.bat",
    "시작하기.vbs",
)
_TEXT_SUFFIXES = {
    ".bat", ".cjs", ".css", ".csv", ".gs", ".html", ".js", ".json",
    ".md", ".mjs", ".ps1", ".py", ".toml", ".ts", ".txt", ".vbs",
    ".xml", ".yaml", ".yml",
}
_JSON_SECRET = re.compile(
    r'''(?i)["'](?:client[_-]?secret|refresh[_-]?token|access[_-]?token)["']\s*:\s*["']([^"']+)["']'''
)
_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")
_REAL_USER_PATH = re.compile(r"(?i)\b[A-Z]:\\Users\\(?!<|Public(?:\\|\b)|WDAGUtilityAccount(?:\\|\b))[^\\\s`\"']+")
_LOCK_HASH = re.compile(r"^[0-9a-f]{40}$")
_FILE_HASH = re.compile(r"^[0-9a-f]{64}$")
_LOCK_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$")


class AiSkillInstallError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ApprovedSkillSource:
    """사람이 검토한 공개 스킬 한 판의 정확한 주소와 모든 파일 지문."""

    repository: str
    commit: str
    archive_sha256: str
    skill_folder_hash: str
    files: Mapping[str, str]


def _without_duplicate_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _safe_approved_relative_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ValueError("unsafe file path")
    value = PurePosixPath(raw)
    if value.is_absolute() or value.as_posix() != raw or any(part in {"", ".", ".."} for part in value.parts):
        raise ValueError("unsafe file path")
    lowered_name = value.name.casefold()
    if lowered_name == "dev-fresh-start.flag" or (
        lowered_name.startswith("prototype-") and lowered_name.endswith(".html")
    ):
        raise ValueError("development file")
    return raw


def validate_approved_skill(value: object) -> ApprovedSkillSource:
    """승인 자료 자체가 정확한 공개 저장소·commit·전체 파일 지문인지 확인한다."""
    try:
        if isinstance(value, ApprovedSkillSource):
            repository = value.repository
            commit = value.commit
            archive_hash = value.archive_sha256
            folder_hash = value.skill_folder_hash
            raw_files = value.files
        elif isinstance(value, Mapping):
            if set(value) != {
                "schema_version", "repository", "commit", "archive_sha256",
                "skill_folder_hash", "files",
            } or value.get("schema_version") != 1:
                raise ValueError("approval keys")
            repository = value["repository"]
            commit = value["commit"]
            archive_hash = value["archive_sha256"]
            folder_hash = value["skill_folder_hash"]
            raw_files = value["files"]
        else:
            raise ValueError("approval type")
        if repository != "rheps/teacher-manager":
            raise ValueError("repository")
        if not isinstance(commit, str) or not _LOCK_HASH.fullmatch(commit):
            raise ValueError("commit")
        if not isinstance(archive_hash, str) or not _FILE_HASH.fullmatch(archive_hash):
            raise ValueError("archive hash")
        if not isinstance(folder_hash, str) or not _LOCK_HASH.fullmatch(folder_hash):
            raise ValueError("folder hash")
        if not isinstance(raw_files, Mapping) or not raw_files:
            raise ValueError("files")
        files: dict[str, str] = {}
        casefolded: set[str] = set()
        for raw_path, raw_hash in raw_files.items():
            relative = _safe_approved_relative_path(raw_path)
            folded = relative.casefold()
            if folded in casefolded:
                raise ValueError("case-colliding file")
            if not isinstance(raw_hash, str) or not _FILE_HASH.fullmatch(raw_hash):
                raise ValueError("file hash")
            casefolded.add(folded)
            files[relative] = raw_hash
        if not set(PUBLIC_SKILL_REQUIRED_FILES).issubset(files):
            raise ValueError("required file missing")
        return ApprovedSkillSource(repository, commit, archive_hash, folder_hash, files)
    except (KeyError, TypeError, ValueError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_APPROVAL_INVALID",
            "검토한 AI 공개판의 파일 목록을 안전하게 확인하지 못했습니다. AI 연결은 시작하지 않았습니다.",
        ) from error


def load_approved_skill(path: Path) -> ApprovedSkillSource:
    """설치본과 함께 승인된 공개 commit·파일 지문을 읽는다. 없으면 닫힌다."""
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise AiSkillInstallError(
            "AI_SKILLS_APPROVAL_REQUIRED",
            "AI 공개판 동기화와 안전 확인이 끝나지 않아 연결 기능을 준비 중입니다.",
        ) from error
    try:
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("approval too large")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_without_duplicate_json_pairs,
        )
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_APPROVAL_INVALID",
            "검토한 AI 공개판의 파일 목록을 안전하게 확인하지 못했습니다. AI 연결은 시작하지 않았습니다.",
        ) from error
    return validate_approved_skill(payload)


@dataclass(frozen=True)
class _PathSnapshot:
    path: Path
    existed: bool
    digest: str


@dataclass(frozen=True)
class InstallPlan:
    destinations: tuple[Path, ...]
    lock_path: Path
    target_snapshots: tuple[_PathSnapshot, ...]
    lock_snapshot: _PathSnapshot
    current_lock: dict


@dataclass
class _TargetApplyState:
    destination: Path
    backup: Path
    had_old: bool
    old_moved: bool = False
    new_placed: bool = False


def exclusive_install_lock(*, timeout: float = 15.0):
    return component_lock.exclusive_lifecycle_mutex(
        AI_SKILL_INSTALL_MUTEX_NAME,
        timeout=timeout,
    )


def make_stage_root(runtime_root: Path) -> Path:
    """관리형 Node와 같은 앱 전용 폴더에 이번 실행만의 폴더를 만든다."""
    try:
        runtime_root = Path(runtime_root).resolve(strict=True)
        app_root = runtime_root.parents[3]
        stages = component_lock.prepare_direct_directory(
            app_root / "ai-skill-install" / "stages"
        )
        stage = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=stages))
        component_lock.prepare_direct_directory(stage)
        marker = component_lock.prepare_direct_file_path(stage / STAGE_MARKER)
        with marker.open("xb") as handle:
            handle.write(stage.name.encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        return stage
    except (OSError, IndexError, component_lock.UnsafeLockPathError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_STAGE_UNSAFE",
            "AI 연결 임시 폴더를 안전하게 준비하지 못해 실제 사용자 파일은 바꾸지 않았습니다.",
        ) from error


def resolve_managed_npx(runtime_root: Path, npx_cmd: Path) -> Path:
    """환경을 격리하기 전에 관리형 root 바로 아래의 단일 npx 파일을 확정한다."""
    try:
        root = Path(os.path.abspath(str(runtime_root)))
        candidate = Path(os.path.abspath(str(npx_cmd)))
        root_info = root.lstat()
        info = candidate.lstat()
        if (
            _is_reparse(root_info)
            or not stat.S_ISDIR(root_info.st_mode)
            or root.resolve(strict=True) != root
            or candidate != root / "npx.cmd"
            or _is_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or int(getattr(info, "st_nlink", 1)) != 1
            or candidate.resolve(strict=True) != candidate
        ):
            raise OSError("unsafe managed npx")
        return candidate
    except OSError as error:
        raise AiSkillInstallError(
            "AI_NODE_NOT_READY",
            "Teacher Manager 전용 npx 파일을 안전하게 확인하지 못했습니다.",
        ) from error


def isolated_environment(
    base: Mapping[str, str], stage_root: Path
) -> dict[str, str]:
    """HOME·설정·cache·임시 파일을 모두 이번 격리 폴더 아래로 돌린다."""
    child = {str(key): str(value) for key, value in base.items()}
    stage_root = Path(stage_root)
    home = stage_root / "home"
    temporary = stage_root / "temp"
    for path in (
        home,
        temporary,
        home / ".claude",
        home / ".codex",
        home / ".state",
        home / ".config",
        home / ".cache",
        home / ".data",
        home / "AppData" / "Roaming",
        home / "AppData" / "Local",
        stage_root / "npm-cache",
        home / ".gh",
    ):
        path.mkdir(parents=True, exist_ok=True)
    drive, tail = os.path.splitdrive(str(home))
    replacements = {
        "USERPROFILE": str(home),
        "HOME": str(home),
        "HOMEDRIVE": drive,
        "HOMEPATH": tail or str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CODEX_HOME": str(home / ".codex"),
        "XDG_STATE_HOME": str(home / ".state"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".data"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "npm_config_cache": str(stage_root / "npm-cache"),
        "npm_config_userconfig": str(stage_root / "npmrc"),
        "GH_CONFIG_DIR": str(home / ".gh"),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
    }
    removed = {"GITHUB_TOKEN", "GH_TOKEN", *replacements}
    for key in tuple(child):
        if key.upper() in {name.upper() for name in removed}:
            child.pop(key, None)
    child.update(replacements)
    (stage_root / "npmrc").write_text("", encoding="utf-8")
    (home / ".gitconfig").write_text("", encoding="utf-8")
    return child


def approved_archive_url(approval: ApprovedSkillSource) -> str:
    approval = validate_approved_skill(approval)
    owner, repository = approval.repository.split("/", 1)
    return f"https://codeload.github.com/{owner}/{repository}/zip/{approval.commit}"


def _download_approved_archive(
    stage_root: Path,
    approval: ApprovedSkillSource,
    *,
    opener=urlopen,
    timeout_seconds: float = 60.0,
) -> Path:
    url = approved_archive_url(approval)
    archive = Path(stage_root) / "approved-public-skill.zip"
    deadline = time.monotonic() + max(0.05, float(timeout_seconds))
    digest = hashlib.sha256()
    written = 0
    response = None
    try:
        _assert_real_tree(Path(stage_root))
        request = Request(url, headers={"User-Agent": "TeacherManager-AI-Skill/1"})
        response = opener(request, timeout=max(0.05, float(timeout_seconds)))
        final_url = str(response.geturl())
        parsed = urlsplit(final_url)
        if (
            parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").casefold() != "codeload.github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or parsed.path != urlsplit(url).path
        ):
            raise AiSkillInstallError(
                "AI_SKILLS_ARCHIVE_REDIRECT_UNSAFE",
                "검토한 GitHub 주소가 아닌 곳으로 연결되어 AI 연결 파일을 받지 않았습니다.",
            )
        if int(getattr(response, "status", 200) or 200) != 200:
            raise OSError("unexpected archive response")
        with archive.open("xb") as handle:
            while True:
                block = deadline_io.read_before(response, 1024 * 1024, deadline)
                if not block:
                    break
                written += len(block)
                if written > APPROVED_ARCHIVE_MAX_BYTES:
                    raise AiSkillInstallError(
                        "AI_SKILLS_ARCHIVE_TOO_LARGE",
                        "검토한 크기보다 AI 연결 파일이 너무 커서 받기를 중단했습니다.",
                    )
                digest.update(block)
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        if not written or digest.hexdigest() != approval.archive_sha256:
            raise AiSkillInstallError(
                "AI_SKILLS_ARCHIVE_SHA256_MISMATCH",
                "받은 AI 연결 압축파일이 검토한 공개판과 달라 사용하지 않았습니다.",
            )
        return archive
    except AiSkillInstallError:
        archive.unlink(missing_ok=True)
        raise
    except deadline_io.TotalDeadlineExpired as error:
        archive.unlink(missing_ok=True)
        raise AiSkillInstallError(
            "NETWORK_TIMEOUT",
            "검토한 AI 연결 파일을 받는 시간이 너무 오래 걸려 중단했습니다.",
        ) from error
    except Exception as error:  # 네트워크 구현마다 예외 모양이 달라 쉬운 한 문장으로 묶는다.
        archive.unlink(missing_ok=True)
        detail = str(error).casefold()
        code = "NETWORK_PROXY_AUTH_REQUIRED" if "407" in detail or "proxy" in detail else "AI_SKILLS_ARCHIVE_DOWNLOAD_FAILED"
        message = (
            "학교나 기관의 인터넷 중계 서버 로그인이 필요합니다."
            if code == "NETWORK_PROXY_AUTH_REQUIRED"
            else "검토한 GitHub AI 연결 파일을 받지 못했습니다. 인터넷 연결을 확인해 주세요."
        )
        raise AiSkillInstallError(code, message) from error
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _zip_parts(raw: str) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ValueError("unsafe zip path")
    path = PurePosixPath(raw.rstrip("/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe zip path")
    return path.parts


def _extract_approved_skill(
    archive_path: Path,
    stage_root: Path,
    approval: ApprovedSkillSource,
) -> Path:
    source = Path(stage_root) / "approved-source" / "teacher-task-manager"
    seen_archive: set[str] = set()
    seen_skill: set[str] = set()
    root_prefixes: set[tuple[str, ...]] = set()
    total = 0
    try:
        source.mkdir(parents=True)
        with zipfile.ZipFile(archive_path, "r") as bundle:
            for item in bundle.infolist():
                parts = _zip_parts(item.filename)
                folded_archive = "/".join(parts).casefold()
                if folded_archive in seen_archive:
                    raise ValueError("duplicate archive path")
                seen_archive.add(folded_archive)
                mode = int(item.external_attr >> 16) & 0xFFFF
                if item.flag_bits & 0x1 or (mode and stat.S_ISLNK(mode)):
                    raise ValueError("encrypted or linked archive entry")
                marker = None
                for index in range(len(parts) - 1):
                    if parts[index:index + 2] == ("skills", "teacher-task-manager"):
                        marker = index
                        break
                if marker is None:
                    continue
                root_prefixes.add(parts[:marker])
                relative_parts = parts[marker + 2:]
                if not relative_parts or item.is_dir():
                    continue
                relative = _safe_approved_relative_path("/".join(relative_parts))
                folded = relative.casefold()
                if folded in seen_skill:
                    raise ValueError("duplicate skill path")
                seen_skill.add(folded)
                if relative not in approval.files:
                    raise ValueError("unapproved skill file")
                if item.file_size < 0 or item.file_size > APPROVED_SKILL_MAX_BYTES:
                    raise ValueError("unsafe file size")
                total += item.file_size
                if total > APPROVED_SKILL_MAX_BYTES:
                    raise ValueError("unsafe total size")
                destination = source / Path(*relative_parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with bundle.open(item, "r") as incoming, destination.open("xb") as outgoing:
                    while True:
                        block = incoming.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > item.file_size or total - item.file_size + size > APPROVED_SKILL_MAX_BYTES:
                            raise ValueError("expanded file too large")
                        digest.update(block)
                        outgoing.write(block)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                if size != item.file_size or digest.hexdigest() != approval.files[relative]:
                    raise ValueError("file digest mismatch")
        if len(root_prefixes) != 1 or seen_skill != {path.casefold() for path in approval.files}:
            raise ValueError("approved skill tree missing")
        _assert_real_tree(source)
        _assert_public_values(source)
        _assert_approved_files(source, approval)
        return source
    except (OSError, ValueError, zipfile.BadZipFile, AiSkillInstallError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_ARCHIVE_UNSAFE",
            "검토한 AI 공개판의 압축 내용이 승인 목록과 달라 기존 파일을 바꾸지 않았습니다.",
        ) from error


def prepare_approved_source(
    stage_root: Path,
    approval: ApprovedSkillSource,
    *,
    opener=urlopen,
    timeout_seconds: float = 60.0,
) -> Path:
    """정확한 GitHub commit 압축을 받고 모든 스킬 파일 지문을 확인한다."""
    approval = validate_approved_skill(approval)
    archive = _download_approved_archive(
        stage_root, approval, opener=opener, timeout_seconds=timeout_seconds,
    )
    return _extract_approved_skill(archive, stage_root, approval)


def write_staged_lock(stage_root: Path, approval: ApprovedSkillSource) -> Path:
    """로컬 경로 설치에는 없는 기록을 승인 자료만으로 격리 폴더에 만든다."""
    approval = validate_approved_skill(approval)
    lock = Path(stage_root) / "home" / ".state" / "skills" / ".skill-lock.json"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(lock):
            info = lock.lstat()
            if _is_reparse(info) or not stat.S_ISREG(info.st_mode) or int(getattr(info, "st_nlink", 1)) != 1:
                raise OSError("unsafe existing lock")
            lock.unlink()
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload = {
            "version": 3,
            "skills": {
                "teacher-task-manager": {
                    "source": approval.repository,
                    "sourceType": "github",
                    "sourceUrl": "https://github.com/rheps/teacher-manager.git",
                    "skillPath": "skills/teacher-task-manager/SKILL.md",
                    "skillFolderHash": approval.skill_folder_hash,
                    "installedAt": now,
                    "updatedAt": now,
                }
            },
            "dismissed": {},
        }
        with lock.open("xb") as handle:
            handle.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return lock
    except OSError as error:
        raise AiSkillInstallError(
            "AI_SKILLS_STAGE_INVALID",
            "검토한 AI 연결 설치 기록을 격리 폴더에 만들지 못했습니다.",
        ) from error


def cleanup_stage(stage_root: Path) -> bool:
    """현재 실행 표식과 같은 실제 폴더일 때만 격리 폴더를 지운다."""
    stage_root = Path(os.path.abspath(str(stage_root)))
    try:
        if not stage_root.name.startswith(STAGE_PREFIX) or not os.path.lexists(stage_root):
            return not os.path.lexists(stage_root)
        root_info = stage_root.lstat()
        if (
            _is_reparse(root_info)
            or not stat.S_ISDIR(root_info.st_mode)
            or stage_root.resolve(strict=True) != stage_root
        ):
            return False
        marker = stage_root / STAGE_MARKER
        marker_info = marker.lstat()
        if (
            _is_reparse(marker_info)
            or not stat.S_ISREG(marker_info.st_mode)
            or int(getattr(marker_info, "st_nlink", 1)) != 1
            or marker.read_text(encoding="ascii") != stage_root.name
        ):
            return False
        # skills@1.5.21은 Claude를 골랐을 때 canonical `.agents` 폴더를 가리키는
        # junction 하나를 만든다. 이 연결을 실제 사용자 폴더로 옮기지는 않으며,
        # 정확히 격리 canonical을 가리킬 때 연결 자체만 먼저 없앤다.
        claude_link = stage_root / "home" / ".claude" / "skills" / "teacher-task-manager"
        if os.path.lexists(claude_link):
            link_info = claude_link.lstat()
            if _is_reparse(link_info):
                canonical = stage_root / "home" / ".agents" / "skills" / "teacher-task-manager"
                if claude_link.resolve(strict=True) != canonical.resolve(strict=True):
                    return False
                if stat.S_ISLNK(link_info.st_mode):
                    claude_link.unlink()
                else:
                    os.rmdir(claude_link)
        for current, directories, files in os.walk(stage_root, followlinks=False):
            current_path = Path(current)
            for name in [*directories, *files]:
                entry = current_path / name
                info = entry.lstat()
                if _is_reparse(info):
                    return False
        shutil.rmtree(stage_root)
        return not os.path.lexists(stage_root)
    except (OSError, UnicodeError):
        return False


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        int(getattr(info, "st_file_attributes", 0) or 0) & 0x400
    )


def _absolute_local_path(value: str | Path, label: str) -> Path:
    raw = str(value or "").strip()
    candidate = Path(raw)
    if not raw or not candidate.is_absolute() or str(candidate.anchor).startswith("\\\\"):
        raise AiSkillInstallError(
            "AI_SKILLS_TARGET_UNSAFE",
            f"{label} 위치가 이 컴퓨터의 절대 폴더가 아니어서 아무것도 바꾸지 않았습니다.",
        )
    return Path(os.path.abspath(raw))


def _assert_safe_directory_chain(path: Path) -> None:
    candidate = Path(os.path.abspath(str(path)))
    existing: list[Path] = []
    cursor = candidate
    while True:
        if os.path.lexists(cursor):
            existing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    if not existing:
        raise AiSkillInstallError(
            "AI_SKILLS_TARGET_UNSAFE",
            "AI 연결 폴더의 실제 위치를 확인하지 못해 아무것도 바꾸지 않았습니다.",
        )
    try:
        for entry in reversed(existing):
            info = entry.lstat()
            if (
                _is_reparse(info)
                or not stat.S_ISDIR(info.st_mode)
                or entry.resolve(strict=True) != entry
            ):
                raise OSError("unsafe parent")
    except OSError as error:
        raise AiSkillInstallError(
            "AI_SKILLS_TARGET_UNSAFE",
            "AI 연결 폴더가 링크나 연결 폴더를 지나서 아무것도 바꾸지 않았습니다.",
        ) from error


def _tree_digest(path: Path) -> str:
    path = Path(os.path.abspath(str(path)))
    if not os.path.lexists(path):
        return ""
    digest = hashlib.sha256()
    try:
        info = path.lstat()
        if _is_reparse(info) or not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path:
            raise OSError("unsafe target")
        for current, directories, files in os.walk(path, followlinks=False):
            directories.sort()
            files.sort()
            current_path = Path(current)
            relative_current = current_path.relative_to(path).as_posix()
            digest.update(b"D\0" + relative_current.encode("utf-8") + b"\0")
            for name in directories:
                entry = current_path / name
                entry_info = entry.lstat()
                if _is_reparse(entry_info) or not stat.S_ISDIR(entry_info.st_mode):
                    raise OSError("unsafe directory")
                if entry.resolve(strict=True) != entry:
                    raise OSError("escaped directory")
            for name in files:
                entry = current_path / name
                entry_info = entry.lstat()
                if (
                    _is_reparse(entry_info)
                    or not stat.S_ISREG(entry_info.st_mode)
                    or int(getattr(entry_info, "st_nlink", 1)) != 1
                    or entry.resolve(strict=True) != entry
                ):
                    raise OSError("unsafe file")
                relative = entry.relative_to(path).as_posix().encode("utf-8")
                digest.update(b"F\0" + relative + b"\0")
                with entry.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
    except OSError as error:
        raise AiSkillInstallError(
            "AI_SKILLS_TARGET_UNSAFE",
            "기존 AI 연결 폴더에 안전하지 않은 연결이 있어 아무것도 바꾸지 않았습니다.",
        ) from error
    return digest.hexdigest()


def _target_snapshot(path: Path) -> _PathSnapshot:
    existed = os.path.lexists(path)
    return _PathSnapshot(path, existed, _tree_digest(path) if existed else "")


def _lock_snapshot(path: Path) -> tuple[_PathSnapshot, dict]:
    if not os.path.lexists(path):
        return _PathSnapshot(path, False, ""), {
            "version": 3, "skills": {}, "dismissed": {},
        }
    try:
        info = path.lstat()
        if (
            _is_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or int(getattr(info, "st_nlink", 1)) != 1
            or path.resolve(strict=True) != path
        ):
            raise OSError("unsafe lock")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 3
            or not isinstance(payload.get("skills"), dict)
            or ("dismissed" in payload and not isinstance(payload["dismissed"], dict))
            or (
                "lastSelectedAgents" in payload
                and not isinstance(payload["lastSelectedAgents"], list)
            )
        ):
            raise ValueError("malformed lock")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_TARGET_UNSAFE",
            "기존 AI 연결 기록을 안전하게 읽지 못해 아무것도 바꾸지 않았습니다.",
        ) from error
    return _PathSnapshot(path, True, hashlib.sha256(raw).hexdigest()), payload


def _snapshot_matches(snapshot: _PathSnapshot) -> bool:
    try:
        exists = os.path.lexists(snapshot.path)
        if exists != snapshot.existed:
            return False
        if not exists:
            return True
        if snapshot.path.is_dir():
            return _tree_digest(snapshot.path) == snapshot.digest
        current, _payload = _lock_snapshot(snapshot.path)
        return current.digest == snapshot.digest
    except (OSError, AiSkillInstallError):
        return False


def prepare_install_plan(
    agents: list[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> InstallPlan:
    """실제 대상은 읽기만 하고, npx가 도는 동안 바뀌는지 비교할 표를 만든다."""
    original = os.environ if environ is None else environ
    home_value = str(original.get("USERPROFILE") or original.get("HOME") or Path.home())
    home = _absolute_local_path(home_value, "사용자 폴더")
    _assert_safe_directory_chain(home)
    destinations: list[Path] = []
    if any(agent != "claude-code" for agent in agents):
        destinations.append(home / ".agents" / "skills" / "teacher-task-manager")
    if "claude-code" in agents:
        claude_value = str(original.get("CLAUDE_CONFIG_DIR") or "").strip()
        claude_root = (
            _absolute_local_path(claude_value, "Claude 설정 폴더")
            if claude_value
            else home / ".claude"
        )
        destinations.append(claude_root / "skills" / "teacher-task-manager")
    destinations = list(dict.fromkeys(destinations))
    for destination in destinations:
        _assert_safe_directory_chain(destination.parent)

    state_value = str(original.get("XDG_STATE_HOME") or "").strip()
    state_root = (
        _absolute_local_path(state_value, "AI 연결 기록 폴더")
        if state_value
        else home / ".agents"
    )
    lock_path = state_root / ("skills/.skill-lock.json" if state_value else ".skill-lock.json")
    _assert_safe_directory_chain(lock_path.parent)
    targets = tuple(_target_snapshot(path) for path in destinations)
    lock_snapshot, current_lock = _lock_snapshot(lock_path)
    return InstallPlan(tuple(destinations), lock_path, targets, lock_snapshot, current_lock)


def _make_directories_tracking(path: Path) -> list[Path]:
    """없는 부모만 만들고 이번 호출이 만든 폴더를 얕은 순서로 돌려준다."""
    path = Path(os.path.abspath(str(path)))
    _assert_safe_directory_chain(path)
    missing: list[Path] = []
    cursor = path
    while not os.path.lexists(cursor):
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    created: list[Path] = []
    try:
        for directory in reversed(missing):
            directory.mkdir()
            created.append(directory)
            _assert_safe_directory_chain(directory)
        return created
    except BaseException:
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _assert_real_tree(root: Path) -> None:
    expected = Path(os.path.abspath(str(root)))
    try:
        if expected.resolve(strict=True) != expected:
            raise OSError("path escaped")
        for current, directories, files in os.walk(expected, followlinks=False):
            current_path = Path(current)
            entries = [current_path, *(current_path / name for name in directories), *(current_path / name for name in files)]
            for entry in entries:
                info = entry.lstat()
                if _is_reparse(info) or entry.resolve(strict=True) != entry:
                    raise OSError("reparse entry")
                if stat.S_ISREG(info.st_mode) and int(getattr(info, "st_nlink", 1)) != 1:
                    raise OSError("hardlinked file")
                if entry != expected and not (
                    stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                ):
                    raise OSError("special entry")
    except OSError as error:
        raise AiSkillInstallError(
            "AI_SKILLS_STAGE_UNSAFE",
            "AI 연결 파일에 안전하지 않은 연결이 있어 기존 파일을 바꾸지 않았습니다.",
        ) from error


def _assert_public_values(skill: Path) -> None:
    try:
        for path in skill.rglob("*"):
            if not path.is_file():
                continue
            if path.name.casefold() in {"gws-oauth-client.json", "client_secret.json"}:
                raise ValueError("private oauth file")
            if path.suffix.casefold() not in _TEXT_SUFFIXES:
                continue
            raw = path.read_bytes()
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("text file too large")
            text = raw.decode("utf-8", errors="replace")
            secret = _JSON_SECRET.search(text)
            if secret and secret.group(1).strip().casefold() not in {
                "your-client-secret", "replace-me", "placeholder", "example",
            }:
                raise ValueError("private secret value")
            if (
                _GOOGLE_API_KEY.search(text)
                or "-----BEGIN PRIVATE KEY-----" in text
                or _REAL_USER_PATH.search(text)
            ):
                raise ValueError("private value")
    except (OSError, ValueError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_STAGE_PRIVATE",
            "AI 연결 파일에 공개하면 안 되는 값이 있어 기존 파일을 바꾸지 않았습니다.",
        ) from error


def _validated_staged_lock(payload: object, approval: ApprovedSkillSource) -> dict:
    try:
        if not isinstance(payload, dict) or set(payload) != {"version", "skills", "dismissed"}:
            raise ValueError("lock keys")
        if payload.get("version") != 3 or payload.get("dismissed") != {}:
            raise ValueError("lock header")
        skills = payload.get("skills")
        if not isinstance(skills, dict) or set(skills) != {"teacher-task-manager"}:
            raise ValueError("lock skills")
        entry = skills["teacher-task-manager"]
        expected_keys = {
            "source", "sourceType", "sourceUrl", "skillPath", "skillFolderHash",
            "installedAt", "updatedAt",
        }
        if not isinstance(entry, dict) or set(entry) != expected_keys:
            raise ValueError("lock entry keys")
        if (
            entry["source"] != "rheps/teacher-manager"
            or entry["sourceType"] != "github"
            or entry["sourceUrl"] != "https://github.com/rheps/teacher-manager.git"
            or entry["skillPath"] != "skills/teacher-task-manager/SKILL.md"
            or entry["skillFolderHash"] != approval.skill_folder_hash
            or not isinstance(entry["installedAt"], str)
            or not _LOCK_TIME.fullmatch(entry["installedAt"])
            or not isinstance(entry["updatedAt"], str)
            or not _LOCK_TIME.fullmatch(entry["updatedAt"])
        ):
            raise ValueError("lock entry values")
        return payload
    except (KeyError, TypeError, ValueError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_STAGE_INVALID",
            "AI 연결 설치 기록을 안전하게 확인하지 못해 기존 파일을 바꾸지 않았습니다.",
        ) from error


def _approved_file_hashes(skill: Path) -> dict[str, str]:
    actual: dict[str, str] = {}
    for path in skill.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(skill).as_posix()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        actual[relative] = digest.hexdigest()
    return actual


def _assert_approved_files(skill: Path, approval: ApprovedSkillSource) -> None:
    """공개 스킬의 파일 이름과 각 바이트가 사람이 승인한 목록과 정확히 같은지 본다."""
    try:
        if _approved_file_hashes(skill) != dict(approval.files):
            raise ValueError("approved files differ")
    except (OSError, ValueError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_STAGE_UNAPPROVED",
            "받은 AI 연결 파일이 검토한 공개판과 정확히 같지 않아 기존 파일을 바꾸지 않았습니다.",
        ) from error


def plan_is_already_approved(plan: InstallPlan, approval: ApprovedSkillSource) -> bool:
    """선택한 모든 AI 폴더와 설치 기록이 이미 승인판이면 다시 받거나 바꾸지 않는다."""
    try:
        approval = validate_approved_skill(approval)
        for destination in plan.destinations:
            _assert_real_tree(destination)
            _assert_public_values(destination)
            if _approved_file_hashes(destination) != dict(approval.files):
                return False
        entry = plan.current_lock.get("skills", {}).get("teacher-task-manager")
        return bool(
            isinstance(entry, dict)
            and entry.get("source") == approval.repository
            and entry.get("sourceType") == "github"
            and entry.get("sourceUrl") == "https://github.com/rheps/teacher-manager.git"
            and entry.get("skillPath") == "skills/teacher-task-manager/SKILL.md"
            and entry.get("skillFolderHash") == approval.skill_folder_hash
        )
    except (AiSkillInstallError, OSError, TypeError):
        return False


def validate_staged_install(
    stage_root: Path,
    approval: ApprovedSkillSource,
) -> tuple[Path, dict]:
    """skills 명령 결과가 승인한 공개 commit의 모든 파일과 정확히 같은지 확인한다."""
    approval = validate_approved_skill(approval)
    stage_root = Path(stage_root)
    skill = stage_root / "home" / ".agents" / "skills" / "teacher-task-manager"
    lock = stage_root / "home" / ".state" / "skills" / ".skill-lock.json"
    try:
        _assert_real_tree(skill)
        for relative in PUBLIC_SKILL_REQUIRED_FILES:
            if not (skill / relative).is_file():
                raise ValueError(f"required file missing: {relative}")
        if any(skill.rglob("prototype-*.html")) or any(skill.rglob("dev-fresh-start.flag")):
            raise ValueError("development file included")
        _assert_public_values(skill)
        _assert_real_tree(lock.parent)
        lock_info = lock.lstat()
        if _is_reparse(lock_info) or int(getattr(lock_info, "st_nlink", 1)) != 1:
            raise ValueError("unsafe lock")
        payload = _validated_staged_lock(
            json.loads(lock.read_text(encoding="utf-8")), approval,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise AiSkillInstallError(
            "AI_SKILLS_STAGE_INVALID",
            "AI 연결 파일을 안전하게 확인하지 못해 기존 파일을 바꾸지 않았습니다.",
        ) from error
    _assert_approved_files(skill, approval)
    return skill, payload


def apply_staged_install(
    stage_root: Path,
    plan: InstallPlan,
    approval: ApprovedSkillSource,
) -> None:
    """검증된 한 스킬만 선택한 AI 폴더와 lock에 한 묶음으로 적용한다."""
    source, staged_lock = validate_staged_install(stage_root, approval)
    destinations = list(plan.destinations)
    lock_path = plan.lock_path
    current_lock = deepcopy(plan.current_lock)
    if not all(_snapshot_matches(snapshot) for snapshot in (*plan.target_snapshots, plan.lock_snapshot)):
        raise AiSkillInstallError(
            "AI_SKILLS_CONCURRENT_CHANGE",
            "AI 연결을 준비하는 동안 기존 파일이 바뀌어 새 내용을 덮어쓰지 않았습니다.",
        )

    staged_entry = dict(staged_lock["skills"]["teacher-task-manager"])
    old_entry = current_lock["skills"].get("teacher-task-manager")
    if isinstance(old_entry, dict) and isinstance(old_entry.get("installedAt"), str):
        staged_entry["installedAt"] = old_entry["installedAt"]
    merged_lock = dict(current_lock)
    merged_skills = dict(current_lock["skills"])
    merged_skills["teacher-task-manager"] = staged_entry
    merged_lock["skills"] = merged_skills
    merged_bytes = (json.dumps(merged_lock, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    nonce = uuid.uuid4().hex
    prepared: list[tuple[Path, Path, Path]] = []
    lock_stage = lock_path.with_name(f".{lock_path.name}.{nonce}.stage")
    lock_backup = lock_path.with_name(f".{lock_path.name}.{nonce}.backup")
    lock_had_old = lock_path.exists()
    applied: list[_TargetApplyState] = []
    lock_old_moved = False
    lock_new_placed = False
    committed = False
    created_directories: list[Path] = []
    try:
        for destination in destinations:
            for made in _make_directories_tracking(destination.parent):
                if made not in created_directories:
                    created_directories.append(made)
            copy = destination.with_name(f".{destination.name}.{nonce}.stage")
            backup = destination.with_name(f".{destination.name}.{nonce}.backup")
            shutil.copytree(source, copy)
            _tree_digest(copy)
            prepared.append((destination, copy, backup))
        for made in _make_directories_tracking(lock_path.parent):
            if made not in created_directories:
                created_directories.append(made)
        with lock_stage.open("xb") as handle:
            handle.write(merged_bytes)
            handle.flush()
            os.fsync(handle.fileno())

        if not all(_snapshot_matches(snapshot) for snapshot in (*plan.target_snapshots, plan.lock_snapshot)):
            raise AiSkillInstallError(
                "AI_SKILLS_CONCURRENT_CHANGE",
                "AI 연결을 적용하기 직전에 기존 파일이 바뀌어 새 내용을 덮어쓰지 않았습니다.",
            )

        for destination, copy, backup in prepared:
            had_old = destination.exists()
            state = _TargetApplyState(destination, backup, had_old)
            applied.append(state)
            if had_old:
                os.replace(destination, backup)
                state.old_moved = True
            os.replace(copy, destination)
            state.new_placed = True
        if lock_had_old:
            os.replace(lock_path, lock_backup)
            lock_old_moved = True
        os.replace(lock_stage, lock_path)
        lock_new_placed = True
        committed = True
    except BaseException as error:
        rollback_errors: list[BaseException] = []
        if lock_new_placed:
            try:
                lock_path.unlink(missing_ok=True)
                lock_new_placed = False
            except Exception as rollback_error:  # noqa: BLE001 - 나머지 복구는 계속한다
                rollback_errors.append(rollback_error)
        if lock_old_moved:
            try:
                if os.path.lexists(lock_path):
                    raise OSError("new lock still exists")
                os.replace(lock_backup, lock_path)
                lock_old_moved = False
            except Exception as rollback_error:  # noqa: BLE001 - 대상 폴더 복구도 계속한다
                rollback_errors.append(rollback_error)
        for state in reversed(applied):
            if state.new_placed:
                try:
                    shutil.rmtree(state.destination)
                    state.new_placed = False
                except Exception as rollback_error:  # noqa: BLE001 - backup 복구는 따로 시도한다
                    rollback_errors.append(rollback_error)
            if state.old_moved:
                try:
                    if os.path.lexists(state.destination):
                        raise OSError("new target still exists")
                    os.replace(state.backup, state.destination)
                    state.old_moved = False
                except Exception as rollback_error:  # noqa: BLE001 - 다른 대상 복구는 계속한다
                    rollback_errors.append(rollback_error)
        if rollback_errors:
            raise AiSkillInstallError(
                "AI_SKILLS_ROLLBACK_INCOMPLETE",
                "AI 연결을 되돌리는 중 일부 파일이 잠겨 완전히 복구하지 못했습니다. "
                "Teacher Manager와 연결한 AI를 모두 닫고 컴퓨터를 다시 시작한 뒤 다시 확인해 주세요.",
            ) from error
        if isinstance(error, AiSkillInstallError):
            raise
        raise AiSkillInstallError(
            "AI_SKILLS_APPLY_FAILED",
            "AI 연결 파일을 적용하지 못해 기존 파일을 되돌렸습니다.",
        ) from error
    finally:
        try:
            lock_stage.unlink(missing_ok=True)
        except OSError:
            pass
        for _destination, copy, _backup in prepared:
            if copy.exists():
                shutil.rmtree(copy, ignore_errors=True)
        if not committed:
            for directory in reversed(created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass

    cleanup_errors: list[OSError] = []
    for state in applied:
        if state.had_old and state.backup.exists():
            try:
                shutil.rmtree(state.backup)
                state.old_moved = False
            except OSError as error:
                cleanup_errors.append(error)
    if lock_had_old and lock_backup.exists():
        try:
            lock_backup.unlink()
            lock_old_moved = False
        except OSError as error:
            cleanup_errors.append(error)
    if cleanup_errors:
        raise AiSkillInstallError(
            "AI_SKILLS_BACKUP_CLEANUP_INCOMPLETE",
            "새 AI 연결은 적용했지만 이전 연결의 임시 백업을 지우지 못했습니다. "
            "Teacher Manager와 연결한 AI를 모두 닫고 다시 실행해 주세요.",
        )
