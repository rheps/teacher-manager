# skills/teacher-task-manager/scripts/dashboard/fresh_start.py
"""개발 검증용 초기화 — dev-fresh-start.flag가 있는 컴퓨터에서만 동작한다.

버전이 바뀐 채 실행되면 설정 폴더 내용을 백업 폴더로 옮겨,
매 업데이트를 처음 사용하는 선생님의 눈으로 검증할 수 있게 한다.
설치판은 flag 파일이 남아 있어도 초기화하지 않으며, 예전 잘못된 초기화로
내 정보가 빠졌다면 가장 최근 정상 백업을 기준으로 되살리되,
거기 없는 파일은 다른 백업에서 보충한다(끊긴 초기화로 분산된 경우).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from brity_bridge import paths, process_win

_KEEP_NAMES = {"dev-fresh-start.flag", "last-run-version.txt"}
_PROFILE_NAME = "profile.generated.json"


class RecoveryError(RuntimeError):
    """설치판의 기존 설정 복구가 안전하게 끝나지 못했다."""


def _record_version(config_dir: Path, app_version: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    paths.last_run_version_path(config_dir).write_text(app_version + "\n", encoding="utf-8")


def _default_logout(run=process_win.run_captured) -> None:
    """구글 로그인은 설정 폴더가 아니라 gws에 있다 — 진짜 처음 흐름을 보려면 함께 지운다.

    Windows에서 "gws" 이름은 npm의 gws.cmd를 못 찾아 127로 실패하므로 gws.cmd로 재시도한다.
    """
    code, _output = run(["gws", "auth", "logout"], timeout=30)
    if code == 127:
        run(["gws.cmd", "auth", "logout"], timeout=30)


def _default_stop_helper() -> None:
    """이동 전에 감시 도우미를 끈다 — 열어둔 로그·기록 파일 잠금을 풀기 위해.

    도우미는 캡처 저장이 진행 중이면 닫기를 최대 20초 미룬다(tray_win
    CLOSE_WAIT_SECONDS). 그보다 길게 기다려야 "창이 사라짐 = 저장 완료"가 된다.
    """
    from dashboard import engine

    engine.stop_helper(timeout_seconds=30.0)


def _backup_time(backup_dir: Path) -> float:
    """백업 이름 끝의 만든 시각을 우선 사용하고, 옛 형식이면 폴더 시각을 사용한다."""
    date_and_time = backup_dir.name.rsplit("-", 2)[-2:]
    if len(date_and_time) == 2:
        try:
            return datetime.strptime("-".join(date_and_time), "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            pass
    return backup_dir.stat().st_mtime


def _read_json_dict(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _is_valid_backup(backup_dir: Path) -> bool:
    """처음 화면을 건너뛰는 데 쓰는 두 파일이 읽을 수 있는지 확인한다."""
    if _read_json_dict(backup_dir / _PROFILE_NAME) is None:
        return False
    setup_state = backup_dir / "setup-state.json"
    return not setup_state.exists() or _read_json_dict(setup_state) is not None


def _copy_item(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_missing(source: Path, destination: Path) -> None:
    """destination에 아직 없는 파일만 보충한다(있는 파일은 절대 덮지 않는다)."""
    if source.is_dir():
        if destination.exists() and not destination.is_dir():
            return
        for child in source.iterdir():
            _copy_missing(child, destination / child.name)
    elif source.is_file() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _tree_matches(source: Path, destination: Path) -> bool:
    """원본 삭제 전 마지막 안전판 — 복사본이 같은 구조·크기인지 확인한다."""
    if source.is_file():
        return destination.is_file() and source.stat().st_size == destination.stat().st_size
    if not destination.is_dir():
        return False
    for item in source.rglob("*"):
        target = destination / item.relative_to(source)
        if item.is_dir():
            if not target.is_dir():
                return False
        elif item.is_file():
            if not (target.is_file() and item.stat().st_size == target.stat().st_size):
                return False
    return True


def _discard_partial_copy(destination: Path) -> None:
    try:
        if destination.is_dir():
            shutil.rmtree(destination, ignore_errors=True)
        elif destination.exists():
            destination.unlink()
    except OSError:
        pass


def _move_item_safely(source: Path, destination: Path) -> bool:
    """원자적 rename을 먼저 쓰고, 안 되면 복사→검증→삭제 순서로 옮긴다.

    복사가 검증되기 전에는 원본을 절대 지우지 않는다. 복사까지 실패하면 원본을
    그대로 두고 False를 돌려준다 — 부분 초기화가 데이터 소실보다 낫다. 검증된
    복사 뒤의 삭제 실패(잠긴 파일)는 성공으로 친다: 자료는 이미 백업에 있다.
    """
    try:
        os.rename(source, destination)
        return True
    except OSError:
        pass
    try:
        _copy_item(source, destination)
        verified = _tree_matches(source, destination)
    except OSError:
        verified = False
    if not verified:
        _discard_partial_copy(destination)
        return False
    try:
        if source.is_dir():
            shutil.rmtree(source)
        else:
            source.unlink()
    except OSError:
        pass
    return True


def _restore_sources(folder: Path, *, profile_last: bool = False) -> list[Path]:
    items = [item for item in folder.iterdir() if item.name not in _KEEP_NAMES]
    if profile_last:
        return sorted(items, key=lambda item: (item.name == _PROFILE_NAME, item.name))
    return sorted(items, key=lambda item: item.name)


def _new_conflict_backup_dir(config_dir: Path) -> Path:
    root = config_dir.parent / "TeacherTaskManager-recovery"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = root / f"before-restore-{stamp}"
    folder.mkdir()
    return folder


def _preserve_current_conflicts(config_dir: Path, staged_dir: Path) -> Path | None:
    conflicts = [
        (config_dir / source.name)
        for source in _restore_sources(staged_dir)
        if (config_dir / source.name).exists()
    ]
    if not conflicts:
        return None
    saved_dir = _new_conflict_backup_dir(config_dir)
    for current in conflicts:
        _copy_item(current, saved_dir / current.name)
    return saved_dir


def _remove_mismatched_destination(source: Path, destination: Path) -> None:
    if not destination.exists() or source.is_dir() == destination.is_dir():
        return
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    else:
        shutil.rmtree(destination)


def _restore_latest_backup_if_needed(config_dir: Path) -> Path | None:
    """현재 내 정보가 없을 때 가장 최근 정상 백업의 사용자 설정을 복사한다.

    끊긴 초기화로 설정이 여러 백업 폴더에 분산됐을 수 있어, 기준 백업에 없는
    파일은 다른 백업에서 보충한다(최신 우선, 있는 파일은 덮지 않음).
    복구할 자료를 먼저 임시 폴더에 모두 복사한 뒤 적용한다. 현재 폴더와 겹치는
    자료는 별도 폴더에 보관하고, profile은 맨 마지막에 복사해 중간 실패 시 다음
    실행에서 다시 복구할 수 있게 한다.
    """
    profile = config_dir / _PROFILE_NAME
    if profile.exists():
        return None
    backup_root = config_dir.parent / "TeacherTaskManager-backup"
    if not backup_root.is_dir():
        return None
    candidates = [item for item in backup_root.iterdir() if item.is_dir()]
    if not candidates:
        return None
    ordered = sorted(candidates, key=_backup_time, reverse=True)
    latest = next((item for item in ordered if _is_valid_backup(item)), None)
    if latest is None:
        return None
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".teacher-manager-restore-", dir=str(config_dir.parent)
    ) as temp_name:
        staged_dir = Path(temp_name)
        for source in _restore_sources(latest):
            _copy_item(source, staged_dir / source.name)
        for backup in ordered:
            if backup == latest:
                continue
            for source in _restore_sources(backup):
                _copy_missing(source, staged_dir / source.name)
        _preserve_current_conflicts(config_dir, staged_dir)
        for source in _restore_sources(staged_dir, profile_last=True):
            destination = config_dir / source.name
            _remove_mismatched_destination(source, destination)
            _copy_item(source, destination)
    return latest


def prepare_for_start(config_dir: Path, app_version: str, *, installed: bool) -> Path | None:
    """설치판은 설정을 지키고, 개발 실행에서만 요청된 초기화를 허용한다."""
    config_dir = Path(config_dir)
    if installed:
        try:
            _restore_latest_backup_if_needed(config_dir)
            _record_version(config_dir, app_version)
        except Exception as error:
            raise RecoveryError("기존 설정을 안전하게 복구하지 못했어요") from error
        return None
    return maybe_fresh_start(config_dir, app_version)


def maybe_fresh_start(config_dir: Path, app_version: str, now: datetime | None = None,
                      logout=_default_logout, stop_helper=_default_stop_helper) -> Path | None:
    config_dir = Path(config_dir)
    flag = paths.fresh_start_flag_path(config_dir)
    version_file = paths.last_run_version_path(config_dir)
    recorded = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    if not flag.exists() or not recorded or recorded == app_version:
        _record_version(config_dir, app_version)
        return None
    try:
        stop_helper()
    except Exception:  # noqa: BLE001 - 도우미 종료 실패가 초기화를 막으면 안 된다
        pass
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup_dir = config_dir.parent / "TeacherTaskManager-backup" / f"{recorded}-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        for item in sorted(config_dir.iterdir()):
            if item.name in _KEEP_NAMES:
                continue
            _move_item_safely(item, backup_dir / item.name)
    finally:
        # 이동이 어디서 끊기든 버전은 기록한다 — 매 실행 재초기화(백업 분산)를 막는다.
        _record_version(config_dir, app_version)
    try:
        logout()
    except Exception:  # noqa: BLE001 - 로그아웃 실패가 초기화를 막으면 안 된다
        pass
    return backup_dir
