"""Teacher Manager 구성요소 파일을 여러 창과 프로세스가 안전하게 함께 쓴다."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
LIFECYCLE_MUTEX_NAME = "TeacherManagerLifecycle-7C1E9B7A-4B2E-4B7C-9A64-52B1E6F0A311"
LIFECYCLE_MUTEX_TIMEOUT = 10.0


class UnsafeLockPathError(OSError):
    """잠금 파일이나 그 폴더가 링크를 통해 적힌 위치 밖으로 나갔다."""


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(str(path)))


def _is_reparse_entry(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _nearest_existing_ancestor(path: Path) -> Path:
    candidate = _absolute_path(path)
    while not os.path.lexists(candidate):
        parent = candidate.parent
        if parent == candidate:
            raise UnsafeLockPathError("잠금 폴더의 실제 위치를 확인하지 못했습니다.")
        candidate = parent
    return candidate


def _assert_direct_entry(
    path: Path,
    *,
    require_regular: bool = False,
    require_directory: bool = False,
) -> None:
    expected = _absolute_path(path)
    if not os.path.lexists(expected):
        return
    try:
        info = expected.lstat()
        resolved = expected.resolve(strict=True)
    except OSError as error:
        raise UnsafeLockPathError("잠금 경로의 실제 위치를 확인하지 못했습니다.") from error
    if _is_reparse_entry(info) or resolved != expected:
        raise UnsafeLockPathError("잠금 경로가 링크나 정션을 가리킵니다.")
    if require_directory and not stat.S_ISDIR(info.st_mode):
        raise UnsafeLockPathError("잠금 폴더가 실제 폴더가 아닙니다.")
    if require_regular and (
        not stat.S_ISREG(info.st_mode)
        or int(getattr(info, "st_nlink", 1)) != 1
    ):
        raise UnsafeLockPathError("잠금 파일이 다른 파일과 연결되어 있습니다.")


def _assert_direct_directory_chain(path: Path) -> None:
    """적힌 폴더와 현재 존재하는 모든 조상이 실제 폴더인지 확인한다."""
    expected = _absolute_path(path)
    candidate = expected
    existing: list[Path] = []
    while True:
        if os.path.lexists(candidate):
            existing.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    if not existing:
        raise UnsafeLockPathError("잠금 폴더의 실제 위치를 확인하지 못했습니다.")
    for entry in reversed(existing):
        _assert_direct_entry(entry, require_directory=True)


def prepare_direct_directory(path: Path) -> Path:
    """링크·정션을 거치지 않는 실제 폴더만 만들고 그대로 돌려준다."""
    expected = _absolute_path(path)
    _assert_direct_directory_chain(_nearest_existing_ancestor(expected))
    expected.mkdir(parents=True, exist_ok=True)
    _assert_direct_directory_chain(expected)
    _assert_direct_entry(expected, require_directory=True)
    return expected


def prepare_direct_file_path(path: Path) -> Path:
    """파일을 열기 전 부모와 기존 파일이 직접 경로·단일 파일인지 확인한다."""
    expected = _absolute_path(path)
    prepare_direct_directory(expected.parent)
    _assert_direct_entry(expected, require_regular=True)
    return expected


def _prepare_direct_lock_path(path: Path) -> Path:
    return prepare_direct_file_path(path)


def _assert_open_lock_is_direct(path: Path, handle) -> tuple[int, int]:
    """파일을 연 뒤에도 같은 단일 파일인지 확인하고 나서만 한 바이트를 쓴다."""
    _assert_direct_directory_chain(path.parent)
    _assert_direct_entry(path, require_regular=True)
    try:
        entry = path.lstat()
        opened = os.fstat(handle.fileno())
    except OSError as error:
        raise UnsafeLockPathError("열린 잠금 파일을 다시 확인하지 못했습니다.") from error
    if (
        not stat.S_ISREG(opened.st_mode)
        or int(getattr(opened, "st_nlink", 1)) != 1
        or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise UnsafeLockPathError("열린 잠금 파일이 다른 파일과 연결되어 있습니다.")
    return int(opened.st_dev), int(opened.st_ino)


def assert_open_file_is_direct(path: Path, handle) -> tuple[int, int]:
    """열린 파일이 지금 적힌 경로의 단일 일반 파일인지 확인한다."""
    return _assert_open_lock_is_direct(_absolute_path(path), handle)


def direct_file_identity(path: Path) -> tuple[int, int]:
    """직접 경로의 단일 일반 파일 identity를 돌려준다."""
    expected = prepare_direct_file_path(path)
    if not os.path.lexists(expected):
        raise UnsafeLockPathError("확인할 파일이 없습니다.")
    try:
        info = expected.lstat()
    except OSError as error:
        raise UnsafeLockPathError("파일의 실제 위치를 확인하지 못했습니다.") from error
    return int(info.st_dev), int(info.st_ino)


def remove_owned_file(path: Path, identity: tuple[int, int]) -> bool:
    """만들 때 확인한 바로 그 단일 파일일 때만 지운다."""
    expected = _absolute_path(path)
    try:
        if not os.path.lexists(expected):
            return True
        current = direct_file_identity(expected)
        if current != tuple(identity):
            return False
        expected.unlink()
        return True
    except (OSError, UnsafeLockPathError):
        return False


def _thread_lock(path: Path) -> threading.Lock:
    # 링크를 안전 확인하기 전에는 따라가지 않고, 적힌 절대 경로 자체로 묶는다.
    key = os.path.normcase(os.path.abspath(str(path)))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _try_file_lock(handle) -> bool:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_file(handle) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_windows() -> bool:
    return os.name == "nt"


@contextmanager
def _windows_lifecycle_mutex(name: str, *, timeout: float):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = (wintypes.HANDLE,)
    release_mutex.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_mutex(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        milliseconds = min(0xFFFFFFFE, max(0, int(float(timeout) * 1000)))
        result = wait_for_single_object(handle, milliseconds)
        if result == 0x00000102:  # WAIT_TIMEOUT
            raise TimeoutError(f"Teacher Manager 수명주기 잠금 대기 시간 초과: {name}")
        if result == 0xFFFFFFFF:  # WAIT_FAILED
            raise ctypes.WinError(ctypes.get_last_error())
        if result not in (0x00000000, 0x00000080):  # WAIT_OBJECT_0, WAIT_ABANDONED
            raise OSError(f"알 수 없는 Windows 잠금 결과: {result}")
        acquired = True
        yield
    finally:
        try:
            if acquired and not release_mutex(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            close_handle(handle)


@contextmanager
def _portable_lifecycle_mutex(name: str, *, timeout: float):
    """Windows가 아닌 개발/시험 환경에서는 같은 이름의 파일 잠금으로 대신한다."""
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    lock_path = (
        Path(tempfile.gettempdir())
        / "BigSilverEduLab"
        / "TeacherManager"
        / "lifecycle-locks"
        / f"{digest}.lock"
    )
    with exclusive_file_lock(lock_path, timeout=timeout):
        yield


@contextmanager
def exclusive_lifecycle_mutex(
    name: str = LIFECYCLE_MUTEX_NAME,
    *,
    timeout: float = LIFECYCLE_MUTEX_TIMEOUT,
):
    """Setup과 구성요소 갱신이 함께 쓰는 컴퓨터 전체 수명주기 잠금이다."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("수명주기 잠금 이름이 비어 있습니다.")
    if timeout < 0:
        raise ValueError("수명주기 잠금 대기 시간은 0 이상이어야 합니다.")
    lock = _windows_lifecycle_mutex if _is_windows() else _portable_lifecycle_mutex
    with lock(name, timeout=float(timeout)):
        yield


@contextmanager
def exclusive_file_lock(path: Path, *, timeout: float = 10.0):
    """같은 잠금 파일을 쓰는 모든 스레드와 프로세스를 한 번에 하나만 들인다."""
    path = _prepare_direct_lock_path(Path(path))
    local_lock = _thread_lock(path)
    if not local_lock.acquire(timeout=timeout):
        raise TimeoutError(f"구성요소 잠금 대기 시간 초과: {path}")
    handle = None
    locked = False
    try:
        try:
            handle = path.open("xb")
        except FileExistsError:
            # r+b는 기존 파일을 바꾸지 않는다. 열린 뒤 경로와 handle identity를
            # 맞춰 본 다음에만 _try_file_lock이 첫 바이트를 쓴다.
            handle = path.open("r+b")
        assert_open_file_is_direct(path, handle)
        deadline = time.monotonic() + timeout
        while not _try_file_lock(handle):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"구성요소 잠금 대기 시간 초과: {path}")
            time.sleep(0.02)
        locked = True
        yield
    finally:
        try:
            if handle is not None:
                try:
                    if locked:
                        _unlock_file(handle)
                finally:
                    handle.close()
        finally:
            local_lock.release()


def atomic_write_text_unique(path: Path, text: str, encoding: str = "utf-8") -> None:
    """같은 폴더의 고유 임시 파일을 완성한 뒤 대상 파일로 한 번에 바꾼다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
